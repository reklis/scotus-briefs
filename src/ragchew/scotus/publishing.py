"""Sanitized SCOTUS case projection building and atomic persistence."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.scotus.briefs import CaseArgumentSession
from ragchew.scotus.contracts import (
    LegalBriefRevision,
    ScotusApprovedClaim,
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
    public_case_slug,
)


class ScotusProjectionReader(Protocol):
    def active_projection(self) -> ScotusPublicProjection | None: ...


def _source_link(
    claim_ids: tuple[UUID, ...], claims: dict[UUID, ScotusApprovedClaim]
) -> tuple[PublicSourceLink, ...]:
    grouped: set[tuple[str, str, str]] = set()
    for claim_id in claim_ids:
        claim = claims.get(claim_id)
        if claim is None:
            raise ValueError("brief references a missing approved claim")
        grouped.add((claim.public_source_label, claim.official_url, claim.page_label))
    return tuple(
        PublicSourceLink(
            evidence_type=label,
            label=f"Official Supreme Court {label} — {page}",
            official_url=url,
            page_label=page,
        )
        for label, url, page in sorted(grouped)
    )


def build_public_case(
    *,
    term: str,
    primary_docket: str,
    caption: str,
    argument_date: datetime,
    case_status: ScotusCaseStatus,
    official_detail_url: str,
    revision: LegalBriefRevision,
    claims: tuple[ScotusApprovedClaim, ...],
    argument_sessions: tuple[CaseArgumentSession, ...],
    case_history: tuple[PublicCaseHistoryEvent, ...],
    revision_history: tuple[PublicBriefRevisionSummary, ...],
    official_disposition_urls: tuple[str, ...] = (),
    topics: tuple[str, ...] = (),
) -> PublicCaseBrief:
    claim_map = {claim.claim_id: claim for claim in claims}
    if set(revision.claim_ids) - set(claim_map):
        raise ValueError("public brief is missing claim provenance")
    sections = tuple(
        PublicBriefSection(
            heading=section.heading,
            paragraphs=section.paragraphs,
            sources=_source_link(section.claim_ids, claim_map),
        )
        for section in revision.sections
    )
    sessions = {session.argument_id: session for session in argument_sessions}
    if tuple(item.argument_id for item in revision.argument_analyses) != tuple(
        session.argument_id for session in argument_sessions
    ):
        raise ValueError("public case argument metadata does not match brief analyses")
    arguments = tuple(
        PublicArgumentAnalysis(
            sequence=analysis.sequence,
            argument_date=analysis.argument_date,
            reargument=analysis.reargument,
            heading=analysis.heading,
            paragraphs=analysis.paragraphs,
            official_detail_url=sessions[analysis.argument_id].official_detail_url,
            official_transcript_url=(sessions[analysis.argument_id].official_transcript_url),
            sources=_source_link(analysis.claim_ids, claim_map),
        )
        for analysis in revision.argument_analyses
    )
    return PublicCaseBrief(
        slug=public_case_slug(term, primary_docket, caption),
        term=term,
        primary_docket=primary_docket,
        caption=caption,
        argument_date=argument_date,
        case_status=case_status,
        maturity=revision.maturity,
        title=revision.title,
        dek=revision.dek,
        title_sources=_source_link(revision.title_claim_ids, claim_map),
        dek_sources=_source_link(revision.dek_claim_ids, claim_map),
        sections=sections,
        arguments=arguments,
        case_history=case_history,
        official_detail_url=official_detail_url,
        official_docket_url=(
            "https://www.supremecourt.gov/docket/docketfiles/html/public/"
            f"{quote(primary_docket, safe='-')}.html"
        ),
        official_disposition_urls=official_disposition_urls,
        revisions=revision_history,
        updated_at=revision.created_at,
        topics=topics,
    )


class InMemoryScotusProjectionStore:
    def __init__(self) -> None:
        self.active: ScotusPublicProjection | None = None
        self.fail_activation = False

    def activate(
        self,
        watermark: datetime,
        generated_at: datetime,
        cases: tuple[PublicCaseBrief, ...],
    ) -> ScotusPublicProjection:
        if self.fail_activation:
            raise RuntimeError("SCOTUS projection activation failed")
        projection = ScotusPublicProjection(
            watermark=watermark,
            generated_at=generated_at,
            cases=cases,
        )
        self.active = projection
        return projection

    def active_projection(self) -> ScotusPublicProjection | None:
        return self.active


class PostgresScotusProjectionStore:
    def __init__(
        self,
        dsn: str,
        pool: ConnectionPool[Connection[dict[str, Any]]] | None = None,
    ) -> None:
        self.pool = pool or ConnectionPool(
            conninfo=dsn,
            kwargs={"row_factory": dict_row},
            min_size=1,
            max_size=5,
            open=True,
        )

    def activate(
        self,
        watermark: datetime,
        generated_at: datetime,
        cases: tuple[PublicCaseBrief, ...],
    ) -> ScotusPublicProjection:
        projection = ScotusPublicProjection(
            watermark=watermark,
            generated_at=generated_at,
            cases=tuple(sorted(cases, key=lambda item: item.updated_at, reverse=True)),
        )
        projection_id = uuid5(NAMESPACE_URL, f"ragchew:scotus-projection:{watermark.isoformat()}")
        with self.pool.connection() as connection, connection.transaction():
            connection.execute(
                """INSERT INTO scotus_public_projections
                   (projection_id,watermark,payload,status)
                   VALUES (%s,%s,%s::jsonb,'building')
                   ON CONFLICT(watermark) DO UPDATE SET
                     payload=excluded.payload,status='building'""",
                (projection_id, watermark, projection.model_dump_json()),
            )
            connection.execute(
                """UPDATE scotus_public_projections SET status='superseded'
                   WHERE status='active'"""
            )
            activated = connection.execute(
                """UPDATE scotus_public_projections SET status='active',activated_at=now()
                   WHERE projection_id=%s""",
                (projection_id,),
            )
            if activated.rowcount != 1:
                raise RuntimeError("SCOTUS projection did not activate")
        return projection

    def active_projection(self) -> ScotusPublicProjection | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM active_scotus_public_projection"
            ).fetchone()
        if row is None:
            return None
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return ScotusPublicProjection.model_validate(payload)
