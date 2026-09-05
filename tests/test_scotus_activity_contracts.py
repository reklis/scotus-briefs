from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from ragchew.proceedings.sources.supreme_court import SlipOpinionKind
from ragchew.scotus.activity_migration import (
    migrate_activity_contracts,
    require_current_activity_contracts,
)
from ragchew.scotus.discovery import ScotusDispositionCandidate, disposition_state
from ragchew.scotus.public_contracts import (
    PublicCaseBrief,
    PublicDisposition,
    ScotusPublicProjection,
    public_case_key,
)
from ragchew.scotus.static_contracts import (
    CaseRevisionPointer,
    DispositionDiscoveryState,
    PendingReason,
    PublicationState,
    PublicCaseRevisionRecord,
    ReleaseManifest,
    canonical_json_bytes,
    sha256_hex,
)
from ragchew.scotus.static_export import StaticSiteExporter, build_search_index
from ragchew.scotus.static_state import (
    GeneratedContent,
    StaticStateError,
    StaticStateStore,
    StoredCaseRevision,
)
from ragchew.scotus.static_urls import StaticUrlPolicy, latest_court_document_date

NOW = datetime(2026, 8, 28, 3, 17, tzinfo=UTC)
ZERO = "0" * 64
URL = "https://www.supremecourt.gov/opinions/25pdf/25-466_abcd.pdf"


def _current_case() -> PublicCaseBrief:
    payload = json.loads(Path("tests/fixtures/static/one-case.json").read_text())
    return PublicCaseBrief.model_validate(payload["projection"]["cases"][0])


def _legacy_case(*, disposition_url: str | None = URL) -> PublicCaseBrief:
    payload = _current_case().model_dump(mode="python")
    payload["schema_version"] = "1.0"
    payload.pop("latest_court_document_date")
    payload.pop("dispositions")
    payload["official_disposition_urls"] = (() if disposition_url is None else (disposition_url,))
    return PublicCaseBrief.model_validate(payload)


def _legacy_content(case: PublicCaseBrief) -> GeneratedContent:
    key = public_case_key(case.term, case.primary_docket)
    case_digest = sha256_hex(canonical_json_bytes(case))
    record = PublicCaseRevisionRecord(
        schema_version="1.0",
        case_key=key,
        revision_number=1,
        accepted_at=NOW,
        case_sha256=case_digest,
        case=case,
    )
    projection = ScotusPublicProjection(
        schema_version="1.0", watermark=NOW, generated_at=NOW, cases=(case,)
    )
    publication = PublicationState(
        schema_version="1.0",
        active_release_id=ZERO,
        updated_at=NOW,
        cases=(
            CaseRevisionPointer(
                case_key=key,
                term=case.term,
                primary_docket=case.primary_docket,
                active_revision=1,
                active_slug=case.slug,
                active_case_sha256=case_digest,
            ),
        ),
    )
    release = ReleaseManifest(
        release_id=ZERO,
        source_commit="a" * 40,
        projection_sha256=sha256_hex(canonical_json_bytes(projection)),
        config_sha256="b" * 64,
        tool_version="legacy-v1",
        generated_at=NOW,
        files=(),
        case_count=1,
        page_count=1,
    )
    serialized = canonical_json_bytes(record)
    return GeneratedContent(
        projection=projection,
        publication=publication,
        cost_ledger=GeneratedContent.empty().cost_ledger,
        release=release,
        revisions={(key, 1): StoredCaseRevision(record, serialized)},
    )


def _disposition(
    *, revised: bool = True, url: str = URL
) -> DispositionDiscoveryState:
    candidate = ScotusDispositionCandidate(
        term="2025",
        primary_docket="25-466",
        caption="Synthetic Example v. Agency",
        release_number="12",
        kind=SlipOpinionKind.OPINION,
        publication_date=NOW + timedelta(days=1),
        official_url=url,
        revision_date=(NOW + timedelta(days=3) if revised else None),
        revision_reference_url=(
            "https://www.supremecourt.gov/opinions/25pdf/25-466_diff.pdf"
            if revised
            else None
        ),
    )
    return disposition_state(candidate, case_key="2025-25-466")


def test_current_public_contract_supports_zero_arguments_and_revised_disposition() -> None:
    base = _current_case()
    revised = PublicDisposition(
        kind="opinion",
        official_url=URL,
        publication_date=NOW + timedelta(days=1),
        revision_date=NOW + timedelta(days=3),
    )
    payload = base.model_dump(mode="python")
    payload.update(
        {
            "argument_date": None,
            "latest_court_document_date": revised.revision_date,
            "arguments": (),
            "official_detail_url": None,
            "dispositions": (revised,),
        }
    )
    case = PublicCaseBrief.model_validate(payload)
    assert case.arguments == ()
    assert latest_court_document_date(case) == revised.revision_date

    payload["latest_court_document_date"] = revised.publication_date
    with pytest.raises(ValidationError, match="latest Court document date"):
        PublicCaseBrief.model_validate(payload)

    argued_payload = base.model_dump(mode="python")
    argued_payload["official_disposition_urls"] = (URL,)
    with pytest.raises(ValidationError, match="explicit migration fallback"):
        PublicCaseBrief.model_validate(argued_payload)


@pytest.mark.parametrize(
    "update,match",
    (
        ({"kind": "order"}, "literal_error"),
        ({"official_url": "https://example.test/opinion.pdf"}, "official Court host"),
        ({"revision_date": NOW}, "must follow publication"),
    ),
)
def test_public_disposition_rejects_malformed_values(
    update: dict[str, object], match: str
) -> None:
    payload: dict[str, object] = {
        "kind": "opinion",
        "official_url": URL,
        "publication_date": NOW + timedelta(days=1),
        "revision_date": NOW + timedelta(days=2),
    }
    payload.update(update)
    with pytest.raises(ValidationError, match=match):
        PublicDisposition.model_validate(payload)


def test_exact_migration_backfills_without_rewriting_v1_bytes_ids_or_paths(
    tmp_path: Path,
) -> None:
    original = _legacy_content(_legacy_case())
    original_bytes = original.revisions[("2025-25-466", 1)].serialized
    migration = migrate_activity_contracts(
        original,
        migrated_at=NOW + timedelta(days=4),
        dispositions=(_disposition(),),
    )
    migrated = migration.content
    assert migration.exact_backfill_case_keys == ("2025-25-466",)
    assert migration.unmatched_case_keys == ()
    assert migrated.release is None
    assert migrated.revisions[("2025-25-466", 1)].serialized == original_bytes
    assert migrated.publication.cases[0].case_key == "2025-25-466"
    assert migrated.publication.cases[0].active_slug == original.publication.cases[0].active_slug
    assert migrated.publication.cases[0].active_revision == 2
    current = migrated.projection.cases[0]  # type: ignore[union-attr]
    assert current.official_disposition_urls == ()
    assert current.dispositions[0].publication_date == NOW + timedelta(days=1)
    assert current.latest_court_document_date == NOW + timedelta(days=3)

    urls = StaticUrlPolicy("https://example.test", "/", "/scotus/")
    assert urls.case(current) == urls.case(original.projection.cases[0])  # type: ignore[union-attr]
    search = build_search_index(migrated.projection, urls)  # type: ignore[arg-type]
    assert search.cases[0].latest_court_document_date == "2026-08-31"
    assert search.cases[0].argument_date == "2026-08-28"

    exported = StaticSiteExporter(urls).export(
        migrated.projection,  # type: ignore[arg-type]
        tmp_path / "site",
        source_commit="c" * 40,
        build_epoch=NOW + timedelta(days=4),
        config_sha256="d" * 64,
        previous_release_id=ZERO,
    )
    finalized = StaticStateStore(tmp_path / "unused").finalize_candidate(
        tmp_path / "candidate", migrated, exported.manifest
    )
    assert finalized.release is not None
    assert finalized.release.previous_release_id == ZERO
    reloaded = StaticStateStore(tmp_path / "candidate").load()
    assert reloaded.revisions[("2025-25-466", 1)].serialized == original_bytes
    require_current_activity_contracts(reloaded)
    repeated = migrate_activity_contracts(
        reloaded, migrated_at=NOW + timedelta(days=5)
    )
    assert repeated.content is reloaded
    assert repeated.migrated_case_keys == ()


def test_unmatched_migration_retains_url_and_argument_date_fallback() -> None:
    migration = migrate_activity_contracts(
        _legacy_content(_legacy_case()),
        migrated_at=NOW + timedelta(days=1),
        dispositions=(_disposition(url="https://www.supremecourt.gov/opinions/25pdf/other.pdf"),),
    )
    case = migration.content.projection.cases[0]  # type: ignore[union-attr]
    assert migration.exact_backfill_case_keys == ()
    assert migration.unmatched_case_keys == ("2025-25-466",)
    assert case.official_disposition_urls == (URL,)
    assert case.dispositions == ()
    assert case.latest_court_document_date == case.argument_date
    assert migration.content.publication.pending_work[0].reason is (
        PendingReason.DATE_BACKFILL_UNMATCHED
    )
    assert migration.content.publication.undated_disposition_case_keys == (
        "2025-25-466",
    )


def test_migration_rejects_digest_conflict_and_preprocessing_requires_migration() -> None:
    original = _legacy_content(_legacy_case(disposition_url=None))
    with pytest.raises(StaticStateError, match="migration is required"):
        require_current_activity_contracts(original)

    pointer = original.publication.cases[0].model_copy(
        update={"active_case_sha256": "f" * 64}
    )
    conflicted = replace(
        original,
        publication=original.publication.model_copy(update={"cases": (pointer,)}),
    )
    with pytest.raises(StaticStateError, match="digest or bytes conflict"):
        migrate_activity_contracts(conflicted, migrated_at=NOW + timedelta(days=1))


def test_legacy_reargument_reader_accepts_historical_top_level_argument_date() -> None:
    payload = json.loads(Path("tests/fixtures/static/reargument.json").read_text())[
        "projection"
    ]["cases"][0]
    payload["schema_version"] = "1.0"
    payload["argument_date"] = payload["arguments"][0]["argument_date"]
    payload.pop("latest_court_document_date")
    payload.pop("dispositions")
    legacy = PublicCaseBrief.model_validate(payload)
    assert legacy.argument_date == legacy.arguments[0].argument_date
    migration = migrate_activity_contracts(
        _legacy_content(legacy), migrated_at=NOW + timedelta(days=1)
    )
    current = migration.content.projection.cases[0]  # type: ignore[union-attr]
    assert current.argument_date == current.arguments[-1].argument_date


def test_legacy_reader_round_trips_exact_canonical_bytes() -> None:
    content = _legacy_content(_legacy_case())
    case = content.projection.cases[0]  # type: ignore[union-attr]
    serialized = canonical_json_bytes(case)
    assert b'"schema_version":"1.0"' in serialized
    assert b'"latest_court_document_date"' not in serialized
    assert b'"dispositions"' not in serialized
    assert canonical_json_bytes(PublicCaseBrief.model_validate_json(serialized)) == serialized
