import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from openai import omit
from pydantic import ValidationError

from ragchew.scotus.briefs import (
    BriefCandidate,
    BriefGenerationService,
    BriefPolicyError,
    BriefValidationError,
    CaseArgumentSession,
    DraftArgumentAnalysis,
    DraftSection,
    InMemoryBriefRevisionStore,
    LegalBriefDraft,
    OpenAILegalBriefGenerator,
    _unsupported_named_phrase,
    _validate_action_sentences,
    _validate_plain_language,
    disposition_only_brief_json_schema,
    evaluate_brief_candidate,
    simple_brief_json_schema,
    validate_brief_draft,
)
from ragchew.scotus.contracts import (
    BriefMaturity,
    LegalCertainty,
    LegalEvidenceRange,
    LegalObservation,
    LegalObservationType,
    LegalStatus,
    ScotusCaseStatus,
    ScotusDocumentKind,
    ScotusSensitivity,
)
from ragchew.scotus.reader_prose import ReaderProsePolicy, load_reader_prose_policy

NOW = datetime(2026, 8, 28, 2, tzinfo=UTC)


def observation(
    observation_type: LegalObservationType,
    status: LegalStatus,
    kind: ScotusDocumentKind,
    value: str,
    *,
    attribution: str | None = None,
    sensitivity: tuple[ScotusSensitivity, ...] = (),
    argument_id: UUID | None = None,
    document_id: UUID | None = None,
) -> LegalObservation:
    return LegalObservation(
        extraction_revision_id=uuid4(),
        case_id=CASE_ID,
        argument_id=argument_id or ARGUMENT_ID,
        observation_type=observation_type,
        legal_status=status,
        certainty=LegalCertainty.ATTRIBUTED,
        raw_value_private=value,
        normalized_value_private=value,
        attribution=attribution,
        confidence=0.95,
        evidence=(
            LegalEvidenceRange(
                document_revision_id=(
                    document_id
                    or (TRANSCRIPT_ID if kind is ScotusDocumentKind.TRANSCRIPT else DOCKET_ID)
                ),
                document_kind=kind,
                start_file_page=5,
                start_line=1,
                end_file_page=5,
                end_line=3,
                quote_private=value,
            ),
        ),
        sensitivity=sensitivity,
    )


CASE_ID = uuid4()
ARGUMENT_ID = uuid4()
TRANSCRIPT_ID = uuid4()
DOCKET_ID = uuid4()
SECOND_ARGUMENT_ID = uuid4()
SECOND_TRANSCRIPT_ID = uuid4()
OPINION_ID = uuid4()


def candidate(**overrides: object) -> BriefCandidate:
    observations = (
        observation(
            LegalObservationType.QUESTION_PRESENTED,
            LegalStatus.DESCRIBED,
            ScotusDocumentKind.DOCKET,
            "Whether the agency exceeded its statutory authority.",
        ),
        observation(
            LegalObservationType.ADVOCATE_CONTENTION,
            LegalStatus.ASSERTED,
            ScotusDocumentKind.TRANSCRIPT,
            "Counsel for petitioner argued that the statute does not authorize the action.",
            attribution="Counsel for petitioner",
        ),
        observation(
            LegalObservationType.JUSTICE_QUESTION,
            LegalStatus.QUESTIONED,
            ScotusDocumentKind.TRANSCRIPT,
            "Justice Kagan asked whether the proposed rule fit the statutory text.",
        ),
    )
    values: dict[str, object] = {
        "case_id": CASE_ID,
        "argument_id": ARGUMENT_ID,
        "caption": "Sripetch v. SEC",
        "primary_docket": "25-466",
        "case_status": ScotusCaseStatus.ARGUED,
        "official_transcript_complete": True,
        "parser_complete": True,
        "privacy_blocking_failure": False,
        "argument_sessions": (
            CaseArgumentSession(
                argument_id=ARGUMENT_ID,
                argument_date=NOW,
                sequence=1,
                reargument=False,
                official_detail_url="https://www.supremecourt.gov/argument",
                official_transcript_url="https://www.supremecourt.gov/transcript.pdf",
            ),
        ),
        "observations": observations,
        "document_urls": {
            TRANSCRIPT_ID: "https://www.supremecourt.gov/transcript.pdf",
            DOCKET_ID: "https://www.supremecourt.gov/docket.html",
        },
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return BriefCandidate(**values)  # type: ignore[arg-type]


class FakeGenerator:
    model_name = "brief-test"

    def __init__(self, mutate: str | None = None):
        self.mutate = mutate

    def generate(self, candidate, claims, maturity):  # type: ignore[no-untyped-def]
        claim_ids = tuple(claim.claim_id for claim in claims)
        paragraph = self.mutate or (
            "The case asks whether Congress gave the agency the power to take this action."
        )
        return LegalBriefDraft(
            title="Did the agency have the power to act?",
            title_claim_ids=(claim_ids[0],),
            dek="The two sides disagree about the power Congress gave the agency.",
            dek_claim_ids=claim_ids[:2],
            sections=(
                DraftSection(
                    heading="What this case is about",
                    paragraphs=(paragraph,),
                    claim_ids=claim_ids,
                ),
                DraftSection(
                    heading="What each side says",
                    paragraphs=(paragraph,),
                    claim_ids=claim_ids,
                ),
                DraftSection(
                    heading="What the justices asked",
                    paragraphs=(paragraph,),
                    claim_ids=claim_ids,
                ),
                DraftSection(
                    heading="Why it matters",
                    paragraphs=(paragraph,),
                    claim_ids=claim_ids,
                ),
            ),
            argument_analyses=tuple(
                DraftArgumentAnalysis(
                    argument_id=session.argument_id,
                    heading=(
                        "What changed in the later argument"
                        if session.reargument
                        else "What happened in the first argument"
                    ),
                    paragraphs=(
                        paragraph,
                        "The justices tested how that reasoning would work in practice.",
                    ),
                    claim_ids=tuple(
                        claim.claim_id
                        for claim in claims
                        if claim.argument_id == session.argument_id
                    ),
                )
                for session in candidate.argument_sessions
            ),
        )


def test_policy_requires_complete_transcript_parser_identity_and_sufficient_evidence() -> None:
    for update, reason in (
        ({"official_transcript_complete": False}, "transcript"),
        ({"parser_complete": False}, "parser"),
        ({"privacy_blocking_failure": True}, "privacy"),
        ({"caption": ""}, "identity"),
        ({"observations": ()}, "insufficient"),
    ):
        decision = evaluate_brief_candidate(candidate(**update), minimum_confidence=0.85)
        assert not decision.eligible
        assert any(reason in value for value in decision.reasons)


def disposition_candidate() -> BriefCandidate:
    observations = tuple(
        item.model_copy(update={"argument_id": None})
        for item in (
            observation(
                LegalObservationType.PROCEDURAL_POSTURE,
                LegalStatus.DESCRIBED,
                ScotusDocumentKind.DOCKET,
                "Docket 25A810 identifies Emergency Applicant v. Agency.",
            ),
            observation(
                LegalObservationType.CASE_BACKGROUND,
                LegalStatus.DESCRIBED,
                ScotusDocumentKind.OPINION,
                "Emergency Applicant challenged an Agency action.",
                document_id=OPINION_ID,
            ),
            observation(
                LegalObservationType.REQUESTED_DISPOSITION,
                LegalStatus.REQUESTED,
                ScotusDocumentKind.OPINION,
                "Emergency Applicant asked the Supreme Court to grant emergency relief.",
                attribution="Official opinion",
                document_id=OPINION_ID,
            ),
            observation(
                LegalObservationType.QUESTION_PRESENTED,
                LegalStatus.DESCRIBED,
                ScotusDocumentKind.OPINION,
                "The legal issue is whether emergency relief is available.",
                document_id=OPINION_ID,
            ),
            observation(
                LegalObservationType.DOCTRINAL_THEME,
                LegalStatus.DESCRIBED,
                ScotusDocumentKind.OPINION,
                "The Court reasoned that emergency relief was warranted.",
                document_id=OPINION_ID,
            ),
            observation(
                LegalObservationType.HOLDING,
                LegalStatus.COURT_HELD,
                ScotusDocumentKind.OPINION,
                "The Court granted the application.",
                document_id=OPINION_ID,
            ),
        )
    )
    return candidate(
        argument_id=None,
        caption="Emergency Applicant v. Agency",
        primary_docket="25A810",
        case_status=ScotusCaseStatus.DECIDED,
        official_transcript_complete=False,
        parser_complete=False,
        argument_sessions=(),
        observations=observations,
        document_urls={
            DOCKET_ID: "https://www.supremecourt.gov/docket.html",
            OPINION_ID: "https://www.supremecourt.gov/opinion.pdf",
        },
    )


def disposition_draft(claims, *, paragraph: str) -> LegalBriefDraft:  # type: ignore[no-untyped-def]
    def ids(*types: LegalObservationType) -> tuple[UUID, ...]:
        return tuple(claim.claim_id for claim in claims if claim.observation_type in types)

    background_ids = ids(LegalObservationType.CASE_BACKGROUND)
    path_ids = ids(
        LegalObservationType.PROCEDURAL_POSTURE,
        LegalObservationType.REQUESTED_DISPOSITION,
        LegalObservationType.LOWER_COURT_ACTION,
    )
    question_ids = ids(LegalObservationType.QUESTION_PRESENTED)
    doctrinal_ids = ids(LegalObservationType.DOCTRINAL_THEME)
    issue_ids = question_ids or doctrinal_ids[:1]
    reasoning_ids = tuple(claim_id for claim_id in doctrinal_ids if claim_id not in issue_ids)
    action_ids = ids(LegalObservationType.HOLDING, LegalObservationType.ORDER)
    docket_ids = ids(LegalObservationType.PROCEDURAL_POSTURE)
    return LegalBriefDraft(
        title="Emergency Applicant v. Agency",
        title_claim_ids=docket_ids,
        dek="Emergency Applicant challenged an Agency action.",
        dek_claim_ids=background_ids,
        sections=(
            DraftSection(
                heading="What this case is about",
                paragraphs=("Emergency Applicant challenged an Agency action.",),
                claim_ids=background_ids,
            ),
            DraftSection(
                heading="Why this case reached the Court",
                paragraphs=(
                    "Emergency Applicant asked the Supreme Court to grant emergency relief.",
                ),
                claim_ids=path_ids,
            ),
            DraftSection(
                heading="The legal issue",
                paragraphs=("The legal issue is whether emergency relief is available.",),
                claim_ids=issue_ids,
            ),
            DraftSection(
                heading="What the Supreme Court did",
                paragraphs=(paragraph,),
                claim_ids=(*action_ids, *path_ids),
            ),
            DraftSection(
                heading="Why the Court did it",
                paragraphs=("The Court reasoned that emergency relief was warranted.",),
                claim_ids=reasoning_ids,
            ),
        ),
        argument_analyses=(),
    )


def test_disposition_only_policy_rejects_two_facts_that_cannot_build_a_guide() -> None:
    source = disposition_candidate()
    two_fact_source = replace(
        source,
        observations=tuple(
            item
            for item in source.observations
            if item.observation_type
            in {LegalObservationType.PROCEDURAL_POSTURE, LegalObservationType.HOLDING}
        ),
    )
    decision = evaluate_brief_candidate(two_fact_source, minimum_confidence=0.85)
    assert not decision.eligible
    reasons = " ".join(decision.reasons)
    assert "case background" in reasons
    assert "procedural path" in reasons
    assert "controlling legal issue" in reasons
    assert "Court reasoning" in reasons


def test_disposition_only_policy_requires_docket_and_typed_court_action() -> None:
    source = disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    assert decision.eligible
    assert decision.maturity is BriefMaturity.POST_OPINION
    assert all(claim.argument_id is None for claim in decision.claims)

    without_docket = replace(
        source,
        observations=tuple(
            item
            for item in source.observations
            if all(
                evidence.document_kind is not ScotusDocumentKind.DOCKET
                for evidence in item.evidence
            )
        ),
    )
    rejected = evaluate_brief_candidate(without_docket, minimum_confidence=0.85)
    assert not rejected.eligible
    assert "docket evidence" in " ".join(rejected.reasons)


def test_disposition_name_guard_allows_only_evidence_derived_acronyms() -> None:
    support = "The Federal Communications Commission action is stayed."
    assert not _unsupported_named_phrase("The FCC action is stayed.", support, "Committee v. Brown")
    assert _unsupported_named_phrase("The FTC action is stayed.", support, "Committee v. Brown")
    assert not _unsupported_named_phrase(
        "The Government\u2019s action is stayed.",
        "The Government action is stayed.",
        "Committee v. Brown",
    )
    assert not _unsupported_named_phrase(
        "Trump Administration officials requested relief.",
        "Trump requested relief.",
        "Trump v. California",
    )
    assert _unsupported_named_phrase(
        "The Acme Corporation requested relief.",
        "The Government requested relief.",
        "Committee v. Brown",
    )


@pytest.mark.parametrize(
    ("paragraph", "safe_code"),
    [
        ("At oral argument, a justice asked about relief.", "invented_oral_argument"),
        (
            "Acme Corporation sought relief.",
            "unsupported_party_section_paragraph",
        ),
        ("The Court denied the application.", "unsupported_court_action"),
        ("The Court did not grant the application.", "unsupported_court_action"),
        ("More details may emerge later.", "unsupported_filler"),
    ],
)
def test_disposition_only_draft_rejects_invention(paragraph: str, safe_code: str) -> None:
    source = disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    with pytest.raises(BriefValidationError) as caught:
        validate_brief_draft(
            disposition_draft(decision.claims, paragraph=paragraph),
            source,
            decision.claims,
            public_quotes=False,
        )
    assert caught.value.safe_code == safe_code


def test_disposition_only_draft_rejects_public_processing_jargon() -> None:
    source = disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    with pytest.raises(BriefValidationError, match="model or schema instructions"):
        validate_brief_draft(
            disposition_draft(
                decision.claims,
                paragraph="The claim_id was copied from the required_output_schema.",
            ),
            source,
            decision.claims,
            public_quotes=False,
        )


def role_aware_disposition_candidate() -> BriefCandidate:
    source = disposition_candidate()
    role_observations = tuple(
        item.model_copy(update={"argument_id": None})
        for item in (
            observation(
                LegalObservationType.REQUESTED_DISPOSITION,
                LegalStatus.REQUESTED,
                ScotusDocumentKind.OPINION,
                "Emergency Applicant asked the Court to reverse the judgment.",
                attribution="Official opinion",
                document_id=OPINION_ID,
            ),
            observation(
                LegalObservationType.LOWER_COURT_ACTION,
                LegalStatus.LOWER_COURT_HELD,
                ScotusDocumentKind.OPINION,
                "The district court affirmed the judgment.",
                attribution="Official opinion",
                document_id=OPINION_ID,
            ),
        )
    )
    return replace(source, observations=(*source.observations, *role_observations))


@pytest.mark.parametrize(
    "paragraph",
    [
        "Emergency Applicant asked the Court to reverse the judgment.",
        "The district court affirmed the judgment.",
        "The Supreme Court granted the application.",
        (
            "Emergency Applicant asked the Court to reverse the judgment. "
            "The district court affirmed the judgment. "
            "The Supreme Court granted the application."
        ),
    ],
)
def test_disposition_action_validation_accepts_each_same_role_claim(paragraph: str) -> None:
    source = role_aware_disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    _validate_action_sentences(paragraph, decision.claims)


@pytest.mark.parametrize(
    ("paragraph", "safe_code"),
    [
        (
            "Emergency Applicant asked the Court to affirm the judgment.",
            "unsupported_requested_action",
        ),
        ("The district court granted the application.", "unsupported_lower_court_action"),
        ("The Supreme Court reversed the judgment.", "unsupported_court_action"),
    ],
)
def test_disposition_action_validation_rejects_actions_from_a_different_role(
    paragraph: str, safe_code: str
) -> None:
    source = role_aware_disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    with pytest.raises(BriefValidationError) as caught:
        _validate_action_sentences(paragraph, decision.claims)
    assert caught.value.safe_code == safe_code


def test_disposition_action_validation_rejects_an_actorless_action() -> None:
    source = role_aware_disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    with pytest.raises(BriefValidationError) as caught:
        _validate_action_sentences("The application was granted.", decision.claims)
    assert caught.value.safe_code == "unsupported_action_role"


def test_disposition_only_draft_accepts_supported_plain_action_synonyms() -> None:
    source = disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    _validate_action_sentences("The Court allowed the application.", decision.claims)

    lower_court_claim = next(
        claim
        for claim in evaluate_brief_candidate(
            role_aware_disposition_candidate(), minimum_confidence=0.85
        ).claims
        if claim.legal_status is LegalStatus.LOWER_COURT_HELD
    ).model_copy(update={"public_value": "The district court remanded the case."})
    _validate_action_sentences("The district court sent the case back.", (lower_court_claim,))


def test_action_validation_accepts_reviewed_ordinary_equivalents_and_preserves_slots() -> None:
    source = evaluate_brief_candidate(role_aware_disposition_candidate(), minimum_confidence=0.85)
    court_claim = next(
        claim for claim in source.claims if claim.legal_status is LegalStatus.COURT_HELD
    )
    vacatur = court_claim.model_copy(
        update={"public_value": "The Supreme Court vacated the judgment."}
    )
    _validate_action_sentences(
        "The Supreme Court cancelled the judgment.",
        (vacatur,),
    )
    remand = court_claim.model_copy(update={"public_value": "The Supreme Court remanded the case."})
    _validate_action_sentences(
        "The Supreme Court sent the case back to the lower court.",
        (remand,),
    )
    stay = court_claim.model_copy(
        update={"public_value": "The Supreme Court stayed the injunction pending appeal."}
    )
    _validate_action_sentences(
        "The Supreme Court temporarily paused the lower court's blocking order while "
        "the appeal continues.",
        (stay,),
    )

    with pytest.raises(BriefValidationError) as wrong_object:
        _validate_action_sentences(
            "The Supreme Court cancelled the appeal.",
            (vacatur,),
        )
    assert wrong_object.value.safe_code == "unsupported_supreme_court_action_object"
    with pytest.raises(BriefValidationError) as wrong_polarity:
        _validate_action_sentences(
            "The Supreme Court did not cancel the judgment.",
            (vacatur,),
        )
    assert wrong_polarity.value.safe_code == "unsupported_court_action"


def test_disposition_only_draft_accepts_zero_argument_analyses() -> None:
    source = disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    validate_brief_draft(
        disposition_draft(decision.claims, paragraph="The Court granted the application."),
        source,
        decision.claims,
        public_quotes=False,
    )


@pytest.mark.parametrize(
    "paragraph",
    [
        "The Court granted the application without oral argument.",
        "No oral argument occurred before the Court granted the application.",
        "Oral argument was not held before the Court granted the application.",
    ],
)
def test_disposition_only_draft_accepts_explicitly_negated_oral_argument(
    paragraph: str,
) -> None:
    source = disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    validate_brief_draft(
        disposition_draft(decision.claims, paragraph=paragraph),
        source,
        decision.claims,
        public_quotes=False,
    )


@pytest.mark.parametrize(
    "paragraph",
    [
        "Oral argument occurred before the Court acted.",
        "Without oral argument, counsel argued that the Court should grant relief.",
        "No oral argument occurred, but a justice asked whether the Court should grant relief.",
    ],
)
def test_disposition_only_draft_rejects_positive_or_mixed_oral_argument(
    paragraph: str,
) -> None:
    source = disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    with pytest.raises(BriefValidationError) as caught:
        validate_brief_draft(
            disposition_draft(decision.claims, paragraph=paragraph),
            source,
            decision.claims,
            public_quotes=False,
        )
    assert caught.value.safe_code == "invented_oral_argument"


def test_policy_builds_page_grounded_claims_and_generation_is_idempotent() -> None:
    source = candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    assert decision.eligible
    assert decision.maturity is BriefMaturity.OFFICIAL_TRANSCRIPT
    assert len(decision.claims) == 3
    assert all(claim.page_label == "file page 5, lines 1-3" for claim in decision.claims)
    store = InMemoryBriefRevisionStore()
    service = BriefGenerationService(FakeGenerator(), store)
    first = service.generate(source, decision, revision_number=1)
    duplicate = service.generate(source, decision, revision_number=1)
    assert duplicate.revision_id == first.revision_id
    assert set(first.claim_ids) == {claim.claim_id for claim in decision.claims}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("The Court will likely rule 5-4 for petitioner.", "prediction"),
        ("Justice Kagan held that the statute controls.", "overstated"),
        ("The conservative bloc appeared hostile.", "ideological"),
        ("You should file a similar claim.", "legal advice"),
        ("The agency likely defended a broader rule.", "speculative"),
        ('Counsel said "the statute controls."', "quotations"),
        ("Counsel called it 'extraordinary and compelling.'", "quotations"),
        ("The Court reversed the judgment.", "final Court action"),
        ("The Supreme Court has not issued a decision.", "incomplete record"),
        ("Smith v. Jones, 599 U.S. 100 controls.", "citation"),
    ],
)
def test_generation_rejects_unsafe_or_unsupported_analysis(text: str, message: str) -> None:
    source = candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    with pytest.raises(BriefValidationError, match=message):
        BriefGenerationService(FakeGenerator(text), InMemoryBriefRevisionStore()).generate(
            source, decision, revision_number=1
        )


@pytest.mark.parametrize(
    ("item", "text"),
    [
        (
            observation(
                LegalObservationType.REQUESTED_DISPOSITION,
                LegalStatus.REQUESTED,
                ScotusDocumentKind.TRANSCRIPT,
                "Counsel asked the Court to reverse the judgment.",
                attribution="Counsel for petitioner",
            ),
            "The side challenging the decision asks the Court to have the judgment reversed.",
        ),
        (
            observation(
                LegalObservationType.LOWER_COURT_ACTION,
                LegalStatus.LOWER_COURT_HELD,
                ScotusDocumentKind.DOCKET,
                "The appeals court reversed the earlier judgment.",
            ),
            "The appeals court reversed the earlier judgment.",
        ),
    ],
)
def test_requested_and_lower_court_actions_are_not_mistaken_for_supreme_court_holdings(
    item: LegalObservation, text: str
) -> None:
    source = candidate(observations=(*candidate().observations, item))
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    revision = BriefGenerationService(FakeGenerator(text), InMemoryBriefRevisionStore()).generate(
        source, decision, revision_number=1
    )
    assert revision.sections[0].paragraphs == (text,)


def test_attribution_variants_for_one_side_do_not_require_duplicate_coverage() -> None:
    alias = observation(
        LegalObservationType.ADVOCATE_CONTENTION,
        LegalStatus.ASSERTED,
        ScotusDocumentKind.TRANSCRIPT,
        "Ms. Harris gave the same position in response to a question.",
        attribution="Ms. Harris (for Petitioner)",
    )
    opposing = observation(
        LegalObservationType.ADVOCATE_CONTENTION,
        LegalStatus.ASSERTED,
        ScotusDocumentKind.TRANSCRIPT,
        "The opposing side defended the agency's reading.",
        attribution="Respondent",
    )
    unknown_side = observation(
        LegalObservationType.ADVOCATE_CONTENTION,
        LegalStatus.ASSERTED,
        ScotusDocumentKind.TRANSCRIPT,
        "Mr. Rivera addressed a separate implementation question.",
        attribution="Mr. Rivera",
    )
    source = candidate(observations=(*candidate().observations, alias, opposing, unknown_side))
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)

    class OneClaimPerSideGenerator(FakeGenerator):
        def generate(self, candidate, claims, maturity):  # type: ignore[no-untyped-def]
            draft = super().generate(candidate, claims, maturity)
            selected = tuple(
                claim.claim_id
                for claim in claims
                if claim.source_observation_ids
                not in {(alias.observation_id,), (unknown_side.observation_id,)}
            )
            return draft.model_copy(
                update={
                    "title_claim_ids": selected[:1],
                    "dek_claim_ids": selected,
                    "sections": tuple(
                        section.model_copy(update={"claim_ids": selected})
                        for section in draft.sections
                    ),
                    "argument_analyses": tuple(
                        analysis.model_copy(update={"claim_ids": selected})
                        for analysis in draft.argument_analyses
                    ),
                }
            )

    revision = BriefGenerationService(
        OneClaimPerSideGenerator(), InMemoryBriefRevisionStore()
    ).generate(source, decision, revision_number=1)
    omitted = {alias.observation_id, unknown_side.observation_id}
    assert omitted.isdisjoint(
        {
            observation_id
            for claim_id in revision.claim_ids
            for claim in decision.claims
            if claim.claim_id == claim_id
            for observation_id in claim.source_observation_ids
        }
    )


@pytest.mark.parametrize(
    ("text", "safe_code"),
    (
        (
            "The approved record does not say when the Court will rule.",
            "unsupported_future_event",
        ),
        ("The Supreme Court has not issued a decision.", "unsupported_no_decision"),
        ("The model extracted these claims from the schema.", "internal_process_language"),
        ("The ruling will clarify the law next year.", "unsupported_future_event"),
        ("The decision will affect every agency.", "unsupported_future_event"),
        ("The Court will decide what happens next.", "unsupported_future_event"),
    ),
)
def test_generation_rejects_process_absence_and_unsupported_future_language(
    text: str, safe_code: str
) -> None:
    source = candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    with pytest.raises(BriefValidationError) as caught:
        BriefGenerationService(FakeGenerator(text), InMemoryBriefRevisionStore()).generate(
            source, decision, revision_number=1
        )
    assert caught.value.safe_code == safe_code


@pytest.mark.parametrize(
    "text",
    (
        "The agency claims that the law limits its power.",
        "The parties dispute the agency's economic model.",
        "The parties dispute whether an economic model generated useful evidence.",
        "The dispute concerns approved insurance claims and benefit claims.",
    ),
)
def test_generation_allows_ordinary_claim_and_model_language(text: str) -> None:
    source = candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    revision = BriefGenerationService(FakeGenerator(text), InMemoryBriefRevisionStore()).generate(
        source, decision, revision_number=1
    )
    assert revision.sections[0].paragraphs == (text,)


def test_generation_accepts_a_future_event_established_by_an_approved_claim() -> None:
    known_future = observation(
        LegalObservationType.PROCEDURAL_POSTURE,
        LegalStatus.DESCRIBED,
        ScotusDocumentKind.DOCKET,
        "Congress set a deadline that will affect agency permits next year.",
    )
    source = candidate(observations=(*candidate().observations, known_future))
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    text = "Congress set a deadline that will affect agency permits next year."
    revision = BriefGenerationService(FakeGenerator(text), InMemoryBriefRevisionStore()).generate(
        source, decision, revision_number=1
    )
    assert revision.sections[0].paragraphs == (text,)


def test_whole_case_brief_requires_and_analyzes_every_argument_session() -> None:
    second_observations = (
        observation(
            LegalObservationType.ADVOCATE_CONTENTION,
            LegalStatus.ASSERTED,
            ScotusDocumentKind.TRANSCRIPT,
            "Counsel explained a narrower reading during reargument.",
            attribution="Counsel for the agency",
            argument_id=SECOND_ARGUMENT_ID,
            document_id=SECOND_TRANSCRIPT_ID,
        ),
        observation(
            LegalObservationType.JUSTICE_QUESTION,
            LegalStatus.QUESTIONED,
            ScotusDocumentKind.TRANSCRIPT,
            "Justice Kagan asked how the narrower reading would work.",
            argument_id=SECOND_ARGUMENT_ID,
            document_id=SECOND_TRANSCRIPT_ID,
        ),
    )
    sessions = (
        *candidate().argument_sessions,
        CaseArgumentSession(
            argument_id=SECOND_ARGUMENT_ID,
            argument_date=NOW.replace(day=29),
            sequence=2,
            reargument=True,
            official_detail_url="https://www.supremecourt.gov/reargument",
            official_transcript_url=("https://www.supremecourt.gov/reargument-transcript.pdf"),
        ),
    )
    source = candidate(
        argument_id=SECOND_ARGUMENT_ID,
        argument_sessions=sessions,
        observations=(*candidate().observations, *second_observations),
        document_urls={
            **candidate().document_urls,
            SECOND_TRANSCRIPT_ID: ("https://www.supremecourt.gov/reargument-transcript.pdf"),
        },
    )
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    assert decision.eligible
    service = BriefGenerationService(FakeGenerator(), InMemoryBriefRevisionStore())
    revision = service.generate(source, decision, revision_number=1)
    anchored_to_first = BriefCandidate(**{**source.__dict__, "argument_id": ARGUMENT_ID})
    second_revision = service.generate(
        anchored_to_first,
        evaluate_brief_candidate(anchored_to_first, minimum_confidence=0.85),
        revision_number=2,
    )
    assert revision.brief_id == second_revision.brief_id
    assert [item.argument_id for item in revision.argument_analyses] == [
        ARGUMENT_ID,
        SECOND_ARGUMENT_ID,
    ]
    assert revision.argument_analyses[1].reargument

    incomplete = evaluate_brief_candidate(
        candidate(
            argument_sessions=sessions,
            official_transcript_complete=False,
        ),
        minimum_confidence=0.85,
    )
    assert not incomplete.eligible
    assert any("complete official transcript" in reason for reason in incomplete.reasons)

    class CrossSessionGenerator(FakeGenerator):
        def generate(self, candidate, claims, maturity):  # type: ignore[no-untyped-def]
            draft = super().generate(candidate, claims, maturity)
            first = draft.argument_analyses[0]
            second = draft.argument_analyses[1].model_copy(update={"claim_ids": first.claim_ids})
            return draft.model_copy(update={"argument_analyses": (first, second)})

    with pytest.raises(BriefValidationError, match="different session"):
        BriefGenerationService(CrossSessionGenerator(), InMemoryBriefRevisionStore()).generate(
            source, decision, revision_number=1
        )


def test_plain_language_warns_on_style_and_rejects_only_severe_length() -> None:
    assert "lawyer_facing_phrase" in _validate_plain_language(
        "Pursuant to the aforementioned rule, the instant case controls.",
        maximum_sentence_words=30,
        maximum_paragraph_words=120,
    )
    slightly_long = " ".join(["word"] * 31) + "."
    assert _validate_plain_language(
        slightly_long,
        maximum_sentence_words=30,
        maximum_paragraph_words=120,
    ) == ("preferred_sentence_length",)
    with pytest.raises(BriefValidationError, match="severe length"):
        _validate_plain_language(
            " ".join(["word"] * 61) + ".",
            maximum_sentence_words=30,
            maximum_paragraph_words=120,
        )
    assert "unexplained_legal_term" in _validate_plain_language(
        "The dispute concerns statutory authority.",
        maximum_sentence_words=30,
        maximum_paragraph_words=120,
    )


@pytest.mark.parametrize(
    "legalese",
    (
        "The plaintiff has Article III standing.",
        "The lower court issued an injunction.",
        "The Court remanded the case.",
        "The dispute is moot.",
        "The rule turns on domicile.",
        "Federal law preempts the state rule.",
    ),
)
def test_plain_language_warns_on_unexplained_legal_terms(legalese: str) -> None:
    assert _validate_plain_language(
        legalese,
        maximum_sentence_words=30,
        maximum_paragraph_words=120,
    ) == ("unexplained_legal_term",)


@pytest.mark.parametrize(
    "explained",
    (
        "Standing means the person must show harm before gaining the right to bring the case.",
        "An injunction is a court order that blocks an action.",
        "The Court remanded the case, meaning it sent the dispute to the lower court.",
        "The dispute is moot, meaning there is nothing left to decide.",
        "Domicile means a person's permanent home.",
        "Federal law preempts the state rule, meaning federal law overrides state law.",
    ),
)
def test_plain_language_accepts_legal_terms_only_with_an_immediate_gloss(
    explained: str,
) -> None:
    _validate_plain_language(
        explained,
        maximum_sentence_words=30,
        maximum_paragraph_words=120,
    )


@pytest.mark.parametrize(
    ("label", "unexplained", "explained"),
    (
        (
            "waiver",
            "The agency relied on waiver.",
            "Waiver means the agency gave up its right to object.",
        ),
        (
            "pretext",
            "The stated reason was pretextual.",
            "The reason was pretextual, meaning the stated reason was not the real reason.",
        ),
        (
            "rebuttal",
            "The filing was a rebuttal.",
            "The rebuttal was an answer to the other side's argument.",
        ),
        (
            "finality",
            "The dispute concerns finality.",
            "Finality asks whether the judgment is final.",
        ),
        (
            "habeas",
            "The prisoner filed for habeas corpus.",
            "Habeas corpus is a challenge to imprisonment.",
        ),
        (
            "sovereign_immunity",
            "The state asserted sovereign immunity.",
            "Sovereign immunity protects the state from being sued.",
        ),
        ("tolling", "The law allows tolling.", "Tolling means the deadline pauses."),
        (
            "due_process",
            "The policy denies due process.",
            "Due process requires fair process here.",
        ),
        (
            "equal_protection",
            "The case raises equal protection.",
            "Equal protection requires the law to treat people equally.",
        ),
        (
            "scrutiny",
            "The court used strict scrutiny.",
            "Strict scrutiny is the most demanding test.",
        ),
        (
            "certiorari",
            "The party sought certiorari.",
            "Certiorari is a request asking the Court to review the case.",
        ),
        (
            "procedural_history",
            "The procedural history is lengthy.",
            "Procedural history means the steps that brought the case through court.",
        ),
    ),
)
def test_reviewed_corpus_terms_require_same_sentence_case_specific_glosses(
    label: str, unexplained: str, explained: str
) -> None:
    assert label
    assert _validate_plain_language(
        unexplained,
        maximum_sentence_words=30,
        maximum_paragraph_words=120,
    ) == ("unexplained_legal_term",)
    assert "unexplained_legal_term" not in _validate_plain_language(
        explained,
        maximum_sentence_words=30,
        maximum_paragraph_words=120,
    )


def test_reader_term_gloss_must_be_in_the_same_sentence() -> None:
    assert "unexplained_legal_term" in _validate_plain_language(
        "The court lacked jurisdiction. It had no power to hear the case.",
        maximum_sentence_words=30,
        maximum_paragraph_words=120,
    )


def test_reader_prose_resource_is_versioned_complete_and_reviewed() -> None:
    policy = load_reader_prose_policy()
    assert policy.version == "scotus-reader-prose-v1"
    labels = {term.label for term in policy.terms}
    assert {
        "waiver",
        "pretext",
        "rebuttal",
        "finality",
        "standing",
        "jurisdiction",
        "injunction",
        "vacatur",
        "remand",
        "habeas",
        "sovereign_immunity",
        "procedural_history",
    }.issubset(labels)
    assert all(term.ordinary_alternatives for term in policy.terms)
    assert any(
        action.canonical == "vacate" and action.ordinary for action in policy.action_equivalents
    )


def test_reader_prose_resource_validation_fails_closed() -> None:
    payload = load_reader_prose_policy().model_dump(mode="json")
    payload["terms"] = [term for term in payload["terms"] if term["label"] != "waiver"]
    with pytest.raises(ValidationError, match="missing required terms"):
        ReaderProsePolicy.model_validate(payload)

    invalid_pattern = load_reader_prose_policy().model_dump(mode="json")
    invalid_pattern["process_patterns"][0] = "["
    with pytest.raises(ValidationError, match="invalid reader-prose regular expression"):
        ReaderProsePolicy.model_validate(invalid_pattern)


def test_headings_cannot_rely_on_inline_legal_glosses() -> None:
    assert "unexplained_legal_term" in _validate_plain_language(
        "Jurisdiction means power to hear the case",
        maximum_sentence_words=30,
        maximum_paragraph_words=120,
        allow_term_explanations=False,
    )


def test_plain_language_warns_on_repetition_and_unreadable_prose() -> None:
    assert "repeated_fragment" in _validate_plain_language(
        "The agency changed the rule. The agency changed the rule.",
        maximum_sentence_words=30,
        maximum_paragraph_words=120,
    )
    assert "readability" in _validate_plain_language(
        "Notwithstanding multifarious constitutional considerations, institutional "
        "adjudication necessitates extraordinarily sophisticated interpretive methodologies.",
        maximum_sentence_words=30,
        maximum_paragraph_words=120,
    )


def test_openai_generator_requests_structured_plain_language_output() -> None:
    source = candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    ids = tuple(claim.claim_id for claim in decision.claims)
    draft = LegalBriefDraft(
        title="Did the agency have the power to act?",
        title_claim_ids=(ids[0],),
        dek="The two sides disagree about the power Congress gave the agency.",
        dek_claim_ids=ids[:2],
        sections=tuple(
            DraftSection(
                heading=heading,
                paragraphs=("The case asks whether Congress gave the agency the power to act.",),
                claim_ids=ids,
            )
            for heading in (
                "What this case is about",
                "What each side says",
                "What the justices asked",
                "Why it matters",
            )
        ),
        argument_analyses=(
            DraftArgumentAnalysis(
                argument_id=ARGUMENT_ID,
                heading="The first argument",
                paragraphs=(
                    "The case asks whether Congress gave the agency the power to act.",
                    "The justices tested how each side's answer would work in practice.",
                ),
                claim_ids=ids,
            ),
        ),
    )

    class Completions:
        def __init__(self) -> None:
            self.request: dict[str, object] = {}
            self.content = draft.model_dump_json()
            self.finish_reason = "stop"

        def create(self, **kwargs: object) -> object:
            self.request = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason=self.finish_reason,
                        message=SimpleNamespace(content=self.content),
                    )
                ]
            )

    completions = Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    generator = OpenAILegalBriefGenerator(
        "gpt-5",  # type: ignore[arg-type]
        client,
        response_schema=simple_brief_json_schema(),
    )
    assert generator.generate(source, decision.claims, decision.maturity) == draft
    assert completions.request["model"] == "gpt-5"
    assert completions.request["temperature"] is omit
    messages = completions.request["messages"]
    prompt = messages[0]["content"]  # type: ignore[index]
    assert prompt.startswith("/no_think")
    assert "no legal training" in prompt
    user_payload = json.loads(messages[1]["content"])  # type: ignore[index]
    assert user_payload["mode"] == "/no_think"
    assert "everyday language" in prompt
    assert "main job is to translate" in prompt
    assert "A reader must not need a law dictionary" in prompt
    assert "What this case is about" in prompt
    assert "position_group" in prompt
    assert "procedural posture" in prompt
    assert "Copy every claim ID exactly" in prompt
    assert "do not use quotation" in prompt
    response_format = completions.request["response_format"]
    serialized_format = json.dumps(response_format)
    assert "json_schema" in serialized_format
    assert "$defs" not in serialized_format
    completions.finish_reason = "length"
    assert generator.generate(source, decision.claims, decision.maturity) == draft
    completions.finish_reason = "stop"
    schema = response_format["json_schema"]["schema"]  # type: ignore[index]
    assert schema["properties"]["sections"]["maxItems"] == 5
    assert schema["properties"]["argument_analyses"]["minItems"] == 1
    assert schema["properties"]["argument_analyses"]["maxItems"] == 1
    assert schema["properties"]["sections"]["items"]["properties"]["paragraphs"]["maxItems"] == 1

    completions.content = draft.model_copy(
        update={"title": "What this case is about"}
    ).model_dump_json()
    assert generator.generate(source, decision.claims, decision.maturity).title == (source.caption)

    unsupported = draft.model_dump(mode="json")
    unsupported["title_claim_ids"] = [str(uuid4())]
    completions.content = json.dumps(unsupported)
    with pytest.raises(ValueError):
        generator.generate(source, decision.claims, decision.maturity)


def test_disposition_generator_uses_compact_positive_role_aware_request() -> None:
    source = disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    draft = disposition_draft(
        decision.claims,
        paragraph="The Supreme Court granted the application.",
    )

    class Completions:
        def __init__(self) -> None:
            self.request: dict[str, object] = {}

        def create(self, **kwargs: object) -> object:
            self.request = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=draft.model_dump_json()))]
            )

    completions = Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    generator = OpenAILegalBriefGenerator(
        "qwen3.8:27b",  # type: ignore[arg-type]
        client,
        response_schema=disposition_only_brief_json_schema(),
    )
    generated = generator.generate(source, decision.claims, decision.maturity)

    messages = completions.request["messages"]
    prompt = messages[0]["content"]  # type: ignore[index]
    user_payload = json.loads(messages[1]["content"])  # type: ignore[index]
    assert prompt.startswith("/no_think")
    assert len(prompt.split()) < 320
    assert "complete plain-English citizen's guide" in prompt
    assert "main job is translation" in prompt
    assert "A reader must not need a law dictionary" in prompt
    assert "operative Supreme Court action" in prompt
    assert "interim relief, not a final merits judgment" in prompt
    assert "Every action sentence must name its" in prompt
    assert "What separate opinions said" in prompt
    for priming in ("oral argument", "argument session", "transcript", "counsel"):
        assert priming not in prompt.casefold()
    assert "argument_sessions" not in user_payload
    assert "position_group" not in json.dumps(user_payload)
    assert user_payload["caption"] == source.caption
    assert user_payload["docket"] == source.primary_docket
    assert user_payload["maturity"] == decision.maturity.value
    planned_claims = [
        claim for section in user_payload["section_plan"] for claim in section["claims"]
    ]
    assert {
        "described",
        "requested",
        "court_held",
    }.issubset({claim["status"] for claim in planned_claims})
    schema = completions.request["response_format"]["json_schema"]["schema"]  # type: ignore[index]
    assert schema["properties"]["argument_analyses"]["minItems"] == 0
    assert schema["properties"]["argument_analyses"]["maxItems"] == 0
    assert generated.title == source.caption
    assert generated.dek == "Emergency Applicant challenged an Agency action."
    assert tuple(section.heading for section in generated.sections) == (
        "What this case is about",
        "Why this case reached the Court",
        "The legal issue",
        "What the Supreme Court did",
        "Why the Court did it",
    )
    assert generated.argument_analyses == ()


def test_26a124_shaped_guide_is_coherent_and_keeps_dissent_separate() -> None:
    observations = tuple(
        item.model_copy(update={"argument_id": None})
        for item in (
            observation(
                LegalObservationType.PROCEDURAL_POSTURE,
                LegalStatus.DESCRIBED,
                ScotusDocumentKind.DOCKET,
                "Docket 26A124 identifies Trump v. California.",
            ),
            observation(
                LegalObservationType.CASE_BACKGROUND,
                LegalStatus.DESCRIBED,
                ScotusDocumentKind.OPINION,
                "The President directed federal agencies to change election administration "
                "practices challenged by several states.",
                document_id=OPINION_ID,
            ),
            observation(
                LegalObservationType.LOWER_COURT_ACTION,
                LegalStatus.LOWER_COURT_HELD,
                ScotusDocumentKind.OPINION,
                "The district court blocked the federal directives.",
                document_id=OPINION_ID,
            ),
            observation(
                LegalObservationType.REQUESTED_DISPOSITION,
                LegalStatus.REQUESTED,
                ScotusDocumentKind.OPINION,
                "The Government asked the Supreme Court to stay the injunction during its appeal.",
                attribution="The Government",
                document_id=OPINION_ID,
            ),
            observation(
                LegalObservationType.DOCTRINAL_THEME,
                LegalStatus.DESCRIBED,
                ScotusDocumentKind.OPINION,
                "The controlling issue is whether the states showed a concrete immediate "
                "injury and brought a dispute ready for judicial review.",
                document_id=OPINION_ID,
            ),
            observation(
                LegalObservationType.ORDER,
                LegalStatus.COURT_ORDERED,
                ScotusDocumentKind.OPINION,
                "The Supreme Court stayed the injunction pending appeal.",
                document_id=OPINION_ID,
            ),
            observation(
                LegalObservationType.DOCTRINAL_THEME,
                LegalStatus.DESCRIBED,
                ScotusDocumentKind.OPINION,
                "The Court concluded that the states had not shown an immediate injury ready "
                "for judicial review.",
                attribution="Opinion of the Court",
                document_id=OPINION_ID,
            ),
            observation(
                LegalObservationType.DOCTRINAL_THEME,
                LegalStatus.DESCRIBED,
                ScotusDocumentKind.OPINION,
                "Justice Jackson said the states already faced immediate election administration "
                "costs.",
                attribution="Justice Jackson, dissenting",
                document_id=OPINION_ID,
            ),
        )
    )
    source = replace(
        disposition_candidate(),
        caption="Trump v. California",
        primary_docket="26A124",
        observations=observations,
    )
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    assert decision.eligible
    by_type = {
        observation_type: tuple(
            claim.claim_id
            for claim in decision.claims
            if claim.observation_type is observation_type
        )
        for observation_type in LegalObservationType
    }
    majority_issue_id, majority_reason_id, dissent_id = by_type[
        LegalObservationType.DOCTRINAL_THEME
    ]
    draft = LegalBriefDraft(
        title="Trump v. California",
        title_claim_ids=by_type[LegalObservationType.PROCEDURAL_POSTURE],
        dek="The case concerns federal election directives challenged by several states.",
        dek_claim_ids=by_type[LegalObservationType.CASE_BACKGROUND],
        sections=(
            DraftSection(
                heading="What this case is about",
                paragraphs=(
                    "The President directed federal agencies to change election administration "
                    "practices challenged by several states.",
                ),
                claim_ids=by_type[LegalObservationType.CASE_BACKGROUND],
            ),
            DraftSection(
                heading="Why this case reached the Court",
                paragraphs=(
                    "The district court blocked the federal directives. The Government asked "
                    "the Supreme Court for a stay, meaning a temporary pause of that lower "
                    "court order during its appeal.",
                ),
                claim_ids=(
                    *by_type[LegalObservationType.LOWER_COURT_ACTION],
                    *by_type[LegalObservationType.REQUESTED_DISPOSITION],
                ),
            ),
            DraftSection(
                heading="The legal issue",
                paragraphs=(
                    "The issue is whether the states showed immediate harm and brought a "
                    "dispute the Court could review.",
                ),
                claim_ids=(majority_issue_id,),
            ),
            DraftSection(
                heading="What the Supreme Court did",
                paragraphs=(
                    "The Supreme Court stayed the injunction, a court order that prevented the "
                    "federal directives, temporarily while the appeal continues.",
                ),
                claim_ids=by_type[LegalObservationType.ORDER],
            ),
            DraftSection(
                heading="Why the Court did it",
                paragraphs=(
                    "The Court concluded that the states had not yet shown immediate harm in a "
                    "dispute the Court could review.",
                ),
                claim_ids=(majority_reason_id,),
            ),
            DraftSection(
                heading="What separate opinions said",
                paragraphs=(
                    "Justice Jackson said in dissent that the states already faced immediate "
                    "election administration costs.",
                ),
                claim_ids=(dissent_id,),
            ),
        ),
        argument_analyses=(),
    )

    validate_brief_draft(draft, source, decision.claims, public_quotes=False)

    ambiguous_dissent = draft.model_copy(
        update={
            "sections": tuple(
                section.model_copy(
                    update={
                        "paragraphs": (
                            "Justice Jackson, dissenting, stated that the states had not shown "
                            "immediate injury in a dispute ready for a court to review.",
                        )
                    }
                )
                if section.heading == "What separate opinions said"
                else section
                for section in draft.sections
            )
        }
    )
    with pytest.raises(BriefValidationError) as caught:
        validate_brief_draft(ambiguous_dissent, source, decision.claims, public_quotes=False)
    assert caught.value.safe_code in {
        "ambiguous_separate_opinion_attribution",
        "ungrounded_separate_opinions_section",
    }

    unattributed_dissent_detail = draft.model_copy(
        update={
            "sections": tuple(
                section.model_copy(
                    update={
                        "paragraphs": (
                            "The states already faced immediate election administration costs. "
                            "A proposed rule changed ballot envelope standards.",
                        )
                    }
                )
                if section.heading == "What separate opinions said"
                else section
                for section in draft.sections
            )
        }
    )
    with pytest.raises(BriefValidationError) as caught:
        validate_brief_draft(
            unattributed_dissent_detail, source, decision.claims, public_quotes=False
        )
    assert caught.value.safe_code == "ambiguous_separate_opinion_attribution"

    dissent_led = draft.model_copy(
        update={
            "sections": tuple(
                section.model_copy(update={"claim_ids": (dissent_id,)})
                if section.heading == "The legal issue"
                else section
                for section in draft.sections
            )
        }
    )
    with pytest.raises(BriefValidationError) as caught:
        validate_brief_draft(dissent_led, source, decision.claims, public_quotes=False)
    assert caught.value.safe_code == "separate_opinion_in_main_guide"

    incomplete_stay = draft.model_copy(
        update={
            "sections": tuple(
                section.model_copy(
                    update={
                        "paragraphs": (
                            "The Supreme Court stayed the injunction, a lower court order "
                            "that blocked the federal directives.",
                        )
                    }
                )
                if section.heading == "What the Supreme Court did"
                else section
                for section in draft.sections
            )
        }
    )
    with pytest.raises(BriefValidationError) as caught:
        validate_brief_draft(incomplete_stay, source, decision.claims, public_quotes=False)
    assert caught.value.safe_code == "incomplete_interim_stay_effect"

    wrong_stay_object = draft.model_copy(
        update={
            "sections": tuple(
                section.model_copy(
                    update={"paragraphs": ("The Supreme Court temporarily stayed the appeal.",)}
                )
                if section.heading == "What the Supreme Court did"
                else section
                for section in draft.sections
            )
        }
    )
    with pytest.raises(BriefValidationError) as caught:
        validate_brief_draft(wrong_stay_object, source, decision.claims, public_quotes=False)
    assert caught.value.safe_code == "unsupported_supreme_court_action_object"

    actionless = draft.model_copy(
        update={
            "sections": tuple(
                section.model_copy(update={"paragraphs": ("The case concerns emergency relief.",)})
                if section.heading == "What the Supreme Court did"
                else section
                for section in draft.sections
            )
        }
    )
    with pytest.raises(BriefValidationError) as caught:
        validate_brief_draft(actionless, source, decision.claims, public_quotes=False)
    assert caught.value.safe_code == "ungrounded_guide_section_what_the_supreme_court_did"

    invented_reason = draft.model_copy(
        update={
            "sections": tuple(
                section.model_copy(
                    update={"paragraphs": ("The election policy was popular nationwide.",)}
                )
                if section.heading == "Why the Court did it"
                else section
                for section in draft.sections
            )
        }
    )
    with pytest.raises(BriefValidationError) as caught:
        validate_brief_draft(invented_reason, source, decision.claims, public_quotes=False)
    assert caught.value.safe_code == (
        "ungrounded_guide_section_why_the_court_did_it_matches_case_background"
    )


def test_local_brief_schema_matches_exact_argument_count() -> None:
    analyses = simple_brief_json_schema(2)["properties"]["argument_analyses"]
    assert analyses["minItems"] == analyses["maxItems"] == 2
    disposition_schema = disposition_only_brief_json_schema()["properties"]
    disposition_analyses = disposition_schema["argument_analyses"]
    assert disposition_analyses["minItems"] == disposition_analyses["maxItems"] == 0
    assert disposition_schema["sections"]["minItems"] == 5
    assert disposition_schema["sections"]["maxItems"] == 6
    assert disposition_schema["sections"]["items"]["properties"]["heading"]["enum"] == [
        "What this case is about",
        "Why this case reached the Court",
        "The legal issue",
        "What the Supreme Court did",
        "Why the Court did it",
        "What separate opinions said",
    ]
    with pytest.raises(ValueError, match="argument count"):
        simple_brief_json_schema(-1)


def test_sensitive_details_are_minimized_or_suppressed() -> None:
    sensitive = observation(
        LegalObservationType.CASE_BACKGROUND,
        LegalStatus.DESCRIBED,
        ScotusDocumentKind.DOCKET,
        "The minor patient Jane Doe receives medical treatment at 100 Main Street.",
        sensitivity=(
            ScotusSensitivity.MINOR,
            ScotusSensitivity.MEDICAL,
            ScotusSensitivity.HOME_ADDRESS,
            ScotusSensitivity.PRIVATE_NAME,
        ),
    )
    source = candidate(observations=(*candidate().observations, sensitive))
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    public = next(
        claim.public_value
        for claim in decision.claims
        if claim.observation_type is LegalObservationType.CASE_BACKGROUND
    )
    assert "Jane Doe" not in public
    assert "100 Main Street" not in public
    assert "a private individual" in public
    assert "a private address" in public

    sealed = sensitive.model_copy(
        update={
            "observation_id": uuid4(),
            "sensitivity": (ScotusSensitivity.SEALED_OR_REDACTED,),
        }
    )
    sealed_source = candidate(observations=(*candidate().observations, sealed))
    sealed_decision = evaluate_brief_candidate(sealed_source, minimum_confidence=0.85)
    assert all(
        claim.source_observation_ids != (sealed.observation_id,) for claim in sealed_decision.claims
    )


def test_maturity_follows_official_case_state_and_correction_note() -> None:
    source = candidate(case_status=ScotusCaseStatus.DECIDED)
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    assert decision.maturity is BriefMaturity.POST_OPINION
    revision = BriefGenerationService(FakeGenerator(), InMemoryBriefRevisionStore()).generate(
        source,
        decision,
        revision_number=2,
        correction_note="Updated after the official opinion.",
    )
    assert revision.maturity is BriefMaturity.POST_OPINION
    assert revision.correction_note is not None


def test_ineligible_case_cannot_reach_generator() -> None:
    source = candidate(official_transcript_complete=False)
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    with pytest.raises(BriefPolicyError, match="not eligible"):
        BriefGenerationService(FakeGenerator(), InMemoryBriefRevisionStore()).generate(
            source, decision, revision_number=1
        )
