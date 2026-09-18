"""Deterministic and adversarial verification with fail-closed publication."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .evidence import _atomic_json, load_extraction
from .models import (
    Citation,
    CitizenGuide,
    DocumentManifest,
    EvidenceKind,
    EvidenceRecord,
    EvidenceStatus,
    Lifecycle,
    NormalizedCase,
    SectionStatus,
    ValidationResult,
    ValidationState,
)
from .ollama import OllamaClient, OllamaResponseError
from .prompts import verification_prompt

QUALITY_THRESHOLDS: dict[str, float] = {
    "factual_accuracy": 0.90,
    "neutrality": 0.80,
    "readability": 0.75,
    "completeness": 0.65,
    "traceability": 0.90,
    "restraint": 0.90,
}
VERIFY_CHECKS = (
    "support",
    "attribution",
    "opinion_distinction",
    "oral_argument_characterization",
    "overstatement",
)


class ModelVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: dict[str, bool]
    scores: dict[str, float]
    messages: list[str] = Field(default_factory=list)


class GuideValidator:
    def __init__(self, root: Path) -> None:
        self.root = root

    def deterministic(
        self,
        case: NormalizedCase,
        candidate: CitizenGuide,
        evidence: Sequence[EvidenceRecord],
        manifest: DocumentManifest,
    ) -> ValidationResult:
        messages: list[str] = []
        checks: dict[str, bool] = {
            "schema": True,
            "case_identity": candidate.case_id == case.case_id,
            "lifecycle": candidate.lifecycle == case.lifecycle,
            "citation_targets": True,
            "page_ranges": True,
            "document_currency": True,
            "attribution": True,
            "opinion_distinction": True,
            "oral_argument": True,
            "provenance": True,
        }
        current = {item.sha256 for item in case.documents if item.current}
        manifest_hashes = {item.sha256 for item in manifest.documents}
        evidence_by_id = {item.evidence_id: item for item in evidence}
        extraction_cache = {item: load_extraction(self.root, item) for item in current}
        for citation, context in _citations(candidate):
            if (
                citation.document_hash not in manifest_hashes
                or citation.document_hash not in current
            ):
                checks["citation_targets"] = False
                messages.append(f"{context}: citation target is absent or not current")
            extraction = extraction_cache.get(citation.document_hash)
            if extraction is None:
                checks["page_ranges"] = False
                messages.append(f"{context}: extraction record is missing")
            else:
                pages = {item.page_number: item for item in extraction.pages}
                cited = range(citation.pages.start, citation.pages.end + 1)
                if any(number not in pages or not pages[number].text for number in cited):
                    checks["page_ranges"] = False
                    messages.append(f"{context}: cited page is unavailable or outside the PDF")
            if not citation.evidence_ids:
                checks["citation_targets"] = False
                messages.append(f"{context}: citation has no evidence IDs")
            for evidence_id in citation.evidence_ids:
                record = evidence_by_id.get(evidence_id)
                if record is None:
                    checks["citation_targets"] = False
                    messages.append(f"{context}: unknown evidence ID {evidence_id}")
                    continue
                if (
                    record.document_hash != citation.document_hash
                    or record.pages.start < citation.pages.start
                    or record.pages.end > citation.pages.end
                    or record.status != EvidenceStatus.SUPPORTED
                ):
                    checks["citation_targets"] = False
                    messages.append(f"{context}: evidence {evidence_id} does not support citation")
        if not candidate.generation.source_hashes or any(
            item not in current for item in candidate.generation.source_hashes
        ):
            checks["provenance"] = False
            messages.append("generation source hashes are absent or non-current")
        referenced_hashes = {citation.document_hash for citation, _ in _citations(candidate)}
        if not referenced_hashes.issubset(set(candidate.generation.source_hashes)):
            checks["provenance"] = False
            messages.append("generation provenance omits a cited source hash")
        if any(
            not reference.current
            for reference in case.documents
            if reference.sha256 in referenced_hashes
        ):
            checks["document_currency"] = False
            messages.append("candidate cites a non-current case document")
        self._check_claim_meaning(candidate, evidence_by_id, checks, messages)
        if case.lifecycle != Lifecycle.DECIDED and candidate.decision.status not in {
            SectionStatus.PENDING,
            SectionStatus.NOT_APPLICABLE,
        }:
            checks["lifecycle"] = False
            messages.append("undecided case decision must be pending or not applicable")
        if (
            case.lifecycle == Lifecycle.DECIDED
            and candidate.decision.status == SectionStatus.COMPLETE
        ):
            decision_evidence = _section_evidence(candidate.decision, evidence_by_id)
            if not any(item.kind == EvidenceKind.HOLDING for item in decision_evidence):
                checks["lifecycle"] = False
                messages.append("complete decision section lacks holding evidence")
        return ValidationResult(
            state=ValidationState.CANDIDATE if all(checks.values()) else ValidationState.REJECTED,
            checked_at=datetime.now(UTC),
            checks=checks,
            messages=messages,
        )

    @staticmethod
    def _check_claim_meaning(
        guide: CitizenGuide,
        evidence: Mapping[str, EvidenceRecord],
        checks: dict[str, bool],
        messages: list[str],
    ) -> None:
        attributed = {
            EvidenceKind.ALLEGATION,
            EvidenceKind.PARTY_ARGUMENT,
            EvidenceKind.AMICUS_ARGUMENT,
            EvidenceKind.JUSTICE_QUESTION,
            EvidenceKind.CONCURRENCE,
            EvidenceKind.DISSENT,
        }
        for section_name, section in _sections(guide):
            for index, claim in enumerate(section.claims):
                records = _claim_evidence(claim.citations, evidence)
                if any(item.kind in attributed for item in records) and not claim.attribution:
                    checks["attribution"] = False
                    messages.append(f"{section_name}.claims[{index}] requires attribution")
                if (
                    section_name == "decision"
                    and records
                    and all(
                        item.kind in {EvidenceKind.CONCURRENCE, EvidenceKind.DISSENT}
                        or item.opinion_part in {"concurrence", "dissent"}
                        for item in records
                    )
                    and (
                        not claim.attribution
                        or not re.search(
                            r"\b(dissent(?:ed|ing)?|concurr(?:ence|ed|ing)?|separate opinion)\b",
                            claim.text,
                            re.I,
                        )
                    )
                ):
                    checks["opinion_distinction"] = False
                    messages.append(
                        "separate-opinion claim must name its author and label the disagreement"
                    )
                if (
                    section_name == "oral_argument"
                    and any(item.kind == EvidenceKind.JUSTICE_QUESTION for item in records)
                    and re.search(r"\b(vote[ds]?|will rule|supports?|opposes?)\b", claim.text, re.I)
                ):
                    checks["oral_argument"] = False
                    messages.append("oral-argument question is characterized as a view or vote")


def adversarial_verify(
    ollama: OllamaClient,
    case: NormalizedCase,
    guide: CitizenGuide,
    evidence: Sequence[EvidenceRecord],
) -> ModelVerification:
    payload = ollama.generate_json(verification_prompt(case, guide, evidence))
    try:
        result = ModelVerification.model_validate(payload)
    except ValidationError as error:
        raise OllamaResponseError(f"malformed verification response: {error}") from error
    missing_checks = set(VERIFY_CHECKS) - result.checks.keys()
    missing_scores = QUALITY_THRESHOLDS.keys() - result.scores.keys()
    if missing_checks or missing_scores:
        missing = sorted(missing_checks | missing_scores)
        raise OllamaResponseError(f"verification response missing checks/scores: {missing}")
    if any(not 0 <= result.scores[name] <= 1 for name in QUALITY_THRESHOLDS):
        raise OllamaResponseError("verification quality scores must be between zero and one")
    return result


def verification_checks(result: ModelVerification) -> dict[str, bool]:
    checks = {f"model_{name}": result.checks[name] for name in VERIFY_CHECKS}
    checks.update(
        {
            f"quality_{name}": result.scores[name] >= threshold
            for name, threshold in QUALITY_THRESHOLDS.items()
        }
    )
    return checks


def publish_candidate(
    root: Path,
    case: NormalizedCase,
    candidate: CitizenGuide,
    deterministic: ValidationResult,
    model_result: ModelVerification,
) -> CitizenGuide | None:
    combined = {**deterministic.checks, **verification_checks(model_result)}
    messages = [*deterministic.messages, *model_result.messages]
    accepted = all(combined.values())
    validation = ValidationResult(
        state=ValidationState.ACCEPTED if accepted else ValidationState.REJECTED,
        checked_at=datetime.now(UTC),
        checks=combined,
        messages=messages,
    )
    final = candidate.model_copy(update={"validation": validation})
    report = {
        "case_id": case.case_id,
        "accepted": accepted,
        "checks": combined,
        "quality_scores": model_result.scores,
        "messages": messages,
    }
    _atomic_json(root / "reports" / "validation" / f"{case.case_id}.json", report)
    if not accepted:
        _atomic_json(
            root / "reports" / "validation" / f"{case.case_id}.candidate.json",
            final.model_dump(mode="json"),
        )
        return None
    _atomic_json(
        root / "data" / "guides" / f"{case.case_id}.json",
        final.model_dump(mode="json"),
    )
    return final


def validate_repository(root: Path) -> list[str]:
    """Validate accepted records without contacting the model."""
    errors: list[str] = []
    manifest_path = root / "manifests" / "documents.json"
    try:
        manifest = (
            DocumentManifest.model_validate_json(manifest_path.read_text())
            if manifest_path.exists()
            else DocumentManifest()
        )
    except (OSError, ValidationError) as error:
        return [f"manifest: {error}"]

    cases: dict[str, NormalizedCase] = {}
    for path in sorted((root / "data" / "cases").glob("*.json")):
        try:
            case = NormalizedCase.model_validate_json(path.read_text())
        except (OSError, ValidationError) as error:
            errors.append(f"{path}: {error}")
            continue
        if case.case_id in cases:
            errors.append(f"{path}: duplicate case ID {case.case_id}")
            continue
        cases[case.case_id] = case

    for entry in manifest.documents:
        for association in entry.cases:
            if association.case_id is None:
                # Historical documents can remain associated only by docket or import group.
                continue
            associated_case = cases.get(association.case_id)
            if associated_case is None:
                errors.append(
                    f"{manifest_path}: document {entry.sha256} has dangling case association "
                    f"{association.case_id}"
                )
                continue
            if entry.sha256 not in {reference.sha256 for reference in associated_case.documents}:
                errors.append(
                    f"{manifest_path}: association {association.case_id} does not reference "
                    f"document {entry.sha256} in its normalized case"
                )

    validator = GuideValidator(root)
    for path in sorted((root / "data" / "guides").glob("*.json")):
        try:
            guide = CitizenGuide.model_validate_json(path.read_text())
            case_path = root / "data" / "cases" / f"{guide.case_id}.json"
            case = NormalizedCase.model_validate_json(case_path.read_text())
            evidence = _load_evidence_for_hashes(
                root, guide.case_id, guide.generation.source_hashes
            )
            result = validator.deterministic(case, guide, evidence, manifest)
            if guide.validation.state != ValidationState.ACCEPTED:
                errors.append(f"{path}: checked-in guide is not accepted")
            if result.state == ValidationState.REJECTED or not all(result.checks.values()):
                failed = sorted(name for name, passed in result.checks.items() if not passed)
                errors.append(f"{path}: deterministic checks failed: {', '.join(failed)}")
            errors.extend(f"{path}: {message}" for message in result.messages)
        except (OSError, ValueError, ValidationError) as error:
            errors.append(f"{path}: {error}")
    return errors


def _load_evidence_for_hashes(
    root: Path, case_id: str, hashes: Iterable[str]
) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for document_hash in hashes:
        path = root / "data" / "evidence" / case_id / f"{document_hash}.json"
        try:
            payload = json.loads(path.read_text())
            records.extend(EvidenceRecord.model_validate(item) for item in payload["records"])
        except (OSError, ValueError, ValidationError, KeyError, TypeError):
            continue
    return records


def _sections(guide: CitizenGuide) -> Iterable[tuple[str, Any]]:
    yield "overview", guide.overview
    yield "background_and_question", guide.background_and_question
    for index, section in enumerate(guide.party_positions):
        yield f"party_positions[{index}]", section
    yield "oral_argument", guide.oral_argument
    yield "decision", guide.decision
    yield "why_it_matters", guide.why_it_matters


def _citations(guide: CitizenGuide) -> Iterable[tuple[Citation, str]]:
    for section_name, section in _sections(guide):
        for citation in section.summary_citations:
            yield citation, f"{section_name}.summary"
        for index, claim in enumerate(section.claims):
            for citation in claim.citations:
                yield citation, f"{section_name}.claims[{index}]"
    for index, entry in enumerate(guide.glossary):
        for citation in entry.citations:
            yield citation, f"glossary[{index}]"
    for index, citation in enumerate(guide.sources):
        yield citation, f"sources[{index}]"


def _claim_evidence(
    citations: Sequence[Citation], evidence: Mapping[str, EvidenceRecord]
) -> list[EvidenceRecord]:
    return [
        evidence[evidence_id]
        for citation in citations
        for evidence_id in citation.evidence_ids
        if evidence_id in evidence
    ]


def _section_evidence(section: Any, evidence: Mapping[str, EvidenceRecord]) -> list[EvidenceRecord]:
    citations = [*section.summary_citations]
    citations.extend(citation for claim in section.claims for citation in claim.citations)
    return _claim_evidence(citations, evidence)
