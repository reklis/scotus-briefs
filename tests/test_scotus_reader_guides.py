from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from ragchew.scotus.briefs import (
    BriefCandidate,
    BriefValidationError,
    CaseArgumentSession,
    LegalBriefDraft,
)
from ragchew.scotus.contracts import (
    AdvocateRole,
    BriefMaturity,
    LegalCertainty,
    LegalObservationType,
    LegalStatus,
    ScotusApprovedClaim,
    ScotusCaseStatus,
)
from ragchew.scotus.reader_guides import (
    ActionEffect,
    CanonicalAction,
    CanonicalActorRole,
    CompactReaderGuideWriter,
    ProcessLocalFieldDiagnostic,
    ReaderGuideFieldKind,
    ReaderGuideFieldPath,
    ReaderGuidePlanner,
    ReaderGuidePlannerLimits,
    ReaderGuidePlanningError,
    ReaderGuidePurpose,
    ReaderGuideWritingError,
    TargetedReaderGuideRepairer,
    build_canonical_action_slots,
    compact_reader_guide_payload,
)

NOW = datetime(2026, 9, 8, tzinfo=UTC)
CASE_ID = uuid4()
ARGUMENT_ID = uuid4()
REARGUMENT_ID = uuid4()

_STATUS = {
    LegalObservationType.CASE_BACKGROUND: LegalStatus.DESCRIBED,
    LegalObservationType.PROCEDURAL_POSTURE: LegalStatus.DESCRIBED,
    LegalObservationType.QUESTION_PRESENTED: LegalStatus.DESCRIBED,
    LegalObservationType.ADVOCATE_CONTENTION: LegalStatus.ASSERTED,
    LegalObservationType.JUSTICE_QUESTION: LegalStatus.QUESTIONED,
    LegalObservationType.ANSWER: LegalStatus.ANSWERED,
    LegalObservationType.CONCESSION: LegalStatus.CONCEDED,
    LegalObservationType.DISPUTED_PREMISE: LegalStatus.DISPUTED,
    LegalObservationType.AUTHORITY_CITATION: LegalStatus.DESCRIBED,
    LegalObservationType.DOCTRINAL_THEME: LegalStatus.DESCRIBED,
    LegalObservationType.REQUESTED_DISPOSITION: LegalStatus.REQUESTED,
    LegalObservationType.LOWER_COURT_ACTION: LegalStatus.LOWER_COURT_HELD,
    LegalObservationType.ORDER: LegalStatus.COURT_ORDERED,
    LegalObservationType.HOLDING: LegalStatus.COURT_HELD,
}


def claim(
    observation_type: LegalObservationType,
    value: str,
    *,
    argument_id: UUID | None = None,
    attribution: str | None = None,
    source: str = "Official docket",
) -> ScotusApprovedClaim:
    return ScotusApprovedClaim(
        case_id=CASE_ID,
        argument_id=argument_id,
        observation_type=observation_type,
        legal_status=_STATUS[observation_type],
        certainty=LegalCertainty.DIRECT,
        public_value=value,
        attribution=attribution,
        official_url="https://www.supremecourt.gov/docket/docketfiles/html/public/25-1.html",
        public_source_label=source,
        page_label="page 3",
        source_observation_ids=(uuid4(),),
        approved_at=NOW,
        policy_version="test-v1",
    )


def session(
    argument_id: UUID, date: datetime, sequence: int, *, reargument: bool = False
) -> CaseArgumentSession:
    return CaseArgumentSession(
        argument_id=argument_id,
        argument_date=date,
        sequence=sequence,
        reargument=reargument,
        official_detail_url=f"https://www.supremecourt.gov/oral_arguments/{argument_id}",
        official_transcript_url=f"https://www.supremecourt.gov/transcripts/{argument_id}.pdf",
    )


def candidate(
    *sessions: CaseArgumentSession, status: ScotusCaseStatus | None = None
) -> BriefCandidate:
    return BriefCandidate(
        case_id=CASE_ID,
        argument_id=sessions[0].argument_id if sessions else None,
        caption="People v. Agency",
        primary_docket="25-1",
        case_status=status or (ScotusCaseStatus.ARGUED if sessions else ScotusCaseStatus.DECIDED),
        official_transcript_complete=bool(sessions),
        parser_complete=True,
        privacy_blocking_failure=False,
        argument_sessions=tuple(sessions),
        observations=(),
        document_urls={},
        evaluated_at=NOW,
    )


def argued_claims(*, second_session: bool = False) -> tuple[ScotusApprovedClaim, ...]:
    values = [
        claim(
            LegalObservationType.CASE_BACKGROUND,
            "People challenge an agency rule governing access to a public benefit.",
            argument_id=ARGUMENT_ID,
        ),
        claim(
            LegalObservationType.PROCEDURAL_POSTURE,
            "The appeals court upheld the agency rule before the case reached the Supreme Court.",
            argument_id=ARGUMENT_ID,
        ),
        claim(
            LegalObservationType.LOWER_COURT_ACTION,
            "The appeals court affirmed the judgment for the agency.",
            argument_id=ARGUMENT_ID,
        ),
        claim(
            LegalObservationType.QUESTION_PRESENTED,
            "Whether Congress gave the agency power to adopt the challenged rule.",
            argument_id=ARGUMENT_ID,
        ),
        claim(
            LegalObservationType.ADVOCATE_CONTENTION,
            "The people argued that Congress did not give the agency that power.",
            argument_id=ARGUMENT_ID,
            attribution="Counsel for petitioner",
        ),
        claim(
            LegalObservationType.ADVOCATE_CONTENTION,
            "The agency argued that the statute permits its rule.",
            argument_id=ARGUMENT_ID,
            attribution="Counsel for respondent",
        ),
        claim(
            LegalObservationType.ADVOCATE_CONTENTION,
            "Counsel explained how the rule operates without identifying a side.",
            argument_id=ARGUMENT_ID,
            attribution="Alex Smith",
        ),
        claim(
            LegalObservationType.JUSTICE_QUESTION,
            "A justice asked how the rule fits the words Congress enacted.",
            argument_id=ARGUMENT_ID,
        ),
        claim(
            LegalObservationType.REQUESTED_DISPOSITION,
            "The people asked the Supreme Court to reverse the judgment and send the case back.",
            argument_id=ARGUMENT_ID,
            attribution="Counsel for petitioner",
        ),
    ]
    if second_session:
        values.extend(
            [
                claim(
                    LegalObservationType.ADVOCATE_CONTENTION,
                    "On reargument, the people focused on the statute's practical limit.",
                    argument_id=REARGUMENT_ID,
                    attribution="Counsel for petitioner",
                ),
                claim(
                    LegalObservationType.ADVOCATE_CONTENTION,
                    "On reargument, the agency defended the same reading of the statute.",
                    argument_id=REARGUMENT_ID,
                    attribution="Counsel for respondent",
                ),
                claim(
                    LegalObservationType.JUSTICE_QUESTION,
                    "A justice asked what had changed since the first argument.",
                    argument_id=REARGUMENT_ID,
                ),
            ]
        )
    return tuple(values)


def disposition_claims() -> tuple[ScotusApprovedClaim, ...]:
    return (
        claim(
            LegalObservationType.CASE_BACKGROUND,
            "An agency rule affects access to a public benefit.",
        ),
        claim(
            LegalObservationType.PROCEDURAL_POSTURE,
            "Docket 25-1 concerns an emergency challenge to a lower court order.",
        ),
        claim(
            LegalObservationType.LOWER_COURT_ACTION,
            "The district court blocked the agency rule.",
        ),
        claim(
            LegalObservationType.REQUESTED_DISPOSITION,
            "The agency asked the Supreme Court to stay the injunction pending appeal.",
            attribution="The agency",
        ),
        claim(
            LegalObservationType.QUESTION_PRESENTED,
            "Whether the lower court had power to block the agency rule.",
        ),
        claim(
            LegalObservationType.ORDER,
            "The Supreme Court vacated the judgment and remanded the case for further proceedings.",
        ),
        claim(
            LegalObservationType.DOCTRINAL_THEME,
            "The Court reasoned that the lower court used the wrong legal rule.",
        ),
        claim(
            LegalObservationType.DOCTRINAL_THEME,
            "Justice Example dissented because the agency had not shown urgent harm.",
            attribution="Justice Example, dissenting",
        ),
    )


def test_planner_is_bounded_deterministic_role_aware_and_nonduplicative() -> None:
    base = argued_claims()
    duplicates = tuple(
        item.model_copy(update={"claim_id": uuid4(), "source_observation_ids": (uuid4(),)})
        for item in base
        for _ in range(4)
    )
    noisy = (*reversed(base), *duplicates)
    limits = ReaderGuidePlannerLimits(
        max_claims_per_section=5,
        max_characters_per_section=2_000,
        max_claims_per_argument=9,
        max_characters_per_argument=3_000,
    )
    planner = ReaderGuidePlanner(limits)
    source = candidate(session(ARGUMENT_ID, NOW, 1))

    first = planner.plan(source, tuple(noisy), BriefMaturity.OFFICIAL_TRANSCRIPT)
    second = planner.plan(source, tuple(reversed(noisy)), BriefMaturity.OFFICIAL_TRANSCRIPT)

    assert first == second
    assert all(len(packet.claims) <= 5 for packet in first.sections)
    assert len(first.arguments[0].claims) <= 9
    assert all(
        len({item.public_value.casefold() for item in packet.claims}) == len(packet.claims)
        for packet in first.sections
    )
    positions = first.arguments[0].positions
    assert {item.role for item in positions if item.established} == {
        AdvocateRole.PETITIONER,
        AdvocateRole.RESPONDENT,
    }
    unknown = next(item for item in positions if not item.established)
    assert unknown.role is AdvocateRole.UNKNOWN
    assert unknown.attribution == "Alex Smith"
    side_section = next(
        item for item in first.sections if item.purpose is ReaderGuidePurpose.POSITIONS
    )
    assert all(
        item.legal_status
        in {
            LegalStatus.ASSERTED,
            LegalStatus.REQUESTED,
            LegalStatus.ANSWERED,
            LegalStatus.CONCEDED,
            LegalStatus.DISPUTED,
        }
        for item in side_section.claims
    )


def test_planner_fails_closed_when_required_purpose_is_unsupported() -> None:
    incomplete = tuple(
        item
        for item in argued_claims()
        if item.observation_type
        not in {
            LegalObservationType.QUESTION_PRESENTED,
            LegalObservationType.JUSTICE_QUESTION,
        }
    )
    with pytest.raises(ReaderGuidePlanningError) as caught:
        ReaderGuidePlanner().plan(
            candidate(session(ARGUMENT_ID, NOW, 1)),
            incomplete,
            BriefMaturity.OFFICIAL_TRANSCRIPT,
        )
    assert caught.value.safe_code == "unsupported_legal_issue"


def test_distinct_long_ledger_is_trimmed_to_total_model_context_bound() -> None:
    extra = tuple(
        claim(
            LegalObservationType.CASE_BACKGROUND,
            f"Additional supported background detail number {index} about the agency program.",
            argument_id=ARGUMENT_ID,
        )
        for index in range(20)
    )
    limits = ReaderGuidePlannerLimits(
        max_claims_per_section=10,
        max_characters_per_section=4_000,
        max_claims_per_argument=12,
        max_characters_per_argument=6_000,
        max_total_claims=15,
        max_total_characters=4_000,
    )
    plan = ReaderGuidePlanner(limits).plan(
        candidate(session(ARGUMENT_ID, NOW, 1)),
        (*argued_claims(), *extra),
        BriefMaturity.OFFICIAL_TRANSCRIPT,
    )
    serialized_claims = (
        *plan.summary_claims,
        *(item for section in plan.sections for item in section.claims),
        *(item for argument in plan.arguments for item in argument.claims),
    )
    assert len(serialized_claims) <= 15
    assert sum(len(item.public_value) for item in serialized_claims) <= 4_000
    assert {item.role for item in plan.arguments[0].positions if item.established} == {
        AdvocateRole.PETITIONER,
        AdvocateRole.RESPONDENT,
    }
    assert plan.arguments[0].justice_question_claim_ids


def test_unknown_role_answer_still_supports_an_argument_packet() -> None:
    source_claims = tuple(
        item
        for item in argued_claims()
        if item.observation_type
        not in {
            LegalObservationType.ADVOCATE_CONTENTION,
            LegalObservationType.REQUESTED_DISPOSITION,
        }
    )
    source_claims = (
        *source_claims,
        claim(
            LegalObservationType.ANSWER,
            "Counsel answered that the rule applies only to new applications.",
            argument_id=ARGUMENT_ID,
            attribution="Alex Smith",
        ),
    )
    plan = ReaderGuidePlanner().plan(
        candidate(session(ARGUMENT_ID, NOW, 1)),
        source_claims,
        BriefMaturity.OFFICIAL_TRANSCRIPT,
    )
    unknown = next(item for item in plan.arguments[0].positions if not item.established)
    assert unknown.attribution == "Alex Smith"


def test_required_claim_that_exceeds_character_bound_fails_before_writing() -> None:
    limits = ReaderGuidePlannerLimits(
        max_characters_per_section=25,
        max_characters_per_argument=100,
        max_total_characters=500,
    )
    with pytest.raises(ReaderGuidePlanningError) as caught:
        ReaderGuidePlanner(limits).plan(
            candidate(session(ARGUMENT_ID, NOW, 1)),
            argued_claims(),
            BriefMaturity.OFFICIAL_TRANSCRIPT,
        )
    assert caught.value.safe_code == "unsupported_background"


def test_action_slots_keep_actor_action_object_polarity_and_effect() -> None:
    source = (
        claim(
            LegalObservationType.REQUESTED_DISPOSITION,
            "The agency asked the Supreme Court not to vacate the judgment.",
            attribution="The agency",
        ),
        claim(
            LegalObservationType.LOWER_COURT_ACTION,
            "The district court blocked the rule.",
        ),
        claim(
            LegalObservationType.ORDER,
            "The Supreme Court stayed the injunction pending appeal.",
        ),
    )
    slots = build_canonical_action_slots(source)
    requested = next(
        item for item in slots if item.actor_role is CanonicalActorRole.REQUESTING_PARTY
    )
    lower = next(item for item in slots if item.actor_role is CanonicalActorRole.LOWER_COURT)
    supreme = next(item for item in slots if item.actor_role is CanonicalActorRole.SUPREME_COURT)

    assert (requested.action, requested.operative_object, requested.negated) == (
        CanonicalAction.VACATE,
        "judgment",
        True,
    )
    assert lower.action is CanonicalAction.BLOCK
    assert lower.operative_object == "rule"
    assert supreme.action is CanonicalAction.STAY
    assert supreme.operative_object == "injunction"
    assert supreme.effect is ActionEffect.INTERIM
    assert supreme.timing == "pending appeal"


def test_action_slots_separate_actors_and_ignore_noun_uses() -> None:
    assert not build_canonical_action_slots(
        (
            claim(
                LegalObservationType.QUESTION_PRESENTED,
                "Whether the lower court had power to block the agency rule.",
            ),
            claim(
                LegalObservationType.PROCEDURAL_POSTURE,
                "The challenge concerns a lower court order.",
            ),
        )
    )
    source = (
        claim(
            LegalObservationType.HOLDING,
            "The Supreme Court held that the District Court blocked the rule.",
        ),
        claim(
            LegalObservationType.ORDER,
            "The Court denied the application for a stay.",
        ),
    )
    slots = build_canonical_action_slots(source)

    assert any(
        item.action is CanonicalAction.HOLD and item.actor_role is CanonicalActorRole.SUPREME_COURT
        for item in slots
    )
    assert any(
        item.action is CanonicalAction.BLOCK and item.actor_role is CanonicalActorRole.LOWER_COURT
        for item in slots
    )
    assert any(item.action is CanonicalAction.DENY for item in slots)
    assert not any(item.action is CanonicalAction.STAY for item in slots)

    clauses = build_canonical_action_slots(
        (
            claim(
                LegalObservationType.HOLDING,
                "The Supreme Court did not reverse the judgment but remanded for further "
                "proceedings.",
            ),
            claim(
                LegalObservationType.ORDER,
                "The Supreme Court stayed the injunction pending appeal and dismissed the case.",
            ),
            claim(
                LegalObservationType.HOLDING,
                "The dissent would reverse the judgment.",
            ),
        )
    )
    reverse = next(item for item in clauses if item.action is CanonicalAction.REVERSE)
    remand = next(item for item in clauses if item.action is CanonicalAction.REMAND)
    stay = next(item for item in clauses if item.action is CanonicalAction.STAY)
    dismiss = next(item for item in clauses if item.action is CanonicalAction.DISMISS)
    assert reverse.negated
    assert not remand.negated
    assert remand.operative_object == "case"
    assert stay.effect is ActionEffect.INTERIM
    assert dismiss.effect is ActionEffect.FINAL
    assert len([item for item in clauses if item.action is CanonicalAction.REVERSE]) == 1


def test_planner_supplies_reviewed_term_guidance_to_sections_and_arguments() -> None:
    claims = tuple(
        item.model_copy(
            update={
                "public_value": (
                    "The petitioner said separation of powers limits which branch may remove "
                    "the official."
                )
            }
        )
        if item.observation_type is LegalObservationType.ADVOCATE_CONTENTION
        and item.attribution == "Counsel for petitioner"
        else item
        for item in argued_claims()
    )

    plan = ReaderGuidePlanner().plan(
        candidate(session(ARGUMENT_ID, NOW, 1)),
        claims,
        BriefMaturity.OFFICIAL_TRANSCRIPT,
    )

    guidance = (
        *(item for section in plan.sections for item in section.plain_language_guidance),
        *(item for argument in plan.arguments for item in argument.plain_language_guidance),
    )
    assert any("how government branches divide and limit their power" in item for item in guidance)
    payload = compact_reader_guide_payload(plan)
    assert any("terms" in argument for argument in payload["arguments"])


def test_reargument_packets_are_chronological_and_never_mix_sessions() -> None:
    source = candidate(
        session(REARGUMENT_ID, NOW + timedelta(days=20), 2, reargument=True),
        session(ARGUMENT_ID, NOW, 1),
        status=ScotusCaseStatus.REARGUED,
    )
    plan = ReaderGuidePlanner().plan(
        source, argued_claims(second_session=True), BriefMaturity.OFFICIAL_TRANSCRIPT
    )

    assert tuple(item.argument_id for item in plan.arguments) == (ARGUMENT_ID, REARGUMENT_ID)
    assert tuple(item.reargument for item in plan.arguments) == (False, True)
    for packet in plan.arguments:
        assert {item.argument_id for item in packet.claims} == {packet.argument_id}
        assert packet.justice_question_claim_ids
        assert {item.role for item in packet.positions if item.established} == {
            AdvocateRole.PETITIONER,
            AdvocateRole.RESPONDENT,
        }


def test_decided_after_argument_keeps_sessions_and_adds_disposition_sections() -> None:
    source = candidate(
        session(ARGUMENT_ID, NOW, 1),
        status=ScotusCaseStatus.DECIDED,
    )
    plan = ReaderGuidePlanner().plan(
        source,
        (*argued_claims(), *disposition_claims()),
        BriefMaturity.POST_OPINION,
    )
    purposes = {item.purpose for item in plan.sections}

    assert plan.arguments[0].argument_id == ARGUMENT_ID
    assert len(plan.sections) <= 8
    assert ReaderGuidePurpose.POSITIONS in purposes
    assert plan.arguments[0].justice_question_claim_ids
    assert ReaderGuidePurpose.COURT_ACTION in purposes
    assert ReaderGuidePurpose.COURT_REASONING in purposes
    next_step = next(
        item for item in plan.sections if item.purpose is ReaderGuidePurpose.NEXT_KNOWN_STEP
    )
    assert all(
        item.observation_type is not LegalObservationType.REQUESTED_DISPOSITION
        for item in next_step.claims
    )


def test_disposition_without_supported_reasoning_fails_closed() -> None:
    sparse = tuple(
        item
        for item in disposition_claims()
        if item.observation_type is not LegalObservationType.DOCTRINAL_THEME
    )
    with pytest.raises(ReaderGuidePlanningError) as caught:
        ReaderGuidePlanner().plan(
            candidate(status=ScotusCaseStatus.DECIDED),
            sparse,
            BriefMaturity.POST_OPINION,
        )
    assert caught.value.safe_code == "unsupported_court_reasoning"


def test_controlling_action_is_not_discarded_when_it_mentions_a_dissent() -> None:
    claims = tuple(
        item.model_copy(
            update={
                "public_value": (
                    "The Supreme Court vacated the judgment and remanded the case, over a "
                    "dissenting opinion."
                )
            }
        )
        if item.observation_type is LegalObservationType.ORDER
        else item
        for item in disposition_claims()
    )

    plan = ReaderGuidePlanner().plan(
        candidate(status=ScotusCaseStatus.DECIDED),
        claims,
        BriefMaturity.POST_OPINION,
    )

    action = next(item for item in plan.sections if item.purpose is ReaderGuidePurpose.COURT_ACTION)
    assert {slot.action for slot in action.action_slots} == {
        CanonicalAction.VACATE,
        CanonicalAction.REMAND,
    }


def test_sparse_disposition_and_separate_opinion_get_distinct_packets() -> None:
    plan = ReaderGuidePlanner().plan(
        candidate(status=ScotusCaseStatus.DECIDED),
        disposition_claims(),
        BriefMaturity.POST_OPINION,
    )
    purposes = tuple(item.purpose for item in plan.sections)
    assert purposes[:5] == (
        ReaderGuidePurpose.BACKGROUND,
        ReaderGuidePurpose.PROCEDURAL_PATH,
        ReaderGuidePurpose.LEGAL_ISSUE,
        ReaderGuidePurpose.COURT_ACTION,
        ReaderGuidePurpose.COURT_REASONING,
    )
    assert tuple(item.heading for item in plan.sections[:5]) == (
        "What this case is about",
        "Why this case reached the Court",
        "The legal issue",
        "What the Supreme Court did",
        "Why the Court did it",
    )
    assert ReaderGuidePurpose.SEPARATE_OPINIONS in purposes
    action = next(item for item in plan.sections if item.purpose is ReaderGuidePurpose.COURT_ACTION)
    assert {slot.action for slot in action.action_slots} == {
        CanonicalAction.VACATE,
        CanonicalAction.REMAND,
    }
    assert all("dissent" not in (item.attribution or "").casefold() for item in action.claims)
    assert not plan.arguments


def test_compact_writer_cannot_choose_identity_order_sessions_or_claim_ids() -> None:
    plan = ReaderGuidePlanner().plan(
        candidate(session(ARGUMENT_ID, NOW, 1)),
        argued_claims(),
        BriefMaturity.OFFICIAL_TRANSCRIPT,
    )
    captured: list[dict[str, object]] = []

    def execute(request: dict[str, object]) -> dict[str, object]:
        captured.append(request)
        return {
            "dek": "People and an agency disagree about the power Congress gave the agency.",
            "section_paragraphs": [
                f"Everyday explanation {index}." for index in range(len(plan.sections))
            ],
            "argument_paragraphs": [
                ["The people explained their reading.", "The justices tested both readings."]
            ],
        }

    draft = CompactReaderGuideWriter("local-test", execute).generate(plan)
    payload = json.loads(captured[0]["messages"][1]["content"])  # type: ignore[index]

    assert draft.title == plan.caption
    assert draft.title_claim_ids == plan.title_claim_ids
    assert tuple(item.heading for item in draft.sections) == tuple(
        item.heading for item in plan.sections
    )
    assert tuple(item.claim_ids for item in draft.sections) == tuple(
        tuple(claim.claim_id for claim in item.claims) for item in plan.sections
    )
    assert draft.argument_analyses[0].argument_id == ARGUMENT_ID
    assert draft.argument_analyses[0].heading == "Argument on 2026-09-08"
    serialized = captured[0]["messages"][1]["content"]  # type: ignore[index]
    assert plan.caption not in serialized
    assert plan.primary_docket not in serialized
    assert "official_url" not in serialized
    assert "page_label" not in serialized
    assert "case_status" not in serialized
    assert "heading" not in payload["sections"][0]


def test_targeted_repair_sends_one_field_and_preserves_every_valid_byte() -> None:
    plan = ReaderGuidePlanner().plan(
        candidate(session(ARGUMENT_ID, NOW, 1)),
        argued_claims(),
        BriefMaturity.OFFICIAL_TRANSCRIPT,
    )
    initial = CompactReaderGuideWriter(
        "local-test",
        lambda request: {
            "dek": "The dispute concerns the agency's jurisdiction.",
            "section_paragraphs": [
                f"Valid section paragraph {index}." for index in range(len(plan.sections))
            ],
            "argument_paragraphs": [["Valid side paragraph.", "Valid question paragraph."]],
        },
    ).generate(plan)
    path = ReaderGuideFieldPath(kind=ReaderGuideFieldKind.DEK)
    diagnostic = ProcessLocalFieldDiagnostic(
        path=path,
        safe_code="unexplained_legal_term_jurisdiction",
        rule="A necessary legal term needs an immediate case-specific explanation.",
        offending_term="jurisdiction",
        required_transformation="Explain that jurisdiction means a court's power to hear the case.",
    )
    captured: list[dict[str, object]] = []
    validation_calls: list[str] = []

    def execute(request: dict[str, object]) -> dict[str, object]:
        captured.append(request)
        return {"text": "The dispute concerns which court has the power to hear the people's case."}

    repaired = TargetedReaderGuideRepairer("local-test", execute).repair(
        plan,
        initial,
        diagnostic,
        validate_field=lambda text, ids: validation_calls.append(f"field:{len(ids)}:{text}"),
        validate_guide=lambda draft: validation_calls.append(f"guide:{draft.dek}"),
    )
    request_payload = json.loads(captured[0]["messages"][1]["content"])  # type: ignore[index]

    assert repaired.dek != initial.dek
    assert repaired.title == initial.title
    assert repaired.title_claim_ids == initial.title_claim_ids
    assert repaired.dek_claim_ids == initial.dek_claim_ids
    assert repaired.sections == initial.sections
    assert repaired.argument_analyses == initial.argument_analyses
    assert request_payload["rejected_text"] == initial.dek
    assert set(request_payload) == {
        "field_path",
        "rejected_text",
        "support_packet",
        "diagnostic",
        "fixed_claim_ids",
    }
    assert "Valid section paragraph" not in captured[0]["messages"][1]["content"]  # type: ignore[index]
    assert [item.split(":", 1)[0] for item in validation_calls] == ["field", "guide"]
    assert repr(diagnostic) == (
        "ProcessLocalFieldDiagnostic(safe_code='unexplained_legal_term_jurisdiction')"
    )


def test_repair_returns_private_rejected_draft_for_a_distinct_bounded_correction() -> None:
    plan = ReaderGuidePlanner().plan(
        candidate(session(ARGUMENT_ID, NOW, 1)),
        argued_claims(),
        BriefMaturity.OFFICIAL_TRANSCRIPT,
    )
    original = CompactReaderGuideWriter(
        "local-test",
        lambda request: {
            "dek": "Original summary.",
            "section_paragraphs": ["Original paragraph."] * len(plan.sections),
            "argument_paragraphs": [["Original side.", "Original questions."]],
        },
    ).generate(plan)
    path = ReaderGuideFieldPath(kind=ReaderGuideFieldKind.DEK)
    diagnostic = ProcessLocalFieldDiagnostic(
        path=path,
        safe_code="unexplained_legal_term_jurisdiction",
        rule="Explain the legal term.",
        offending_term="jurisdiction",
        required_transformation="Explain which court has power to hear the case.",
    )

    with pytest.raises(BriefValidationError) as caught:
        TargetedReaderGuideRepairer(
            "local-test",
            lambda request: {"text": "A changed but still invalid summary."},
        ).repair(
            plan,
            original,
            diagnostic,
            validate_field=lambda text, ids: (_ for _ in ()).throw(
                BriefValidationError(
                    "the repair changed an action",
                    safe_code="unsupported_lower_court_action",
                )
            ),
            validate_guide=lambda draft: None,
        )

    assert caught.value.safe_code == "unsupported_lower_court_action"
    assert caught.value.draft is not None
    assert caught.value.draft.dek == "A changed but still invalid summary."
    assert caught.value.draft.sections == original.sections


def test_repair_rejects_tampered_citations_and_strict_schema_overflow() -> None:
    plan = ReaderGuidePlanner().plan(
        candidate(session(ARGUMENT_ID, NOW, 1)),
        argued_claims(),
        BriefMaturity.OFFICIAL_TRANSCRIPT,
    )
    original = CompactReaderGuideWriter(
        "local-test",
        lambda request: {
            "dek": "A valid summary.",
            "section_paragraphs": ["Original paragraph."] * len(plan.sections),
            "argument_paragraphs": [["Original side.", "Original questions."]],
        },
    ).generate(plan)
    path = ReaderGuideFieldPath(kind=ReaderGuideFieldKind.DEK)
    diagnostic = ProcessLocalFieldDiagnostic(
        path=path,
        safe_code="reader_language",
        rule="Use everyday words.",
        offending_term=None,
        required_transformation="Rewrite the summary in everyday words.",
    )
    tampered = original.model_copy(update={"dek_claim_ids": (uuid4(),)})
    repairer = TargetedReaderGuideRepairer(
        "local-test", lambda request: {"text": "A repaired summary."}
    )
    with pytest.raises(ReaderGuideWritingError, match="citations differ"):
        repairer.build_request(plan, tampered, diagnostic)

    overlong = TargetedReaderGuideRepairer("local-test", lambda request: {"text": "x" * 501})
    with pytest.raises(ReaderGuideWritingError) as caught:
        overlong.repair(
            plan,
            original,
            diagnostic,
            validate_field=lambda text, ids: None,
            validate_guide=lambda draft: None,
        )
    assert caught.value.safe_code == "invalid_repair_schema"

    extra = TargetedReaderGuideRepairer(
        "local-test", lambda request: {"text": "A repaired summary.", "private": "leak"}
    )
    with pytest.raises(ReaderGuideWritingError) as caught:
        extra.repair(
            plan,
            original,
            diagnostic,
            validate_field=lambda text, ids: None,
            validate_guide=lambda draft: None,
        )
    assert caught.value.safe_code == "invalid_repair_schema"


def test_repair_revalidates_complete_guide_and_does_not_mutate_failed_draft() -> None:
    plan = ReaderGuidePlanner().plan(
        candidate(session(ARGUMENT_ID, NOW, 1)),
        argued_claims(),
        BriefMaturity.OFFICIAL_TRANSCRIPT,
    )
    original = CompactReaderGuideWriter(
        "local-test",
        lambda request: {
            "dek": "A valid summary.",
            "section_paragraphs": ["Original paragraph."] * len(plan.sections),
            "argument_paragraphs": [["Original side.", "Original questions."]],
        },
    ).generate(plan)
    before = original.model_dump_json()
    path = ReaderGuideFieldPath(
        kind=ReaderGuideFieldKind.SECTION_PARAGRAPH,
        section_index=1,
        paragraph_index=0,
    )
    diagnostic = ProcessLocalFieldDiagnostic(
        path=path,
        safe_code="unsupported_action_role",
        rule="An action must retain its actor.",
        offending_term=None,
        required_transformation="Name the lower court and preserve its supported action.",
    )

    def reject_guide(draft: LegalBriefDraft) -> None:
        raise BriefValidationError("actor changed", safe_code="unsupported_action_role")

    with pytest.raises(BriefValidationError) as caught:
        TargetedReaderGuideRepairer(
            "local-test", lambda request: {"text": "The Supreme Court blocked the rule."}
        ).repair(
            plan,
            original,
            diagnostic,
            validate_field=lambda text, ids: None,
            validate_guide=reject_guide,
        )
    assert caught.value.safe_code == "unsupported_action_role"
    assert original.model_dump_json() == before
