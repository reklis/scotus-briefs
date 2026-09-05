from datetime import UTC, datetime
from uuid import uuid4

from ragchew.scotus.contracts import (
    LegalCertainty,
    LegalEvidenceRange,
    LegalObservation,
    LegalObservationType,
    LegalStatus,
    ScotusCaseStatus,
    ScotusDocumentKind,
)
from ragchew.scotus.correlation import ScotusCorrelationEngine

NOW = datetime(2026, 8, 28, 2, tzinfo=UTC)


def observation(
    observation_type: LegalObservationType,
    status: LegalStatus,
    kind: ScotusDocumentKind,
    *,
    case_id=None,
    value: str = "The statute does not authorize the action.",
    citations: tuple[str, ...] = (),
    supersedes=None,
) -> LegalObservation:
    case_id = case_id or uuid4()
    attributed_types = {
        LegalObservationType.ADVOCATE_CONTENTION,
        LegalObservationType.ANSWER,
        LegalObservationType.CONCESSION,
        LegalObservationType.DISPUTED_PREMISE,
        LegalObservationType.REQUESTED_DISPOSITION,
    }
    return LegalObservation(
        extraction_revision_id=uuid4(),
        case_id=case_id,
        argument_id=uuid4(),
        observation_type=observation_type,
        legal_status=status,
        certainty=LegalCertainty.DIRECT,
        raw_value_private=value,
        normalized_value_private=value,
        attribution="Counsel" if observation_type in attributed_types else None,
        authority_citations=citations,
        confidence=1,
        evidence=(
            LegalEvidenceRange(
                document_revision_id=uuid4(),
                document_kind=kind,
                start_file_page=1,
                start_line=1,
                end_file_page=1,
                end_line=2,
                quote_private=value,
            ),
        ),
        supersedes_observation_id=supersedes,
    )


def test_transcript_questions_and_requests_cannot_finalize_case() -> None:
    case_id = uuid4()
    observations = (
        observation(
            LegalObservationType.JUSTICE_QUESTION,
            LegalStatus.QUESTIONED,
            ScotusDocumentKind.TRANSCRIPT,
            case_id=case_id,
            value="Does your rule apply to a different statute?",
        ),
        observation(
            LegalObservationType.REQUESTED_DISPOSITION,
            LegalStatus.REQUESTED,
            ScotusDocumentKind.TRANSCRIPT,
            case_id=case_id,
            value="The judgment should be reversed.",
        ),
    )
    result = ScotusCorrelationEngine().correlate(
        case_id, ScotusCaseStatus.DOCKETED, observations, NOW
    )
    assert result.aggregate.status is ScotusCaseStatus.ARGUED
    assert result.aggregate.status is not ScotusCaseStatus.DECIDED


def test_later_opinion_decides_case_but_preserves_argument_issues() -> None:
    case_id = uuid4()
    question = observation(
        LegalObservationType.JUSTICE_QUESTION,
        LegalStatus.QUESTIONED,
        ScotusDocumentKind.TRANSCRIPT,
        case_id=case_id,
        value="Does the statute authorize this action?",
        citations=("Smith v. Jones, 599 U.S. 100",),
    )
    holding = observation(
        LegalObservationType.HOLDING,
        LegalStatus.COURT_HELD,
        ScotusDocumentKind.OPINION,
        case_id=case_id,
    )
    result = ScotusCorrelationEngine().correlate(
        case_id, ScotusCaseStatus.ARGUED, (question, holding), NOW
    )
    assert result.aggregate.status is ScotusCaseStatus.DECIDED
    assert set(result.aggregate.observation_ids) == {
        question.observation_id,
        holding.observation_id,
    }
    assert len(result.issues) == 2


def test_reargument_and_transcript_correction_are_append_only_states() -> None:
    case_id = uuid4()
    original = observation(
        LegalObservationType.ADVOCATE_CONTENTION,
        LegalStatus.ASSERTED,
        ScotusDocumentKind.TRANSCRIPT,
        case_id=case_id,
        value="The rule is categorical.",
    )
    reargued = ScotusCorrelationEngine().correlate(
        case_id, ScotusCaseStatus.ARGUED, (original,), NOW, reargued=True
    )
    assert reargued.aggregate.status is ScotusCaseStatus.REARGUED

    corrected = observation(
        LegalObservationType.ADVOCATE_CONTENTION,
        LegalStatus.ASSERTED,
        ScotusDocumentKind.TRANSCRIPT,
        case_id=case_id,
        value="The rule is not categorical.",
        supersedes=original.observation_id,
    )
    result = ScotusCorrelationEngine().correlate(
        case_id, ScotusCaseStatus.REARGUED, (original, corrected), NOW
    )
    assert result.aggregate.status is ScotusCaseStatus.CORRECTED
    assert set(result.aggregate.observation_ids) == {
        original.observation_id,
        corrected.observation_id,
    }


def test_typed_final_court_action_outranks_reargument_and_correction() -> None:
    case_id = uuid4()
    prior = uuid4()
    order = observation(
        LegalObservationType.ORDER,
        LegalStatus.COURT_ORDERED,
        ScotusDocumentKind.OPINION,
        case_id=case_id,
        value="The Court denied the application.",
        supersedes=prior,
    )
    ordered = ScotusCorrelationEngine().correlate(
        case_id,
        ScotusCaseStatus.REARGUED,
        (order,),
        NOW,
        reargued=True,
    )
    assert ordered.aggregate.status is ScotusCaseStatus.ORDER_ISSUED

    holding = observation(
        LegalObservationType.HOLDING,
        LegalStatus.COURT_HELD,
        ScotusDocumentKind.OPINION,
        case_id=case_id,
        value="The Court held that the statute controls.",
    )
    decided = ScotusCorrelationEngine().correlate(
        case_id,
        ScotusCaseStatus.ORDER_ISSUED,
        (order, holding),
        NOW,
        reargued=True,
    )
    assert decided.aggregate.status is ScotusCaseStatus.DECIDED


def test_issue_grouping_uses_authorities_and_question_identity() -> None:
    case_id = uuid4()
    first = observation(
        LegalObservationType.AUTHORITY_CITATION,
        LegalStatus.DESCRIBED,
        ScotusDocumentKind.TRANSCRIPT,
        case_id=case_id,
        citations=("Smith v. Jones, 599 U.S. 100",),
    )
    second = observation(
        LegalObservationType.ADVOCATE_CONTENTION,
        LegalStatus.ASSERTED,
        ScotusDocumentKind.TRANSCRIPT,
        case_id=case_id,
        citations=("Smith v. Jones, 599 U.S. 100",),
    )
    question = observation(
        LegalObservationType.QUESTION_PRESENTED,
        LegalStatus.DESCRIBED,
        ScotusDocumentKind.DOCKET,
        case_id=case_id,
        value="Whether the agency exceeded its statutory authority.",
    )
    result = ScotusCorrelationEngine().correlate(
        case_id, ScotusCaseStatus.ARGUED, (first, second, question), NOW
    )
    assert len(result.issues) == 2
    grouped = next(issue for issue in result.issues if issue.issue_key.startswith("authority:"))
    assert set(grouped.observation_ids) == {first.observation_id, second.observation_id}


def test_similar_captions_cannot_merge_distinct_case_ids_and_replay_is_deterministic() -> None:
    engine = ScotusCorrelationEngine()
    first_case = uuid4()
    second_case = uuid4()
    first_observation = observation(
        LegalObservationType.PROCEDURAL_POSTURE,
        LegalStatus.DESCRIBED,
        ScotusDocumentKind.DOCKET,
        case_id=first_case,
        value="The court of appeals affirmed.",
    )
    second_observation = observation(
        LegalObservationType.PROCEDURAL_POSTURE,
        LegalStatus.DESCRIBED,
        ScotusDocumentKind.DOCKET,
        case_id=second_case,
        value="The court of appeals affirmed.",
    )
    first = engine.correlate(first_case, ScotusCaseStatus.DOCKETED, (first_observation,), NOW)
    second = engine.correlate(second_case, ScotusCaseStatus.DOCKETED, (second_observation,), NOW)
    replay = engine.replay(first_case, ScotusCaseStatus.DOCKETED, (first_observation,), NOW)
    assert first.aggregate.case_id != second.aggregate.case_id
    assert replay == first
