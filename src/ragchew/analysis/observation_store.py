"""Immutable PostgreSQL observation revision persistence."""

from __future__ import annotations

import json
from datetime import UTC
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.analysis.extraction import TranscriptForExtraction
from ragchew.contracts import (
    EpistemicStatus,
    EvidenceRange,
    JobStage,
    Observation,
    ObservationType,
    SensitivityLabel,
)


class PostgresObservationStore:
    def __init__(
        self,
        dsn: str,
        pool: ConnectionPool[Connection[dict[str, Any]]] | None = None,
    ) -> None:
        self.pool = pool or ConnectionPool(
            dsn,
            kwargs={"row_factory": dict_row},
            min_size=1,
            max_size=5,
            open=True,
        )

    def get_transcript(self, revision_id: UUID) -> TranscriptForExtraction | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                """SELECT t.revision_id,t.capture_id,t.normalized_text_private AS text,
                          (c.manifest->>'started_at')::timestamptz AS occurred_at,
                          (c.manifest->>'talkgroup_id')::integer AS talkgroup_id,
                          c.manifest->>'talkgroup_name' AS talkgroup_name
                   FROM transcript_revisions t JOIN captures c
                     ON c.receiver_id=t.receiver_id AND c.capture_id=t.capture_id
                   WHERE t.revision_id=%s AND t.status='complete'
                     AND t.normalized_text_private IS NOT NULL""",
                (revision_id,),
            ).fetchone()
        return TranscriptForExtraction(**row) if row else None

    @staticmethod
    def _observation(row: dict[str, Any]) -> Observation:
        evidence_value = row["evidence_private"]
        sensitivity_value = row["sensitivity"]
        evidence = (
            json.loads(evidence_value) if isinstance(evidence_value, str) else evidence_value
        )
        sensitivity = (
            json.loads(sensitivity_value)
            if isinstance(sensitivity_value, str)
            else sensitivity_value
        )
        return Observation(
            observation_id=row["observation_id"],
            transcript_revision_id=row["transcript_revision_id"],
            capture_id=row["capture_id"],
            type=ObservationType(row["observation_type"]),
            raw_value=row["raw_value_private"],
            normalized_value=row["normalized_value_private"],
            confidence=row["confidence"],
            epistemic_status=EpistemicStatus(row["epistemic_status"]),
            evidence=EvidenceRange.model_validate(evidence),
            occurred_at=row["occurred_at"].astimezone(UTC),
            sensitivity=tuple(SensitivityLabel(value) for value in sensitivity),
            routine=row["routine"],
            supersedes_observation_id=row["supersedes_observation_id"],
        )

    def save_extraction(
        self,
        transcript: TranscriptForExtraction,
        observations: list[Observation],
        *,
        model: str,
        schema_version: str,
        prompt_version: str,
        vocabulary_version: str,
    ) -> list[Observation]:
        with self.pool.connection() as connection, connection.transaction():
            inserted = connection.execute(
                """INSERT INTO extraction_revisions
                   (transcript_revision_id,model,schema_version,prompt_version,vocabulary_version)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT(transcript_revision_id,model,schema_version,prompt_version,
                               vocabulary_version) DO NOTHING
                   RETURNING extraction_revision_id""",
                (
                    transcript.revision_id,
                    model,
                    schema_version,
                    prompt_version,
                    vocabulary_version,
                ),
            ).fetchone()
            if inserted is None:
                revision = connection.execute(
                    """SELECT extraction_revision_id FROM extraction_revisions
                       WHERE transcript_revision_id=%s AND model=%s AND schema_version=%s
                         AND prompt_version=%s AND vocabulary_version=%s""",
                    (
                        transcript.revision_id,
                        model,
                        schema_version,
                        prompt_version,
                        vocabulary_version,
                    ),
                ).fetchone()
                if revision is None:
                    raise RuntimeError("extraction revision disappeared")
                extraction_revision_id = revision["extraction_revision_id"]
            else:
                extraction_revision_id = inserted["extraction_revision_id"]
                for item in observations:
                    connection.execute(
                        """INSERT INTO observations
                           (observation_id,extraction_revision_id,transcript_revision_id,capture_id,
                            observation_type,raw_value_private,normalized_value_private,confidence,
                            epistemic_status,evidence_private,occurred_at,sensitivity,routine,
                            supersedes_observation_id)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s)""",
                        (
                            item.observation_id,
                            extraction_revision_id,
                            item.transcript_revision_id,
                            item.capture_id,
                            item.type.value,
                            item.raw_value,
                            item.normalized_value,
                            item.confidence,
                            item.epistemic_status.value,
                            item.evidence.model_dump_json(),
                            item.occurred_at,
                            json.dumps([label.value for label in item.sensitivity]),
                            item.routine,
                            item.supersedes_observation_id,
                        ),
                    )
                connection.execute(
                    """INSERT INTO jobs(stage,input_kind,input_id,input_version)
                       VALUES (%s,'extraction',%s,%s)
                       ON CONFLICT(stage,input_kind,input_id,input_version) DO NOTHING""",
                    (
                        JobStage.CORRELATE.value,
                        str(extraction_revision_id),
                        f"{schema_version}:{prompt_version}:{vocabulary_version}",
                    ),
                )
            rows = connection.execute(
                "SELECT * FROM observations WHERE extraction_revision_id=%s ORDER BY created_at",
                (extraction_revision_id,),
            ).fetchall()
        return [self._observation(row) for row in rows]
