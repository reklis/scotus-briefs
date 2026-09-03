"""PostgreSQL persistence and durable job queue."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.contracts import CaptureEnvelope, EdgeHeartbeat, JobStage


@dataclass(frozen=True)
class CaptureRecord:
    receiver_id: str
    capture_id: str
    audio_sha256: str
    audio_bytes: int
    content_type: str
    object_key: str
    status: str


@dataclass(frozen=True)
class JobRecord:
    job_id: UUID
    stage: str
    input_kind: str
    input_id: str
    input_version: str
    attempts: int


class Repository(Protocol):
    def get_capture(self, receiver_id: str, capture_id: str) -> CaptureRecord | None: ...

    def create_capture(self, envelope: CaptureEnvelope, object_key: str) -> CaptureRecord: ...

    def commit_capture(self, receiver_id: str, capture_id: str, audio_hours: int) -> bool: ...

    def reject_capture(self, receiver_id: str, capture_id: str, diagnostic: str) -> None: ...

    def record_heartbeat(self, heartbeat: EdgeHeartbeat) -> None: ...

    def claim_job(
        self,
        worker_id: str,
        lease_seconds: int,
        stages: tuple[str, ...] | None = None,
    ) -> JobRecord | None: ...

    def complete_job(self, job_id: UUID, worker_id: str, output_id: str) -> bool: ...

    def fail_job(
        self,
        job_id: UUID,
        worker_id: str,
        error: str,
        maximum_attempts: int,
        delay_seconds: float,
    ) -> bool: ...

    def expire_abandoned_uploads(self, older_than: datetime) -> list[str]: ...

    def expire_audio(self, now: datetime) -> list[str]: ...

    def expire_transcripts(self, now: datetime) -> int: ...

    def job_backlog(self) -> dict[str, int]: ...


class PostgresRepository:
    def __init__(self, dsn: str, pool: ConnectionPool[Connection[dict[str, Any]]] | None = None):
        self.pool = pool or ConnectionPool(
            conninfo=dsn,
            kwargs={"row_factory": dict_row},
            min_size=1,
            max_size=10,
            open=True,
        )

    @contextmanager
    def _connection(self) -> Iterator[Connection[dict[str, Any]]]:
        with self.pool.connection() as connection:
            yield connection

    @staticmethod
    def _capture(row: dict[str, Any]) -> CaptureRecord:
        return CaptureRecord(
            receiver_id=row["receiver_id"],
            capture_id=row["capture_id"],
            audio_sha256=row["audio_sha256"],
            audio_bytes=row["audio_bytes"],
            content_type=row["content_type"],
            object_key=row["object_key"],
            status=row["status"],
        )

    def get_capture(self, receiver_id: str, capture_id: str) -> CaptureRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT receiver_id, capture_id, audio_sha256, audio_bytes, content_type,
                          object_key, status::text
                   FROM captures WHERE receiver_id = %s AND capture_id = %s""",
                (receiver_id, capture_id),
            ).fetchone()
        return self._capture(row) if row else None

    def create_capture(self, envelope: CaptureEnvelope, object_key: str) -> CaptureRecord:
        with self._connection() as connection, connection.transaction():
            connection.execute(
                """INSERT INTO captures
                   (capture_id, receiver_id, schema_version, manifest, audio_sha256,
                    audio_bytes, content_type, object_key, status)
                   VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,'uploading')
                   ON CONFLICT (receiver_id, capture_id) DO NOTHING""",
                (
                    envelope.capture_id,
                    envelope.receiver_id,
                    envelope.schema_version,
                    envelope.model_dump_json(),
                    envelope.audio.sha256,
                    envelope.audio.byte_count,
                    envelope.audio.content_type,
                    object_key,
                ),
            )
            row = connection.execute(
                """SELECT receiver_id, capture_id, audio_sha256, audio_bytes, content_type,
                          object_key, status::text
                   FROM captures WHERE receiver_id = %s AND capture_id = %s""",
                (envelope.receiver_id, envelope.capture_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("capture insert did not produce a row")
        return self._capture(row)

    def commit_capture(self, receiver_id: str, capture_id: str, audio_hours: int) -> bool:
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """SELECT status::text FROM captures
                   WHERE receiver_id=%s AND capture_id=%s FOR UPDATE""",
                (receiver_id, capture_id),
            ).fetchone()
            if row is None:
                return False
            if row["status"] == "ready":
                return True
            if row["status"] not in {"created", "uploading"}:
                return False
            result = connection.execute(
                """UPDATE captures SET status='ready', committed_at=now(),
                   audio_delete_after=now() + (%s * interval '1 hour')
                   WHERE receiver_id=%s AND capture_id=%s""",
                (audio_hours, receiver_id, capture_id),
            )
            connection.execute(
                """INSERT INTO jobs(stage,input_kind,input_id,input_version)
                   SELECT %s,'capture',capture_id,audio_sha256 FROM captures
                   WHERE receiver_id=%s AND capture_id=%s
                   ON CONFLICT(stage,input_kind,input_id,input_version) DO NOTHING""",
                (JobStage.TRANSCRIBE.value, receiver_id, capture_id),
            )
            return result.rowcount == 1

    def reject_capture(self, receiver_id: str, capture_id: str, diagnostic: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """UPDATE captures SET status='rejected', diagnostic=%s
                   WHERE receiver_id=%s AND capture_id=%s AND status <> 'ready'""",
                (diagnostic[:2_000], receiver_id, capture_id),
            )
            connection.commit()

    def record_heartbeat(self, heartbeat: EdgeHeartbeat) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO edge_heartbeats(receiver_id,payload,observed_at)
                   VALUES (%s,%s::jsonb,%s)""",
                (heartbeat.receiver_id, heartbeat.model_dump_json(), heartbeat.observed_at),
            )
            connection.commit()

    def claim_job(
        self,
        worker_id: str,
        lease_seconds: int,
        stages: tuple[str, ...] | None = None,
    ) -> JobRecord | None:
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """WITH candidate AS (
                     SELECT job_id FROM jobs
                     WHERE (((status IN ('pending','retry') AND available_at <= now())
                        OR (status='leased' AND lease_expires_at <= now())))
                       AND (%s::text[] IS NULL OR stage = ANY(%s::text[]))
                     ORDER BY priority, available_at, created_at
                     FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE jobs j SET status='leased', lease_owner=%s,
                     lease_expires_at=now() + (%s * interval '1 second'), attempts=attempts+1
                   FROM candidate WHERE j.job_id=candidate.job_id
                   RETURNING j.job_id,j.stage,j.input_kind,j.input_id,j.input_version,j.attempts""",
                (
                    list(stages) if stages else None,
                    list(stages) if stages else None,
                    worker_id,
                    lease_seconds,
                ),
            ).fetchone()
        return JobRecord(**row) if row else None

    def complete_job(self, job_id: UUID, worker_id: str, output_id: str) -> bool:
        with self._connection() as connection:
            result = connection.execute(
                """UPDATE jobs SET status='complete',output_id=%s,completed_at=now(),
                   lease_owner=NULL,lease_expires_at=NULL
                   WHERE job_id=%s AND status='leased' AND lease_owner=%s""",
                (output_id, job_id, worker_id),
            )
            connection.commit()
            return result.rowcount == 1

    def fail_job(
        self,
        job_id: UUID,
        worker_id: str,
        error: str,
        maximum_attempts: int,
        delay_seconds: float,
    ) -> bool:
        with self._connection() as connection:
            result = connection.execute(
                """UPDATE jobs SET
                   status=CASE WHEN attempts >= %s
                     THEN 'failed'::job_status ELSE 'retry'::job_status END,
                   available_at=now() + (%s * interval '1 second'),last_error=%s,
                   lease_owner=NULL,lease_expires_at=NULL
                   WHERE job_id=%s AND status='leased' AND lease_owner=%s""",
                (maximum_attempts, delay_seconds, error[:4_000], job_id, worker_id),
            )
            connection.commit()
            return result.rowcount == 1

    def expire_abandoned_uploads(self, older_than: datetime) -> list[str]:
        with self._connection() as connection:
            rows = connection.execute(
                """UPDATE captures SET status='expired'
                   WHERE status IN ('created','uploading') AND created_at < %s
                   RETURNING object_key""",
                (older_than,),
            ).fetchall()
            connection.commit()
        return [row["object_key"] for row in rows]

    def expire_audio(self, now: datetime) -> list[str]:
        with self._connection() as connection:
            rows = connection.execute(
                """UPDATE captures c SET status='audio_deleted',audio_deleted_at=%s
                   WHERE c.status='ready' AND c.audio_delete_after <= %s
                     AND NOT EXISTS (
                       SELECT 1 FROM jobs j WHERE j.input_id=c.capture_id
                         AND j.status='leased' AND j.lease_expires_at > %s)
                   RETURNING object_key""",
                (now, now, now),
            ).fetchall()
            connection.commit()
        return [row["object_key"] for row in rows]

    def expire_transcripts(self, now: datetime) -> int:
        with self._connection() as connection:
            result = connection.execute(
                """UPDATE transcript_revisions SET text_private=NULL,
                   normalized_text_private=NULL,text_deleted_at=%s
                   WHERE text_delete_after <= %s AND text_deleted_at IS NULL""",
                (now, now),
            )
            connection.commit()
            return result.rowcount

    def job_backlog(self) -> dict[str, int]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT stage,count(*) AS count FROM jobs
                   WHERE status IN ('pending','retry')
                      OR (status='leased' AND lease_expires_at <= now())
                   GROUP BY stage"""
            ).fetchall()
        return {row["stage"]: row["count"] for row in rows}

    def active_job_lease_count(self, stages: tuple[str, ...] | None = None) -> int:
        """Count unexpired leases in the selected bounded-worker stage set."""
        with self._connection() as connection:
            row = connection.execute(
                """SELECT count(*) AS count FROM jobs
                   WHERE status='leased' AND lease_expires_at > now()
                     AND (%s::text[] IS NULL OR stage = ANY(%s::text[]))""",
                (list(stages) if stages else None, list(stages) if stages else None),
            ).fetchone()
        return int(row["count"]) if row else 0
