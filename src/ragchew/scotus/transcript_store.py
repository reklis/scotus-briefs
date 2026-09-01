"""Immutable transcript parse persistence and private-text retention."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.scotus.transcript_parser import TranscriptParseResult


class InMemoryTranscriptParseStore:
    def __init__(self) -> None:
        self.results: dict[tuple[UUID, str], TranscriptParseResult] = {}
        self.extract_jobs: set[tuple[UUID, str]] = set()

    def save(self, result: TranscriptParseResult, retention_days: int) -> bool:
        key = (result.document_revision_id, result.config_hash)
        if key in self.results:
            return False
        self.results[key] = result
        before = len(self.extract_jobs)
        self.extract_jobs.add((result.parse_revision_id, result.config_hash))
        return len(self.extract_jobs) != before


class PostgresTranscriptParseStore:
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

    def save(self, result: TranscriptParseResult, retention_days: int) -> bool:
        with self.pool.connection() as connection, connection.transaction():
            existing = connection.execute(
                """SELECT parse_revision_id FROM scotus_document_parses
                   WHERE document_revision_id=%s AND parser=%s AND parser_version=%s
                     AND config_hash=%s""",
                (
                    result.document_revision_id,
                    result.parser_name,
                    result.parser_version,
                    result.config_hash,
                ),
            ).fetchone()
            if existing:
                return False
            connection.execute(
                """INSERT INTO scotus_document_parses
                   (parse_revision_id,document_revision_id,parser,parser_version,config_hash,
                    status,page_count,text_delete_after)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,now() + (%s * interval '1 day'))""",
                (
                    result.parse_revision_id,
                    result.document_revision_id,
                    result.parser_name,
                    result.parser_version,
                    result.config_hash,
                    result.status.value,
                    result.page_count,
                    retention_days,
                ),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO scotus_transcript_lines
                       (line_id,parse_revision_id,document_revision_id,file_page,printed_page,
                        line_number,raw_text_private,normalized_text_private,artifact)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        (
                            line.line_id,
                            line.parse_revision_id,
                            line.document_revision_id,
                            line.file_page,
                            line.printed_page,
                            line.line_number,
                            line.raw_text_private,
                            line.normalized_text_private,
                            line.artifact,
                        )
                        for line in result.lines
                    ],
                )
                cursor.executemany(
                    """INSERT INTO scotus_transcript_turns
                       (turn_id,parse_revision_id,document_revision_id,sequence,start_file_page,
                        start_line,end_file_page,end_line,speaker_label_private,speaker_name,
                        speaker_kind,advocate_role,identity_basis,text_private,confidence)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        (
                            turn.turn_id,
                            turn.parse_revision_id,
                            turn.document_revision_id,
                            turn.sequence,
                            turn.start_file_page,
                            turn.start_line,
                            turn.end_file_page,
                            turn.end_line,
                            turn.speaker_label_private,
                            turn.speaker_name,
                            turn.speaker_kind.value,
                            turn.advocate_role.value if turn.advocate_role else None,
                            turn.identity_basis.value,
                            turn.text_private,
                            turn.confidence,
                        )
                        for turn in result.turns
                    ],
                )
            connection.execute(
                """UPDATE scotus_document_revisions SET status='parsed'
                   WHERE document_revision_id=%s""",
                (result.document_revision_id,),
            )
            connection.execute(
                """UPDATE scotus_argument_sessions a SET
                     transcript_document_revision_id=%s,status='transcript_ready',updated_at=now()
                   FROM scotus_document_revisions d
                   WHERE d.document_revision_id=%s AND a.argument_id=d.argument_id""",
                (result.document_revision_id, result.document_revision_id),
            )
            job = connection.execute(
                """INSERT INTO jobs(stage,input_kind,input_id,input_version,priority)
                   VALUES ('extract','scotus_transcript_parse',%s,%s,10)
                   ON CONFLICT(stage,input_kind,input_id,input_version) DO NOTHING""",
                (str(result.parse_revision_id), result.config_hash),
            )
            return job.rowcount == 1

    def expire_documents(self, now: datetime) -> list[str]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """UPDATE scotus_document_revisions d SET
                     status='content_deleted',content_deleted_at=%s
                   WHERE d.delete_after <= %s AND d.content_deleted_at IS NULL
                     AND d.object_key IS NOT NULL
                     AND NOT EXISTS (
                       SELECT 1 FROM jobs j
                       WHERE j.input_id=d.document_revision_id::text
                         AND j.status='leased' AND j.lease_expires_at > %s
                     )
                   RETURNING d.object_key""",
                (now, now, now),
            ).fetchall()
            connection.commit()
        return [row["object_key"] for row in rows]

    def expire_extracted_text(self, now: datetime) -> int:
        with self.pool.connection() as connection, connection.transaction():
            parse_ids = connection.execute(
                """UPDATE scotus_document_parses SET text_deleted_at=%s
                   WHERE text_delete_after <= %s AND text_deleted_at IS NULL
                     AND NOT EXISTS (
                       SELECT 1 FROM jobs j
                       WHERE j.input_id=scotus_document_parses.parse_revision_id::text
                         AND j.status='leased' AND j.lease_expires_at > %s
                     )
                   RETURNING parse_revision_id""",
                (now, now, now),
            ).fetchall()
            ids = [row["parse_revision_id"] for row in parse_ids]
            if not ids:
                return 0
            connection.execute(
                """UPDATE scotus_transcript_lines SET
                     raw_text_private=NULL,normalized_text_private=NULL
                   WHERE parse_revision_id = ANY(%s)""",
                (ids,),
            )
            connection.execute(
                """UPDATE scotus_transcript_turns SET text_private=NULL
                   WHERE parse_revision_id = ANY(%s)""",
                (ids,),
            )
            return len(ids)
