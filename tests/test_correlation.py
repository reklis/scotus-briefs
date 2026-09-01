from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from ragchew.contracts import (
    EpistemicStatus,
    EvidenceRange,
    IncidentState,
    Observation,
    ObservationType,
    SensitivityLabel,
)
from ragchew.correlation.engine import CorrelationEngine, ObservationContext

BASE = datetime(2026, 8, 27, 18, tzinfo=UTC)


def context(
    name: str,
    observation_type: ObservationType,
    value: str,
    *,
    at: datetime = BASE,
    talkgroup: int = 101,
    confidence: float = 0.9,
    epistemic: EpistemicStatus = EpistemicStatus.REPORTED,
    sensitivity: tuple[SensitivityLabel, ...] = (),
    routine: bool = False,
    capture: str = "call-a",
) -> ObservationContext:
    identifier = uuid5(NAMESPACE_URL, f"{name}:{capture}:{at.isoformat()}")
    observation = Observation(
        observation_id=identifier,
        transcript_revision_id=uuid5(NAMESPACE_URL, f"transcript:{capture}"),
        capture_id=capture,
        type=observation_type,
        raw_value=value,
        normalized_value=value,
        confidence=confidence,
        epistemic_status=epistemic,
        evidence=EvidenceRange(start_char=0, end_char=len(value), quote=value),
        occurred_at=at,
        sensitivity=sensitivity,
        routine=routine,
    )
    return ObservationContext(observation, talkgroup, f"TG {talkgroup}")


def incident_batch(
    location: str,
    *,
    at: datetime = BASE,
    talkgroup: int = 101,
    capture: str = "call-a",
) -> list[ObservationContext]:
    return [
        context(
            "location",
            ObservationType.LOCATION,
            location,
            at=at,
            talkgroup=talkgroup,
            capture=capture,
        ),
        context(
            "type",
            ObservationType.INCIDENT_TYPE,
            "structure_fire",
            at=at,
            talkgroup=talkgroup,
            capture=capture,
        ),
        context(
            "dispatch",
            ObservationType.DISPATCH,
            "respond",
            at=at,
            talkgroup=talkgroup,
            confidence=0.9,
            epistemic=EpistemicStatus.DISPATCHED,
            capture=capture,
        ),
    ]


def test_calls_across_talkgroups_and_hours_form_one_incident() -> None:
    engine = CorrelationEngine()
    first = engine.correlate(incident_batch("1400 H St NE"), [])
    assert first is not None
    later = incident_batch(
        "1400 H St NE",
        at=BASE + timedelta(hours=1),
        talkgroup=700,
        capture="call-b",
    )
    second = engine.correlate(later, [first])
    assert second is not None
    assert second.incident.incident_id == first.incident.incident_id
    assert second.talkgroup_ids == {101, 700}


def test_similar_incidents_at_distinct_locations_stay_separate() -> None:
    engine = CorrelationEngine()
    first = engine.correlate(incident_batch("1400 H St NE"), [])
    assert first is not None
    second = engine.correlate(
        incident_batch("1200 K St NW", capture="call-b"), [first]
    )
    assert second is not None
    assert second.incident.incident_id != first.incident.incident_id


def test_weak_dispatch_stays_candidate_until_strong_on_scene_evidence() -> None:
    engine = CorrelationEngine()
    first = engine.correlate(
        [
            context(
                "dispatch",
                ObservationType.DISPATCH,
                "respond",
                confidence=0.5,
                epistemic=EpistemicStatus.DISPATCHED,
            )
        ],
        [],
    )
    assert first is not None
    assert first.incident.state == IncidentState.CANDIDATE
    on_scene = context(
        "onscene",
        ObservationType.ON_SCENE,
        "smoke showing",
        at=BASE + timedelta(minutes=3),
        confidence=0.95,
        epistemic=EpistemicStatus.ON_SCENE_REPORTED,
        capture="call-b",
    )
    promoted = engine.correlate([on_scene], [first])
    assert promoted is not None
    assert promoted.incident.state == IncidentState.PUBLISHABLE


def test_cancelled_candidate_closes_without_losing_evidence() -> None:
    engine = CorrelationEngine()
    first = engine.correlate(incident_batch("1200 K St NW", talkgroup=700), [])
    assert first is not None
    cancellation = context(
        "cancel",
        ObservationType.CANCELLATION,
        "unfounded",
        at=BASE + timedelta(minutes=5),
        epistemic=EpistemicStatus.CONFIRMED,
        talkgroup=700,
        capture="call-b",
    )
    closed = engine.correlate([cancellation], [first])
    assert closed is not None
    assert closed.incident.state == IncidentState.RESOLVED
    assert len(closed.contexts) == len(first.contexts) + 1


def test_published_correction_is_append_only() -> None:
    engine = CorrelationEngine()
    first = engine.correlate(incident_batch("1400 H St NE", talkgroup=700), [])
    assert first is not None
    published = replace(first, published=True)
    correction = context(
        "correction",
        ObservationType.CORRECTION,
        "correction",
        at=BASE + timedelta(minutes=5),
        epistemic=EpistemicStatus.CORRECTED,
        talkgroup=700,
        capture="call-b",
    )
    corrected = engine.correlate([correction], [published])
    assert corrected is not None
    assert corrected.incident.state == IncidentState.CORRECTED
    assert set(first.incident.observation_ids).issubset(corrected.incident.observation_ids)
    assert corrected.history[-1].prior_state == first.incident.state


def test_duplicate_observation_is_linked_once() -> None:
    engine = CorrelationEngine()
    batch = incident_batch("1400 H St NE")
    first = engine.correlate(batch, [])
    assert first is not None
    retry = engine.correlate(batch, [first])
    assert retry is not None
    assert retry.incident.observation_ids == first.incident.observation_ids


def test_mandatory_sensitivity_cannot_be_erased() -> None:
    engine = CorrelationEngine()
    sensitive = [
        *incident_batch("1400 H St NE"),
        context(
            "medical",
            ObservationType.PRIVACY,
            "patient",
            sensitivity=(SensitivityLabel.MEDICAL,),
        )
    ]
    first = engine.correlate(sensitive, [])
    assert first is not None and first.incident.state == IncidentState.SUPPRESSED
    # Later benign observations remain attached and cannot erase suppression.
    benign = engine.correlate(
        incident_batch("1400 H St NE", capture="call-b"), [first]
    )
    assert benign is not None
    assert benign.incident.incident_id == first.incident.incident_id
    assert benign.incident.state == IncidentState.SUPPRESSED
    assert SensitivityLabel.MEDICAL in benign.incident.sensitivity


def test_replay_is_deterministic() -> None:
    engine = CorrelationEngine()
    contexts = incident_batch("1400 H St NE") + incident_batch(
        "1400 H St NE", at=BASE + timedelta(minutes=10), capture="call-b", talkgroup=700
    )
    first = engine.replay(contexts)
    second = engine.replay(list(reversed(contexts)))
    assert [item.incident.model_dump() for item in first] == [
        item.incident.model_dump() for item in second
    ]
