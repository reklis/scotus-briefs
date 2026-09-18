from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scotus_guide.evidence import _atomic_json
from scotus_guide.extraction import ExtractedDocument, ExtractedPage, PageStatus
from scotus_guide.models import (
    CaseDocumentReference,
    Citation,
    CitizenGuide,
    DocumentManifest,
    DocumentManifestEntry,
    DocumentType,
    EvidenceKind,
    EvidenceRecord,
    EvidenceStatus,
    GenerationProvenance,
    GuideSection,
    Lifecycle,
    MaterialClaim,
    NormalizedCase,
    PageRange,
    SectionStatus,
    SourceIdentity,
    ValidationResult,
    ValidationState,
)
from scotus_guide.validation import (
    GuideValidator,
    ModelVerification,
    adversarial_verify,
    publish_candidate,
    verification_checks,
)

HASH = "b" * 64
NOW = datetime(2025, 1, 1, tzinfo=UTC)


class FakeOllama:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def generate_json(self, _prompt: str) -> Any:
        return self.payload


def fixtures(
    tmp_path: Path,
) -> tuple[NormalizedCase, DocumentManifest, EvidenceRecord, CitizenGuide]:
    case = NormalizedCase(
        case_id="scotus-24-7",
        slug="scotus-24-7",
        title="Example v. Citizen",
        term=2024,
        docket_numbers=["24-7"],
        primary_docket="24-7",
        lifecycle=Lifecycle.ARGUED,
        documents=[CaseDocumentReference(sha256=HASH, document_type=DocumentType.MERITS_BRIEF)],
    )
    manifest = DocumentManifest(
        documents=[
            DocumentManifestEntry(
                sha256=HASH,
                archive_path=f"documents/bb/{HASH}.pdf",
                byte_size=100,
                document_type=DocumentType.MERITS_BRIEF,
                sources=[SourceIdentity(url="https://www.supremecourt.gov/brief.pdf")],
                retrieved_at=NOW,
            )
        ]
    )
    evidence = EvidenceRecord(
        evidence_id="ev-1",
        case_id=case.case_id,
        document_hash=HASH,
        pages=PageRange(start=1, end=1),
        kind=EvidenceKind.PARTY_ARGUMENT,
        attribution="Petitioner",
        text="Petitioner argues that the rule is invalid.",
        confidence=0.9,
        status=EvidenceStatus.SUPPORTED,
    )
    extraction = ExtractedDocument(
        document_hash=HASH,
        document_type=DocumentType.MERITS_BRIEF,
        classification_confident=True,
        pages=[
            ExtractedPage(
                document_hash=HASH,
                page_number=1,
                text="Petitioner argues that the rule is invalid.",
                status=PageStatus.EMBEDDED,
                quality=0.9,
            )
        ],
    )
    _atomic_json(
        tmp_path / "data" / "evidence" / "extracted" / f"{HASH}.json",
        extraction.model_dump(mode="json"),
    )
    citation = Citation(document_hash=HASH, pages=PageRange(start=1, end=1), evidence_ids=["ev-1"])
    section = GuideSection(
        status=SectionStatus.COMPLETE,
        heading="Overview",
        summary="The petitioner challenges a rule.",
        summary_citations=[citation],
        claims=[
            MaterialClaim(
                text="The petitioner says the rule is invalid.",
                attribution="Petitioner",
                citations=[citation],
            )
        ],
    )
    pending = GuideSection(status=SectionStatus.PENDING, heading="Decision")
    guide = CitizenGuide(
        case_id=case.case_id,
        lifecycle=case.lifecycle,
        overview=section,
        background_and_question=section,
        party_positions=[section],
        oral_argument=GuideSection(status=SectionStatus.PENDING, heading="Oral argument"),
        decision=pending,
        why_it_matters=section,
        sources=[citation],
        generation=GenerationProvenance(
            source_hashes=[HASH],
            model_name="model",
            model_digest="digest",
            parameters={"temperature": 0},
            prompt_version="test",
            schema_version="1.0.0",
            extractor_version="1.0.0",
            generated_at=NOW,
        ),
        validation=ValidationResult(
            state=ValidationState.CANDIDATE,
            checked_at=NOW,
            checks={},
        ),
    )
    return case, manifest, evidence, guide


def good_model_result() -> ModelVerification:
    return ModelVerification(
        checks={
            "support": True,
            "attribution": True,
            "opinion_distinction": True,
            "oral_argument_characterization": True,
            "overstatement": True,
        },
        scores={
            "factual_accuracy": 0.95,
            "neutrality": 0.9,
            "readability": 0.8,
            "completeness": 0.8,
            "traceability": 1,
            "restraint": 0.95,
        },
    )


def test_deterministic_validation_checks_citations_attribution_and_lifecycle(
    tmp_path: Path,
) -> None:
    case, manifest, evidence, guide = fixtures(tmp_path)
    result = GuideValidator(tmp_path).deterministic(case, guide, [evidence], manifest)
    assert result.state == ValidationState.CANDIDATE
    assert all(result.checks.values())

    bad_claim = guide.overview.claims[0].model_copy(update={"attribution": None})
    bad_section = guide.overview.model_copy(update={"claims": [bad_claim]})
    invalid = guide.model_copy(update={"overview": bad_section})
    result = GuideValidator(tmp_path).deterministic(case, invalid, [evidence], manifest)
    assert not result.checks["attribution"]


def test_rejected_candidate_retains_prior_accepted_guide(tmp_path: Path) -> None:
    case, manifest, evidence, guide = fixtures(tmp_path)
    deterministic = GuideValidator(tmp_path).deterministic(case, guide, [evidence], manifest)
    accepted = publish_candidate(tmp_path, case, guide, deterministic, good_model_result())
    assert accepted is not None
    path = tmp_path / "data" / "guides" / f"{case.case_id}.json"
    previous = path.read_text()

    failed = good_model_result().model_copy(
        update={"checks": {**good_model_result().checks, "support": False}}
    )
    assert publish_candidate(tmp_path, case, guide, deterministic, failed) is None
    assert path.read_text() == previous
    report = json.loads((tmp_path / "reports" / "validation" / f"{case.case_id}.json").read_text())
    assert report["accepted"] is False


def test_adversarial_verification_enforces_quality_thresholds(tmp_path: Path) -> None:
    case, _manifest, evidence, guide = fixtures(tmp_path)
    payload = good_model_result().model_dump(mode="json")
    payload["scores"]["restraint"] = 0.5
    result = adversarial_verify(FakeOllama(payload), case, guide, [evidence])  # type: ignore[arg-type]
    assert verification_checks(result)["quality_restraint"] is False
