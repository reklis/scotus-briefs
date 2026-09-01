"""PostgreSQL persistence for immutable analysis revisions."""

from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.analysis.stt import CaptureForAnalysis
from ragchew.contracts import JobStage, TranscriptRevision, TranscriptStatus


class PostgresTranscriptStore:
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

    def get_capture(self, capture_id: str) -> CaptureForAnalysis | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                """SELECT receiver_id,capture_id,object_key,audio_bytes,audio_sha256,
                          content_type,manifest->>'talkgroup_name' AS talkgroup_name
                   FROM captures WHERE capture_id=%s AND status='ready'""",
                (capture_id,),
            ).fetchone()
        return CaptureForAnalysis(**row) if row else None

    def save_transcript(
        self,
        revision: TranscriptRevision,
        input_version: str,
        retention_days: int,
    ) -> TranscriptRevision:
        with self.pool.connection() as connection, connection.transaction():
            capture = connection.execute(
                "SELECT receiver_id FROM captures WHERE capture_id=%s",
                (revision.capture_id,),
            ).fetchone()
            if capture is None:
                raise ValueError("source capture does not exist")
            connection.execute(
                """INSERT INTO transcript_revisions
                   (revision_id,receiver_id,capture_id,status,text_private,
                    normalized_text_private,model,model_config_hash,hint_set_version,
                    confidence,started_at,completed_at,text_delete_after)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           now() + (%s * interval '1 day'))
                   ON CONFLICT(receiver_id,capture_id,model_config_hash) DO NOTHING""",
                (
                    revision.revision_id,
                    capture["receiver_id"],
                    revision.capture_id,
                    revision.status.value,
                    revision.text,
                    revision.normalized_text,
                    revision.model,
                    revision.model_config_hash,
                    revision.hint_set_version,
                    revision.confidence,
                    revision.started_at,
                    revision.completed_at,
                    retention_days,
                ),
            )
            row = connection.execute(
                """SELECT revision_id,capture_id,status,text_private AS text,
                          normalized_text_private AS normalized_text,model,
                          model_config_hash,hint_set_version,confidence,started_at,completed_at
                   FROM transcript_revisions
                   WHERE receiver_id=%s AND capture_id=%s AND model_config_hash=%s""",
                (capture["receiver_id"], revision.capture_id, revision.model_config_hash),
            ).fetchone()
            if row is None:
                raise RuntimeError("transcript insert did not produce a row")
            if revision.status == TranscriptStatus.COMPLETE:
                connection.execute(
                    """INSERT INTO jobs(stage,input_kind,input_id,input_version)
                       VALUES (%s,'transcript',%s,%s)
                       ON CONFLICT(stage,input_kind,input_id,input_version) DO NOTHING""",
                    (JobStage.EXTRACT.value, str(row["revision_id"]), input_version),
                )
        row["status"] = TranscriptStatus(row["status"])
        return TranscriptRevision.model_validate(row)
