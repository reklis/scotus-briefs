from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from scotus_guide.evidence import EvidenceGenerationError, EvidenceGenerator, JobState
from scotus_guide.extraction import PdfTextExtractor
from scotus_guide.models import (
    CaseAssociation,
    CaseDocumentReference,
    DocumentManifestEntry,
    DocumentType,
    Lifecycle,
    NormalizedCase,
    SourceIdentity,
)


class FakeOllama:
    parameters: ClassVar[dict[str, object]] = {}

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls = 0

    def generate_json(self, _prompt: str, *, schema: dict[str, Any] | None = None) -> Any:
        self.calls += 1
        return self.payload


def setup_source(tmp_path: Path) -> tuple[NormalizedCase, DocumentManifestEntry, str]:
    content = b"%PDF-1.7 evidence fixture"
    digest = hashlib.sha256(content).hexdigest()
    archive = tmp_path / "documents" / digest[:2] / f"{digest}.pdf"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(content)
    case = NormalizedCase(
        case_id="scotus-24-7",
        slug="scotus-24-7",
        title="Example v. Citizen",
        term=2024,
        docket_numbers=["24-7"],
        primary_docket="24-7",
        lifecycle=Lifecycle.ARGUED,
        documents=[CaseDocumentReference(sha256=digest, document_type=DocumentType.TRANSCRIPT)],
    )
    entry = DocumentManifestEntry(
        sha256=digest,
        archive_path=f"documents/{digest[:2]}/{digest}.pdf",
        byte_size=len(content),
        document_type=DocumentType.TRANSCRIPT,
        sources=[SourceIdentity(url="https://www.supremecourt.gov/example.pdf")],
        retrieved_at=datetime.now(UTC),
        cases=[CaseAssociation(case_id=case.case_id, docket_numbers=["24-7"])],
    )
    return case, entry, digest


def test_evidence_job_is_resumable_and_records_unavailable_pages(tmp_path: Path) -> None:
    case, entry, digest = setup_source(tmp_path)
    payload = {
        "records": [
            {
                "evidence_id": "model-controlled",
                "case_id": case.case_id,
                "document_hash": digest,
                "pages": {"start": 1, "end": 1},
                "kind": "party_argument",
                "attribution": "Petitioner",
                "text": "Petitioner argues the rule is invalid and gives supporting detail.",
                "confidence": 0.9,
                "status": "supported",
                "opinion_part": None,
            }
        ]
    }
    model = FakeOllama(payload)
    extractor = PdfTextExtractor(
        minimum_characters=20,
        page_reader=lambda _path: [
            "Petitioner argues the rule is invalid and gives supporting detail.",
            None,
        ],
    )
    generator = EvidenceGenerator(tmp_path, extractor, model)  # type: ignore[arg-type]
    records = generator.generate_document(case, entry)
    assert records[0].evidence_id.startswith(f"ev-{digest[:12]}")
    assert records[0].attribution == "Petitioner"
    assert records[1].status == "unavailable"
    assert records[1].pages.start == 2
    assert generator.generate_document(case, entry) == records
    assert model.calls == 1
    status = json.loads(
        (tmp_path / "data" / "evidence" / "status" / case.case_id / f"{digest}.json").read_text()
    )
    assert status["state"] == JobState.COMPLETED


def test_malformed_evidence_output_writes_failure_report(tmp_path: Path) -> None:
    case, entry, digest = setup_source(tmp_path)
    model = FakeOllama({"wrong": []})
    extractor = PdfTextExtractor(
        minimum_characters=1,
        page_reader=lambda _path: ["usable alphabetic source text"],
    )
    with pytest.raises(EvidenceGenerationError, match="records array"):
        EvidenceGenerator(tmp_path, extractor, model).generate_document(  # type: ignore[arg-type]
            case, entry
        )
    report = json.loads(
        (tmp_path / "reports" / "extraction" / case.case_id / f"{digest}.json").read_text()
    )
    assert report["state"] == "failed"


def test_transcript_speaker_controls_justice_question_attribution(tmp_path: Path) -> None:
    case, entry, digest = setup_source(tmp_path)
    text = "JUSTICE ALITO: Is this rule jurisdictional?"
    model = FakeOllama(
        {
            "records": [
                {
                    "evidence_id": "model-id",
                    "case_id": case.case_id,
                    "document_hash": digest,
                    "pages": {"start": 1, "end": 1},
                    "kind": "procedural_event",
                    "attribution": "Court",
                    "text": text,
                    "confidence": 1,
                    "status": "supported",
                    "opinion_part": None,
                }
            ]
        }
    )
    records = EvidenceGenerator(
        tmp_path,
        PdfTextExtractor(minimum_characters=1, page_reader=lambda _path: [text]),
        model,  # type: ignore[arg-type]
    ).generate_document(case, entry)

    assert records[0].kind == "justice_question"
    assert records[0].attribution == "Justice Alito"


def test_case_document_type_and_disposition_control_holding_classification(
    tmp_path: Path,
) -> None:
    case, entry, digest = setup_source(tmp_path)
    case = case.model_copy(
        update={
            "documents": [
                CaseDocumentReference(sha256=digest, document_type=DocumentType.OPINION)
            ]
        }
    )
    entry = entry.model_copy(update={"document_type": DocumentType.UNKNOWN})
    text = (
        "Page Proof Pending Publication\n"
        "Justice Alpha delivered the opinion of the Court.\n"
        "The judgment of the Court of Appeals is reversed, and the case is remanded."
    )
    disposition = (
        "The judgment of the Court of Appeals is reversed, and the case is remanded."
    )
    model = FakeOllama(
        {
            "records": [
                {
                    "evidence_id": "model-id",
                    "case_id": case.case_id,
                    "document_hash": digest,
                    "pages": {"start": 1, "end": 1},
                    "kind": "procedural_event",
                    "attribution": "Court",
                    "text": disposition,
                    "confidence": 1,
                    "status": "supported",
                    "opinion_part": None,
                }
            ]
        }
    )
    records = EvidenceGenerator(
        tmp_path,
        PdfTextExtractor(minimum_characters=1, page_reader=lambda _path: [text]),
        model,  # type: ignore[arg-type]
    ).generate_document(case, entry)

    assert records[0].kind == "holding"
    assert records[0].attribution == "Court"
    assert records[0].opinion_part == "majority"


def test_evidence_accepts_layout_hyphenation_and_rejects_paraphrases(tmp_path: Path) -> None:
    case, entry, digest = setup_source(tmp_path)
    model = FakeOllama(
        {
            "records": [
                {
                    "evidence_id": "exact",
                    "case_id": case.case_id,
                    "document_hash": digest,
                    "pages": {"start": 1, "end": 1},
                    "kind": "fact",
                    "attribution": "Source",
                    "text": "The order concerns election integrity.",
                    "confidence": 1,
                    "status": "supported",
                    "opinion_part": None,
                },
                {
                    "evidence_id": "paraphrase",
                    "case_id": case.case_id,
                    "document_hash": digest,
                    "pages": {"start": 1, "end": 1},
                    "kind": "fact",
                    "attribution": "Source",
                    "text": "The President changed election rules.",
                    "confidence": 1,
                    "status": "supported",
                    "opinion_part": None,
                },
            ]
        }
    )
    extractor = PdfTextExtractor(
        minimum_characters=1,
        page_reader=lambda _path: ["The order concerns election integ-\nrity."],
    )

    records = EvidenceGenerator(tmp_path, extractor, model).generate_document(  # type: ignore[arg-type]
        case, entry
    )

    assert [record.text for record in records] == ["The order concerns election integrity."]


def test_shared_document_keeps_case_scoped_evidence(tmp_path: Path) -> None:
    case, entry, digest = setup_source(tmp_path)
    extractor = PdfTextExtractor(
        minimum_characters=1,
        page_reader=lambda _path: ["A party provides an exact source quotation."],
    )

    def payload(case_id: str) -> dict[str, object]:
        return {
            "records": [
                {
                    "evidence_id": "model-id",
                    "case_id": case_id,
                    "document_hash": digest,
                    "pages": {"start": 1, "end": 1},
                    "kind": "fact",
                    "attribution": "Source",
                    "text": "A party provides an exact source quotation.",
                    "confidence": 0.9,
                    "status": "supported",
                    "opinion_part": None,
                }
            ]
        }

    EvidenceGenerator(tmp_path, extractor, FakeOllama(payload(case.case_id))).generate_document(
        case, entry
    )  # type: ignore[arg-type]
    second = case.model_copy(update={"case_id": "scotus-24-8", "slug": "scotus-24-8"})
    records = EvidenceGenerator(
        tmp_path, extractor, FakeOllama(payload(second.case_id))
    ).generate_document(second, entry)  # type: ignore[arg-type]
    assert records[0].case_id == second.case_id
    assert (tmp_path / "data" / "evidence" / case.case_id / f"{digest}.json").exists()
    assert (tmp_path / "data" / "evidence" / second.case_id / f"{digest}.json").exists()


def test_interrupted_document_resumes_after_completed_chunk(tmp_path: Path) -> None:
    case, entry, digest = setup_source(tmp_path)
    page_text = {
        1: "Alpha source quotation " * 8,
        2: "Beta source quotation " * 8,
    }

    class InterruptOnce:
        model = "test-model"
        parameters: ClassVar[dict[str, object]] = {}

        def __init__(self) -> None:
            self.calls: list[int] = []
            self.interrupted = False

        def generate_json(
            self, prompt: str, *, schema: dict[str, Any] | None = None
        ) -> dict[str, object]:
            page = 1 if "[SOURCE PAGE 1]" in prompt else 2
            self.calls.append(page)
            if page == 2 and not self.interrupted:
                self.interrupted = True
                raise RuntimeError("interrupted")
            text = page_text[page].strip()
            return {
                "records": [
                    {
                        "evidence_id": "model-id",
                        "case_id": case.case_id,
                        "document_hash": digest,
                        "pages": {"start": page, "end": page},
                        "kind": "fact",
                        "attribution": "Source",
                        "text": text,
                        "confidence": 1,
                        "status": "supported",
                        "opinion_part": None,
                    }
                ]
            }

    model = InterruptOnce()
    generator = EvidenceGenerator(
        tmp_path,
        PdfTextExtractor(
            minimum_characters=1,
            page_reader=lambda _path: [page_text[1], page_text[2]],
        ),
        model,  # type: ignore[arg-type]
        context_tokens=100,
        reserved_tokens=40,
    )
    with pytest.raises(EvidenceGenerationError, match="interrupted"):
        generator.generate_document(case, entry)
    records = generator.generate_document(case, entry)
    assert model.calls == [1, 2, 2]
    assert len(records) == 2
    assert len({record.evidence_id for record in records}) == 2
