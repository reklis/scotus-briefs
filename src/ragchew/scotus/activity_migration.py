"""Explicit no-model migration to dated SCOTUS public activity contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from ragchew.scotus.discovery import normalize_docket
from ragchew.scotus.public_contracts import (
    STATIC_PUBLIC_SCHEMA_VERSION,
    PublicBriefRevisionSummary,
    PublicCaseBrief,
    PublicDisposition,
    ScotusPublicProjection,
    derive_latest_court_document_date,
    public_case_key,
)
from ragchew.scotus.static_contracts import (
    CASE_REVISION_SCHEMA_VERSION,
    STATE_SCHEMA_VERSION,
    CaseRevisionPointer,
    DispositionDiscoveryState,
    PendingReason,
    PendingWork,
    PublicationState,
    PublicCaseRevisionRecord,
    canonical_json_bytes,
    sha256_hex,
)
from ragchew.scotus.static_state import (
    GeneratedContent,
    StaticStateError,
    StoredCaseRevision,
)

_MIGRATION_NOTE = "Migrated to the dated official Court activity contract without model use."


@dataclass(frozen=True)
class ActivityContractMigration:
    """Sanitized outcome of one deterministic generated-content migration."""

    content: GeneratedContent
    migrated_case_keys: tuple[str, ...]
    exact_backfill_case_keys: tuple[str, ...]
    unmatched_case_keys: tuple[str, ...]
    parent_release_id: str | None


def _state_disposition(state: DispositionDiscoveryState) -> PublicDisposition:
    return PublicDisposition(
        kind=state.kind,
        official_url=state.official_url,
        publication_date=state.publication_date,
        revision_date=state.revision_date,
    )


def _exact_matches(
    case: PublicCaseBrief,
    url: str,
    states: tuple[DispositionDiscoveryState, ...],
) -> tuple[DispositionDiscoveryState, ...]:
    try:
        docket = normalize_docket(case.primary_docket)
    except ValueError as error:
        raise StaticStateError("legacy case docket is not valid for activity migration") from error
    return tuple(
        state
        for state in states
        if state.term == case.term
        and state.official_url == url
        and docket
        in {
            normalize_docket(value)
            for value in (state.primary_docket, *state.consolidated_dockets)
        }
    )


def _migrate_case(
    case: PublicCaseBrief,
    states: tuple[DispositionDiscoveryState, ...],
    *,
    revision_number: int,
    migrated_at: datetime,
) -> tuple[PublicCaseBrief, bool, bool]:
    """Return current case, whether an exact backfill occurred, and unmatched status."""
    structured = {item.official_url: item for item in case.dispositions}
    unmatched: list[str] = []
    exact_backfill = False

    for url in case.official_disposition_urls:
        matches = _exact_matches(case, url, states)
        if len(matches) > 1:
            identities = {
                (
                    item.kind,
                    item.publication_date,
                    item.revision_date,
                    item.metadata_sha256,
                )
                for item in matches
            }
            if len(identities) > 1:
                raise StaticStateError("conflicting exact disposition date backfill")
            # Duplicate logical rows are not guessed through even if their metadata is
            # currently equal: one URL/docket must identify exactly one reviewed row.
            raise StaticStateError("ambiguous exact disposition date backfill")
        if not matches:
            unmatched.append(url)
            continue
        candidate = _state_disposition(matches[0])
        existing = structured.get(url)
        if existing is not None and existing != candidate:
            raise StaticStateError("disposition state conflicts with public activity")
        structured[url] = candidate
        exact_backfill = True

    dispositions = tuple(
        sorted(
            structured.values(),
            key=lambda item: (
                item.publication_date,
                item.revision_date or item.publication_date,
                item.kind,
                item.official_url,
            ),
        )
    )
    latest = derive_latest_court_document_date(
        case.arguments,
        dispositions,
        legacy_argument_date=(case.argument_date if unmatched else None),
    )
    revision_history = (
        *case.revisions,
        PublicBriefRevisionSummary(
            revision_number=revision_number,
            maturity=case.maturity,
            created_at=migrated_at,
            correction_note=_MIGRATION_NOTE,
        ),
    )
    payload = case.model_dump(mode="python")
    payload.update(
        {
            "schema_version": STATIC_PUBLIC_SCHEMA_VERSION,
            "argument_date": (
                max(item.argument_date for item in case.arguments)
                if case.arguments
                else None
            ),
            "latest_court_document_date": latest,
            "official_disposition_urls": tuple(sorted(unmatched)),
            "undated_disposition_date_fallback": (
                "latest_argument_date" if unmatched else None
            ),
            "dispositions": dispositions,
            "revisions": revision_history,
            "updated_at": migrated_at,
        }
    )
    return PublicCaseBrief.model_validate(payload), exact_backfill, bool(unmatched)


def require_current_activity_contracts(content: GeneratedContent) -> None:
    """Fail before Court/model processing unless explicit migration is complete."""
    if content.publication.schema_version != STATE_SCHEMA_VERSION:
        raise StaticStateError("generated-content activity contract migration is required")
    if content.projection is not None:
        if content.projection.schema_version != STATIC_PUBLIC_SCHEMA_VERSION or any(
            case.schema_version != STATIC_PUBLIC_SCHEMA_VERSION
            or case.latest_court_document_date is None
            for case in content.projection.cases
        ):
            raise StaticStateError("generated-content activity contract migration is required")
        pointers = {item.case_key: item for item in content.publication.cases}
        expected_undated = tuple(
            sorted(
                public_case_key(case.term, case.primary_docket)
                for case in content.projection.cases
                if case.undated_disposition_date_fallback is not None
            )
        )
        if content.publication.undated_disposition_case_keys != expected_undated:
            raise StaticStateError("undated disposition migration state is inconsistent")
        if any(
            item.reason is PendingReason.DATE_BACKFILL_UNMATCHED
            and item.case_key not in expected_undated
            for item in content.publication.pending_work
        ):
            raise StaticStateError("undated disposition pending state is stale")
        for case in content.projection.cases:
            key = public_case_key(case.term, case.primary_docket)
            pointer = pointers.get(key)
            if pointer is None:
                raise StaticStateError("current projection is missing a case pointer")
            active = content.revisions.get((key, pointer.active_revision))
            if active is None or active.record.schema_version != CASE_REVISION_SCHEMA_VERSION:
                raise StaticStateError("generated-content activity contract migration is required")


def migrate_activity_contracts(
    content: GeneratedContent,
    *,
    migrated_at: datetime,
    watermark: datetime | None = None,
    dispositions: tuple[DispositionDiscoveryState, ...] | None = None,
) -> ActivityContractMigration:
    """Append current revisions using only exact reviewed disposition metadata.

    Historical revision objects and their serialized bytes are carried by reference.
    The caller must export/finalize a new release whose parent is ``parent_release_id``;
    normal promotion CAS then protects both the release parent and branch digest.
    """
    if migrated_at.tzinfo is None or migrated_at.utcoffset() is None:
        raise ValueError("migration timestamp must be timezone-aware")
    if watermark is not None and (watermark.tzinfo is None or watermark.utcoffset() is None):
        raise ValueError("migration watermark must be timezone-aware")
    if content.projection is None:
        raise StaticStateError("activity migration requires a complete public projection")
    if content.release is None:
        raise StaticStateError("activity migration requires an active parent release")
    if content.publication.active_release_id != content.release.release_id:
        raise StaticStateError("activity migration parent release is inconsistent")

    states = dispositions if dispositions is not None else content.publication.dispositions
    state_keys = tuple(item.logical_key for item in states)
    if state_keys != tuple(sorted(state_keys)) or len(state_keys) != len(set(state_keys)):
        raise StaticStateError("migration disposition state must be uniquely ordered")

    pointers = {item.case_key: item for item in content.publication.cases}
    revisions = dict(content.revisions)
    current_cases: dict[str, PublicCaseBrief] = {}
    migrated: list[str] = []
    backfilled: list[str] = []
    unmatched: list[str] = []
    current_pointers: dict[str, CaseRevisionPointer] = {}

    for case in content.projection.cases:
        key = public_case_key(case.term, case.primary_docket)
        pointer = pointers.get(key)
        if pointer is None:
            raise StaticStateError("migration case pointer is missing")
        active = revisions.get((key, pointer.active_revision))
        if active is None or active.record.case != case:
            raise StaticStateError("migration active case revision is inconsistent")
        if (
            active.record.case_sha256 != pointer.active_case_sha256
            or active.serialized != canonical_json_bytes(active.record)
        ):
            raise StaticStateError("migration active case digest or bytes conflict")

        needs_migration = case.schema_version != STATIC_PUBLIC_SCHEMA_VERSION or any(
            _exact_matches(case, url, states)
            for url in case.official_disposition_urls
        )
        if not needs_migration:
            current_cases[key] = case
            current_pointers[key] = pointer
            if case.official_disposition_urls:
                unmatched.append(key)
            continue

        number = pointer.active_revision + 1
        migrated_case, had_backfill, has_unmatched = _migrate_case(
            case,
            states,
            revision_number=number,
            migrated_at=migrated_at,
        )
        case_bytes = canonical_json_bytes(migrated_case)
        case_digest = sha256_hex(case_bytes)
        record = PublicCaseRevisionRecord(
            case_key=key,
            revision_number=number,
            accepted_at=migrated_at,
            case_sha256=case_digest,
            previous_case_sha256=pointer.active_case_sha256,
            case=migrated_case,
        )
        revisions[(key, number)] = StoredCaseRevision(
            record=record,
            serialized=canonical_json_bytes(record),
        )
        current_pointers[key] = CaseRevisionPointer(
            case_key=pointer.case_key,
            term=pointer.term,
            primary_docket=pointer.primary_docket,
            active_revision=number,
            active_slug=pointer.active_slug,
            active_case_sha256=case_digest,
            processor_sha256=pointer.processor_sha256,
            legacy_slugs=pointer.legacy_slugs,
        )
        current_cases[key] = migrated_case
        migrated.append(key)
        if had_backfill:
            backfilled.append(key)
        if has_unmatched:
            unmatched.append(key)

    if (
        not migrated
        and content.projection.schema_version == STATIC_PUBLIC_SCHEMA_VERSION
        and content.publication.schema_version == STATE_SCHEMA_VERSION
    ):
        return ActivityContractMigration(
            content=content,
            migrated_case_keys=(),
            exact_backfill_case_keys=(),
            unmatched_case_keys=tuple(sorted(unmatched)),
            parent_release_id=content.release.release_id,
        )

    projection = ScotusPublicProjection(
        schema_version=STATIC_PUBLIC_SCHEMA_VERSION,
        watermark=watermark or content.projection.watermark,
        generated_at=migrated_at,
        cases=tuple(current_cases[key] for key in sorted(current_cases)),
        disclosure=content.projection.disclosure,
        site_name=content.projection.site_name,
    )
    pending = {
        item.case_key: item
        for item in content.publication.pending_work
        if not (
            item.reason is PendingReason.DATE_BACKFILL_UNMATCHED
            and item.case_key in backfilled
        )
    }
    for key in unmatched:
        pending.setdefault(
            key,
            PendingWork(
                case_key=key,
                reason=PendingReason.DATE_BACKFILL_UNMATCHED,
                attempts=0,
                first_seen_at=migrated_at,
            ),
        )
    publication_payload = content.publication.model_dump(mode="python")
    publication_payload.update(
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "updated_at": migrated_at,
            "dispositions": states,
            "undated_disposition_case_keys": tuple(sorted(unmatched)),
            "cases": tuple(current_pointers[key] for key in sorted(current_pointers)),
            "pending_work": tuple(pending[key] for key in sorted(pending)),
        }
    )
    publication = PublicationState.model_validate(publication_payload)
    migrated_content = replace(
        content,
        projection=projection,
        publication=publication,
        release=None,
        revisions=revisions,
    )
    return ActivityContractMigration(
        content=migrated_content,
        migrated_case_keys=tuple(sorted(migrated)),
        exact_backfill_case_keys=tuple(sorted(backfilled)),
        unmatched_case_keys=tuple(sorted(unmatched)),
        parent_release_id=content.release.release_id,
    )
