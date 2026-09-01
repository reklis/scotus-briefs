"""Deterministic Supreme Court case, issue, and legal-state correlation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.scotus.contracts import (
    LegalCertainty,
    LegalEvidenceRange,
    LegalObservation,
    LegalObservationType,
    LegalStatus,
    ScotusCaseAggregate,
    ScotusCaseStatus,
    ScotusDocumentKind,
    ScotusIssue,
    ScotusSensitivity,
    SpeakerIdentityBasis,
    SpeakerKind,
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _compact(value: str) -> str:
    return " ".join(_TOKEN.findall(value.lower()))


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def _issue_key(observation: LegalObservation) -> str:
    value = observation.normalized_value_private or observation.raw_value_private
    if observation.observation_type is LegalObservationType.QUESTION_PRESENTED:
        return f"question:{_short_hash(_compact(value))}"
    if observation.authority_citations:
        return f"authority:{_compact(observation.authority_citations[0])[:220]}"
    if observation.observation_type is LegalObservationType.DOCTRINAL_THEME:
        return f"doctrine:{_short_hash(_compact(value))}"
    return f"type:{observation.observation_type.value}"


def deterministic_issue_id(case_id: UUID, issue_key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"ragchew:scotus-issue:{case_id}:{issue_key}")


@dataclass(frozen=True)
class CorrelationResult:
    aggregate: ScotusCaseAggregate
    issues: tuple[ScotusIssue, ...]
    prior_status: ScotusCaseStatus


class ScotusCorrelationEngine:
    VERSION = "scotus-correlation-v1"

    def correlate(
        self,
        case_id: UUID,
        prior_status: ScotusCaseStatus,
        observations: tuple[LegalObservation, ...],
        updated_at: datetime,
        *,
        reargued: bool = False,
    ) -> CorrelationResult:
        issues_by_key: dict[str, list[LegalObservation]] = {}
        for observation in observations:
            issues_by_key.setdefault(_issue_key(observation), []).append(observation)
        issues = tuple(
            ScotusIssue(
                issue_id=deterministic_issue_id(case_id, key),
                case_id=case_id,
                issue_key=key,
                title_private=self._issue_title(group),
                authority_citations=tuple(
                    dict.fromkeys(
                        citation for item in group for citation in item.authority_citations
                    )
                ),
                observation_ids=tuple(item.observation_id for item in group),
                first_observed_at=updated_at,
                updated_at=updated_at,
                correlation_version=self.VERSION,
            )
            for key, group in sorted(issues_by_key.items())
        )
        status = self._derive_status(prior_status, observations, reargued=reargued)
        aggregate = ScotusCaseAggregate(
            case_id=case_id,
            status=status,
            issue_ids=tuple(issue.issue_id for issue in issues),
            observation_ids=tuple(item.observation_id for item in observations),
            updated_at=updated_at,
            correlation_version=self.VERSION,
        )
        return CorrelationResult(aggregate=aggregate, issues=issues, prior_status=prior_status)

    @staticmethod
    def _issue_title(observations: list[LegalObservation]) -> str:
        preferred = next(
            (
                item
                for item in observations
                if item.observation_type
                in {LegalObservationType.QUESTION_PRESENTED, LegalObservationType.DOCTRINAL_THEME}
            ),
            observations[0],
        )
        return (preferred.normalized_value_private or preferred.raw_value_private)[:500]

    @staticmethod
    def _derive_status(
        prior: ScotusCaseStatus,
        observations: tuple[LegalObservation, ...],
        *,
        reargued: bool,
    ) -> ScotusCaseStatus:
        if prior is ScotusCaseStatus.DECIDED:
            return prior
        has_holding = any(
            item.observation_type is LegalObservationType.HOLDING
            and item.legal_status is LegalStatus.COURT_HELD
            and any(
                evidence.document_kind is ScotusDocumentKind.OPINION
                for evidence in item.evidence
            )
            for item in observations
        )
        if has_holding:
            return ScotusCaseStatus.DECIDED
        has_correction = any(item.supersedes_observation_id for item in observations)
        if has_correction:
            return ScotusCaseStatus.CORRECTED
        if prior is ScotusCaseStatus.ORDER_ISSUED:
            return prior
        has_order = any(
            item.observation_type is LegalObservationType.ORDER
            and item.legal_status is LegalStatus.COURT_ORDERED
            and any(
                evidence.document_kind
                in {ScotusDocumentKind.ORDER, ScotusDocumentKind.OPINION}
                for evidence in item.evidence
            )
            for item in observations
        )
        if has_order:
            return ScotusCaseStatus.ORDER_ISSUED
        if reargued:
            return ScotusCaseStatus.REARGUED
        if any(
            evidence.document_kind is ScotusDocumentKind.TRANSCRIPT
            for item in observations
            for evidence in item.evidence
        ):
            return ScotusCaseStatus.ARGUED
        return prior

    def replay(
        self,
        case_id: UUID,
        prior_status: ScotusCaseStatus,
        observations: tuple[LegalObservation, ...],
        updated_at: datetime,
        *,
        reargued: bool = False,
    ) -> CorrelationResult:
        return self.correlate(
            case_id,
            prior_status,
            observations,
            updated_at,
            reargued=reargued,
        )


class PostgresScotusCorrelationStore:
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

    def correlate_extraction(
        self,
        extraction_revision_id: UUID,
        engine: ScotusCorrelationEngine,
        updated_at: datetime,
    ) -> CorrelationResult | None:
        with self.pool.connection() as connection, connection.transaction():
            rows = connection.execute(
                """SELECT o.*,c.status::text AS case_status,
                          EXISTS(
                            SELECT 1 FROM scotus_argument_sessions a
                            WHERE a.case_id=o.case_id AND a.reargument
                          ) AS reargued
                   FROM scotus_legal_observations o
                   JOIN scotus_cases c ON c.case_id=o.case_id
                   WHERE o.extraction_revision_id=%s
                   ORDER BY o.created_at,o.observation_id""",
                (extraction_revision_id,),
            ).fetchall()
            if not rows:
                return None
            case_ids = {row["case_id"] for row in rows}
            if len(case_ids) != 1:
                raise RuntimeError("one extraction revision cannot span SCOTUS cases")
            case_id = next(iter(case_ids))
            observations = tuple(self._observation(row) for row in rows)
            result = engine.correlate(
                case_id,
                ScotusCaseStatus(rows[0]["case_status"]),
                observations,
                updated_at,
                reargued=rows[0]["reargued"],
            )
            for observation in observations:
                connection.execute(
                    """INSERT INTO scotus_case_observations(case_id,observation_id)
                       VALUES (%s,%s) ON CONFLICT DO NOTHING""",
                    (case_id, observation.observation_id),
                )
            for issue in result.issues:
                connection.execute(
                    """INSERT INTO scotus_issues
                       (issue_id,case_id,issue_key,title_private,authority_citations_private,
                        first_observed_at,updated_at,correlation_version)
                       VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                       ON CONFLICT(case_id,issue_key) DO UPDATE SET
                         title_private=excluded.title_private,
                         authority_citations_private=excluded.authority_citations_private,
                         updated_at=excluded.updated_at,
                         correlation_version=excluded.correlation_version""",
                    (
                        issue.issue_id,
                        issue.case_id,
                        issue.issue_key,
                        issue.title_private,
                        json.dumps(issue.authority_citations),
                        issue.first_observed_at,
                        issue.updated_at,
                        issue.correlation_version,
                    ),
                )
                for observation_id in issue.observation_ids:
                    connection.execute(
                        """INSERT INTO scotus_issue_observations(issue_id,observation_id)
                           VALUES (%s,%s) ON CONFLICT DO NOTHING""",
                        (issue.issue_id, observation_id),
                    )
            connection.execute(
                """UPDATE scotus_cases SET status=%s,updated_at=%s WHERE case_id=%s""",
                (result.aggregate.status.value, updated_at, case_id),
            )
            evidence_json = json.dumps(
                [str(value) for value in result.aggregate.observation_ids], sort_keys=True
            )
            prior_history = connection.execute(
                """SELECT 1 FROM scotus_case_history
                   WHERE case_id=%s AND new_status=%s AND correlation_version=%s
                     AND evidence_ids=%s::jsonb""",
                (
                    case_id,
                    result.aggregate.status.value,
                    engine.VERSION,
                    evidence_json,
                ),
            ).fetchone()
            if prior_history is None:
                connection.execute(
                    """INSERT INTO scotus_case_history
                       (case_id,prior_status,new_status,reason,evidence_ids,correlation_version)
                       VALUES (%s,%s,%s,%s,%s::jsonb,%s)""",
                    (
                        case_id,
                        result.prior_status.value,
                        result.aggregate.status.value,
                        "deterministic evidence correlation",
                        evidence_json,
                        engine.VERSION,
                    ),
                )
            connection.execute(
                """INSERT INTO jobs(stage,input_kind,input_id,input_version,priority)
                   VALUES ('policy','scotus_case',%s,%s,10)
                   ON CONFLICT(stage,input_kind,input_id,input_version) DO NOTHING""",
                (str(case_id), engine.VERSION),
            )
            return result

    @staticmethod
    def _observation(row: dict[str, Any]) -> LegalObservation:
        return LegalObservation(
            observation_id=row["observation_id"],
            extraction_revision_id=row["extraction_revision_id"],
            case_id=row["case_id"],
            argument_id=row["argument_id"],
            observation_type=LegalObservationType(row["observation_type"]),
            legal_status=LegalStatus(row["legal_status"]),
            certainty=LegalCertainty(row["certainty"]),
            raw_value_private=row["raw_value_private"],
            normalized_value_private=row["normalized_value_private"],
            attribution=row["attribution_private"],
            speaker_name=row["speaker_name"],
            speaker_kind=SpeakerKind(row["speaker_kind"]),
            identity_basis=SpeakerIdentityBasis(row["identity_basis"]),
            authority_citations=tuple(row["authority_citations_private"]),
            confidence=row["confidence"],
            evidence=tuple(
                LegalEvidenceRange.model_validate(value) for value in row["evidence_private"]
            ),
            sensitivity=tuple(ScotusSensitivity(value) for value in row["sensitivity"]),
            supersedes_observation_id=row["supersedes_observation_id"],
        )
