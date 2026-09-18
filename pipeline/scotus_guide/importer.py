"""Non-destructive importer for the historical JSONL/PDF corpus."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .archive import (
    ArchiveError,
    ContentAddressedArchive,
    ManifestStore,
    archive_relative_path,
    validate_pdf_file,
)
from .models import (
    CaseAssociation,
    CaseDocumentReference,
    DocumentManifest,
    DocumentManifestEntry,
    DocumentType,
    Lifecycle,
    NormalizedCase,
    SourceIdentity,
)


@dataclass(slots=True)
class ImportProblem:
    line: int
    path: str | None
    error: str


@dataclass(slots=True)
class ImportReport:
    imported_hashes: list[str] = field(default_factory=list)
    unchanged_hashes: list[str] = field(default_factory=list)
    unresolved_case_ids: list[str] = field(default_factory=list)
    problems: list[ImportProblem] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "imported_hashes": self.imported_hashes,
            "unchanged_hashes": self.unchanged_hashes,
            "unresolved_case_ids": self.unresolved_case_ids,
            "problems": [asdict(item) for item in self.problems],
        }


_HASH_KEYS = ("sha256", "hash", "checksum")
_PATH_KEYS = ("path", "file", "filename", "key", "relative_path")
_GROUP_KEYS = ("group", "group_id", "case_group", "uuid")


def import_corpus(
    jsonl_path: Path,
    corpus_root: Path,
    repository_root: Path,
    manifest_store: ManifestStore,
) -> ImportReport:
    """Verify every source before admitting it; malformed lines are reported and skipped."""
    report = ImportReport()
    archive = ContentAddressedArchive(repository_root)
    groups: dict[str, list[CaseDocumentReference]] = {}
    with manifest_store.locked():
        manifest = manifest_store.load()
        entries = {item.sha256: item for item in manifest.documents}
        manifest_changed = False
        with jsonl_path.open(encoding="utf-8") as stream:
            for line_number, raw_line in enumerate(stream, 1):
                source_path: Path | None = None
                try:
                    row = json.loads(raw_line)
                    if not isinstance(row, dict):
                        raise ValueError("JSONL row must be an object")
                    relative = _first_string(row, _PATH_KEYS)
                    expected_hash = _first_string(row, _HASH_KEYS).removeprefix("sha256:").lower()
                    source_path = _safe_source_path(corpus_root, relative)
                    actual_hash, size = validate_pdf_file(source_path)
                    if expected_hash != actual_hash:
                        raise ArchiveError(
                            f"checksum mismatch: expected {expected_hash}, got {actual_hash}"
                        )
                    expected_size = row.get("size", row.get("byte_size"))
                    if expected_size is not None and int(expected_size) != size:
                        raise ArchiveError(f"size mismatch: expected {expected_size}, got {size}")
                    document_type = _document_type(row.get("document_type", row.get("type")))
                    group = _optional_string(row, _GROUP_KEYS) or _group_from_path(relative)
                    association = CaseAssociation(historical_group=group)
                    imported_source = SourceIdentity(
                        url=row.get("source_url"),
                        source_id=str(row.get("source_id", group)),
                        official_filename=source_path.name,
                        import_path=relative,
                    )
                    archive.admit(source_path, actual_hash)
                    if actual_hash not in entries:
                        entries[actual_hash] = DocumentManifestEntry(
                            sha256=actual_hash,
                            archive_path=archive_relative_path(actual_hash).as_posix(),
                            byte_size=size,
                            document_type=document_type,
                            sources=[imported_source],
                            retrieved_at=datetime.now(UTC),
                            cases=[association],
                        )
                        report.imported_hashes.append(actual_hash)
                        manifest_changed = True
                    else:
                        existing = entries[actual_hash]
                        cases = list(existing.cases)
                        sources = list(existing.sources)
                        if association not in cases:
                            cases.append(association)
                        if imported_source not in sources:
                            sources.append(imported_source)
                        if cases != existing.cases or sources != existing.sources:
                            entries[actual_hash] = existing.model_copy(
                                update={"cases": cases, "sources": sources}
                            )
                            manifest_changed = True
                        report.unchanged_hashes.append(actual_hash)
                    groups.setdefault(group, []).append(
                        CaseDocumentReference(sha256=actual_hash, document_type=document_type)
                    )
                except (ArchiveError, OSError, ValueError, TypeError) as error:
                    report.problems.append(
                        ImportProblem(
                            line=line_number,
                            path=str(source_path) if source_path else None,
                            error=str(error),
                        )
                    )
        if manifest_changed:
            manifest_store.save(
                DocumentManifest(documents=sorted(entries.values(), key=lambda item: item.sha256))
            )
    for group, references in sorted(groups.items()):
        case = _write_unresolved_case(repository_root, group, references)
        report.unresolved_case_ids.append(case.case_id)
    return report


def _first_string(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    result = _optional_string(row, keys)
    if result is None:
        raise ValueError(f"missing one of required fields: {', '.join(keys)}")
    return result


def _optional_string(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _safe_source_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"import path escapes corpus root: {relative}") from error
    return candidate


def _group_from_path(relative: str) -> str:
    parent = Path(relative).parent.name
    return parent if parent and parent != "." else "ungrouped"


def _document_type(value: object) -> DocumentType:
    if value is None:
        return DocumentType.UNKNOWN
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "transcript": DocumentType.TRANSCRIPT,
        "oral_argument": DocumentType.TRANSCRIPT,
        "brief": DocumentType.MERITS_BRIEF,
    }
    try:
        return aliases.get(normalized, DocumentType(normalized))
    except ValueError:
        return DocumentType.UNKNOWN


def _write_unresolved_case(
    repository_root: Path, group: str, references: list[CaseDocumentReference]
) -> NormalizedCase:
    digest = hashlib.sha256(group.encode()).hexdigest()[:16]
    case_id = f"unresolved-{digest}"
    unique = {item.sha256: item for item in references}
    destination = repository_root / "data" / "cases" / f"{case_id}.json"
    if destination.exists():
        existing_case = NormalizedCase.model_validate_json(destination.read_text())
        unique.update({item.sha256: item for item in existing_case.documents})
        case = existing_case.model_copy(
            update={"documents": sorted(unique.values(), key=lambda item: item.sha256)}
        )
    else:
        case = NormalizedCase(
            case_id=case_id,
            slug=case_id,
            title=f"Unresolved historical group {group}",
            lifecycle=Lifecycle.UNRESOLVED,
            unresolved_group=group,
            documents=sorted(unique.values(), key=lambda item: item.sha256),
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(case.model_dump_json(indent=2) + "\n")
    temporary.replace(destination)
    return case
