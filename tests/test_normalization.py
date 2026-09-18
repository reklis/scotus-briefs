from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from scotus_guide.discovery import DiscoveredCase
from scotus_guide.models import (
    CaseAssociation,
    DocumentManifest,
    DocumentManifestEntry,
    DocumentType,
    Lifecycle,
    SourceIdentity,
)
from scotus_guide.normalization import reconcile_normalized_cases

HASH = "d" * 64


def test_discovery_reconciles_canonical_case_and_document_reference(tmp_path: Path) -> None:
    discovered = DiscoveredCase(
        case_id="scotus-24-10",
        title="Citizen v. Example",
        term=2024,
        docket_numbers=["24-10"],
        primary_docket="24-10",
        lifecycle=Lifecycle.DECIDED,
        dates={"decision": date(2025, 1, 10)},
        source_url="https://www.supremecourt.gov/opinions/slipopinion/24",
    )
    manifest = DocumentManifest(
        documents=[
            DocumentManifestEntry(
                sha256=HASH,
                archive_path=f"documents/dd/{HASH}.pdf",
                byte_size=12,
                document_type=DocumentType.OPINION,
                sources=[SourceIdentity(url="https://www.supremecourt.gov/opinion.pdf")],
                retrieved_at=datetime.now(UTC),
                cases=[CaseAssociation(case_id=discovered.case_id, docket_numbers=["24-10"])],
            )
        ]
    )
    assert reconcile_normalized_cases(tmp_path, [discovered], manifest) == {discovered.case_id}
    path = tmp_path / "data" / "cases" / f"{discovered.case_id}.json"
    payload = path.read_text()
    assert HASH in payload
    # The observation timestamp alone does not make every nightly run a change.
    assert reconcile_normalized_cases(tmp_path, [discovered], manifest) == set()
