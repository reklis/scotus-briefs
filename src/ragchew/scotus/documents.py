"""Bounded private ingestion of official Supreme Court documents."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Protocol, cast
from urllib.parse import urlparse
from uuid import UUID

import httpx
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ragchew.config import ScotusConfig
from ragchew.contracts import StrictModel
from ragchew.proceedings.contracts import SourceAccessMethod, UtcDatetime
from ragchew.proceedings.registry import SourceAuthorizer
from ragchew.scotus.contracts import ScotusDocumentKind
from ragchew.storage import ObjectStore


class DocumentCollectionError(RuntimeError):
    """Raised when an official document cannot be accepted safely."""


class PendingDocument(StrictModel):
    document_revision_id: UUID
    case_id: UUID
    argument_id: UUID | None = None
    source_id: str = "supreme_court"
    kind: ScotusDocumentKind
    external_id: str = Field(min_length=1, max_length=500)
    revision_number: int = Field(ge=1)
    official_url: str
    expected_content_type: str
    observed_at: UtcDatetime


class AcceptedDocument(StrictModel):
    document_revision_id: UUID
    case_id: UUID
    kind: ScotusDocumentKind
    external_id: str
    revision_number: int
    official_url: str
    content_type: str
    byte_count: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_key: str
    page_count: int | None = Field(default=None, gt=0)
    ready_at: UtcDatetime


class IngestionOutcome(StrictModel):
    status: str
    document_revision_id: UUID
    sha256: str | None = None
    object_key: str | None = None
    parse_job_created: bool = False
    diagnostic: str | None = None


class DocumentIngestionStore(Protocol):
    def accepted_for_identity(
        self, case_id: UUID, kind: ScotusDocumentKind, external_id: str
    ) -> AcceptedDocument | None: ...

    def commit(
        self, document: AcceptedDocument, delete_after: datetime, *, priority: int
    ) -> bool: ...

    def quarantine(self, revision_id: UUID, diagnostic: str) -> None: ...

    def fail(self, revision_id: UUID, diagnostic: str) -> None: ...


class InMemoryDocumentIngestionStore:
    def __init__(self) -> None:
        self.accepted: dict[UUID, AcceptedDocument] = {}
        self.identity: dict[tuple[UUID, ScotusDocumentKind, str], UUID] = {}
        self.parse_jobs: set[tuple[UUID, str, int]] = set()
        self.quarantined: dict[UUID, str] = {}
        self.failures: dict[UUID, str] = {}

    def accepted_for_identity(
        self, case_id: UUID, kind: ScotusDocumentKind, external_id: str
    ) -> AcceptedDocument | None:
        revision_id = self.identity.get((case_id, kind, external_id))
        return self.accepted.get(revision_id) if revision_id else None

    def commit(
        self, document: AcceptedDocument, delete_after: datetime, *, priority: int
    ) -> bool:
        key = (document.case_id, document.kind, document.external_id)
        prior_id = self.identity.get(key)
        if prior_id:
            prior = self.accepted[prior_id]
            if prior.sha256 == document.sha256:
                return False
        self.identity[key] = document.document_revision_id
        self.accepted[document.document_revision_id] = document
        before = len(self.parse_jobs)
        self.parse_jobs.add((document.document_revision_id, document.sha256, priority))
        return len(self.parse_jobs) != before

    def quarantine(self, revision_id: UUID, diagnostic: str) -> None:
        self.quarantined[revision_id] = diagnostic

    def fail(self, revision_id: UUID, diagnostic: str) -> None:
        self.failures[revision_id] = diagnostic


class PostgresDocumentIngestionStore:
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

    @staticmethod
    def _accepted(row: dict[str, Any]) -> AcceptedDocument:
        return AcceptedDocument(
            document_revision_id=row["document_revision_id"],
            case_id=row["case_id"],
            kind=ScotusDocumentKind(row["document_kind"]),
            external_id=row["external_id"],
            revision_number=row["revision_number"],
            official_url=row["official_url_private"],
            content_type=row["content_type"],
            byte_count=row["byte_count"],
            sha256=row["sha256"],
            object_key=row["object_key"],
            page_count=None,
            ready_at=row["ready_at"],
        )

    def accepted_for_identity(
        self, case_id: UUID, kind: ScotusDocumentKind, external_id: str
    ) -> AcceptedDocument | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                """SELECT document_revision_id,case_id,document_kind,external_id,
                          revision_number,official_url_private,content_type,byte_count,sha256,
                          object_key,ready_at
                   FROM scotus_document_revisions
                   WHERE case_id=%s AND document_kind=%s AND external_id=%s
                     AND status IN ('ready','parsed') AND canonical
                   ORDER BY revision_number DESC LIMIT 1""",
                (case_id, kind.value, external_id),
            ).fetchone()
        return self._accepted(row) if row else None

    def commit(
        self, document: AcceptedDocument, delete_after: datetime, *, priority: int
    ) -> bool:
        with self.pool.connection() as connection, connection.transaction():
            existing = connection.execute(
                """SELECT document_revision_id,sha256 FROM scotus_document_revisions
                   WHERE case_id=%s AND document_kind=%s AND external_id=%s
                     AND status IN ('ready','parsed') AND canonical FOR UPDATE""",
                (document.case_id, document.kind.value, document.external_id),
            ).fetchone()
            if existing and existing["sha256"] == document.sha256:
                return False
            if existing:
                connection.execute(
                    """UPDATE scotus_document_revisions SET canonical=false
                       WHERE document_revision_id=%s""",
                    (existing["document_revision_id"],),
                )
            result = connection.execute(
                """UPDATE scotus_document_revisions SET
                     status='ready',content_type=%s,byte_count=%s,sha256=%s,object_key=%s,
                     canonical=true,ready_at=%s,delete_after=%s,diagnostic_private=NULL
                   WHERE document_revision_id=%s AND status IN ('discovered','downloading')""",
                (
                    document.content_type,
                    document.byte_count,
                    document.sha256,
                    document.object_key,
                    document.ready_at,
                    delete_after,
                    document.document_revision_id,
                ),
            )
            if result.rowcount != 1:
                raise RuntimeError("document revision was not in a committable state")
            job = connection.execute(
                """INSERT INTO jobs(stage,input_kind,input_id,input_version,priority)
                   VALUES ('parse','scotus_document',%s,%s,%s)
                   ON CONFLICT(stage,input_kind,input_id,input_version) DO NOTHING""",
                (
                    str(document.document_revision_id),
                    document.sha256,
                    priority,
                ),
            )
            return job.rowcount == 1

    def quarantine(self, revision_id: UUID, diagnostic: str) -> None:
        self._set_status(revision_id, "quarantined", diagnostic)

    def fail(self, revision_id: UUID, diagnostic: str) -> None:
        self._set_status(revision_id, "parse_failed", diagnostic)

    def _set_status(self, revision_id: UUID, status: str, diagnostic: str) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                """UPDATE scotus_document_revisions SET status=%s,diagnostic_private=%s
                   WHERE document_revision_id=%s""",
                (status, diagnostic[:4_000], revision_id),
            )
            connection.commit()


def document_object_key(document: PendingDocument, sha256: str) -> str:
    safe_external = hashlib.sha256(document.external_id.encode()).hexdigest()[:20]
    extension = ".pdf" if document.expected_content_type == "application/pdf" else ".html"
    return str(
        PurePosixPath(
            "official",
            "us_supreme_court",
            document.source_id,
            str(document.case_id),
            document.kind.value,
            f"{safe_external}-r{document.revision_number}-{sha256[:16]}{extension}",
        )
    )


def _validate_path(document: PendingDocument) -> None:
    parsed = urlparse(document.official_url)
    path = parsed.path.lower()
    prefixes = {
        ScotusDocumentKind.TRANSCRIPT: (
            "/oral_arguments/argument_transcripts/",
            "/pdfs/transcripts/",
        ),
        ScotusDocumentKind.DOCKET: ("/docket/",),
        ScotusDocumentKind.ORDER: ("/opinions/", "/orders/"),
        ScotusDocumentKind.OPINION: ("/opinions/",),
        ScotusDocumentKind.QUESTION_PRESENTED: ("/docket/", "/qp/"),
        ScotusDocumentKind.OTHER_OFFICIAL: ("/",),
    }
    if not any(path.startswith(prefix) for prefix in prefixes[document.kind]):
        raise DocumentCollectionError("official document path is outside the approved kind scope")


def _validate_pdf(file: BinaryIO, maximum_pages: int) -> int:
    file.seek(0)
    signature = file.read(5)
    if signature != b"%PDF-":
        raise DocumentCollectionError("document does not have a PDF signature")
    file.seek(0)
    try:
        reader = PdfReader(file, strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentCollectionError("password-protected PDFs are not supported")
        page_count = len(reader.pages)
    except (PdfReadError, ValueError) as error:
        raise DocumentCollectionError(f"PDF is not decodable: {error}") from error
    if page_count < 1 or page_count > maximum_pages:
        raise DocumentCollectionError("PDF page count is outside configured bounds")
    file.seek(0)
    return page_count


def _validate_html(file: BinaryIO) -> None:
    file.seek(0)
    prefix = file.read(1_024).lstrip().lower()
    if not (prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html")):
        raise DocumentCollectionError("document does not have an HTML signature")
    file.seek(0)


class ScotusDocumentCollector:
    def __init__(
        self,
        authorizer: SourceAuthorizer,
        store: DocumentIngestionStore,
        objects: ObjectStore,
        config: ScotusConfig,
        *,
        user_agent: str,
        client: httpx.Client | None = None,
    ) -> None:
        if "contact" not in user_agent.lower():
            raise ValueError("collector user agent must include contact information")
        self.authorizer = authorizer
        self.store = store
        self.objects = objects
        self.config = config
        self.user_agent = user_agent
        self.client = client or httpx.Client(follow_redirects=False)

    def collect(
        self, document: PendingDocument, now: datetime, *, priority: int = 10
    ) -> IngestionOutcome:
        try:
            source = self.authorizer.authorize_url(
                document.source_id,
                document.official_url,
                SourceAccessMethod.OFFICIAL_PAGE,
                now,
                media=False,
            )
            if source.source_id != "supreme_court":
                raise DocumentCollectionError("document source is not Supreme Court")
            _validate_path(document)
            accepted = self._download(document)
            prior = self.store.accepted_for_identity(
                document.case_id, document.kind, document.external_id
            )
            if prior and prior.sha256 == accepted.sha256:
                accepted.file.close()
                return IngestionOutcome(
                    status="duplicate",
                    document_revision_id=prior.document_revision_id,
                    sha256=prior.sha256,
                    object_key=prior.object_key,
                )
            if (
                prior
                and prior.sha256 != accepted.sha256
                and document.revision_number <= prior.revision_number
            ):
                diagnostic = "conflicting bytes under an existing document revision identity"
                accepted.file.close()
                self.store.quarantine(document.document_revision_id, diagnostic)
                return IngestionOutcome(
                    status="quarantined",
                    document_revision_id=document.document_revision_id,
                    sha256=accepted.sha256,
                    diagnostic=diagnostic,
                )
            self.objects.put_file(
                accepted.document.object_key,
                accepted.file,
                accepted.document.content_type,
                accepted.document.sha256,
            )
            job_created = self.store.commit(
                accepted.document,
                now + timedelta(hours=self.config.retention.copied_document_hours),
                priority=priority,
            )
            accepted.file.close()
            return IngestionOutcome(
                status="ready",
                document_revision_id=document.document_revision_id,
                sha256=accepted.document.sha256,
                object_key=accepted.document.object_key,
                parse_job_created=job_created,
            )
        except (DocumentCollectionError, httpx.HTTPError) as error:
            self.store.fail(document.document_revision_id, str(error))
            return IngestionOutcome(
                status="failed",
                document_revision_id=document.document_revision_id,
                diagnostic=str(error),
            )

    def _download(self, document: PendingDocument) -> _DownloadedDocument:
        maximum = self.config.documents.maximum_pdf_bytes
        with self.client.stream(
            "GET",
            document.official_url,
            headers={"User-Agent": self.user_agent, "Accept": document.expected_content_type},
            timeout=60,
        ) as response:
            if 300 <= response.status_code < 400:
                raise DocumentCollectionError("redirects are not permitted")
            if response.status_code != 200:
                raise DocumentCollectionError(
                    f"official document returned HTTP {response.status_code}"
                )
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type != document.expected_content_type:
                raise DocumentCollectionError("document MIME does not match descriptor")
            if content_type not in self.config.documents.allowed_content_types:
                raise DocumentCollectionError("document MIME is not configured")
            announced = response.headers.get("content-length")
            if announced and int(announced) > maximum:
                raise DocumentCollectionError("document exceeds configured byte bound")
            file = cast(
                BinaryIO,
                tempfile.SpooledTemporaryFile(  # noqa: SIM115 - returned to object writer
                    max_size=self.config.documents.spool_memory_bytes
                ),
            )
            digest = hashlib.sha256()
            byte_count = 0
            for chunk in response.iter_bytes():
                byte_count += len(chunk)
                if byte_count > maximum:
                    file.close()
                    raise DocumentCollectionError("document exceeds configured byte bound")
                digest.update(chunk)
                file.write(chunk)
        if byte_count == 0:
            file.close()
            raise DocumentCollectionError("document is empty")
        if content_type == "application/pdf":
            page_count = _validate_pdf(file, self.config.documents.maximum_pages)
        else:
            _validate_html(file)
            page_count = None
        sha256 = digest.hexdigest()
        key = document_object_key(document, sha256)
        accepted = AcceptedDocument(
            document_revision_id=document.document_revision_id,
            case_id=document.case_id,
            kind=document.kind,
            external_id=document.external_id,
            revision_number=document.revision_number,
            official_url=document.official_url,
            content_type=content_type,
            byte_count=byte_count,
            sha256=sha256,
            object_key=key,
            page_count=page_count,
            ready_at=document.observed_at,
        )
        file.seek(0)
        return _DownloadedDocument(document=accepted, file=file)


@dataclass
class _DownloadedDocument:
    document: AcceptedDocument
    file: BinaryIO

    @property
    def sha256(self) -> str:
        return self.document.sha256
