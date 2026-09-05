import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from openai import omit

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
                "Emergency Applicant sought relief from Agency.",
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
    claim_ids = tuple(claim.claim_id for claim in claims)
    return LegalBriefDraft(
        title="Emergency Applicant v. Agency: Court action",
        title_claim_ids=claim_ids,
        dek="The official docket identifies the case.",
        dek_claim_ids=claim_ids,
        sections=(
            DraftSection(
                heading="What the Court did",
                paragraphs=(paragraph,),
                claim_ids=claim_ids,
            ),
        ),
        argument_analyses=(),
    )


def test_disposition_only_policy_accepts_two_independent_required_facts() -> None:
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
    assert decision.eligible
    assert len(decision.claims) == 2


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
    assert not _unsupported_named_phrase(
        "The FCC action is stayed.", support, "Committee v. Brown"
    )
    assert _unsupported_named_phrase(
        "The FTC action is stayed.", support, "Committee v. Brown"
    )


@pytest.mark.parametrize(
    ("paragraph", "safe_code"),
    [
        ("At oral argument, a justice asked about relief.", "invented_oral_argument"),
        (
            "Acme Corporation sought relief.",
            "unsupported_party_section_paragraph",
        ),
        ("The Court denied the application.", "unsupported_disposition"),
        ("The Court did not grant the application.", "unsupported_disposition"),
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
                paragraph="No further details are provided in the approved claims.",
            ),
            source,
            decision.claims,
            public_quotes=False,
        )


def test_disposition_only_draft_accepts_supported_plain_action_synonyms() -> None:
    source = disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    validate_brief_draft(
        disposition_draft(decision.claims, paragraph="The Court allowed the application."),
        source,
        decision.claims,
        public_quotes=False,
    )


def test_disposition_only_draft_accepts_zero_argument_analyses() -> None:
    source = disposition_candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    validate_brief_draft(
        disposition_draft(decision.claims, paragraph="The Court granted the application."),
        source,
        decision.claims,
        public_quotes=False,
    )


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


def test_generation_allows_explicit_uncertainty_about_when_court_will_rule() -> None:
    source = candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    text = (
        "The approved record does not say when the Court will rule or what it will decide. "
        "No outcome can be predicted from this argument alone."
    )
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


def test_plain_language_rejects_legalese_and_overlong_prose() -> None:
    source = candidate()
    decision = evaluate_brief_candidate(source, minimum_confidence=0.85)
    with pytest.raises(BriefValidationError, match="legalese"):
        BriefGenerationService(
            FakeGenerator("Pursuant to the aforementioned rule, the instant case controls."),
            InMemoryBriefRevisionStore(),
        ).generate(source, decision, revision_number=1)
    long_sentence = " ".join(["word"] * 31) + "."
    with pytest.raises(BriefValidationError, match="sentence is too long"):
        BriefGenerationService(FakeGenerator(long_sentence), InMemoryBriefRevisionStore()).generate(
            source, decision, revision_number=1
        )
    with pytest.raises(BriefValidationError, match="unexplained legal concept"):
        BriefGenerationService(
            FakeGenerator("The dispute concerns statutory authority."),
            InMemoryBriefRevisionStore(),
        ).generate(source, decision, revision_number=1)


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


def test_local_brief_schema_matches_exact_argument_count() -> None:
    analyses = simple_brief_json_schema(2)["properties"]["argument_analyses"]
    assert analyses["minItems"] == analyses["maxItems"] == 2
    disposition_analyses = disposition_only_brief_json_schema()["properties"]["argument_analyses"]
    assert disposition_analyses["minItems"] == disposition_analyses["maxItems"] == 0
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
