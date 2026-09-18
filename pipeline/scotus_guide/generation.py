"""Evidence-bounded case synthesis and fail-closed guide publication."""

from __future__ import annotations

import json
import re
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
    EvidenceKind,
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
    revision_prompt,
    synthesis_prompt,
)
from .validation import (
    GuideValidator,
    ModelVerification,
    adversarial_verify,
    publish_candidate,
    verification_checks,
)


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
            candidate = _candidate(raw, case, provenance, bounded)
            validator = GuideValidator(self.root)
            for attempt in range(4):
                deterministic = validator.deterministic(case, candidate, bounded, manifest)
                if deterministic.state == ValidationState.REJECTED:
                    model_result = _failed_model_result(deterministic.messages)
                    feedback = deterministic.messages
                else:
                    model_result = adversarial_verify(self.ollama, case, candidate, bounded)
                    if all(verification_checks(model_result).values()):
                        return publish_candidate(
                            self.root, case, candidate, deterministic, model_result
                        )
                    feedback = model_result.messages
                if attempt == 3:
                    return publish_candidate(
                        self.root, case, candidate, deterministic, model_result
                    )
                revised = self.ollama.generate_json(
                    revision_prompt(case, candidate, bounded, feedback),
                    schema=CitizenGuide.model_json_schema(mode="validation"),
                )
                candidate = _candidate(revised, case, provenance, bounded)
            raise AssertionError("unreachable guide revision loop")
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


def _candidate(
    raw: Any,
    case: NormalizedCase,
    provenance: GenerationProvenance,
    evidence: list[EvidenceRecord],
) -> CitizenGuide:
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
    _drop_invalid_cited_content(raw, evidence)
    _make_attribution_explicit(raw, evidence)
    return CitizenGuide.model_validate(raw)


def _drop_invalid_cited_content(raw: dict[str, Any], evidence: list[EvidenceRecord]) -> None:
    """Remove model claims whose structured citations cannot pass deterministic checks."""
    by_id = {item.evidence_id: item for item in evidence}

    def valid_citation(value: object) -> bool:
        if not isinstance(value, dict):
            return False
        document_hash = value.get("document_hash")
        pages = value.get("pages")
        evidence_ids = value.get("evidence_ids")
        if (
            not isinstance(document_hash, str)
            or not isinstance(pages, dict)
            or not isinstance(pages.get("start"), int)
            or not isinstance(pages.get("end"), int)
            or not isinstance(evidence_ids, list)
            or not evidence_ids
        ):
            return False
        for evidence_id in evidence_ids:
            record = by_id.get(evidence_id) if isinstance(evidence_id, str) else None
            if (
                record is None
                or record.status != EvidenceStatus.SUPPORTED
                or record.document_hash != document_hash
                or record.pages.start < pages["start"]
                or record.pages.end > pages["end"]
            ):
                return False
        return True

    def citation_records(citations: list[object]) -> list[EvidenceRecord]:
        records: list[EvidenceRecord] = []
        for citation in citations:
            if not isinstance(citation, dict) or not isinstance(citation.get("evidence_ids"), list):
                continue
            records.extend(
                by_id[item]
                for item in citation["evidence_ids"]
                if isinstance(item, str) and item in by_id
            )
        return records

    date_pattern = re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|"
        r"November|December)\s+\d{1,2}(?:,\s*|\s+)\d{4}\b",
        re.I,
    )

    def valid_claim(value: object, *, party_position: bool) -> bool:
        if not isinstance(value, dict) or not isinstance(value.get("citations"), list):
            return False
        citations = value["citations"]
        if not citations or not all(valid_citation(item) for item in citations):
            return False
        records = citation_records(citations)
        if party_position and any(
            item.kind not in {EvidenceKind.PARTY_ARGUMENT, EvidenceKind.AMICUS_ARGUMENT}
            for item in records
        ):
            return False
        text = value.get("text")
        if isinstance(text, str):
            evidence_words = {
                word.casefold()
                for item in records
                for word in re.findall(r"[A-Za-z]+|\d+", item.text)
            }
            for date in date_pattern.findall(text):
                if not all(
                    word.casefold() in evidence_words for word in re.findall(r"[A-Za-z]+|\d+", date)
                ):
                    return False
        return True

    sections: list[tuple[bool, object]] = [
        (False, raw.get("overview")),
        (False, raw.get("background_and_question")),
        (False, raw.get("oral_argument")),
        (False, raw.get("decision")),
        (False, raw.get("why_it_matters")),
    ]
    party_positions = raw.get("party_positions")
    if isinstance(party_positions, list):
        sections.extend((True, item) for item in party_positions)
    for party_position, section in sections:
        if not isinstance(section, dict):
            continue
        claims = section.get("claims")
        if isinstance(claims, list):
            section["claims"] = [
                claim for claim in claims if valid_claim(claim, party_position=party_position)
            ]
        if section.get("summary"):
            citations = section.get("summary_citations")
            if (
                not isinstance(citations, list)
                or not citations
                or not all(valid_citation(item) for item in citations)
            ):
                section["summary"] = None
                section["summary_citations"] = []
    glossary = raw.get("glossary")
    if isinstance(glossary, list):
        raw["glossary"] = [
            entry
            for entry in glossary
            if isinstance(entry, dict)
            and isinstance(entry.get("citations"), list)
            and entry["citations"]
            and all(valid_citation(item) for item in entry["citations"])
        ]


def _failed_model_result(messages: list[str]) -> ModelVerification:
    return ModelVerification(
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
        messages=["Adversarial verification skipped after deterministic failure", *messages],
    )


def _make_attribution_explicit(raw: dict[str, Any], evidence: list[EvidenceRecord]) -> None:
    """Make source-required attribution visible in both metadata and reader prose."""
    by_id = {item.evidence_id: item for item in evidence}
    section_values: list[object] = [
        raw.get("overview"),
        raw.get("background_and_question"),
        raw.get("oral_argument"),
        raw.get("decision"),
        raw.get("why_it_matters"),
    ]
    party_positions = raw.get("party_positions")
    if isinstance(party_positions, list):
        section_values.extend(party_positions)
    attributed_kinds = {
        EvidenceKind.ALLEGATION,
        EvidenceKind.PARTY_ARGUMENT,
        EvidenceKind.AMICUS_ARGUMENT,
        EvidenceKind.JUSTICE_QUESTION,
        EvidenceKind.CONCURRENCE,
        EvidenceKind.DISSENT,
    }
    for section in section_values:
        if not isinstance(section, dict) or not isinstance(section.get("claims"), list):
            continue
        for claim in section["claims"]:
            if not isinstance(claim, dict):
                continue
            records: list[EvidenceRecord] = []
            citations = claim.get("citations", [])
            if not isinstance(citations, list):
                continue
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                ids = citation.get("evidence_ids", [])
                if isinstance(ids, list):
                    records.extend(by_id[item] for item in ids if item in by_id)
            requiring = [item for item in records if item.kind in attributed_kinds]
            attributions = {
                item.attribution.strip() for item in records if item.attribution.strip()
            }
            text = claim.get("text")
            if not attributions and isinstance(claim.get("attribution"), str):
                attributions = {claim["attribution"].strip()}
            if (
                len(attributions) > 1
                and isinstance(text, str)
                and all(attribution.casefold() in text.casefold() for attribution in attributions)
            ):
                claim["attribution"] = "; ".join(sorted(attributions))
                continue
            if len(attributions) != 1:
                continue
            attribution = next(iter(attributions))
            claim["attribution"] = attribution
            if not requiring or not isinstance(text, str) or not text.strip():
                continue
            speech_verbs = r"argue|contend|say|ask|maintain|claim|warn|dissent|concur"
            speech_pattern = (
                rf"(?:\baccording to\s+{re.escape(attribution)}\b|"
                rf"\b{re.escape(attribution)}\b.{{0,120}}\b({speech_verbs}))"
            )
            if not re.search(speech_pattern, text, re.I):
                claim["text"] = f"According to {attribution}, {text[0].lower()}{text[1:]}"


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
