"""Evidence-bounded case synthesis and fail-closed guide publication."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .chunking import estimate_tokens
from .evidence import EvidenceJobStatus, JobState, load_case_evidence
from .extraction import EXTRACTOR_VERSION
from .models import (
    SCHEMA_VERSION,
    CitizenGuide,
    DocumentManifest,
    EvidenceRecord,
    EvidenceStatus,
    GenerationProvenance,
    GuideSection,
    Lifecycle,
    NormalizedCase,
    SectionStatus,
    ValidationResult,
    ValidationState,
)
from .ollama import ModelIdentity, OllamaClient, OllamaError, OllamaResponseError
from .prompts import (
    EVIDENCE_PROMPT_VERSION,
    PROMPT_VERSION,
    consolidation_prompt,
    synthesis_prompt,
)
from .validation import GuideValidator, adversarial_verify, publish_candidate


class GuideGenerationError(RuntimeError):
    pass


class GuideGenerator:
    def __init__(
        self,
        root: Path,
        ollama: OllamaClient,
        identity: ModelIdentity,
        *,
        synthesis_budget: int = 20_000,
    ) -> None:
        self.root = root
        self.ollama = ollama
        self.identity = identity
        self.synthesis_budget = synthesis_budget

    def generate_case(
        self,
        case: NormalizedCase,
        manifest: DocumentManifest,
    ) -> CitizenGuide | None:
        incomplete = []
        for reference in case.documents:
            if not reference.current:
                continue
            status_path = (
                self.root
                / "data"
                / "evidence"
                / "status"
                / case.case_id
                / f"{reference.sha256}.json"
            )
            try:
                status = EvidenceJobStatus.model_validate_json(status_path.read_text())
                if (
                    status.state != JobState.COMPLETED
                    or status.extractor_version != EXTRACTOR_VERSION
                    or status.prompt_version != EVIDENCE_PROMPT_VERSION
                    or status.model_name != self.identity.name
                    or status.model_digest != self.identity.digest
                    or status.parameters != dict(self.ollama.parameters)
                ):
                    incomplete.append(reference.sha256)
            except (OSError, ValidationError):
                incomplete.append(reference.sha256)
        if incomplete:
            raise GuideGenerationError(
                f"evidence extraction is incomplete for: {', '.join(sorted(incomplete))}"
            )
        evidence = [
            item
            for item in load_case_evidence(self.root, case)
            if item.status == EvidenceStatus.SUPPORTED
        ]
        if not evidence:
            raise GuideGenerationError(f"no supported evidence for {case.case_id}")
        try:
            bounded = self._bounded_evidence(evidence)
            source_hashes = sorted({item.document_hash for item in bounded})
            provenance = GenerationProvenance(
                source_hashes=source_hashes,
                model_name=self.identity.name,
                model_digest=self.identity.digest,
                parameters=dict(self.ollama.parameters),
                prompt_version=PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
                extractor_version=EXTRACTOR_VERSION,
                generated_at=datetime.now(UTC),
            )
            raw = self.ollama.generate_json(
                synthesis_prompt(case, bounded),
                schema=CitizenGuide.model_json_schema(mode="validation"),
            )
            if not isinstance(raw, dict):
                raise OllamaResponseError("guide response must be a JSON object")
            raw.update(
                {
                    "case_id": case.case_id,
                    "lifecycle": case.lifecycle,
                    "generation": provenance.model_dump(mode="json"),
                    "validation": ValidationResult(
                        state=ValidationState.CANDIDATE,
                        checked_at=datetime.now(UTC),
                        checks={},
                    ).model_dump(mode="json"),
                }
            )
            if case.lifecycle != Lifecycle.DECIDED:
                raw["decision"] = GuideSection(
                    status=SectionStatus.PENDING,
                    heading="Decision",
                ).model_dump(mode="json")
            candidate = CitizenGuide.model_validate(raw)
            deterministic = GuideValidator(self.root).deterministic(
                case, candidate, bounded, manifest
            )
            if deterministic.state == ValidationState.REJECTED:
                # Do not spend another request verifying a deterministically invalid guide.
                from .validation import ModelVerification

                failed_model = ModelVerification(
                    checks={
                        name: False
                        for name in (
                            "support",
                            "attribution",
                            "opinion_distinction",
                            "oral_argument_characterization",
                            "overstatement",
                        )
                    },
                    scores={
                        "factual_accuracy": 0,
                        "neutrality": 0,
                        "readability": 0,
                        "completeness": 0,
                        "traceability": 0,
                        "restraint": 0,
                    },
                    messages=["Adversarial verification skipped after deterministic failure"],
                )
                return publish_candidate(self.root, case, candidate, deterministic, failed_model)
            model_result = adversarial_verify(self.ollama, case, candidate, bounded)
            return publish_candidate(self.root, case, candidate, deterministic, model_result)
        except (ValidationError, ValueError, OllamaError) as error:
            raise GuideGenerationError(str(error)) from error

    def _bounded_evidence(self, records: list[EvidenceRecord]) -> list[EvidenceRecord]:
        current = records
        while _evidence_tokens(current) > self.synthesis_budget:
            groups = _groups_within_budget(current, max(1, self.synthesis_budget // 2))
            consolidated: list[EvidenceRecord] = []
            for group in groups:
                payload = self.ollama.generate_json(consolidation_prompt(group))
                parsed = _parse_consolidation(payload, group)
                consolidated.extend(parsed)
            if len(consolidated) >= len(current):
                raise GuideGenerationError(
                    "staged evidence consolidation did not reduce the synthesis payload"
                )
            current = consolidated
        return current


def _evidence_tokens(records: list[EvidenceRecord]) -> int:
    return estimate_tokens(
        json.dumps([record.model_dump(mode="json") for record in records], sort_keys=True)
    )


def _groups_within_budget(records: list[EvidenceRecord], budget: int) -> list[list[EvidenceRecord]]:
    groups: list[list[EvidenceRecord]] = []
    pending: list[EvidenceRecord] = []
    for record in records:
        candidate = [*pending, record]
        if pending and _evidence_tokens(candidate) > budget:
            groups.append(pending)
            pending = []
        pending.append(record)
    if pending:
        groups.append(pending)
    return groups


def _parse_consolidation(payload: Any, source: list[EvidenceRecord]) -> list[EvidenceRecord]:
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise OllamaResponseError("consolidation output must contain records")
    permitted = {item.model_dump_json(): item for item in source}
    output = [EvidenceRecord.model_validate(item) for item in payload["records"]]
    for record in output:
        if record.model_dump_json() not in permitted:
            raise OllamaResponseError("consolidation invented or changed evidence")
    if len({item.evidence_id for item in output}) != len(output):
        raise OllamaResponseError("consolidation duplicated an evidence record")
    return output
