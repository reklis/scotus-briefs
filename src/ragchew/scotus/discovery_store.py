"""PostgreSQL persistence for SCOTUS transcript-first discovery."""

from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.scotus.discovery import (
    BackfillCheckpoint,
    CollectionJob,
    DiscoveredDocument,
    DocumentCollectionJob,
    ScotusArgumentCandidate,
    deterministic_case_id,
)


def _docket_id(term: str, normalized_docket: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"ragchew:scotus-docket:{term}:{normalized_docket}")


def _document_revision_id(
    case_id: UUID, kind: str, external_id: str, revision_number: int
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"ragchew:scotus-document:{case_id}:{kind}:{external_id}:{revision_number}",
    )


class PostgresScotusDiscoveryStore:
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

    def save_candidate(
        self,
        candidate: ScotusArgumentCandidate,
        case_id: UUID,
        argument_id: UUID,
        payload_sha256: str,
        documents: tuple[DiscoveredDocument, ...],
    ) -> tuple[bool, bool, bool]:
        payload = candidate.model_dump(mode="json")
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self.pool.connection() as connection, connection.transaction():
            prior_case = connection.execute(
                "SELECT official_url FROM scotus_cases WHERE case_id=%s", (case_id,)
            ).fetchone()
            prior_argument = connection.execute(
                """SELECT official_detail_url FROM scotus_argument_sessions
                   WHERE argument_id=%s""",
                (argument_id,),
            ).fetchone()
            archive_index = bool(candidate.source_metadata.get("archive_index"))
            case_official_url = (
                prior_case["official_url"]
                if archive_index and prior_case is not None
                else candidate.official_detail_url
            )
            argument_official_url = (
                prior_argument["official_detail_url"]
                if archive_index and prior_argument is not None
                else candidate.official_detail_url
            )
            connection.execute(
                """INSERT INTO scotus_cases
                   (case_id,schema_version,term,caption_private,primary_docket,official_url,
                    status,first_observed_at,updated_at)
                   VALUES (%s,'1.0',%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(case_id) DO UPDATE SET
                     caption_private=excluded.caption_private,
                     official_url=excluded.official_url,
                     updated_at=excluded.updated_at""",
                (
                    case_id,
                    candidate.term,
                    candidate.caption,
                    candidate.primary_docket,
                    case_official_url,
                    "reargued" if candidate.reargument else "argued",
                    candidate.argument_date,
                    candidate.argument_date,
                ),
            )
            all_dockets = (candidate.primary_docket, *candidate.consolidated_dockets)
            for docket_number in all_dockets:
                docket_id = _docket_id(candidate.term, docket_number)
                docket_url = (
                    "https://www.supremecourt.gov/docket/docketfiles/html/public/"
                    f"{docket_number}.html"
                )
                connection.execute(
                    """INSERT INTO scotus_dockets
                       (docket_id,term,docket_number,normalized_docket,official_url)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT(term,normalized_docket) DO NOTHING""",
                    (
                        docket_id,
                        candidate.term,
                        docket_number,
                        docket_number,
                        docket_url,
                    ),
                )
                connection.execute(
                    """INSERT INTO scotus_case_dockets(case_id,docket_id,primary_docket)
                       VALUES (%s,%s,%s)
                       ON CONFLICT(case_id,docket_id) DO NOTHING""",
                    (case_id, docket_id, docket_number == candidate.primary_docket),
                )
            case_revision = connection.execute(
                """INSERT INTO scotus_case_revisions
                   (revision_id,case_id,revision_number,payload_private,payload_sha256,observed_at)
                   SELECT %s,%s,COALESCE(max(revision_number),0)+1,%s::jsonb,%s,%s
                   FROM scotus_case_revisions WHERE case_id=%s
                   ON CONFLICT(case_id,payload_sha256) DO NOTHING""",
                (
                    uuid4(),
                    case_id,
                    payload_json,
                    payload_sha256,
                    candidate.argument_date,
                    case_id,
                ),
            )
            connection.execute(
                """INSERT INTO scotus_argument_sessions
                   (argument_id,case_id,term,session_key,argument_date,sequence,reargument,
                    status,official_detail_url,discovered_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'transcript_pending',%s,%s,%s)
                   ON CONFLICT(argument_id) DO UPDATE SET
                     official_detail_url=excluded.official_detail_url,
                     updated_at=excluded.updated_at""",
                (
                    argument_id,
                    case_id,
                    candidate.term,
                    f"{candidate.primary_docket}:{candidate.argument_date.date()}:{candidate.sequence}",
                    candidate.argument_date,
                    candidate.sequence,
                    candidate.reargument,
                    argument_official_url,
                    candidate.argument_date,
                    candidate.argument_date,
                ),
            )
            argument_revision = connection.execute(
                """INSERT INTO scotus_argument_revisions
                   (revision_id,argument_id,revision_number,payload_private,payload_sha256,observed_at)
                   SELECT %s,%s,COALESCE(max(revision_number),0)+1,%s::jsonb,%s,%s
                   FROM scotus_argument_revisions WHERE argument_id=%s
                   ON CONFLICT(argument_id,payload_sha256) DO NOTHING""",
                (
                    uuid4(),
                    argument_id,
                    payload_json,
                    payload_sha256,
                    candidate.argument_date,
                    argument_id,
                ),
            )
            for document in documents:
                revision_id = _document_revision_id(
                    case_id, document.kind.value, document.external_id, 1
                )
                connection.execute(
                    """INSERT INTO scotus_document_revisions
                       (document_revision_id,case_id,argument_id,document_kind,external_id,
                        revision_number,official_url_private,status,content_type,observed_at)
                       VALUES (%s,%s,%s,%s,%s,1,%s,'discovered',%s,%s)
                       ON CONFLICT(case_id,document_kind,external_id,revision_number) DO NOTHING""",
                    (
                        revision_id,
                        case_id,
                        document.argument_id,
                        document.kind.value,
                        document.external_id,
                        document.official_url,
                        document.content_type,
                        candidate.argument_date,
                    ),
                )
        revision_created = case_revision.rowcount == 1 or argument_revision.rowcount == 1
        return prior_case is None, prior_argument is None, revision_created

    def enqueue_transcript(self, job: CollectionJob) -> bool:
        with self.pool.connection() as connection, connection.transaction():
            document = connection.execute(
                """SELECT document_revision_id FROM scotus_document_revisions
                   WHERE argument_id=%s AND document_kind='transcript' AND external_id=%s
                   ORDER BY revision_number DESC LIMIT 1""",
                (job.argument_id, job.external_id),
            ).fetchone()
            if document is None:
                raise RuntimeError("transcript descriptor was not persisted")
            result = connection.execute(
                """INSERT INTO jobs(stage,input_kind,input_id,input_version,priority)
                   VALUES ('collect','scotus_document',%s,%s,%s)
                   ON CONFLICT(stage,input_kind,input_id,input_version) DO NOTHING""",
                (
                    str(document["document_revision_id"]),
                    job.input_version,
                    job.priority,
                ),
            )
            return result.rowcount == 1

    def enqueue_document(self, job: DocumentCollectionJob) -> bool:
        with self.pool.connection() as connection, connection.transaction():
            document = connection.execute(
                """SELECT document_revision_id FROM scotus_document_revisions
                   WHERE case_id=%s AND document_kind=%s AND external_id=%s
                   ORDER BY revision_number DESC LIMIT 1""",
                (job.case_id, job.kind.value, job.external_id),
            ).fetchone()
            if document is None:
                raise RuntimeError("official document descriptor was not persisted")
            result = connection.execute(
                """INSERT INTO jobs(stage,input_kind,input_id,input_version,priority)
                   VALUES ('collect','scotus_document',%s,%s,%s)
                   ON CONFLICT(stage,input_kind,input_id,input_version) DO NOTHING""",
                (
                    str(document["document_revision_id"]),
                    job.input_version,
                    job.priority,
                ),
            )
            return result.rowcount == 1

    def get_backfill_checkpoint(self, term: str) -> BackfillCheckpoint:
        with self.pool.connection() as connection:
            row = connection.execute(
                """SELECT checkpoint_value FROM source_checkpoints
                   WHERE source_id='supreme_court' AND checkpoint_kind='scotus_backfill'
                     AND checkpoint_key=%s""",
                (term,),
            ).fetchone()
        return (
            BackfillCheckpoint.model_validate(row["checkpoint_value"])
            if row
            else BackfillCheckpoint(term=term)
        )

    def save_backfill_checkpoint(self, checkpoint: BackfillCheckpoint) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                """INSERT INTO source_checkpoints
                   (source_id,checkpoint_kind,checkpoint_key,checkpoint_value)
                   VALUES ('supreme_court','scotus_backfill',%s,%s::jsonb)
                   ON CONFLICT(source_id,checkpoint_kind,checkpoint_key) DO UPDATE SET
                     checkpoint_value=excluded.checkpoint_value,updated_at=now()""",
                (checkpoint.term, checkpoint.model_dump_json()),
            )
            connection.commit()

    def get_case_id(self, term: str, primary_docket: str) -> UUID:
        """Expose deterministic lookup for operational tooling."""
        return deterministic_case_id(term, primary_docket)
