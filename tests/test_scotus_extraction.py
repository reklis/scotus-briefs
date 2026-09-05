from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ragchew.scotus.contracts import (
    AdvocateRole,
    LegalCertainty,
    LegalObservationType,
    LegalStatus,
    ScotusDocumentKind,
    ScotusSensitivity,
    SpeakerIdentityBasis,
    SpeakerKind,
)
from ragchew.scotus.extraction import (
    DeterministicTranscriptObservationExtractor,
    InMemoryLegalObservationStore,
    LegalEvidenceBlock,
    LegalExtractionBatch,
    LegalExtractionError,
    LegalExtractionInput,
    LegalExtractionService,
    OpenAILegalObservationExtractor,
    ProposedEvidence,
    ProposedLegalObservation,
    bounded_contexts,
    find_supported_legal_references,
    normalize_advocate_role,
    normalize_constitutional_reference,
    normalize_court,
    normalize_disposition,
    normalize_docket_list,
    normalize_docket_reference,
    normalize_legal_citation,
    sensitivity_labels,
)


class FakeExtractor:
    model_name = "legal-test"
    PROMPT_VERSION = "test-v1"

    def __init__(self, observations: list[ProposedLegalObservation]):
        self.observations = observations

    def extract(self, source: LegalExtractionInput) -> LegalExtractionBatch:
        return LegalExtractionBatch(observations=self.observations)


def block(
    text: str,
    *,
    kind: ScotusDocumentKind = ScotusDocumentKind.TRANSCRIPT,
    speaker_name: str | None = "Justice Kagan",
    block_id: str = "turn-1",
) -> LegalEvidenceBlock:
    return LegalEvidenceBlock(
        block_id=block_id,
        document_revision_id=uuid4(),
        document_kind=kind,
        official_url="https://www.supremecourt.gov/official.pdf",
        start_file_page=5,
        start_line=1,
        end_file_page=5,
        end_line=4,
        text_private=text,
        speaker_name=speaker_name,
        speaker_kind=SpeakerKind.JUSTICE if speaker_name else SpeakerKind.UNKNOWN,
        identity_basis=(
            SpeakerIdentityBasis.OFFICIAL_TRANSCRIPT_LABEL
            if speaker_name
            else SpeakerIdentityBasis.ANONYMOUS
        ),
        attribution=speaker_name,
    )


def source(*blocks: LegalEvidenceBlock) -> LegalExtractionInput:
    return LegalExtractionInput(
        case_id=uuid4(),
        argument_id=uuid4(),
        blocks=blocks,
        parser_versions=("fixture:1",),
        document_revision_ids=tuple(item.document_revision_id for item in blocks),
    )


def proposed(
    source_block: LegalEvidenceBlock,
    quote: str,
    *,
    observation_type: LegalObservationType = LegalObservationType.JUSTICE_QUESTION,
    status: LegalStatus = LegalStatus.QUESTIONED,
    raw_value: str = "Justice Kagan asked whether the rule matched Smith v. Jones.",
    attribution: str | None = None,
    speaker_name: str | None = "Justice Kagan",
    citations: tuple[str, ...] = (),
) -> ProposedLegalObservation:
    return ProposedLegalObservation(
        observation_type=observation_type,
        legal_status=status,
        certainty=LegalCertainty.ATTRIBUTED,
        raw_value=raw_value,
        attribution=attribution,
        speaker_name=speaker_name,
        speaker_kind=SpeakerKind.JUSTICE if speaker_name else SpeakerKind.UNKNOWN,
        identity_basis=(
            SpeakerIdentityBasis.OFFICIAL_TRANSCRIPT_LABEL
            if speaker_name
            else SpeakerIdentityBasis.ANONYMOUS
        ),
        authority_citations=citations,
        confidence=0.9,
        evidence=(ProposedEvidence(block_id=source_block.block_id, quote=quote),),
    )


def process(
    source_value: LegalExtractionInput, item: ProposedLegalObservation
) -> list:
    return LegalExtractionService(
        FakeExtractor([item]), InMemoryLegalObservationStore()
    ).process(source_value)


def test_openai_extraction_supplies_and_derives_exact_block_identity() -> None:
    evidence = block("What text supports the rule?")
    source_value = source(evidence)
    item = proposed(evidence, "  What text supports the rule?  ").model_copy(
        update={
            "speaker_name": "Invented Name",
            "speaker_kind": SpeakerKind.ADVOCATE,
            "identity_basis": SpeakerIdentityBasis.ANONYMOUS,
            "attribution": "Invented attribution",
        }
    )
    completion = SimpleNamespace(
        choices=(
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content=LegalExtractionBatch(observations=[item]).model_dump_json()
                ),
            ),
        )
    )
    requests: list[dict[str, object]] = []
    extractor = OpenAILegalObservationExtractor(
        "qwen3.8:27b",
        SimpleNamespace(),
        request_executor=lambda request: (requests.append(request), completion)[1],
    )

    batch = extractor.extract(source_value)
    payload = json.loads(requests[0]["messages"][1]["content"])  # type: ignore[index]
    assert payload["mode"] == "/no_think"
    sent = payload["evidence"][0]
    assert sent["speaker_name"] == evidence.speaker_name
    assert sent["speaker_kind"] == evidence.speaker_kind.value
    assert sent["identity_basis"] == evidence.identity_basis.value
    normalized = batch.observations[0]
    assert normalized.speaker_name == evidence.speaker_name
    assert normalized.speaker_kind is evidence.speaker_kind
    assert normalized.identity_basis is evidence.identity_basis
    assert normalized.attribution == evidence.attribution
    assert process(source_value, normalized)[0].evidence[0].quote_private == (
        "What text supports the rule?"
    )


def test_openai_extraction_reports_safe_truncation_code() -> None:
    completion = SimpleNamespace(
        choices=(
            SimpleNamespace(
                finish_reason="length",
                message=SimpleNamespace(content="private partial output"),
            ),
        )
    )
    with pytest.raises(LegalExtractionError) as captured:
        OpenAILegalObservationExtractor.parse_completion(completion)
    assert captured.value.safe_code == "output_truncated"
    assert "private partial output" not in str(captured.value)


def test_bounded_contexts_never_split_or_overflow_blocks() -> None:
    blocks = (
        block("a" * 10, block_id="a"),
        block("b" * 10, block_id="b"),
        block("c" * 10, block_id="c"),
    )
    batches = bounded_contexts(blocks, 20)
    assert [len(batch) for batch in batches] == [2, 1]
    with pytest.raises(LegalExtractionError, match="exceeds"):
        bounded_contexts((block("x" * 21),), 20)


def test_private_deterministic_extractor_builds_conservative_grounded_types() -> None:
    opening = block(
        "We will hear argument in this case.", block_id="opening"
    ).model_copy(update={"speaker_name": "Chief Justice", "attribution": "Chief Justice"})
    advocate = block(
        "The federal law does not authorize this action.",
        block_id="advocate",
    ).model_copy(
        update={
            "speaker_name": "Mr. Smith",
            "speaker_kind": SpeakerKind.ADVOCATE,
            "attribution": "Mr. Smith",
        }
    )
    question = block(
        "What text supports your proposed rule?", block_id="question"
    )
    result = LegalExtractionService(
        DeterministicTranscriptObservationExtractor(),
        InMemoryLegalObservationStore(),
    ).process(source(opening, advocate, question))
    assert {item.observation_type for item in result} == {
        LegalObservationType.PROCEDURAL_POSTURE,
        LegalObservationType.ADVOCATE_CONTENTION,
        LegalObservationType.JUSTICE_QUESTION,
    }
    assert all(item.evidence[0].quote_private in item.raw_value_private for item in result)


def test_supported_question_keeps_question_status_and_identity() -> None:
    evidence = block("Is that rule consistent with Smith v. Jones, 599 U. S. 100?")
    observations = process(
        source(evidence),
        proposed(
            evidence,
            "Is that rule consistent with Smith v. Jones, 599 U. S. 100?",
            citations=("Smith v. Jones, 599 U.S. 100",),
        ),
    )
    assert observations[0].legal_status is LegalStatus.QUESTIONED
    assert observations[0].speaker_name == "Justice Kagan"
    assert observations[0].authority_citations == ("Smith v. Jones, 599 U.S. 100",)


@pytest.mark.parametrize(
    ("item_update", "message"),
    [
        ({"legal_status": LegalStatus.COURT_HELD}, "questioned status"),
        ({"speaker_name": "Justice Sotomayor"}, "speaker name"),
        ({"authority_citations": ("Invented v. Case, 999 U.S. 1",)}, "citation"),
        ({"raw_value": "The Court will likely rule 5-4."}, "prediction"),
    ],
)
def test_unsupported_status_identity_citation_and_prediction_fail_closed(
    item_update: dict[str, object], message: str
) -> None:
    evidence = block("Is that rule consistent with Smith v. Jones, 599 U. S. 100?")
    item = proposed(
        evidence,
        "Is that rule consistent with Smith v. Jones, 599 U. S. 100?",
    ).model_copy(update=item_update)
    assert process(source(evidence), item) == []


def test_rejected_observation_exposes_only_a_fixed_safe_code() -> None:
    evidence = block("What text supports the rule?")
    invalid = proposed(evidence, "private invented quote")
    service = LegalExtractionService(
        FakeExtractor([invalid]), InMemoryLegalObservationStore()
    )
    assert service.process(source(evidence)) == []
    assert service.rejection_codes == [
        "evidence_quote_does_not_exactly_match_source_block"
    ]
    assert "private invented quote" not in " ".join(service.rejection_codes)


def test_transcript_cannot_establish_holding_or_requested_reversal() -> None:
    evidence = block("The judgment should be reversed.", speaker_name=None)
    holding = proposed(
        evidence,
        "The judgment should be reversed.",
        observation_type=LegalObservationType.HOLDING,
        status=LegalStatus.COURT_HELD,
        raw_value="The Court reversed.",
        speaker_name=None,
    )
    assert process(source(evidence), holding) == []

    request = holding.model_copy(
        update={
            "observation_type": LegalObservationType.REQUESTED_DISPOSITION,
            "legal_status": LegalStatus.REQUESTED,
            "raw_value": "Counsel requested reversal.",
            "attribution": "Counsel for petitioner",
        }
    )
    observations = process(source(evidence), request)
    assert observations[0].legal_status is LegalStatus.REQUESTED


def test_opinion_rejects_invented_action_and_named_party_despite_real_quote() -> None:
    evidence = block(
        "The Court granted relief to Example Agency.",
        kind=ScotusDocumentKind.OPINION,
        speaker_name=None,
    )
    invented_action = proposed(
        evidence,
        "The Court granted relief to Example Agency.",
        observation_type=LegalObservationType.HOLDING,
        status=LegalStatus.COURT_HELD,
        raw_value="The Court denied relief to Example Agency.",
        speaker_name=None,
    )
    service = LegalExtractionService(
        FakeExtractor([invented_action]), InMemoryLegalObservationStore()
    )
    assert service.process(source(evidence)) == []
    assert service.rejection_codes == ["unsupported_legal_action"]

    invented_party = invented_action.model_copy(
        update={
            "observation_type": LegalObservationType.CASE_BACKGROUND,
            "legal_status": LegalStatus.DESCRIBED,
            "raw_value": "Acme Corporation requested review.",
            "normalized_value": None,
        }
    )
    service = LegalExtractionService(
        FakeExtractor([invented_party]), InMemoryLegalObservationStore()
    )
    assert service.process(source(evidence)) == []
    assert service.rejection_codes == ["unsupported_named_party"]


def test_lower_court_action_in_opinion_cannot_become_supreme_court_holding() -> None:
    evidence = block(
        "The Court of Appeals denied relief.",
        kind=ScotusDocumentKind.OPINION,
        speaker_name=None,
    )
    item = proposed(
        evidence,
        "The Court of Appeals denied relief.",
        observation_type=LegalObservationType.HOLDING,
        status=LegalStatus.COURT_HELD,
        raw_value="The Court of Appeals denied relief.",
        speaker_name=None,
    )
    service = LegalExtractionService(
        FakeExtractor([item]), InMemoryLegalObservationStore()
    )
    assert service.process(source(evidence)) == []
    assert service.rejection_codes == ["unsupported_court_attribution"]


def test_opinion_can_support_holding() -> None:
    evidence = block(
        "We hold that the statute does not authorize the action.",
        kind=ScotusDocumentKind.OPINION,
        speaker_name=None,
    )
    item = proposed(
        evidence,
        "We hold that the statute does not authorize the action.",
        observation_type=LegalObservationType.HOLDING,
        status=LegalStatus.COURT_HELD,
        raw_value="We hold that the statute does not authorize the action.",
        speaker_name=None,
    )
    assert process(source(evidence), item)[0].legal_status is LegalStatus.COURT_HELD


def test_advocate_contention_requires_attribution() -> None:
    evidence = block("The statute does not authorize the action.", speaker_name=None)
    item = proposed(
        evidence,
        "The statute does not authorize the action.",
        observation_type=LegalObservationType.ADVOCATE_CONTENTION,
        status=LegalStatus.ASSERTED,
        raw_value="The statute does not authorize the action.",
        speaker_name=None,
    )
    assert process(source(evidence), item) == []


def test_normalization_reference_detection_and_sensitivity() -> None:
    assert normalize_docket_reference(" 25\N{EN DASH}466 ") == "25-466"
    assert normalize_docket_list(("25-466", " 25-466 ", "25-467")) == (
        "25-466",
        "25-467",
    )
    assert normalize_legal_citation("599 U. S. 100") == "599 U.S. 100"
    assert normalize_advocate_role("Counsel for the United States") is AdvocateRole.UNITED_STATES
    assert normalize_court("D.C. Circuit") == "U.S. Court of Appeals for the D.C. Circuit"
    assert normalize_disposition("REVERSE") == "reversed"
    assert normalize_constitutional_reference("U.S. Const. Amend. I") == (
        "U.S. Constitution Amendment I"
    )
    references = find_supported_legal_references(
        "Smith v. Jones, 599 U. S. 100 and 42 U. S. C. § 1983"
    )
    assert "Smith v. Jones, 599 U.S. 100" in references
    assert "42 U.S.C. § 1983" in references

    evidence = block(
        "The minor receives medical treatment at 100 Main Street.", speaker_name=None
    )
    item = proposed(
        evidence,
        "The minor receives medical treatment at 100 Main Street.",
        observation_type=LegalObservationType.CASE_BACKGROUND,
        status=LegalStatus.DESCRIBED,
        raw_value="The record describes sensitive personal circumstances.",
        speaker_name=None,
    )
    labels = set(process(source(evidence), item)[0].sensitivity)
    assert labels >= {
        ScotusSensitivity.MINOR,
        ScotusSensitivity.MEDICAL,
        ScotusSensitivity.HOME_ADDRESS,
        ScotusSensitivity.PRIVATE_NAME,
    }

    middle_name = sensitivity_labels(
        "The record identifies Jane A. Doe as a minor and protected victim."
    )
    assert ScotusSensitivity.PRIVATE_NAME in middle_name


def test_extraction_replay_is_idempotent() -> None:
    evidence = block("Is that rule consistent with the statute?")
    source_value = source(evidence)
    item = proposed(evidence, "Is that rule consistent with the statute?")
    store = InMemoryLegalObservationStore()
    service = LegalExtractionService(FakeExtractor([item]), store)
    first = service.process(source_value)
    duplicate = service.process(source_value)
    assert [value.observation_id for value in duplicate] == [
        value.observation_id for value in first
    ]
