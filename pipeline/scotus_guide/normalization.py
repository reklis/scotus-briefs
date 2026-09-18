"""Reconcile discovery snapshots and archive associations into canonical case records."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .discovery import DiscoveredCase
from .models import (
    CaseDates,
    CaseDocumentReference,
    DocumentManifest,
    Lifecycle,
    MetadataProvenance,
    NormalizedCase,
)

_LIFECYCLE_ORDER = {
    Lifecycle.UNRESOLVED: 0,
    Lifecycle.PENDING: 1,
    Lifecycle.SCHEDULED: 2,
    Lifecycle.ARGUED: 3,
    Lifecycle.AWAITING_DECISION: 4,
    Lifecycle.DECIDED: 5,
    Lifecycle.DISMISSED: 5,
}


def reconcile_normalized_cases(
    root: Path,
    discovered: list[DiscoveredCase],
    manifest: DocumentManifest,
) -> set[str]:
    """Atomically update discovered fields without deleting prior supported metadata."""
    changed: set[str] = set()
    superseded = {entry.supersedes for entry in manifest.documents if entry.supersedes}
    references: dict[str, dict[str, CaseDocumentReference]] = {}
    for entry in manifest.documents:
        for association in entry.cases:
            if not association.case_id:
                continue
            references.setdefault(association.case_id, {})[entry.sha256] = CaseDocumentReference(
                sha256=entry.sha256,
                document_type=entry.document_type,
                current=entry.sha256 not in superseded,
            )
    for item in discovered:
        destination = root / "data" / "cases" / f"{item.case_id}.json"
        prior = (
            NormalizedCase.model_validate_json(destination.read_text())
            if destination.exists()
            else None
        )
        dates = CaseDates(
            **{
                field: value
                for field, value in item.dates.items()
                if field in {"filed", "granted", "argument", "decision"}
            }
        )
        lifecycle = item.lifecycle
        if lifecycle == Lifecycle.DECIDED and dates.decision is None:
            lifecycle = Lifecycle.AWAITING_DECISION
        provenance = MetadataProvenance(
            field="discovery",
            source_url=item.source_url,
            observed_at=datetime.now(UTC),
            method="official",
        )
        if prior:
            merged_dates = prior.dates.model_copy(
                update={
                    field: value for field, value in dates.model_dump().items() if value is not None
                }
            )
            if lifecycle == Lifecycle.DECIDED and merged_dates.decision is None:
                lifecycle = Lifecycle.AWAITING_DECISION
            lifecycle = max(
                (prior.lifecycle, lifecycle),
                key=lambda value: _LIFECYCLE_ORDER[value],
            )
            documents = {entry.sha256: entry for entry in prior.documents}
            documents.update(references.get(item.case_id, {}))
            provenance_items = [
                entry
                for entry in prior.provenance
                if not (entry.field == "discovery" and entry.source_url == item.source_url)
            ]
            case = prior.model_copy(
                update={
                    "title": item.title,
                    "term": item.term,
                    "docket_numbers": item.docket_numbers,
                    "primary_docket": item.primary_docket,
                    "lifecycle": lifecycle,
                    "dates": merged_dates,
                    "documents": sorted(documents.values(), key=lambda entry: entry.sha256),
                    "provenance": [*provenance_items, provenance],
                }
            )
        else:
            case = NormalizedCase(
                case_id=item.case_id,
                slug=item.case_id,
                title=item.title,
                term=item.term,
                docket_numbers=item.docket_numbers,
                primary_docket=item.primary_docket,
                lifecycle=lifecycle,
                dates=dates,
                provenance=[provenance],
                documents=sorted(
                    references.get(item.case_id, {}).values(), key=lambda entry: entry.sha256
                ),
            )
        if prior is None or _stable_dump(prior) != _stable_dump(case):
            _atomic_case(destination, case)
            changed.add(case.case_id)
    return changed


def _stable_dump(case: NormalizedCase) -> dict[str, object]:
    payload = case.model_dump(mode="json")
    for provenance in payload.get("provenance", []):
        if isinstance(provenance, dict) and provenance.get("field") == "discovery":
            provenance["observed_at"] = None
    return payload


def _atomic_case(path: Path, case: NormalizedCase) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(case.model_dump_json(indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
