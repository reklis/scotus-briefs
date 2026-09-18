"""Resumable extraction and schema-constrained evidence generation."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .chunking import chunk_pages
from .extraction import (
    EXTRACTOR_VERSION,
    ExtractedDocument,
    OpinionPart,
    PageStatus,
    PdfTextExtractor,
    normalize_text,
)
from .models import (
    DocumentManifestEntry,
    EvidenceKind,
    EvidenceRecord,
    EvidenceStatus,
    NormalizedCase,
    PageRange,
)
from .ollama import OllamaClient, OllamaResponseError
from .prompts import EVIDENCE_PROMPT_VERSION, evidence_prompt


class JobState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EvidenceJobStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_id: str
    state: JobState
    completed_chunks: int = Field(ge=0)
    total_chunks: int = Field(ge=0)
    updated_at: datetime
    extractor_version: str = EXTRACTOR_VERSION
    prompt_version: str = EVIDENCE_PROMPT_VERSION
    model_name: str
    model_digest: str
    parameters: dict[str, Any]
    error: str | None = None


class EvidenceGenerationError(RuntimeError):
    pass


class EvidenceGenerator:
    def __init__(
        self,
        root: Path,
        extractor: PdfTextExtractor,
        ollama: OllamaClient,
        *,
        context_tokens: int = 32_768,
        reserved_tokens: int = 8_192,
        model_digest: str = "unknown",
    ) -> None:
        self.root = root
        self.extractor = extractor
        self.ollama = ollama
        self.context_tokens = context_tokens
        self.reserved_tokens = reserved_tokens
        self.model_digest = model_digest

    def generate_document(
        self,
        case: NormalizedCase,
        manifest: DocumentManifestEntry,
        *,
        force: bool = False,
    ) -> list[EvidenceRecord]:
        case_directory = self.root / "data" / "evidence" / case.case_id
        records_path = case_directory / f"{manifest.sha256}.json"
        extraction_path = self.root / "data" / "evidence" / "extracted" / f"{manifest.sha256}.json"
        status_path = (
            self.root / "data" / "evidence" / "status" / case.case_id / f"{manifest.sha256}.json"
        )
        model_name = str(getattr(self.ollama, "model", "test-model"))
        parameters = dict(getattr(self.ollama, "parameters", {}))
        prior = self._status(status_path)
        compatible = prior is not None and (
            prior.extractor_version == EXTRACTOR_VERSION
            and prior.prompt_version == EVIDENCE_PROMPT_VERSION
            and prior.model_name == model_name
            and prior.model_digest == self.model_digest
            and prior.parameters == parameters
        )
        if (
            not force
            and compatible
            and prior is not None
            and prior.state == JobState.COMPLETED
            and records_path.exists()
        ):
            return _read_records(records_path)
        try:
            extracted = self.extractor.extract(
                self.root / manifest.archive_path,
                manifest.sha256,
                manifest.document_type,
            )
            _atomic_json(extraction_path, extracted.model_dump(mode="json"))
            chunks = chunk_pages(
                extracted.pages,
                context_tokens=self.context_tokens,
                reserved_tokens=self.reserved_tokens,
            )
            resume_at = (
                prior.completed_chunks
                if not force and compatible and prior is not None and records_path.exists()
                else 0
            )
            records = (
                _read_records(records_path)
                if resume_at
                else _unavailable_records(case.case_id, extracted)
            )
            status = EvidenceJobStatus(
                document_hash=manifest.sha256,
                case_id=case.case_id,
                state=JobState.RUNNING,
                completed_chunks=resume_at,
                total_chunks=len(chunks),
                updated_at=datetime.now(UTC),
                model_name=model_name,
                model_digest=self.model_digest,
                parameters=parameters,
            )
            _atomic_json(status_path, status.model_dump(mode="json"))
            for chunk in chunks[resume_at:]:
                payload = self.ollama.generate_json(
                    evidence_prompt(case.case_id, manifest.sha256, chunk),
                    schema=_evidence_response_schema(),
                )
                chunk_records = _parse_evidence_payload(
                    payload,
                    case,
                    extracted,
                    chunk.start_page,
                    chunk.end_page,
                    chunk.index,
                )
                by_id = {item.evidence_id: item for item in records}
                by_id.update({item.evidence_id: item for item in chunk_records})
                records = list(by_id.values())
                status = status.model_copy(
                    update={
                        "completed_chunks": chunk.index + 1,
                        "updated_at": datetime.now(UTC),
                    }
                )
                records.sort(key=lambda item: (item.pages.start, item.pages.end, item.evidence_id))
                _write_records(records_path, case.case_id, manifest.sha256, records)
                _atomic_json(status_path, status.model_dump(mode="json"))
            _write_records(records_path, case.case_id, manifest.sha256, records)
            completed = status.model_copy(
                update={"state": JobState.COMPLETED, "updated_at": datetime.now(UTC)}
            )
            _atomic_json(status_path, completed.model_dump(mode="json"))
            return records
        except Exception as error:  # Every per-document failure must leave durable status.
            failed = EvidenceJobStatus(
                document_hash=manifest.sha256,
                case_id=case.case_id,
                state=JobState.FAILED,
                completed_chunks=_completed_chunks(status_path),
                total_chunks=_total_chunks(status_path),
                updated_at=datetime.now(UTC),
                model_name=str(getattr(self.ollama, "model", "test-model")),
                model_digest=self.model_digest,
                parameters=dict(getattr(self.ollama, "parameters", {})),
                error=str(error),
            )
            _atomic_json(status_path, failed.model_dump(mode="json"))
            _atomic_json(
                self.root / "reports" / "extraction" / case.case_id / f"{manifest.sha256}.json",
                failed.model_dump(mode="json"),
            )
            raise EvidenceGenerationError(str(error)) from error

    @staticmethod
    def _status(path: Path) -> EvidenceJobStatus | None:
        if not path.exists():
            return None
        try:
            return EvidenceJobStatus.model_validate_json(path.read_text())
        except (OSError, ValidationError):
            return None


def _parse_evidence_payload(
    payload: Any,
    case: NormalizedCase,
    extracted: ExtractedDocument,
    start_page: int,
    end_page: int,
    chunk_index: int,
) -> list[EvidenceRecord]:
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise OllamaResponseError("evidence output must contain a records array")
    pages = {page.page_number: page for page in extracted.pages}
    records: list[EvidenceRecord] = []
    for index, raw in enumerate(payload["records"]):
        record = EvidenceRecord.model_validate(raw)
        if record.case_id != case.case_id or record.document_hash != extracted.document_hash:
            raise OllamaResponseError("evidence output changed case or document identity")
        if record.pages.start < start_page or record.pages.end > end_page:
            raise OllamaResponseError("evidence citation is outside the supplied chunk")
        cited_pages = [pages[number] for number in range(record.pages.start, record.pages.end + 1)]
        if any(page.status == PageStatus.UNAVAILABLE for page in cited_pages):
            raise OllamaResponseError("evidence cites an unavailable page")
        source_text = normalize_text("\n".join(page.text for page in cited_pages)).casefold()
        quoted_text = normalize_text(record.text).casefold()
        if not quoted_text or quoted_text not in source_text:
            raise OllamaResponseError("evidence text is not present on the cited source pages")
        parts = {page.opinion_part.value for page in cited_pages if page.opinion_part}
        if record.opinion_part and record.opinion_part not in parts:
            raise OllamaResponseError("evidence opinion part conflicts with page classification")
        if record.kind == EvidenceKind.HOLDING and not parts.intersection(
            {OpinionPart.MAJORITY.value, OpinionPart.PLURALITY.value, OpinionPart.PER_CURIAM.value}
        ):
            raise OllamaResponseError("holding evidence is not from a controlling opinion part")
        update: dict[str, object] = {
            "evidence_id": f"ev-{extracted.document_hash[:12]}-{chunk_index:04d}-{index:04d}"
        }
        if not extracted.classification_confident and record.kind in {
            EvidenceKind.HOLDING,
            EvidenceKind.PARTY_ARGUMENT,
            EvidenceKind.AMICUS_ARGUMENT,
        }:
            update["status"] = EvidenceStatus.UNCERTAIN
        records.append(record.model_copy(update=update))
    return records


def _unavailable_records(case_id: str, extracted: ExtractedDocument) -> list[EvidenceRecord]:
    return [
        EvidenceRecord(
            evidence_id=f"unavailable-{extracted.document_hash[:12]}-{page.page_number}",
            case_id=case_id,
            document_hash=extracted.document_hash,
            pages=PageRange(start=page.page_number, end=page.page_number),
            kind=EvidenceKind.FACT,
            attribution="Source extraction",
            text=page.reason or "Page text unavailable",
            confidence=0,
            status=EvidenceStatus.UNAVAILABLE,
        )
        for page in extracted.pages
        if page.status == PageStatus.UNAVAILABLE
    ]


def _read_records(path: Path) -> list[EvidenceRecord]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError(f"malformed evidence file: {path}")
    return [EvidenceRecord.model_validate(item) for item in payload["records"]]


def load_case_evidence(root: Path, case: NormalizedCase) -> list[EvidenceRecord]:
    records: dict[str, EvidenceRecord] = {}
    for reference in case.documents:
        path = root / "data" / "evidence" / case.case_id / f"{reference.sha256}.json"
        if not path.exists():
            continue
        for record in _read_records(path):
            if record.case_id == case.case_id:
                records[record.evidence_id] = record
    return sorted(
        records.values(),
        key=lambda item: (item.document_hash, item.pages.start, item.evidence_id),
    )


def _write_records(
    path: Path,
    case_id: str,
    document_hash: str,
    records: list[EvidenceRecord],
) -> None:
    _atomic_json(
        path,
        {
            "schema_version": "1.0.0",
            "document_hash": document_hash,
            "case_id": case_id,
            "records": [item.model_dump(mode="json") for item in records],
        },
    )


def _evidence_response_schema() -> dict[str, Any]:
    record_schema = EvidenceRecord.model_json_schema(mode="validation")
    definitions = record_schema.pop("$defs", {})
    return {
        "$defs": definitions,
        "type": "object",
        "additionalProperties": False,
        "required": ["records"],
        "properties": {"records": {"type": "array", "items": record_schema}},
    }


def load_extraction(root: Path, document_hash: str) -> ExtractedDocument | None:
    path = root / "data" / "evidence" / "extracted" / f"{document_hash}.json"
    if not path.exists():
        return None
    return ExtractedDocument.model_validate_json(path.read_text())


def _completed_chunks(path: Path) -> int:
    return _status_number(path, "completed_chunks")


def _total_chunks(path: Path) -> int:
    return _status_number(path, "total_chunks")


def _status_number(path: Path, field: str) -> int:
    try:
        payload = json.loads(path.read_text())
        value = payload.get(field, 0)
        return value if isinstance(value, int) and value >= 0 else 0
    except (OSError, ValueError, AttributeError):
        return 0


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True, default=str)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
