"""Validated, immutable PDF acquisition and atomic provenance manifests."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .models import (
    CaseAssociation,
    DocumentManifest,
    DocumentManifestEntry,
    DocumentType,
    SourceIdentity,
)


class ArchiveError(RuntimeError):
    """A candidate could not safely be admitted to the archive."""


class EmptyDiscoveryError(ArchiveError):
    """A successful-looking source unexpectedly returned no candidates."""


class DocumentCandidate(BaseModel):
    """Normalized document metadata emitted by source adapters."""

    model_config = ConfigDict(extra="forbid")
    url: HttpUrl
    document_type: DocumentType
    cases: list[CaseAssociation] = Field(default_factory=list)
    source_id: str | None = None
    page_url: HttpUrl | None = None
    official_filename: str | None = None
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class DownloadedPdf:
    path: Path
    sha256: str
    byte_size: int
    retrieved_at: datetime


@dataclass(slots=True)
class ReconcileFailure:
    url: str
    error: str


@dataclass(slots=True)
class ReconcileReport:
    accepted_hashes: list[str] = field(default_factory=list)
    unchanged_hashes: list[str] = field(default_factory=list)
    changed_case_ids: set[str] = field(default_factory=set)
    failures: list[ReconcileFailure] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted_hashes": self.accepted_hashes,
            "unchanged_hashes": self.unchanged_hashes,
            "changed_case_ids": sorted(self.changed_case_ids),
            "failures": [asdict(item) for item in self.failures],
        }


class PdfDownloader:
    """Stream remote PDFs to a temporary file while validating and hashing bytes."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        max_bytes: int = 100 * 1024 * 1024,
        chunk_size: int = 64 * 1024,
    ) -> None:
        self.client = client
        self.max_bytes = max_bytes
        self.chunk_size = chunk_size

    def download(self, url: str, temp_dir: Path) -> DownloadedPdf:
        temp_dir.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(prefix="download-", suffix=".part", dir=temp_dir)
        path = Path(raw_path)
        digest = hashlib.sha256()
        size = 0
        prefix = bytearray()
        try:
            with os.fdopen(descriptor, "wb") as output, self.client.stream("GET", url) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(self.chunk_size):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ArchiveError(f"PDF exceeds configured {self.max_bytes}-byte limit")
                    if len(prefix) < 5:
                        prefix.extend(chunk[: 5 - len(prefix)])
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size == 0:
                raise ArchiveError("response body is empty")
            if bytes(prefix) != b"%PDF-":
                raise ArchiveError("response does not begin with a PDF signature")
            return DownloadedPdf(path, digest.hexdigest(), size, datetime.now(UTC))
        except Exception:
            path.unlink(missing_ok=True)
            raise


def archive_relative_path(sha256: str) -> Path:
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ValueError("invalid lowercase SHA-256")
    return Path("documents") / sha256[:2] / f"{sha256}.pdf"


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def validate_pdf_file(path: Path) -> tuple[str, int]:
    try:
        with path.open("rb") as stream:
            signature = stream.read(5)
    except OSError as error:
        raise ArchiveError(f"cannot read PDF: {error}") from error
    if signature != b"%PDF-":
        raise ArchiveError("file does not begin with a PDF signature")
    digest, size = hash_file(path)
    if size == 0:
        raise ArchiveError("PDF is empty")
    return digest, size


class ContentAddressedArchive:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, sha256: str) -> Path:
        return self.root / archive_relative_path(sha256)

    def admit(self, source: Path, sha256: str) -> tuple[Path, bool]:
        """Atomically add bytes; return path and whether a new blob was created."""
        target = self.path_for(sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            actual, _ = hash_file(target)
            if actual != sha256:
                raise ArchiveError(f"immutable archive collision at {target}")
            return target, False
        descriptor, raw_temp = tempfile.mkstemp(prefix=f".{sha256}.", dir=target.parent)
        temp = Path(raw_temp)
        try:
            with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output)
                output.flush()
                os.fsync(output.fileno())
            actual, _ = hash_file(temp)
            if actual != sha256:
                raise ArchiveError("source changed while it was copied")
            try:
                temp.chmod(0o644)
                os.link(temp, target)
                created = True
            except FileExistsError:
                actual, _ = hash_file(target)
                if actual != sha256:
                    raise ArchiveError(f"immutable archive collision at {target}") from None
                created = False
            return target, created
        finally:
            temp.unlink(missing_ok=True)


class ManifestStore:
    """Schema validation and lock-protected atomic replacement for one JSON manifest."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def load(self) -> DocumentManifest:
        if not self.path.exists():
            return DocumentManifest()
        return DocumentManifest.model_validate_json(self.path.read_text())

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
            fcntl.flock(lock, fcntl.LOCK_UN)

    def save(self, manifest: DocumentManifest) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_temp = tempfile.mkstemp(
            prefix=self.path.name, suffix=".tmp", dir=self.path.parent
        )
        temp = Path(raw_temp)
        try:
            payload = manifest.model_dump_json(indent=2) + "\n"
            with os.fdopen(descriptor, "w") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, self.path)
            directory_fd = os.open(self.path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp.unlink(missing_ok=True)


class Reconciler:
    def __init__(
        self,
        archive: ContentAddressedArchive,
        manifest_store: ManifestStore,
        downloader: PdfDownloader,
        temp_dir: Path,
    ) -> None:
        self.archive = archive
        self.manifest_store = manifest_store
        self.downloader = downloader
        self.temp_dir = temp_dir

    def reconcile(
        self, candidates: list[DocumentCandidate], *, allow_empty: bool = False
    ) -> ReconcileReport:
        if not candidates and not allow_empty:
            raise EmptyDiscoveryError("source returned no document candidates")
        report = ReconcileReport()
        with self.manifest_store.locked():
            manifest = self.manifest_store.load()
            entries = {item.sha256: item for item in manifest.documents}
            for candidate in candidates:
                try:
                    self._reconcile_one(candidate, entries, report)
                except (ArchiveError, httpx.HTTPError, OSError, ValueError) as error:
                    report.failures.append(ReconcileFailure(str(candidate.url), str(error)))
            if report.accepted_hashes:
                updated = DocumentManifest(
                    documents=sorted(entries.values(), key=lambda item: item.sha256)
                )
                self.manifest_store.save(updated)
        return report

    def _reconcile_one(
        self,
        candidate: DocumentCandidate,
        entries: dict[str, DocumentManifestEntry],
        report: ReconcileReport,
    ) -> None:
        source_url = str(candidate.url)
        existing_for_url = sorted(
            (
                item
                for item in entries.values()
                if any(str(source.url) == source_url for source in item.sources if source.url)
            ),
            key=lambda item: item.retrieved_at,
        )
        candidate_source = _candidate_source(candidate)
        if candidate.expected_sha256:
            known = next(
                (item for item in existing_for_url if item.sha256 == candidate.expected_sha256),
                None,
            )
            if known:
                merged_cases = _merge_associations(known.cases, candidate.cases)
                merged_sources = _merge_sources(known.sources, [candidate_source])
                if merged_cases != known.cases or merged_sources != known.sources:
                    entries[known.sha256] = known.model_copy(
                        update={"cases": merged_cases, "sources": merged_sources}
                    )
                    report.accepted_hashes.append(known.sha256)
                    _mark_cases(report, candidate.cases)
                else:
                    report.unchanged_hashes.append(known.sha256)
                return
        downloaded = self.downloader.download(source_url, self.temp_dir)
        try:
            if candidate.expected_sha256 and downloaded.sha256 != candidate.expected_sha256:
                raise ArchiveError("downloaded SHA-256 does not match source metadata")
            if downloaded.sha256 in entries:
                existing = entries[downloaded.sha256]
                merged_cases = _merge_associations(existing.cases, candidate.cases)
                merged_sources = _merge_sources(existing.sources, [candidate_source])
                if merged_cases != existing.cases or merged_sources != existing.sources:
                    entries[downloaded.sha256] = existing.model_copy(
                        update={"cases": merged_cases, "sources": merged_sources}
                    )
                    report.accepted_hashes.append(downloaded.sha256)
                    _mark_cases(report, candidate.cases)
                else:
                    report.unchanged_hashes.append(downloaded.sha256)
                return
            self.archive.admit(downloaded.path, downloaded.sha256)
            supersedes = existing_for_url[-1].sha256 if existing_for_url else None
            entry = DocumentManifestEntry(
                sha256=downloaded.sha256,
                archive_path=archive_relative_path(downloaded.sha256).as_posix(),
                byte_size=downloaded.byte_size,
                document_type=candidate.document_type,
                sources=[candidate_source],
                retrieved_at=downloaded.retrieved_at,
                cases=candidate.cases,
                supersedes=supersedes,
            )
            entries[entry.sha256] = entry
            report.accepted_hashes.append(entry.sha256)
            _mark_cases(report, candidate.cases)
        finally:
            downloaded.path.unlink(missing_ok=True)


def _candidate_source(candidate: DocumentCandidate) -> SourceIdentity:
    return SourceIdentity(
        url=candidate.url,
        source_id=candidate.source_id,
        page_url=candidate.page_url,
        official_filename=candidate.official_filename,
    )


def _merge_sources(
    current: list[SourceIdentity], incoming: list[SourceIdentity]
) -> list[SourceIdentity]:
    keyed: dict[str, SourceIdentity] = {}
    for source in [*current, *incoming]:
        key = json.dumps(source.model_dump(mode="json"), sort_keys=True)
        keyed[key] = source
    return [keyed[key] for key in sorted(keyed)]


def _merge_associations(
    current: list[CaseAssociation], incoming: list[CaseAssociation]
) -> list[CaseAssociation]:
    keyed: dict[str, CaseAssociation] = {}
    for association in [*current, *incoming]:
        key = json.dumps(association.model_dump(mode="json"), sort_keys=True)
        keyed[key] = association
    return [keyed[key] for key in sorted(keyed)]


def _mark_cases(report: ReconcileReport, associations: list[CaseAssociation]) -> None:
    report.changed_case_ids.update(
        association.case_id for association in associations if association.case_id
    )
