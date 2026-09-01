from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ragchew.config import MvpConfig
from ragchew.contracts import (
    ApprovedPublicClaim,
    Certainty,
    EpistemicStatus,
    IncidentState,
    ObservationType,
    SensitivityLabel,
)
from ragchew.correlation.engine import CorrelationEngine
from ragchew.generation import (
    GroundedText,
    StoryDraft,
    StoryValidationError,
    build_story_revision,
    validate_story,
)
from ragchew.policy import PolicyDecision, PublicationPolicy
from tests.test_correlation import BASE, context, incident_batch


def policy() -> PublicationPolicy:
    config = MvpConfig.from_yaml("config/mvp.yaml")
    return PublicationPolicy(config.publication)


def confirmed_snapshot(
    *,
    incident_type: str = "structure_fire",
    location: str = "1456 H St NE apartment 4B",
    sensitive: bool = False,
):
    engine = CorrelationEngine()
    batch = incident_batch(location, talkgroup=700)
    type_context = batch[1]
    batch[1] = type_context.__class__(
        type_context.observation.model_copy(
            update={"raw_value": incident_type, "normalized_value": incident_type}
        ),
        type_context.talkgroup_id,
        type_context.talkgroup_name,
    )
    if sensitive:
        batch.append(
            context(
                "sensitive",
                ObservationType.PRIVACY,
                "patient",
                talkgroup=700,
                sensitivity=(SensitivityLabel.MEDICAL,),
            )
        )
    first = engine.correlate(batch, [])
    assert first is not None
    on_scene = context(
        "on-scene",
        ObservationType.ON_SCENE,
        "smoke showing",
        at=BASE + timedelta(minutes=3),
        talkgroup=700,
        confidence=0.95,
        epistemic=EpistemicStatus.ON_SCENE_REPORTED,
        capture="call-b",
    )
    snapshot = engine.correlate([on_scene], [first])
    assert snapshot is not None
    return snapshot


def test_confirmed_allowlisted_fire_is_eligible_and_location_is_generalized() -> None:
    decision = policy().evaluate(confirmed_snapshot(), BASE + timedelta(minutes=10))
    assert decision.eligible
    values = [claim.public_value for claim in decision.approved_claims]
    assert any(value == "1400 block of H St NE" for value in values)
    assert all("apartment" not in value.lower() for value in values)
    assert all(claim.source_observation_ids for claim in decision.approved_claims)


def test_unknown_category_is_default_denied() -> None:
    decision = policy().evaluate(confirmed_snapshot(incident_type="unknown_event"))
    assert not decision.eligible
    assert "incident category is not allowlisted" in decision.reasons
    assert decision.approved_claims == ()


def test_sensitive_mixed_incident_is_suppressed_from_all_claims() -> None:
    decision = policy().evaluate(confirmed_snapshot(sensitive=True))
    assert not decision.eligible
    assert "mandatory sensitive category" in decision.reasons
    assert decision.approved_claims == ()


def test_unconfirmed_cancelled_report_is_not_publishable() -> None:
    engine = CorrelationEngine()
    first = engine.correlate(incident_batch("1200 K St NW", talkgroup=700), [])
    assert first is not None
    cancelled = engine.correlate(
        [
            context(
                "cancel",
                ObservationType.CANCELLATION,
                "unfounded",
                at=BASE + timedelta(minutes=5),
                talkgroup=700,
                epistemic=EpistemicStatus.CONFIRMED,
                capture="call-b",
            )
        ],
        [first],
    )
    assert cancelled is not None
    decision = policy().evaluate(cancelled)
    assert not decision.eligible
    assert "unconfirmed report was cancelled or unfounded" in decision.reasons


def claim(certainty: Certainty, value: str = "1400 block of H St NE") -> ApprovedPublicClaim:
    return ApprovedPublicClaim(
        incident_id=uuid4(),
        claim_type=ObservationType.DISPATCH,
        public_value=value,
        certainty=certainty,
        source_observation_ids=(uuid4(),),
        approved_at=datetime.now(UTC),
        policy_version="test",
    )


def test_generator_rejects_unknown_claim_and_unsupported_outcome() -> None:
    approved = claim(Certainty.CONFIRMED)
    unknown = uuid4()
    with pytest.raises(StoryValidationError, match="unknown claim"):
        validate_story(
            StoryDraft(
                title=GroundedText(text="Fire reported", claim_ids=(unknown,)),
                summary=GroundedText(text="Crews reported smoke.", claim_ids=(approved.claim_id,)),
                status=IncidentState.ACTIVE,
            ),
            (approved,),
        )
    with pytest.raises(StoryValidationError, match="outcome"):
        validate_story(
            StoryDraft(
                title=GroundedText(text="Fire reported", claim_ids=(approved.claim_id,)),
                summary=GroundedText(
                    text="Two people injured in the fire.", claim_ids=(approved.claim_id,)
                ),
                status=IncidentState.ACTIVE,
            ),
            (approved,),
        )


def test_dispatch_only_language_cannot_be_overstated() -> None:
    dispatched = claim(Certainty.DISPATCHED)
    with pytest.raises(StoryValidationError, match="overstates"):
        validate_story(
            StoryDraft(
                title=GroundedText(
                    text="Building is on fire", claim_ids=(dispatched.claim_id,)
                ),
                summary=GroundedText(
                    text="The fire was confirmed.", claim_ids=(dispatched.claim_id,)
                ),
                status=IncidentState.ACTIVE,
            ),
            (dispatched,),
        )


def test_valid_grounded_story_builds_stable_story_identity() -> None:
    snapshot = confirmed_snapshot(location="1400 H St NE")
    decision = policy().evaluate(snapshot)
    assert decision.eligible
    ids = tuple(claim.claim_id for claim in decision.approved_claims)
    draft = StoryDraft(
        title=GroundedText(text="Crews reported a fire on H Street NE", claim_ids=ids),
        summary=GroundedText(
            text="Radio traffic indicated crews reported smoke after responding to H Street NE.",
            claim_ids=ids,
        ),
        status=IncidentState.ACTIVE,
    )
    first = build_story_revision(decision, draft, "llm-test", BASE)
    second = build_story_revision(decision, draft, "llm-test", BASE + timedelta(hours=1))
    assert first.story_id == second.story_id
    assert first.revision_id != second.revision_id
    assert set(first.claim_ids) == set(ids)


def test_ineligible_decision_cannot_generate() -> None:
    decision = PolicyDecision(
        decision_id=uuid4(),
        incident_id=uuid4(),
        eligible=False,
        policy_version="test",
        reasons=("denied",),
        created_at=datetime.now(UTC),
    )
    draft = StoryDraft(
        title=GroundedText(text="Reported event", claim_ids=(uuid4(),)),
        summary=GroundedText(text="Responders were dispatched.", claim_ids=(uuid4(),)),
        status=IncidentState.CANDIDATE,
    )
    with pytest.raises(StoryValidationError, match="ineligible"):
        build_story_revision(decision, draft, "llm-test")
