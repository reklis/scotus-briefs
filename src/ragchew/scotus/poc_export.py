"""Operator-only recovery of accepted public briefs from a local POC database.

This is deliberately separate from the legacy projection exporter.  It is only for
an otherwise unrecoverable POC whose projection table is empty.  The SQL below is an
allowlist: it reads accepted public brief fields, public claim provenance, and the
small amount of official case metadata needed by :class:`PublicCaseBrief`.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ragchew.scotus.contracts import (
    BriefArgumentAnalysis,
    BriefMaturity,
    BriefSection,
    ScotusCaseStatus,
)
from ragchew.scotus.public_contracts import (
    PublicArgumentAnalysis,
    PublicBriefRevisionSummary,
    PublicBriefSection,
    PublicCaseBrief,
    PublicCaseHistoryEvent,
    PublicSourceLink,
    ScotusPublicProjection,
    public_case_key,
    public_case_slug,
)
from ragchew.scotus.static_contracts import assert_public_payload
from ragchew.scotus.static_export import StaticSiteExporter
from ragchew.scotus.static_state import GeneratedContent, StaticStateError, StaticStateStore
from ragchew.scotus.static_urls import StaticUrlPolicy
from ragchew.scotus.static_validation import validate_static_candidate

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_INTERNAL_UUID = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)


class PocExportError(RuntimeError):
    """The POC records cannot be represented as sanitized generated content."""


class _BriefBody(BaseModel):
    """The only fields selected from ``public_payload`` by the recovery query."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    brief_id: UUID
    case_id: UUID
    argument_id: UUID
    revision_number: int = Field(ge=1)
    maturity: BriefMaturity
    correction_note: str | None = Field(default=None, max_length=1_000)
    created_at: datetime
    title: str = Field(min_length=1, max_length=180)
    title_claim_ids: tuple[UUID, ...] = Field(min_length=1)
    dek: str = Field(min_length=1, max_length=500)
    dek_claim_ids: tuple[UUID, ...] = Field(min_length=1)
    sections: tuple[BriefSection, ...] = Field(min_length=1)
    argument_analyses: tuple[BriefArgumentAnalysis, ...] = Field(min_length=1)
    claim_ids: tuple[UUID, ...] = Field(min_length=1)


@dataclass(frozen=True)
class _BriefRow:
    brief_id: UUID
    case_id: UUID
    revision_number: int
    maturity: BriefMaturity
    correction_note: str | None
    created_at: datetime
    term: str
    primary_docket: str
    caption: str
    body: _BriefBody


@dataclass(frozen=True)
class _Provenance:
    case_id: UUID
    official_url: str
    source_label: str
    page_label: str


@dataclass(frozen=True)
class _Session:
    argument_id: UUID
    case_id: UUID
    argument_date: datetime
    sequence: int
    reargument: bool
    official_detail_url: str
    official_transcript_url: str


_CASE_EVENT_EXPLANATIONS = {
    ScotusCaseStatus.DOCKETED: "An official docket record was verified.",
    ScotusCaseStatus.ARGUED: "An official oral-argument transcript was verified.",
    ScotusCaseStatus.REARGUED: "Official transcripts for a later argument were verified.",
    ScotusCaseStatus.ORDER_ISSUED: "An official Court order was verified.",
    ScotusCaseStatus.DECIDED: "An official Court opinion was verified.",
    ScotusCaseStatus.CORRECTED: "Official case material or analysis was corrected.",
    ScotusCaseStatus.UNRESOLVED: "The verified source set does not establish a later event.",
}


def _json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _public_text(value: str | None) -> str | None:
    """Replace legacy inline claim IDs without exposing or silently dropping prose."""
    if value is None:
        return None
    return _INTERNAL_UUID.sub("official source", value)


def _source_links(
    claim_ids: tuple[UUID, ...],
    *,
    case_id: UUID,
    provenance: Mapping[UUID, _Provenance],
) -> tuple[PublicSourceLink, ...]:
    grouped: set[tuple[str, str, str]] = set()
    for claim_id in claim_ids:
        claim = provenance.get(claim_id)
        if claim is None or claim.case_id != case_id:
            raise PocExportError("accepted brief has missing or cross-case public provenance")
        grouped.add((claim.source_label, claim.official_url, claim.page_label))
    return tuple(
        PublicSourceLink(
            evidence_type=label,
            label=f"Official Supreme Court {label} — {page}",
            official_url=url,
            page_label=page,
        )
        for label, url, page in sorted(grouped)
    )


class PostgresPocBriefReader:
    """Read the minimal public-recovery allowlist in one read-only snapshot."""

    def __init__(
        self,
        dsn: str,
        pool: ConnectionPool[Connection[dict[str, Any]]] | None = None,
    ) -> None:
        self.pool = pool or ConnectionPool(
            conninfo=dsn,
            kwargs={"row_factory": dict_row},
            min_size=1,
            max_size=1,
            open=True,
        )
        self._owns_pool = pool is None

    def case_revisions(self) -> tuple[tuple[PublicCaseBrief, ...], ...]:
        """Reconstruct every accepted public revision, grouped by public case."""
        try:
            with self.pool.connection() as connection, connection.transaction():
                # Even an accidentally privileged operator DSN cannot mutate the POC.
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                projection_count = connection.execute(
                    "SELECT count(*) AS count FROM scotus_public_projections"
                ).fetchone()
                if projection_count is None or int(projection_count["count"]) != 0:
                    raise PocExportError(
                        "POC brief recovery requires an empty public projection table"
                    )
                brief_rows = connection.execute(
                    """SELECT b.brief_id,b.case_id,b.argument_id,b.revision_number,b.maturity,
                              b.correction_note,b.created_at,c.term,c.primary_docket,
                              COALESCE(c.public_caption,c.caption_private) AS caption,
                              b.public_payload ->> 'schema_version' AS payload_schema_version,
                              b.public_payload ->> 'brief_id' AS payload_brief_id,
                              b.public_payload ->> 'case_id' AS payload_case_id,
                              b.public_payload ->> 'argument_id' AS payload_argument_id,
                              b.public_payload ->> 'revision_number' AS payload_revision_number,
                              b.public_payload ->> 'maturity' AS payload_maturity,
                              b.public_payload ->> 'correction_note' AS payload_correction_note,
                              b.public_payload ->> 'created_at' AS payload_created_at,
                              b.public_payload ->> 'title' AS title,
                              b.public_payload -> 'title_claim_ids' AS title_claim_ids,
                              b.public_payload ->> 'dek' AS dek,
                              b.public_payload -> 'dek_claim_ids' AS dek_claim_ids,
                              b.public_payload -> 'sections' AS sections,
                              b.public_payload -> 'argument_analyses' AS argument_analyses,
                              b.public_payload -> 'claim_ids' AS claim_ids
                       FROM scotus_brief_revisions b
                       JOIN scotus_cases c ON c.case_id=b.case_id
                       ORDER BY c.term,c.primary_docket,b.revision_number"""
                ).fetchall()
                if not brief_rows:
                    raise PocExportError("POC database has no accepted public brief records")
                briefs = tuple(self._brief(row) for row in brief_rows)
                case_ids = sorted({brief.case_id for brief in briefs}, key=str)
                claim_ids = sorted(
                    {claim_id for brief in briefs for claim_id in brief.body.claim_ids}, key=str
                )
                claim_rows = connection.execute(
                    """SELECT claim_id,case_id,official_url,public_source_label,page_label
                       FROM scotus_approved_claims WHERE claim_id = ANY(%s)
                       ORDER BY claim_id""",
                    (claim_ids,),
                ).fetchall()
                session_rows = connection.execute(
                    """SELECT a.argument_id,a.case_id,a.argument_date,a.sequence,a.reargument,
                              a.official_detail_url,
                              d.official_url_private AS official_transcript_url
                       FROM scotus_argument_sessions a
                       JOIN scotus_document_revisions d
                         ON d.document_revision_id=a.transcript_document_revision_id
                       WHERE a.case_id = ANY(%s)
                         AND EXISTS (
                           SELECT 1 FROM scotus_document_parses p
                           WHERE p.document_revision_id=d.document_revision_id
                             AND p.status='complete'
                         )
                       ORDER BY a.case_id,a.argument_date,a.sequence,a.argument_id""",
                    (case_ids,),
                ).fetchall()
                history_rows = connection.execute(
                    """SELECT case_id,new_status::text AS status,changed_at
                       FROM scotus_case_history WHERE case_id = ANY(%s)
                       ORDER BY case_id,changed_at,history_id""",
                    (case_ids,),
                ).fetchall()
                disposition_rows = connection.execute(
                    """SELECT case_id,official_url_private AS official_url,
                              ready_at AS available_at
                       FROM scotus_document_revisions
                       WHERE case_id = ANY(%s) AND canonical
                         AND document_kind IN ('opinion','order')
                         AND status IN ('ready','parsed') AND ready_at IS NOT NULL
                       ORDER BY case_id,document_kind,official_url_private""",
                    (case_ids,),
                ).fetchall()
        except PocExportError:
            raise
        except ValidationError as error:
            locations = ",".join(
                ".".join(str(part) for part in item["loc"])
                for item in error.errors(
                    include_url=False, include_context=False, include_input=False
                )[:10]
            )
            raise PocExportError(
                f"POC public records failed contract validation at: {locations or 'root'}"
            ) from None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            # Never include exception text because it may contain a rejected private value.
            raise PocExportError(
                f"POC public records failed recovery validation ({type(error).__name__})"
            ) from None

        provenance = {
            row["claim_id"]: _Provenance(
                case_id=row["case_id"],
                official_url=row["official_url"],
                source_label=row["public_source_label"],
                page_label=row["page_label"],
            )
            for row in claim_rows
        }
        sessions = {
            row["argument_id"]: _Session(
                argument_id=row["argument_id"],
                case_id=row["case_id"],
                argument_date=row["argument_date"],
                sequence=row["sequence"],
                reargument=row["reargument"],
                official_detail_url=row["official_detail_url"],
                official_transcript_url=row["official_transcript_url"],
            )
            for row in session_rows
        }
        histories: dict[UUID, list[PublicCaseHistoryEvent]] = {}
        for row in history_rows:
            status = ScotusCaseStatus(row["status"])
            histories.setdefault(row["case_id"], []).append(
                PublicCaseHistoryEvent(
                    status=status,
                    changed_at=row["changed_at"],
                    explanation=_CASE_EVENT_EXPLANATIONS[status],
                )
            )
        dispositions: dict[UUID, list[tuple[datetime, str]]] = {}
        for row in disposition_rows:
            dispositions.setdefault(row["case_id"], []).append(
                (row["available_at"], row["official_url"])
            )
        try:
            return self._build_cases(briefs, provenance, sessions, histories, dispositions)
        except PocExportError:
            raise
        except ValidationError as error:
            locations = ",".join(
                ".".join(str(part) for part in item["loc"])
                for item in error.errors(
                    include_url=False, include_context=False, include_input=False
                )[:10]
            )
            raise PocExportError(
                f"POC public records failed contract validation at: {locations or 'root'}"
            ) from None
        except (KeyError, TypeError, ValueError) as error:
            detail = str(error)
            safe_detail = (
                detail
                if detail.startswith(
                    (
                        "forbidden public field at ",
                        "internal UUID is forbidden at ",
                        "private or credential-like text is forbidden at ",
                    )
                )
                else type(error).__name__
            )
            raise PocExportError(
                f"POC public records failed recovery validation ({safe_detail})"
            ) from None

    @staticmethod
    def _brief(row: Mapping[str, Any]) -> _BriefRow:
        body = _BriefBody.model_validate(
            {
                "schema_version": row["payload_schema_version"],
                "brief_id": row["payload_brief_id"],
                "case_id": row["payload_case_id"],
                "argument_id": row["payload_argument_id"],
                "revision_number": row["payload_revision_number"],
                "maturity": row["payload_maturity"],
                "correction_note": row["payload_correction_note"],
                "created_at": row["payload_created_at"],
                "title": row["title"],
                "title_claim_ids": _json(row["title_claim_ids"]),
                "dek": row["dek"],
                "dek_claim_ids": _json(row["dek_claim_ids"]),
                "sections": _json(row["sections"]),
                "argument_analyses": _json(row["argument_analyses"]),
                "claim_ids": _json(row["claim_ids"]),
            }
        )
        if (
            body.brief_id != row["brief_id"]
            or body.case_id != row["case_id"]
            or body.argument_id != row["argument_id"]
            or body.revision_number != row["revision_number"]
            or body.maturity != BriefMaturity(row["maturity"])
            or body.correction_note != row["correction_note"]
            or body.created_at != row["created_at"]
        ):
            raise PocExportError("accepted public payload metadata differs from its row")
        referenced = {
            *body.title_claim_ids,
            *body.dek_claim_ids,
            *(claim for section in body.sections for claim in section.claim_ids),
            *(claim for argument in body.argument_analyses for claim in argument.claim_ids),
        }
        if not referenced.issubset(set(body.claim_ids)):
            raise PocExportError("accepted brief references undeclared public provenance")
        return _BriefRow(
            brief_id=row["brief_id"],
            case_id=row["case_id"],
            revision_number=row["revision_number"],
            maturity=BriefMaturity(row["maturity"]),
            correction_note=row["correction_note"],
            created_at=row["created_at"],
            term=row["term"],
            primary_docket=row["primary_docket"],
            caption=row["caption"],
            body=body,
        )

    @staticmethod
    def _build_cases(
        briefs: tuple[_BriefRow, ...],
        provenance: Mapping[UUID, _Provenance],
        sessions: Mapping[UUID, _Session],
        histories: Mapping[UUID, list[PublicCaseHistoryEvent]],
        dispositions: Mapping[UUID, list[tuple[datetime, str]]],
    ) -> tuple[tuple[PublicCaseBrief, ...], ...]:
        grouped: dict[UUID, list[_BriefRow]] = {}
        for brief in briefs:
            grouped.setdefault(brief.case_id, []).append(brief)
        result: list[tuple[PublicCaseBrief, ...]] = []
        for case_id, values in grouped.items():
            values.sort(key=lambda value: value.revision_number)
            if [value.revision_number for value in values] != list(range(1, len(values) + 1)):
                raise PocExportError("accepted public brief history is not contiguous")
            if len({value.brief_id for value in values}) != 1:
                raise PocExportError("a POC case has more than one accepted public brief identity")
            metadata = {
                (value.term, value.primary_docket, value.caption) for value in values
            }
            if len(metadata) != 1:
                raise PocExportError("case metadata changed within accepted brief history")
            summaries = tuple(
                PublicBriefRevisionSummary(
                    revision_number=value.revision_number,
                    maturity=value.maturity,
                    created_at=value.created_at,
                    correction_note=_public_text(value.correction_note),
                )
                for value in values
            )
            versions: list[PublicCaseBrief] = []
            for value in values:
                # Validate even provenance declared by the accepted brief but not attached
                # to an individual rendered field; the publisher required all of it.
                _source_links(value.body.claim_ids, case_id=case_id, provenance=provenance)
                analyses_sessions: list[_Session] = []
                for analysis in value.body.argument_analyses:
                    session = sessions.get(analysis.argument_id)
                    if session is None or session.case_id != case_id:
                        raise PocExportError(
                            "accepted brief lacks a complete argument session official URL"
                        )
                    if (
                        analysis.sequence != session.sequence
                        or analysis.argument_date != session.argument_date
                        or analysis.reargument != session.reargument
                    ):
                        raise PocExportError(
                            "accepted brief argument metadata differs from its complete session"
                        )
                    analyses_sessions.append(session)
                if len({item.argument_id for item in analyses_sessions}) != len(analyses_sessions):
                    raise PocExportError("accepted brief repeats an argument session")
                ordered_sessions = sorted(
                    analyses_sessions,
                    key=lambda item: (item.argument_date, item.sequence, str(item.argument_id)),
                )
                if analyses_sessions != ordered_sessions:
                    raise PocExportError("accepted brief argument sessions are out of order")
                all_events = histories.get(case_id, [])
                events = tuple(
                    event
                    for event in all_events
                    if event.changed_at <= value.created_at
                )
                if not events:
                    raise PocExportError(
                        "accepted brief revision lacks contemporaneous case history"
                    )
                case_status = events[-1].status
                source = partial(_source_links, case_id=case_id, provenance=provenance)
                public_case = PublicCaseBrief(
                    slug=public_case_slug(value.term, value.primary_docket, value.caption),
                    term=value.term,
                    primary_docket=value.primary_docket,
                    caption=value.caption,
                    argument_date=analyses_sessions[-1].argument_date,
                    case_status=case_status,
                    maturity=value.maturity,
                    title=_public_text(value.body.title) or value.body.title,
                    dek=_public_text(value.body.dek) or value.body.dek,
                    title_sources=source(value.body.title_claim_ids),
                    dek_sources=source(value.body.dek_claim_ids),
                    sections=tuple(
                        PublicBriefSection(
                            heading=_public_text(section.heading) or section.heading,
                            paragraphs=tuple(
                                _public_text(paragraph) or paragraph
                                for paragraph in section.paragraphs
                            ),
                            sources=source(section.claim_ids),
                        )
                        for section in value.body.sections
                    ),
                    arguments=tuple(
                        PublicArgumentAnalysis(
                            sequence=analysis.sequence,
                            argument_date=analysis.argument_date,
                            reargument=analysis.reargument,
                            heading=_public_text(analysis.heading) or analysis.heading,
                            paragraphs=tuple(
                                _public_text(paragraph) or paragraph
                                for paragraph in analysis.paragraphs
                            ),
                            official_detail_url=session.official_detail_url,
                            official_transcript_url=session.official_transcript_url,
                            sources=source(analysis.claim_ids),
                        )
                        for analysis, session in zip(
                            value.body.argument_analyses, analyses_sessions, strict=True
                        )
                    ),
                    case_history=events,
                    official_detail_url=analyses_sessions[-1].official_detail_url,
                    official_docket_url=(
                        "https://www.supremecourt.gov/docket/docketfiles/html/public/"
                        f"{quote(value.primary_docket, safe='-')}.html"
                    ),
                    official_disposition_urls=tuple(
                        sorted(
                            {
                                url
                                for available_at, url in dispositions.get(case_id, [])
                                if available_at <= value.created_at
                            }
                        )
                    ),
                    revisions=summaries[: value.revision_number],
                    updated_at=value.created_at,
                )
                assert_public_payload(public_case.model_dump(mode="python"))
                versions.append(public_case)
            # The current accepted brief must account for every complete session.  Older
            # revisions may legitimately predate reargument.
            complete_ids = {
                session.argument_id for session in sessions.values() if session.case_id == case_id
            }
            latest_ids = {
                analysis.argument_id for analysis in values[-1].body.argument_analyses
            }
            if latest_ids != complete_ids:
                raise PocExportError(
                    "latest accepted brief does not cover every complete argument session"
                )
            result.append(tuple(versions))
        return tuple(
            sorted(
                result,
                key=lambda versions: public_case_key(
                    versions[-1].term, versions[-1].primary_docket
                ),
            )
        )

    def close(self) -> None:
        if self._owns_pool:
            self.pool.close()


def build_poc_generated_content(
    parent: GeneratedContent,
    case_revisions: tuple[tuple[PublicCaseBrief, ...], ...],
    *,
    build_epoch: datetime,
) -> GeneratedContent:
    """Merge recovered revisions onto a complete parent, without minting a release."""
    if build_epoch.tzinfo is None or build_epoch.utcoffset() is None:
        raise PocExportError("build epoch must be timezone-aware")
    if parent.projection is None or parent.release is None:
        raise PocExportError("POC recovery requires a complete generated-content parent")
    parent_release_id = parent.publication.active_release_id
    if parent_release_id is None or parent.release.release_id != parent_release_id:
        raise PocExportError("generated-content parent release pointer is inconsistent")
    if not case_revisions:
        raise PocExportError("POC recovery contains no accepted public case revisions")

    store = StaticStateStore(".poc-recovery-read-only-placeholder")
    # A changed projection must never retain the parent's manifest. StaticSiteExporter
    # constructs the only release manifest that may be attached to this result.
    working = replace(parent, release=None)
    seen: set[str] = set()
    for versions in case_revisions:
        if not versions:
            raise PocExportError("POC recovery contains an empty case history")
        key = public_case_key(versions[-1].term, versions[-1].primary_docket)
        if key in seen:
            raise PocExportError("POC recovery contains duplicate public cases")
        seen.add(key)
        pointer = next(
            (item for item in working.publication.cases if item.case_key == key), None
        )
        existing_count = pointer.active_revision if pointer is not None else 0
        for number, version in enumerate(versions, start=1):
            if version.revisions[-1].revision_number != number:
                raise PocExportError("recovered public case history is not contiguous")
            existing = working.revisions.get((key, number))
            if number <= existing_count:
                if existing is None or existing.record.case != version:
                    raise PocExportError(
                        "POC recovery conflicts with an immutable parent case revision"
                    )
                continue
            if number != existing_count + 1:
                raise PocExportError("POC recovery cannot skip a parent case revision")
            try:
                working = store.merge_accepted_case(
                    working,
                    version,
                    watermark=build_epoch,
                    generated_at=version.updated_at,
                )
            except StaticStateError as error:
                raise PocExportError("recovered case could not be merged append-only") from error
            existing_count = number

    active_cases = tuple(
        sorted(
            (working.projection.cases if working.projection is not None else ()),
            key=lambda case: public_case_key(case.term, case.primary_docket),
        )
    )
    projection = ScotusPublicProjection(
        watermark=build_epoch,
        generated_at=build_epoch,
        cases=active_cases,
        disclosure=parent.projection.disclosure,
        site_name=parent.projection.site_name,
    )
    publication = working.publication.model_copy(update={"updated_at": build_epoch})
    candidate = replace(working, projection=projection, publication=publication)
    assert_public_payload(projection.model_dump(mode="python"))
    try:
        store._validate_consistency(candidate)
    except StaticStateError as error:
        raise PocExportError("POC recovery candidate is internally inconsistent") from error
    return candidate


def export_poc_generated_content(
    parent: GeneratedContent,
    case_revisions: tuple[tuple[PublicCaseBrief, ...], ...],
    destination: Path,
    *,
    site_destination: Path,
    urls: StaticUrlPolicy,
    source_commit: str,
    config_sha256: str,
    build_epoch: datetime,
) -> Path:
    """Render and cross-validate a static release and its complete merge candidate."""
    if not _GIT_COMMIT.fullmatch(source_commit):
        raise PocExportError("source commit must be a full lowercase Git SHA")
    if not _SHA256.fullmatch(config_sha256):
        raise PocExportError("config digest must be a lowercase SHA-256")
    if destination.exists() or site_destination.exists():
        raise PocExportError("POC recovery destinations must not already exist")
    content = build_poc_generated_content(
        parent,
        case_revisions,
        build_epoch=build_epoch,
    )
    if content.projection is None:
        raise PocExportError("POC recovery did not produce a public projection")
    parent_release_id = parent.publication.active_release_id
    exporter = StaticSiteExporter(urls)
    try:
        exported = exporter.export(
            content.projection,
            site_destination,
            source_commit=source_commit,
            build_epoch=build_epoch,
            config_sha256=config_sha256,
            previous_release_id=parent_release_id,
            legacy_slugs={
                pointer.case_key: pointer.legacy_slugs
                for pointer in content.publication.cases
            },
        )
        state_store = StaticStateStore(destination.parent / ".active-not-mutated")
        state_store.finalize_candidate(destination, content, exported.manifest)
        validate_static_candidate(site_destination, urls, state_root=destination)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        shutil.rmtree(site_destination, ignore_errors=True)
        raise
    return destination
