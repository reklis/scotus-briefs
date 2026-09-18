"""Archive integrity checks for manifests, immutable paths, and orphan blobs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from .archive import ManifestStore, archive_relative_path, hash_file


@dataclass(slots=True)
class IntegrityIssue:
    kind: str
    path: str
    detail: str


@dataclass(slots=True)
class IntegrityReport:
    checked: int = 0
    issues: list[IntegrityIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "checked": self.checked,
            "issues": [asdict(issue) for issue in self.issues],
        }


def check_archive(repository_root: Path, store: ManifestStore) -> IntegrityReport:
    report = IntegrityReport()
    manifest = store.load()
    referenced: set[Path] = set()
    for entry in manifest.documents:
        relative = archive_relative_path(entry.sha256)
        referenced.add(relative)
        path = repository_root / relative
        report.checked += 1
        if entry.archive_path != relative.as_posix():
            report.issues.append(
                IntegrityIssue("wrong_path", entry.archive_path, f"expected {relative.as_posix()}")
            )
            continue
        if not path.is_file():
            report.issues.append(IntegrityIssue("missing", relative.as_posix(), "blob is absent"))
            continue
        actual_hash, actual_size = hash_file(path)
        if actual_hash != entry.sha256:
            report.issues.append(
                IntegrityIssue("hash_mismatch", relative.as_posix(), f"actual {actual_hash}")
            )
        if actual_size != entry.byte_size:
            report.issues.append(
                IntegrityIssue(
                    "size_mismatch",
                    relative.as_posix(),
                    f"expected {entry.byte_size}, got {actual_size}",
                )
            )
    documents = repository_root / "documents"
    if documents.exists():
        for path in documents.rglob("*.pdf"):
            relative = path.relative_to(repository_root)
            if relative not in referenced:
                report.issues.append(
                    IntegrityIssue(
                        "unreferenced", relative.as_posix(), "blob has no manifest entry"
                    )
                )
    return report
