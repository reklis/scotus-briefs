from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from ragchew.scotus.contracts import (
    LegalObservationType,
    LegalStatus,
    ScotusCaseStatus,
)
from ragchew.scotus.reader_contracts import (
    ActionEffect,
    CanonicalAction,
    CanonicalActionSlot,
    CanonicalActorRole,
    ReaderArgumentPacket,
    ReaderClaim,
    ReaderGuidePlan,
    ReaderSectionPacket,
    ReaderSectionPurpose,
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000001")
ARGUMENT_ID = UUID("20000000-0000-4000-8000-000000000001")
CLAIM_ID = UUID("30000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 8, tzinfo=UTC)


def claim(
    *,
    claim_id: UUID = CLAIM_ID,
    argument_id: UUID | None = None,
    observation_type: LegalObservationType = LegalObservationType.ORDER,
    legal_status: LegalStatus = LegalStatus.COURT_ORDERED,
    value: str = "The Supreme Court sent the case back to the lower court.",
) -> ReaderClaim:
    return ReaderClaim(
        claim_id=claim_id,
        case_id=CASE_ID,
        observation_type=observation_type,
        legal_status=legal_status,
        public_value=value,
        argument_id=argument_id,
    )


def section() -> ReaderSectionPacket:
    item = claim()
    return ReaderSectionPacket(
        heading="What the Supreme Court did",
        purpose=ReaderSectionPurpose.COURT_ACTION,
        reader_purpose="Explain the supported result in ordinary words.",
        allowed_observation_types=(LegalObservationType.ORDER,),
        required_observation_types=(LegalObservationType.ORDER,),
        allowed_legal_statuses=(LegalStatus.COURT_ORDERED,),
        required_legal_statuses=(LegalStatus.COURT_ORDERED,),
        claims=(item,),
        action_slots=(
            CanonicalActionSlot(
                actor_role=CanonicalActorRole.SUPREME_COURT,
                actor="The Supreme Court",
                action=CanonicalAction.REMAND,
                operative_object="the case",
                effect=ActionEffect.FINAL,
                claim_ids=(item.claim_id,),
            ),
        ),
    )


def argument(*, argument_id: UUID = ARGUMENT_ID, sequence: int = 1) -> ReaderArgumentPacket:
    item = claim(
        claim_id=argument_id,
        argument_id=argument_id,
        observation_type=LegalObservationType.JUSTICE_QUESTION,
        legal_status=LegalStatus.QUESTIONED,
        value="A justice asked how the rule would apply to small organizations.",
    )
    return ReaderArgumentPacket(
        argument_id=argument_id,
        sequence=sequence,
        argument_date=NOW + timedelta(days=sequence),
        heading="What the justices asked",
        reader_purpose="Describe questions from this argument session only.",
        allowed_observation_types=(LegalObservationType.JUSTICE_QUESTION,),
        required_observation_types=(LegalObservationType.JUSTICE_QUESTION,),
        allowed_legal_statuses=(LegalStatus.QUESTIONED,),
        required_legal_statuses=(LegalStatus.QUESTIONED,),
        claims=(item,),
    )


def test_reader_guide_plan_round_trips_with_strict_bounded_packets() -> None:
    plan = ReaderGuidePlan(
        case_id=CASE_ID,
        case_status=ScotusCaseStatus.ORDER_ISSUED,
        sections=(section(),),
        arguments=(argument(),),
    )

    assert ReaderGuidePlan.model_validate_json(plan.model_dump_json()) == plan
    assert plan.sections[0].action_slots[0].action is CanonicalAction.REMAND

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ReaderGuidePlan.model_validate({**plan.model_dump(), "prompt": "private"})


def test_section_packet_fails_closed_for_missing_or_cross_packet_support() -> None:
    payload = section().model_dump()
    payload["required_observation_types"] = (LegalObservationType.HOLDING,)
    payload["allowed_observation_types"] = (
        LegalObservationType.ORDER,
        LegalObservationType.HOLDING,
    )
    with pytest.raises(ValidationError, match="does not support"):
        ReaderSectionPacket.model_validate(payload)

    payload = section().model_dump()
    payload["action_slots"] = (
        section().action_slots[0].model_copy(
            update={"claim_ids": (UUID("30000000-0000-4000-8000-000000000099"),)}
        ),
    )
    with pytest.raises(ValidationError, match="same packet"):
        ReaderSectionPacket.model_validate(payload)


def test_argument_packets_cannot_mix_sessions_or_break_chronological_order() -> None:
    other_argument = UUID("20000000-0000-4000-8000-000000000002")
    payload = argument().model_dump()
    payload["claims"] = (argument(argument_id=other_argument).claims[0],)
    with pytest.raises(ValidationError, match="own session"):
        ReaderArgumentPacket.model_validate(payload)

    first = argument(sequence=1)
    second = argument(argument_id=other_argument, sequence=2).model_copy(
        update={"argument_date": NOW}
    )
    with pytest.raises(ValidationError, match="chronological"):
        ReaderGuidePlan(
            case_id=CASE_ID,
            case_status=ScotusCaseStatus.REARGUED,
            sections=(section(),),
            arguments=(first, second),
        )


def test_reader_plan_rejects_cross_case_claims() -> None:
    foreign = section().model_copy(
        update={
            "claims": (
                section().claims[0].model_copy(
                    update={"case_id": UUID("10000000-0000-4000-8000-000000000099")}
                ),
            )
        }
    )
    with pytest.raises(ValidationError, match="planned case"):
        ReaderGuidePlan(
            case_id=CASE_ID,
            case_status=ScotusCaseStatus.ORDER_ISSUED,
            sections=(foreign,),
        )


def test_reader_packets_enforce_claim_and_character_bounds() -> None:
    items = tuple(
        claim(claim_id=UUID(int=index + 1), value="x") for index in range(17)
    )
    payload = section().model_dump()
    payload["claims"] = items
    with pytest.raises(ValidationError):
        ReaderSectionPacket.model_validate(payload)

    with pytest.raises(ValidationError, match="legal status"):
        claim(legal_status=LegalStatus.COURT_HELD)
