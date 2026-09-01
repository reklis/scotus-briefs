from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ragchew.analysis.extraction import (
    ExtractedObservation,
    ExtractionBatch,
    ExtractionService,
    ObservationValidationError,
    TranscriptForExtraction,
    deterministic_observations,
    validate_extracted,
)
from ragchew.analysis.normalize import (
    normalize_incident_type,
    normalize_location,
    normalize_talkgroup,
    normalize_unit,
)
from ragchew.contracts import (
    EpistemicStatus,
    EvidenceRange,
    Observation,
    ObservationType,
    SensitivityLabel,
)


class FakeExtractor:
    model_name = "extract-test"
    PROMPT_VERSION = "test-v1"

    def __init__(self, observations: list[ExtractedObservation] | None = None) -> None:
        self.observations = observations or []

    def extract(self, transcript: TranscriptForExtraction) -> ExtractionBatch:
        return ExtractionBatch(observations=self.observations)


class MemoryStore:
    def __init__(self) -> None:
        self.saved: dict[tuple[object, ...], list[Observation]] = {}

    def save_extraction(
        self,
        transcript: TranscriptForExtraction,
        observations: list[Observation],
        *,
        model: str,
        schema_version: str,
        prompt_version: str,
        vocabulary_version: str,
    ) -> list[Observation]:
        key = (
            transcript.revision_id,
            model,
            schema_version,
            prompt_version,
            vocabulary_version,
        )
        return self.saved.setdefault(key, observations)


def transcript(text: str) -> TranscriptForExtraction:
    return TranscriptForExtraction(
        revision_id=uuid4(),
        capture_id="call_0123456789abcdef",
        text=text,
        occurred_at=datetime(2026, 8, 27, 18, tzinfo=UTC),
        talkgroup_id=101,
        talkgroup_name="01 DISP",
    )


def extracted(
    text: str,
    raw: str,
    observation_type: ObservationType,
    *,
    normalized: str | None = None,
    epistemic: EpistemicStatus = EpistemicStatus.REPORTED,
) -> ExtractedObservation:
    start = text.index(raw)
    return ExtractedObservation(
        type=observation_type,
        raw_value=raw,
        normalized_value=normalized,
        confidence=0.9,
        epistemic_status=epistemic,
        evidence=EvidenceRange(start_char=start, end_char=start + len(raw), quote=raw),
    )


def test_evidence_offsets_and_quotes_must_match() -> None:
    text = "Engine 10 responding."
    item = extracted(text, "Engine 10", ObservationType.UNIT_ASSIGNMENT)
    validate_extracted(item, text)
    bad = item.model_copy(
        update={"evidence": EvidenceRange(start_char=1, end_char=10, quote="Engine 10")}
    )
    with pytest.raises(ObservationValidationError, match="quote"):
        validate_extracted(bad, text)


def test_positive_claim_cannot_drop_negation() -> None:
    text = "No smoke and no fire showing."
    item = extracted(text, "No smoke", ObservationType.ON_SCENE)
    with pytest.raises(ObservationValidationError, match="negation"):
        validate_extracted(item, text)
    validate_extracted(
        item.model_copy(update={"epistemic_status": EpistemicStatus.NEGATED}), text
    )


def test_normalizer_cannot_guess_quadrant() -> None:
    text = "Respond to 1400 H Street."
    item = extracted(
        text,
        "1400 H Street",
        ObservationType.LOCATION,
        normalized="1400 H St NE",
    )
    with pytest.raises(ObservationValidationError, match="quadrant"):
        validate_extracted(item, text)


def test_unsupported_injury_claim_is_rejected() -> None:
    text = "Working fire at H Street."
    item = extracted(text, "Working fire", ObservationType.INJURY_MENTION)
    with pytest.raises(ObservationValidationError, match="supporting"):
        validate_extracted(item, text)


def test_deterministic_modality_correction_routine_and_privacy() -> None:
    corrected = deterministic_observations(transcript("Correction, 1400 H Street Northeast."))
    assert ObservationType.CORRECTION in {item.type for item in corrected}
    assert ObservationType.LOCATION in {item.type for item in corrected}

    routine = deterministic_observations(transcript("Engine 10, okay."))
    assert any(item.type == ObservationType.ROUTINE and item.routine for item in routine)

    sensitive = deterministic_observations(
        transcript("Patient John Doe in apartment 4B, overdose reported.")
    )
    labels = {label for item in sensitive for label in item.sensitivity}
    assert SensitivityLabel.PERSONAL_IDENTIFIER in labels
    assert SensitivityLabel.EXACT_RESIDENTIAL_UNIT in labels
    assert SensitivityLabel.OVERDOSE in labels


def test_normalization_retains_uncertainty_and_domain_values() -> None:
    assert normalize_location("1400 H Street Northeast") == ("1400 H St NE", True)
    assert normalize_location("1400 H Street") == ("1400 H St", False)
    assert normalize_unit("E 10") == "Engine 10"
    assert normalize_incident_type("reported working fire") == "structure_fire"
    assert normalize_talkgroup(101, "01 disp") == "101:01 DISP"


def test_extraction_is_immutable_on_idempotent_reprocessing() -> None:
    source = transcript("Engine 10 respond to 1400 H Street Northeast.")
    store = MemoryStore()
    service = ExtractionService(FakeExtractor(), store)
    first = service.process(source)
    retry = service.process(source)
    assert [item.observation_id for item in retry] == [item.observation_id for item in first]
    assert {item.type for item in first} >= {ObservationType.DISPATCH, ObservationType.LOCATION}


def test_invalid_model_observation_fails_closed() -> None:
    source = transcript("No smoke showing.")
    unsafe = extracted(source.text, "No smoke", ObservationType.ON_SCENE)
    service = ExtractionService(FakeExtractor([unsafe]), MemoryStore())
    with pytest.raises(ObservationValidationError):
        service.process(source)
