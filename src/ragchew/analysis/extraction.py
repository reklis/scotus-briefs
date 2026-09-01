"""Schema-constrained, evidence-grounded observation extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from ragchew.analysis.normalize import (
    normalize_incident_type,
    normalize_location,
    normalize_timestamp,
    normalize_unit,
)
from ragchew.contracts import (
    EpistemicStatus,
    EvidenceRange,
    Observation,
    ObservationType,
    SensitivityLabel,
)


class ExtractedObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ObservationType
    raw_value: str = Field(min_length=1)
    normalized_value: str | None = None
    confidence: float = Field(ge=0, le=1)
    epistemic_status: EpistemicStatus
    evidence: EvidenceRange
    sensitivity: tuple[SensitivityLabel, ...] = ()
    routine: bool = False
    supersedes_observation_id: UUID | None = None


class ExtractionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observations: list[ExtractedObservation]


@dataclass(frozen=True)
class TranscriptForExtraction:
    revision_id: UUID
    capture_id: str
    text: str
    occurred_at: datetime
    talkgroup_id: int
    talkgroup_name: str


class ObservationExtractor(Protocol):
    model_name: str

    def extract(self, transcript: TranscriptForExtraction) -> ExtractionBatch: ...


class ObservationStore(Protocol):
    def save_extraction(
        self,
        transcript: TranscriptForExtraction,
        observations: list[Observation],
        *,
        model: str,
        schema_version: str,
        prompt_version: str,
        vocabulary_version: str,
    ) -> list[Observation]: ...


class OpenAIObservationExtractor:
    PROMPT_VERSION = "dcfd-extraction-v1"

    def __init__(self, model_name: str, client: OpenAI) -> None:
        self.model_name = model_name
        self.client = client

    def extract(self, transcript: TranscriptForExtraction) -> ExtractionBatch:
        completion = self.client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract only facts explicitly supported by the DCFD radio transcript. "
                        "Preserve reported/dispatched/on-scene modality, negation, corrections, "
                        "and uncertainty. Evidence offsets and quote must exactly match input. "
                        "Do not infer causes, casualties, outcomes, quadrants, or identities."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Talkgroup {transcript.talkgroup_id} {transcript.talkgroup_name}\n"
                        f"Transcript:\n{transcript.text}"
                    ),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "dcfd_observations",
                    "strict": True,
                    "schema": ExtractionBatch.model_json_schema(),
                },
            },
        )
        content = completion.choices[0].message.content
        if not content:
            raise ValueError("extraction model returned no structured content")
        return ExtractionBatch.model_validate_json(content)


class ObservationValidationError(ValueError):
    pass


NEGATION = re.compile(r"\b(no|not|nothing|without|unable to confirm|unfounded)\b", re.I)
QUADRANT = re.compile(r"\b(NE|NW|SE|SW|northeast|northwest|southeast|southwest)\b", re.I)
CASUALTY_OR_CAUSE = re.compile(r"\b(injur|fatal|dead|casualt|caused|because|origin)\w*\b", re.I)
ADDRESS = re.compile(
    r"\b(?:\d{1,5}|\w+ hundred block of)\s+[A-Za-z0-9 ]+?"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Place|Pl)"
    r"(?:\s+(?:NE|NW|SE|SW|Northeast|Northwest|Southeast|Southwest))?\b",
    re.I,
)

SENSITIVITY_PATTERNS: dict[SensitivityLabel, re.Pattern[str]] = {
    SensitivityLabel.MEDICAL: re.compile(
        r"\b(patient|medic|ambulance|cardiac|unconscious|breathing|injur)\w*\b", re.I
    ),
    SensitivityLabel.PERSONAL_IDENTIFIER: re.compile(
        r"\b(?:patient|caller|victim)\s+(?:is\s+)?[A-Z][a-z]+\s+[A-Z][a-z]+\b",
        re.I,
    ),
    SensitivityLabel.EXACT_RESIDENTIAL_UNIT: re.compile(
        r"\b(?:apartment|apt|unit)\s*[A-Z0-9-]+\b", re.I
    ),
    SensitivityLabel.BEHAVIORAL_HEALTH: re.compile(
        r"\b(behavioral|mental health|psychiatric|emotionally disturbed)\b", re.I
    ),
    SensitivityLabel.SUICIDE: re.compile(r"\b(suicid|self-harm)\w*\b", re.I),
    SensitivityLabel.OVERDOSE: re.compile(r"\b(overdose|narcan|opioid)\w*\b", re.I),
    SensitivityLabel.JUVENILE: re.compile(r"\b(child|juvenile|infant|minor)\b", re.I),
}

MODALITY_PATTERNS: tuple[tuple[ObservationType, EpistemicStatus, re.Pattern[str]], ...] = (
    (ObservationType.CORRECTION, EpistemicStatus.CORRECTED, re.compile(r"\bcorrection\b", re.I)),
    (
        ObservationType.CANCELLATION,
        EpistemicStatus.CONFIRMED,
        re.compile(r"\b(cancel|return to service|unfounded|disregard)\w*\b", re.I),
    ),
    (
        ObservationType.ESCALATION,
        EpistemicStatus.ON_SCENE_REPORTED,
        re.compile(r"\b(working fire|additional alarm|second alarm|special alarm)\b", re.I),
    ),
    (
        ObservationType.ARRIVAL,
        EpistemicStatus.ON_SCENE_REPORTED,
        re.compile(r"\b(on scene|arriving)\b", re.I),
    ),
    (
        ObservationType.DISPATCH,
        EpistemicStatus.DISPATCHED,
        re.compile(r"\b(respond|dispatch)\w*\b", re.I),
    ),
    (
        ObservationType.RESPONSE,
        EpistemicStatus.RESPONDING,
        re.compile(r"\b(responding|en route)\b", re.I),
    ),
    (
        ObservationType.RESOLUTION,
        EpistemicStatus.ON_SCENE_REPORTED,
        re.compile(r"\b(under control|extinguished|incident terminated|all clear)\b", re.I),
    ),
)
ROUTINE = re.compile(r"^\s*(?:[\w ]+,?\s*)?(?:okay|10-4|copy|go ahead|clear)\.?\s*$", re.I)


def _evidence(match: re.Match[str]) -> EvidenceRange:
    return EvidenceRange(start_char=match.start(), end_char=match.end(), quote=match.group(0))


def deterministic_observations(transcript: TranscriptForExtraction) -> list[ExtractedObservation]:
    text = transcript.text
    results: list[ExtractedObservation] = []
    if match := ROUTINE.match(text):
        results.append(
            ExtractedObservation(
                type=ObservationType.ROUTINE,
                raw_value=match.group(0),
                confidence=1,
                epistemic_status=EpistemicStatus.CONFIRMED,
                evidence=_evidence(match),
                routine=True,
            )
        )
    for observation_type, epistemic, pattern in MODALITY_PATTERNS:
        if match := pattern.search(text):
            results.append(
                ExtractedObservation(
                    type=observation_type,
                    raw_value=match.group(0),
                    confidence=0.95,
                    epistemic_status=epistemic,
                    evidence=_evidence(match),
                )
            )
    if match := ADDRESS.search(text):
        normalized, _ = normalize_location(match.group(0))
        results.append(
            ExtractedObservation(
                type=ObservationType.LOCATION,
                raw_value=match.group(0),
                normalized_value=normalized,
                confidence=0.9,
                epistemic_status=EpistemicStatus.REPORTED,
                evidence=_evidence(match),
            )
        )
    for label, pattern in SENSITIVITY_PATTERNS.items():
        if match := pattern.search(text):
            results.append(
                ExtractedObservation(
                    type=ObservationType.PRIVACY,
                    raw_value=match.group(0),
                    confidence=1,
                    epistemic_status=EpistemicStatus.CONFIRMED,
                    evidence=_evidence(match),
                    sensitivity=(label,),
                )
            )
    return results


def validate_extracted(item: ExtractedObservation, transcript: str) -> None:
    evidence = item.evidence
    if evidence.end_char > len(transcript):
        raise ObservationValidationError("evidence range exceeds transcript")
    if transcript[evidence.start_char : evidence.end_char] != evidence.quote:
        raise ObservationValidationError("evidence quote does not match transcript range")
    if item.raw_value.lower() not in evidence.quote.lower():
        raise ObservationValidationError("raw value is not present in evidence quote")
    if (
        item.type
        in {
            ObservationType.ON_SCENE,
            ObservationType.INCIDENT_TYPE,
            ObservationType.INJURY_MENTION,
        }
        and NEGATION.search(evidence.quote)
        and item.epistemic_status != EpistemicStatus.NEGATED
    ):
        raise ObservationValidationError("positive claim loses source negation")
    if (
        item.type == ObservationType.LOCATION
        and item.normalized_value
        and QUADRANT.search(item.normalized_value)
        and not QUADRANT.search(evidence.quote)
    ):
        raise ObservationValidationError("normalized location guesses a quadrant")
    if item.type == ObservationType.INJURY_MENTION and not CASUALTY_OR_CAUSE.search(evidence.quote):
        raise ObservationValidationError("injury claim lacks supporting language")


def normalize_extracted(item: ExtractedObservation) -> ExtractedObservation:
    normalized = item.normalized_value
    if item.type == ObservationType.LOCATION:
        normalized, _ = normalize_location(item.raw_value)
    elif item.type == ObservationType.UNIT_ASSIGNMENT:
        normalized = normalize_unit(item.raw_value)
    elif item.type == ObservationType.INCIDENT_TYPE:
        normalized = normalize_incident_type(item.raw_value)
    return item.model_copy(update={"normalized_value": normalized})


class ExtractionService:
    SCHEMA_VERSION = "observation-v1"
    VOCABULARY_VERSION = "dcfd-v1"

    def __init__(self, extractor: ObservationExtractor, store: ObservationStore) -> None:
        self.extractor = extractor
        self.store = store

    def process(self, transcript: TranscriptForExtraction) -> list[Observation]:
        batch = self.extractor.extract(transcript)
        combined = batch.observations + deterministic_observations(transcript)
        unique: dict[tuple[Any, ...], ExtractedObservation] = {}
        for candidate in combined:
            normalized = normalize_extracted(candidate)
            validate_extracted(normalized, transcript.text)
            key = (
                normalized.type,
                normalized.evidence.start_char,
                normalized.evidence.end_char,
                normalized.normalized_value,
            )
            unique[key] = normalized
        occurred_at = normalize_timestamp(transcript.occurred_at)
        observations = [
            Observation(
                transcript_revision_id=transcript.revision_id,
                capture_id=transcript.capture_id,
                type=item.type,
                raw_value=item.raw_value,
                normalized_value=item.normalized_value,
                confidence=item.confidence,
                epistemic_status=item.epistemic_status,
                evidence=item.evidence,
                occurred_at=occurred_at,
                sensitivity=item.sensitivity,
                routine=item.routine,
                supersedes_observation_id=item.supersedes_observation_id,
            )
            for item in unique.values()
        ]
        prompt_version = getattr(
            self.extractor, "PROMPT_VERSION", "deterministic-extraction-v1"
        )
        return self.store.save_extraction(
            transcript,
            observations,
            model=self.extractor.model_name,
            schema_version=self.SCHEMA_VERSION,
            prompt_version=prompt_version,
            vocabulary_version=self.VOCABULARY_VERSION,
        )
