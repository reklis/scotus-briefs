from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from scotus_guide.evidence import EvidenceJobStatus, JobState, _atomic_json
from scotus_guide.extraction import ExtractedDocument, ExtractedPage, PageStatus
from scotus_guide.generation import GuideGenerator, _drop_invalid_cited_content
from scotus_guide.models import (
    CaseDocumentReference,
    DocumentManifest,
    DocumentManifestEntry,
    DocumentType,
    EvidenceKind,
    EvidenceRecord,
    EvidenceStatus,
    Lifecycle,
    NormalizedCase,
    PageRange,
    SourceIdentity,
)
from scotus_guide.ollama import ModelIdentity

HASH = "c" * 64
NOW = datetime(2025, 1, 1, tzinfo=UTC)


class FakeOllama:
    parameters: ClassVar[dict[str, object]] = {"temperature": 0, "num_ctx": 32768}

    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def generate_json(self, prompt: str, *, schema: dict[str, Any] | None = None) -> Any:
        self.prompts.append(prompt)
        return self.responses.pop(0)


def test_invalid_model_citations_are_removed_before_validation() -> None:
    evidence = EvidenceRecord(
        evidence_id="ev-1",
        case_id="scotus-24-7",
        document_hash=HASH,
        pages=PageRange(start=2, end=2),
        kind=EvidenceKind.FACT,
        attribution="Court",
        text="Supported text.",
        confidence=1,
        status=EvidenceStatus.SUPPORTED,
    )
    valid = {
        "document_hash": HASH,
        "pages": {"start": 2, "end": 2},
        "evidence_ids": ["ev-1"],
    }
    invalid = {
        "document_hash": HASH,
        "pages": {"start": 1, "end": 1},
        "evidence_ids": ["missing"],
    }
    raw: dict[str, Any] = {
        "overview": {
            "summary": "Unsupported summary.",
            "summary_citations": [invalid],
            "claims": [
                {"text": "Supported.", "citations": [valid]},
                {"text": "Unsupported.", "citations": [invalid]},
                {"text": "Decided April 29, 2025.", "citations": [valid]},
            ],
        },
        "why_it_matters": {
            "status": "complete",
            "claims": [
                {
                    "text": "The ruling preserves the separation of powers.",
                    "attribution": "Court",
                    "citations": [valid],
                }
            ],
        },
        "party_positions": [
            {
                "status": "complete",
                "claims": [
                    {"text": "A procedural fact is not a party argument.", "citations": [valid]}
                ],
            }
        ],
        "glossary": [
            {"term": "Valid", "definition": "Supported.", "citations": [valid]},
            {"term": "Invalid", "definition": "Unsupported.", "citations": [invalid]},
        ],
    }

    _drop_invalid_cited_content(raw, [evidence])

    assert [claim["text"] for claim in raw["overview"]["claims"]] == ["Supported."]
    assert raw["overview"]["summary"] is None
    assert raw["party_positions"][0]["claims"] == []
    assert raw["party_positions"][0]["status"] == "source_limited"
    assert raw["why_it_matters"]["claims"] == []
    assert raw["why_it_matters"]["status"] == "source_limited"
    assert [entry["term"] for entry in raw["glossary"]] == ["Valid"]


def test_case_synthesis_uses_evidence_forces_pending_decision_and_accepts(tmp_path: Path) -> None:
    case = NormalizedCase(
        case_id="scotus-24-9",
        slug="scotus-24-9",
        title="Pending Example",
        term=2024,
        docket_numbers=["24-9"],
        primary_docket="24-9",
        lifecycle=Lifecycle.ARGUED,
        documents=[CaseDocumentReference(sha256=HASH, document_type=DocumentType.MERITS_BRIEF)],
    )
    evidence = EvidenceRecord(
        evidence_id="ev-1",
        case_id=case.case_id,
        document_hash=HASH,
        pages=PageRange(start=1, end=1),
        kind=EvidenceKind.PARTY_ARGUMENT,
        attribution="Petitioner",
        text="Petitioner asks the Court to invalidate the rule.",
        confidence=0.9,
        status=EvidenceStatus.SUPPORTED,
    )
    _atomic_json(
        tmp_path / "data" / "evidence" / case.case_id / f"{HASH}.json",
        {"records": [evidence.model_dump(mode="json")]},
    )
    _atomic_json(
        tmp_path / "data" / "evidence" / "status" / case.case_id / f"{HASH}.json",
        EvidenceJobStatus(
            document_hash=HASH,
            case_id=case.case_id,
            state=JobState.COMPLETED,
            completed_chunks=1,
            total_chunks=1,
            updated_at=NOW,
            model_name="ragchew-gpt-oss:120b-32k",
            model_digest="sha256:model",
            parameters={"temperature": 0, "num_ctx": 32768},
        ).model_dump(mode="json"),
    )
    _atomic_json(
        tmp_path / "data" / "evidence" / "extracted" / f"{HASH}.json",
        ExtractedDocument(
            document_hash=HASH,
            document_type=DocumentType.MERITS_BRIEF,
            classification_confident=True,
            pages=[
                ExtractedPage(
                    document_hash=HASH,
                    page_number=1,
                    text=evidence.text,
                    status=PageStatus.EMBEDDED,
                    quality=0.9,
                )
            ],
        ).model_dump(mode="json"),
    )
    citation = {"document_hash": HASH, "pages": {"start": 1, "end": 1}, "evidence_ids": ["ev-1"]}
    complete = {
        "status": "complete",
        "heading": "Overview",
        "summary": "The petitioner challenges a rule.",
        "summary_citations": [citation],
        "claims": [
            {
                "text": "The petitioner asks the Court to invalidate the rule.",
                "attribution": "Petitioner",
                "citations": [citation],
            }
        ],
    }
    pending = {"status": "pending", "heading": "Pending", "summary_citations": [], "claims": []}
    raw_guide = {
        "schema_version": "1.0.0",
        "case_id": "model-may-not-change-this",
        "lifecycle": "decided",
        "overview": complete,
        "background_and_question": complete,
        "party_positions": [complete],
        "oral_argument": pending,
        # The generator must replace this unsupported predicted decision.
        "decision": complete,
        "why_it_matters": complete,
        "glossary": [],
        "sources": [citation],
        "generation": {},
        "validation": {},
    }
    verification = {
        "checks": {
            "support": True,
            "attribution": True,
            "opinion_distinction": True,
            "oral_argument_characterization": True,
            "overstatement": True,
        },
        "scores": {
            "factual_accuracy": 1,
            "neutrality": 1,
            "readability": 1,
            "completeness": 1,
            "traceability": 1,
            "restraint": 1,
        },
        "messages": [],
    }
    model = FakeOllama([raw_guide, verification])
    manifest = DocumentManifest(
        documents=[
            DocumentManifestEntry(
                sha256=HASH,
                archive_path=f"documents/cc/{HASH}.pdf",
                byte_size=10,
                document_type=DocumentType.MERITS_BRIEF,
                sources=[SourceIdentity(url="https://www.supremecourt.gov/brief.pdf")],
                retrieved_at=NOW,
            )
        ]
    )
    result = GuideGenerator(
        tmp_path,
        model,  # type: ignore[arg-type]
        ModelIdentity("ragchew-gpt-oss:120b-32k", "sha256:model"),
    ).generate_case(case, manifest)
    assert result is not None
    assert result.decision.status == "pending"
    assert result.validation.state == "accepted"
    assert result.generation.source_hashes == [HASH]
    assert "UNTRUSTED_EVIDENCE" in model.prompts[0]
