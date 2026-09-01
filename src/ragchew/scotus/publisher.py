"""Event-driven SCOTUS brief policy, generation, and projection command."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from uuid import UUID

from openai import OpenAI

from ragchew.config import ScotusConfig, ServiceSettings
from ragchew.logging_config import configure_logging
from ragchew.metrics import (
    SCOTUS_BRIEF_POLICY_OUTCOMES,
    SCOTUS_CORRECTIONS,
    SCOTUS_LAST_PUBLICATION,
)
from ragchew.repository import PostgresRepository
from ragchew.scotus.briefs import (
    BriefCandidate,
    BriefGenerationService,
    BriefValidationError,
    CaseArgumentSession,
    OpenAILegalBriefGenerator,
    PostgresBriefRevisionStore,
    evaluate_brief_candidate,
)
from ragchew.scotus.contracts import (
    BriefMaturity,
    LegalBriefRevision,
    ScotusApprovedClaim,
    ScotusCaseStatus,
)
from ragchew.scotus.correlation import PostgresScotusCorrelationStore
from ragchew.scotus.public_contracts import (
    PublicBriefRevisionSummary,
    PublicCaseBrief,
    PublicCaseHistoryEvent,
)
from ragchew.scotus.publishing import PostgresScotusProjectionStore, build_public_case

LOG = logging.getLogger("ragchew.scotus.publisher")


def _candidate_rows(repository: PostgresRepository) -> list[dict[str, object]]:
    with repository.pool.connection() as connection:
        return connection.execute(
            """SELECT c.case_id,c.term,c.caption_private,c.primary_docket,c.official_url,
                      c.status::text AS case_status,
                      GREATEST(
                        c.updated_at,
                        COALESCE(state.argument_updated_at,c.updated_at),
                        COALESCE(state.observation_updated_at,c.updated_at),
                        COALESCE(state.document_updated_at,c.updated_at)
                      ) AS case_watermark,
                      anchor.argument_id,anchor.argument_date,anchor.official_detail_url,
                      state.session_count,state.complete_session_count,
                      latest.created_at AS latest_brief_at
               FROM scotus_cases c
               JOIN LATERAL (
                 SELECT a.argument_id,a.argument_date,a.official_detail_url
                 FROM scotus_argument_sessions a
                 WHERE a.case_id=c.case_id
                 ORDER BY a.argument_date DESC,a.sequence DESC LIMIT 1
               ) anchor ON true
               JOIN LATERAL (
                 SELECT
                   count(*) AS session_count,
                   count(*) FILTER (
                     WHERE a.transcript_document_revision_id IS NOT NULL
                       AND EXISTS (
                         SELECT 1 FROM scotus_document_parses p
                         WHERE p.document_revision_id=a.transcript_document_revision_id
                           AND p.status='complete'
                       )
                   ) AS complete_session_count,
                   max(a.updated_at) AS argument_updated_at,
                   (SELECT max(o.created_at) FROM scotus_legal_observations o
                    WHERE o.case_id=c.case_id) AS observation_updated_at,
                   (SELECT max(d.observed_at) FROM scotus_document_revisions d
                    WHERE d.case_id=c.case_id) AS document_updated_at
                 FROM scotus_argument_sessions a WHERE a.case_id=c.case_id
               ) state ON true
               LEFT JOIN LATERAL (
                 SELECT created_at FROM scotus_brief_revisions b
                 WHERE b.case_id=c.case_id
                 ORDER BY revision_number DESC LIMIT 1
               ) latest ON true
               WHERE state.session_count > 0
                 AND state.complete_session_count=state.session_count
               ORDER BY anchor.argument_date"""
        ).fetchall()


def _build_candidate(
    repository: PostgresRepository, row: dict[str, object], now: datetime
) -> BriefCandidate:
    case_id = row["case_id"]
    argument_id = row["argument_id"]
    assert isinstance(case_id, UUID) and isinstance(argument_id, UUID)
    with repository.pool.connection() as connection:
        observation_rows = connection.execute(
            """SELECT * FROM scotus_legal_observations
               WHERE case_id=%s ORDER BY created_at,observation_id""",
            (case_id,),
        ).fetchall()
        document_rows = connection.execute(
            """SELECT document_revision_id,official_url_private
               FROM scotus_document_revisions WHERE case_id=%s""",
            (case_id,),
        ).fetchall()
        session_rows = connection.execute(
            """SELECT a.argument_id,a.argument_date,a.sequence,a.reargument,
                      a.official_detail_url,d.official_url_private AS transcript_url
               FROM scotus_argument_sessions a
               JOIN scotus_document_revisions d
                 ON d.document_revision_id=a.transcript_document_revision_id
               JOIN scotus_document_parses p
                 ON p.document_revision_id=d.document_revision_id AND p.status='complete'
               WHERE a.case_id=%s
               ORDER BY a.argument_date,a.sequence""",
            (case_id,),
        ).fetchall()
    observations = tuple(
        PostgresScotusCorrelationStore._observation(value) for value in observation_rows
    )
    urls = {
        value["document_revision_id"]: value["official_url_private"]
        for value in document_rows
    }
    sessions = tuple(
        CaseArgumentSession(
            argument_id=value["argument_id"],
            argument_date=value["argument_date"],
            sequence=value["sequence"],
            reargument=value["reargument"],
            official_detail_url=value["official_detail_url"],
            official_transcript_url=value["transcript_url"],
        )
        for value in session_rows
    )
    return BriefCandidate(
        case_id=case_id,
        argument_id=argument_id,
        caption=str(row["caption_private"]),
        primary_docket=str(row["primary_docket"]),
        case_status=ScotusCaseStatus(str(row["case_status"])),
        official_transcript_complete=(
            row["complete_session_count"] == row["session_count"]
        ),
        parser_complete=(row["complete_session_count"] == row["session_count"]),
        privacy_blocking_failure=False,
        argument_sessions=sessions,
        observations=observations,
        document_urls=urls,
        evaluated_at=now,
    )


def _claims(
    repository: PostgresRepository, claim_ids: tuple[UUID, ...]
) -> tuple[ScotusApprovedClaim, ...]:
    if not claim_ids:
        return ()
    with repository.pool.connection() as connection:
        rows = connection.execute(
            "SELECT * FROM scotus_approved_claims WHERE claim_id = ANY(%s)",
            (list(claim_ids),),
        ).fetchall()
    by_id = {row["claim_id"]: row for row in rows}
    return tuple(
        ScotusApprovedClaim(
            claim_id=claim_id,
            case_id=by_id[claim_id]["case_id"],
            argument_id=by_id[claim_id]["argument_id"],
            observation_type=by_id[claim_id]["observation_type"],
            legal_status=by_id[claim_id]["legal_status"],
            certainty=by_id[claim_id]["certainty"],
            public_value=by_id[claim_id]["public_value"],
            attribution=by_id[claim_id]["attribution"],
            official_url=by_id[claim_id]["official_url"],
            public_source_label=by_id[claim_id]["public_source_label"],
            page_label=by_id[claim_id]["page_label"],
            source_observation_ids=tuple(
                UUID(value) for value in by_id[claim_id]["source_observation_ids"]
            ),
            policy_version=by_id[claim_id]["policy_version"],
            approved_at=by_id[claim_id]["approved_at"],
        )
        for claim_id in claim_ids
    )


def _argument_sessions(
    repository: PostgresRepository, case_id: UUID
) -> tuple[CaseArgumentSession, ...]:
    with repository.pool.connection() as connection:
        rows = connection.execute(
            """SELECT a.argument_id,a.argument_date,a.sequence,a.reargument,
                      a.official_detail_url,d.official_url_private AS transcript_url
               FROM scotus_argument_sessions a
               JOIN scotus_document_revisions d
                 ON d.document_revision_id=a.transcript_document_revision_id
               JOIN scotus_document_parses p
                 ON p.document_revision_id=d.document_revision_id AND p.status='complete'
               WHERE a.case_id=%s ORDER BY a.argument_date,a.sequence""",
            (case_id,),
        ).fetchall()
    return tuple(
        CaseArgumentSession(
            argument_id=row["argument_id"],
            argument_date=row["argument_date"],
            sequence=row["sequence"],
            reargument=row["reargument"],
            official_detail_url=row["official_detail_url"],
            official_transcript_url=row["transcript_url"],
        )
        for row in rows
    )


_CASE_EVENT_EXPLANATIONS = {
    ScotusCaseStatus.DOCKETED: "An official docket record was verified.",
    ScotusCaseStatus.ARGUED: "An official oral-argument transcript was verified.",
    ScotusCaseStatus.REARGUED: "Official transcripts for a later argument were verified.",
    ScotusCaseStatus.ORDER_ISSUED: "An official Court order was verified.",
    ScotusCaseStatus.DECIDED: "An official Court opinion was verified.",
    ScotusCaseStatus.CORRECTED: "Official case material or analysis was corrected.",
    ScotusCaseStatus.UNRESOLVED: "The verified source set does not establish a later event.",
}


def _case_history(
    repository: PostgresRepository,
    case_id: UUID,
    fallback_status: ScotusCaseStatus,
    fallback_time: datetime,
) -> tuple[PublicCaseHistoryEvent, ...]:
    with repository.pool.connection() as connection:
        rows = connection.execute(
            """SELECT new_status::text AS status,changed_at
               FROM scotus_case_history WHERE case_id=%s ORDER BY changed_at,history_id""",
            (case_id,),
        ).fetchall()
    if not rows:
        return (
            PublicCaseHistoryEvent(
                status=fallback_status,
                changed_at=fallback_time,
                explanation=_CASE_EVENT_EXPLANATIONS[fallback_status],
            ),
        )
    return tuple(
        PublicCaseHistoryEvent(
            status=(status := ScotusCaseStatus(row["status"])),
            changed_at=row["changed_at"],
            explanation=_CASE_EVENT_EXPLANATIONS[status],
        )
        for row in rows
    )


def _public_cases(repository: PostgresRepository) -> tuple[PublicCaseBrief, ...]:
    with repository.pool.connection() as connection:
        rows = connection.execute(
            """SELECT DISTINCT ON (b.case_id)
                      b.public_payload,b.brief_id,b.case_id,c.term,c.primary_docket,
                      COALESCE(c.public_caption,c.caption_private) AS caption,
                      c.status::text AS case_status
               FROM scotus_brief_revisions b
               JOIN scotus_cases c USING(case_id)
               ORDER BY b.case_id,b.revision_number DESC"""
        ).fetchall()
    cases: list[PublicCaseBrief] = []
    for row in rows:
        revision = LegalBriefRevision.model_validate(row["public_payload"])
        claims = _claims(repository, revision.claim_ids)
        sessions = _argument_sessions(repository, row["case_id"])
        status = ScotusCaseStatus(row["case_status"])
        case_history = _case_history(
            repository, row["case_id"], status, revision.created_at
        )
        if not sessions:
            raise RuntimeError("published SCOTUS case has no complete argument sessions")
        with repository.pool.connection() as connection:
            history_rows = connection.execute(
                """SELECT revision_number,maturity,created_at,correction_note
                   FROM scotus_brief_revisions WHERE brief_id=%s ORDER BY revision_number""",
                (row["brief_id"],),
            ).fetchall()
            disposition_rows = connection.execute(
                """SELECT official_url_private FROM scotus_document_revisions
                   WHERE case_id=%s AND document_kind IN ('opinion','order')
                     AND status IN ('ready','parsed') AND canonical
                   ORDER BY document_kind,official_url_private""",
                (row["case_id"],),
            ).fetchall()
        history = tuple(
            PublicBriefRevisionSummary(
                revision_number=value["revision_number"],
                maturity=BriefMaturity(value["maturity"]),
                created_at=value["created_at"],
                correction_note=value["correction_note"],
            )
            for value in history_rows
        )
        cases.append(
            build_public_case(
                term=row["term"],
                primary_docket=row["primary_docket"],
                caption=row["caption"],
                argument_date=sessions[-1].argument_date,
                case_status=status,
                official_detail_url=sessions[-1].official_detail_url,
                revision=revision,
                claims=claims,
                argument_sessions=sessions,
                case_history=case_history,
                revision_history=history,
                official_disposition_urls=tuple(
                    value["official_url_private"] for value in disposition_rows
                ),
            )
        )
    return tuple(cases)


def _reserve_generation_attempt(
    repository: PostgresRepository,
    *,
    case_id: UUID,
    case_watermark: datetime,
    model: str,
    prompt_version: str,
) -> bool:
    with repository.pool.connection() as connection, connection.transaction():
        row = connection.execute(
            """INSERT INTO scotus_generation_attempts
               (case_id,case_watermark,generator_model,prompt_version,outcome)
               VALUES (%s,%s,%s,%s,'started')
               ON CONFLICT(case_id,case_watermark,generator_model,prompt_version)
               DO NOTHING RETURNING attempt_id""",
            (case_id, case_watermark, model, prompt_version),
        ).fetchone()
    return row is not None


def _complete_generation_attempt(
    repository: PostgresRepository,
    *,
    case_id: UUID,
    case_watermark: datetime,
    model: str,
    prompt_version: str,
    outcome: str,
    failure_code: str | None = None,
) -> None:
    with repository.pool.connection() as connection, connection.transaction():
        connection.execute(
            """UPDATE scotus_generation_attempts
               SET outcome=%s,failure_code=%s,completed_at=now()
               WHERE case_id=%s AND case_watermark=%s
                 AND generator_model=%s AND prompt_version=%s""",
            (
                outcome,
                failure_code,
                case_id,
                case_watermark,
                model,
                prompt_version,
            ),
        )


def run_once(settings: ServiceSettings, config: ScotusConfig, now: datetime) -> int:
    if not config.generation.brief_generation_enabled:
        LOG.info(
            "SCOTUS brief generation disabled; no model requests made",
            extra={"outcome": "disabled"},
        )
        return 0
    if config.generation.prompt_version != OpenAILegalBriefGenerator.PROMPT_VERSION:
        raise RuntimeError("configured SCOTUS brief prompt version does not match the code")
    repository = PostgresRepository(settings.database_dsn)
    llm = OpenAI(api_key=settings.openai_api_key.get_secret_value())
    generator = OpenAILegalBriefGenerator(
        config.generation.model,
        llm,
        maximum_sentence_words=config.generation.maximum_sentence_words,
        maximum_paragraph_words=config.generation.maximum_paragraph_words,
    )
    brief_store = PostgresBriefRevisionStore("", pool=repository.pool)
    service = BriefGenerationService(
        generator,
        brief_store,
        public_quotes=config.generation.public_quotes,
        maximum_sentence_words=config.generation.maximum_sentence_words,
        maximum_paragraph_words=config.generation.maximum_paragraph_words,
    )
    changed = 0
    api_calls = 0
    for row in _candidate_rows(repository):
        latest = row["latest_brief_at"]
        updated = row["case_watermark"]
        if not isinstance(updated, datetime):
            raise RuntimeError("SCOTUS case update timestamp is invalid")
        if isinstance(latest, datetime) and latest >= updated:
            continue
        candidate = _build_candidate(repository, row, now)
        decision = evaluate_brief_candidate(
            candidate,
            minimum_confidence=config.generation.minimum_observation_confidence,
        )
        SCOTUS_BRIEF_POLICY_OUTCOMES.labels(
            "eligible" if decision.eligible else "denied"
        ).inc()
        if not decision.eligible:
            continue
        with repository.pool.connection() as connection:
            number_row = connection.execute(
                """SELECT COALESCE(max(revision_number),0)+1 AS number
                   FROM scotus_brief_revisions WHERE case_id=%s""",
                (candidate.case_id,),
            ).fetchone()
        if number_row is None:
            raise RuntimeError("SCOTUS brief revision number query failed")
        number = number_row["number"]
        if api_calls >= config.generation.maximum_brief_api_calls_per_run:
            LOG.info(
                "SCOTUS brief API call budget reached",
                extra={"outcome": "budget_reached", "api_calls": api_calls},
            )
            break
        if not _reserve_generation_attempt(
            repository,
            case_id=candidate.case_id,
            case_watermark=updated,
            model=config.generation.model,
            prompt_version=config.generation.prompt_version,
        ):
            continue
        api_calls += 1
        try:
            generated = service.generate(candidate, decision, revision_number=number)
        except BriefValidationError as error:
            _complete_generation_attempt(
                repository,
                case_id=candidate.case_id,
                case_watermark=updated,
                model=config.generation.model,
                prompt_version=config.generation.prompt_version,
                outcome="validation_denied",
                failure_code=str(error)[:200],
            )
            SCOTUS_BRIEF_POLICY_OUTCOMES.labels("generation_validation_denied").inc()
            LOG.exception(
                "SCOTUS brief generation failed deterministic validation",
                extra={"outcome": "denied", "case_id": candidate.case_id},
            )
            if config.generation.stop_after_brief_validation_failure:
                break
            continue
        except Exception as error:
            _complete_generation_attempt(
                repository,
                case_id=candidate.case_id,
                case_watermark=updated,
                model=config.generation.model,
                prompt_version=config.generation.prompt_version,
                outcome="request_failed",
                failure_code=type(error).__name__,
            )
            raise
        _complete_generation_attempt(
            repository,
            case_id=candidate.case_id,
            case_watermark=updated,
            model=config.generation.model,
            prompt_version=config.generation.prompt_version,
            outcome="accepted",
        )
        if generated.maturity in {BriefMaturity.CORRECTED, BriefMaturity.RETRACTED}:
            SCOTUS_CORRECTIONS.labels(generated.maturity.value).inc()
        with repository.pool.connection() as connection:
            connection.execute(
                """UPDATE scotus_argument_sessions SET status='published',updated_at=%s
                   WHERE case_id=%s AND status <> 'retracted'""",
                (now, candidate.case_id),
            )
            connection.commit()
        changed += 1
    if changed:
        projection_store = PostgresScotusProjectionStore("", pool=repository.pool)
        projection_store.activate(now, now, _public_cases(repository))
        SCOTUS_LAST_PUBLICATION.set(now.timestamp())
    repository.pool.close()
    LOG.info("SCOTUS publisher complete", extra={"outcome": "complete"})
    return changed


def main() -> None:
    configure_logging(os.getenv("RAGCHEW_LOG_LEVEL", "INFO"))
    settings = ServiceSettings()
    run_once(settings, ScotusConfig.from_yaml(settings.scotus_config_path), datetime.now(UTC))
