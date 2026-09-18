from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path

import jsonschema
import pytest
from pydantic import TypeAdapter, ValidationError
from scotus_guide.cli import _schema_with_invariants
from scotus_guide.models import (
    CANONICAL_MODELS,
    CaseAssociation,
    CaseDates,
    CitizenGuide,
    DocketNumber,
    DocumentManifest,
    DocumentManifestEntry,
    DocumentType,
    EvidenceKind,
    EvidenceRecord,
    EvidenceStatus,
    GenerationProvenance,
    GuideSection,
    Lifecycle,
    NormalizedCase,
    PageRange,
    SectionStatus,
    SourceIdentity,
    ValidationResult,
    ValidationState,
)

HASH = "a" * 64
NOW = datetime(2025, 1, 1, tzinfo=UTC)


def test_manifest_contract_enforces_content_path_and_unique_hash() -> None:
    entry = DocumentManifestEntry(
        sha256=HASH,
        archive_path=f"documents/aa/{HASH}.pdf",
        byte_size=42,
        document_type=DocumentType.OPINION,
        sources=[SourceIdentity(url="https://www.supremecourt.gov/opinions/example.pdf")],
        retrieved_at=NOW,
    )
    assert DocumentManifest(documents=[entry]).documents[0].byte_size == 42
    with pytest.raises(ValidationError, match="duplicate hashes"):
        DocumentManifest(documents=[entry, entry])
    with pytest.raises(ValidationError, match="archive_path"):
        entry.model_copy(update={"archive_path": "documents/wrong.pdf"}).model_validate(
            {**entry.model_dump(), "archive_path": "documents/wrong.pdf"}
        )


def test_docket_contract_accepts_canonical_original_jurisdiction_dockets() -> None:
    adapter = TypeAdapter(DocketNumber)
    assert adapter.validate_python("65O") == "65O"
    with pytest.raises(ValidationError):
        adapter.validate_python("No. 65, Orig.")


def test_case_association_requires_canonical_case_id_when_present() -> None:
    assert CaseAssociation(case_id="scotus-24-7").case_id == "scotus-24-7"
    assert CaseAssociation(historical_group="legacy").case_id is None
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        CaseAssociation(case_id="scotus-24A7")


def test_case_requires_real_or_unresolved_identity_and_decision_date() -> None:
    case = NormalizedCase(
        case_id="scotus-24-7",
        slug="scotus-24-7",
        title="Example v. Citizen",
        term=2024,
        docket_numbers=["24-7"],
        primary_docket="24-7",
        lifecycle=Lifecycle.DECIDED,
        dates=CaseDates(decision=date(2025, 1, 2)),
    )
    assert case.primary_docket == "24-7"
    with pytest.raises(ValidationError, match="decision date"):
        NormalizedCase(
            case_id="scotus-24-7",
            slug="scotus-24-7",
            title="Example",
            docket_numbers=["24-7"],
            lifecycle=Lifecycle.DECIDED,
        )


def test_evidence_page_ranges_and_confidence_are_bounded() -> None:
    evidence = EvidenceRecord(
        evidence_id="ev-1",
        case_id="scotus-24-7",
        document_hash=HASH,
        pages=PageRange(start=2, end=3),
        kind=EvidenceKind.PARTY_ARGUMENT,
        attribution="Petitioner",
        text="The petitioner argues that the rule is invalid.",
        confidence=0.8,
        status=EvidenceStatus.SUPPORTED,
    )
    assert evidence.pages.end == 3
    with pytest.raises(ValidationError):
        PageRange(start=3, end=2)


def test_guide_prevents_holding_for_pending_case() -> None:
    complete = GuideSection(
        status=SectionStatus.COMPLETE,
        heading="Overview",
        summary="Supported",
        summary_citations=[{"document_hash": HASH, "pages": {"start": 1, "end": 1}}],
    )
    pending = GuideSection(status=SectionStatus.PENDING, heading="Pending")
    generation = GenerationProvenance(
        source_hashes=[HASH],
        model_name="test",
        model_digest="digest",
        parameters={},
        prompt_version="1",
        schema_version="1",
        extractor_version="1",
        generated_at=NOW,
    )
    validation = ValidationResult(
        state=ValidationState.CANDIDATE,
        checked_at=NOW,
        checks={"schema": True},
    )
    payload = dict(
        case_id="scotus-24-7",
        lifecycle=Lifecycle.PENDING,
        overview=complete,
        background_and_question=complete,
        party_positions=[complete],
        oral_argument=pending,
        decision=complete,
        why_it_matters=complete,
        sources=[{"document_hash": HASH, "pages": {"start": 1, "end": 1}}],
        generation=generation,
        validation=validation,
    )
    with pytest.raises(ValidationError, match="undecided"):
        CitizenGuide(**payload)


def test_checked_json_schemas_accept_cross_language_fixtures() -> None:
    fixture_directory = Path("tests/fixtures/contracts")
    for name, model in CANONICAL_MODELS.items():
        payload = json.loads((fixture_directory / f"{name}.json").read_text())
        model.model_validate(payload)
        schema = json.loads((Path("schemas") / f"{name}.schema.json").read_text())
        expected_schema = _schema_with_invariants(
            name, model.model_json_schema(mode="serialization")
        )
        expected_schema["$id"] = f"https://scotusbriefs.us/schemas/{name}/1.0.0"
        assert schema == expected_schema
        jsonschema.Draft202012Validator(schema).validate(payload)
    assert "export interface CitizenGuide" in Path("schemas/contracts.ts").read_text()


def test_json_schemas_include_cross_field_invariants() -> None:
    manifest = json.loads(Path("tests/fixtures/contracts/document-manifest.json").read_text())
    manifest_schema = json.loads(Path("schemas/document-manifest.schema.json").read_text())
    invalid_source = copy.deepcopy(manifest)
    invalid_source["documents"][0]["sources"] = [{}]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(manifest_schema).validate(invalid_source)

    guide = json.loads(Path("tests/fixtures/contracts/citizen-guide.json").read_text())
    guide_schema = json.loads(Path("schemas/citizen-guide.schema.json").read_text())
    uncited = copy.deepcopy(guide)
    uncited["overview"]["summary_citations"] = []
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(guide_schema).validate(uncited)
    guide["decision"] = copy.deepcopy(guide["overview"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(guide_schema).validate(guide)
