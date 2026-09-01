from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ragchew.config import ScotusConfig
from ragchew.scotus.briefs import (
    BriefCandidate,
    BriefGenerationService,
    CaseArgumentSession,
    DraftArgumentAnalysis,
    DraftSection,
    InMemoryBriefRevisionStore,
    LegalBriefDraft,
    evaluate_brief_candidate,
)
from ragchew.scotus.contracts import (
    BriefMaturity,
    LegalCertainty,
    LegalObservationType,
    LegalStatus,
    ScotusCaseStatus,
    ScotusDocumentKind,
)
from ragchew.scotus.correlation import ScotusCorrelationEngine
from ragchew.scotus.extraction import (
    InMemoryLegalObservationStore,
    LegalExtractionBatch,
    LegalExtractionInput,
    LegalExtractionService,
    ProposedEvidence,
    ProposedLegalObservation,
    document_text_block,
    transcript_turn_block,
)
from ragchew.scotus.public_contracts import (
    PublicBriefRevisionSummary,
    PublicCaseHistoryEvent,
)
from ragchew.scotus.publishing import InMemoryScotusProjectionStore, build_public_case
from ragchew.scotus.transcript_parser import PdfTextBackend, ScotusTranscriptParser

NOW = datetime(2026, 8, 28, 2, tzinfo=UTC)
CASE_ID = uuid4()
ARGUMENT_ID = uuid4()
TRANSCRIPT_ID = uuid4()
DOCKET_ID = uuid4()


class Pages(PdfTextBackend):
    name = "official-transcript-fixture"
    version = "1"

    def __init__(self, contention: str):
        self.contention = contention

    def extract_pages(self, file: object) -> tuple[str, ...]:  # type: ignore[override]
        return (
            "\n".join(
                (
                    "Official - Subject to Final Review",
                    "1 MR. SMITH: " + self.contention,
                    "2 JUSTICE KAGAN: Does that rule fit the statutory text?",
                    "3 MR. SMITH: Yes, because the text addresses agency authority.",
                )
            ),
        )


class Extractor:
    model_name = "legal-fixture"
    PROMPT_VERSION = "fixture-v1"

    def __init__(self, proposals: list[ProposedLegalObservation]):
        self.proposals = proposals

    def extract(self, source: LegalExtractionInput) -> LegalExtractionBatch:
        return LegalExtractionBatch(observations=self.proposals)


class Generator:
    model_name = "brief-fixture"

    def generate(self, candidate, claims, maturity):  # type: ignore[no-untyped-def]
        ids = tuple(claim.claim_id for claim in claims)
        return LegalBriefDraft(
            title="Did the agency have the power to act?",
            title_claim_ids=(ids[0],),
            dek="The two sides disagree about the power Congress gave the agency.",
            dek_claim_ids=ids[:2],
            sections=tuple(
                DraftSection(
                    heading=heading,
                    paragraphs=(
                        "The case asks whether Congress gave the agency the power to act.",
                    ),
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
                        "The two sides explained their different readings of the law.",
                        "The justices tested how each reading would work in practice.",
                    ),
                    claim_ids=ids,
                ),
            ),
        )


def run_analysis(contention: str, *, supersedes=None):  # type: ignore[no-untyped-def]
    parser = ScotusTranscriptParser(
        Pages(contention), ScotusConfig.from_yaml("config/scotus.yaml").parser
    )
    parsed = parser.parse(
        io.BytesIO(), parse_revision_id=uuid4(), document_revision_id=TRANSCRIPT_ID
    )
    transcript_url = "https://www.supremecourt.gov/official-transcript.pdf"
    blocks = tuple(transcript_turn_block(turn, transcript_url) for turn in parsed.turns)
    docket = document_text_block(
        document_revision_id=DOCKET_ID,
        kind=ScotusDocumentKind.DOCKET,
        official_url="https://www.supremecourt.gov/docket.html",
        file_page=1,
        start_line=1,
        end_line=2,
        text="Whether the agency exceeded its statutory authority.",
        label="Question presented",
    )
    proposals = [
        ProposedLegalObservation(
            observation_type=LegalObservationType.QUESTION_PRESENTED,
            legal_status=LegalStatus.DESCRIBED,
            certainty=LegalCertainty.DIRECT,
            raw_value="Whether the agency exceeded its statutory authority.",
            confidence=1,
            evidence=(
                ProposedEvidence(
                    block_id=docket.block_id,
                    quote="Whether the agency exceeded its statutory authority.",
                ),
            ),
        ),
        ProposedLegalObservation(
            observation_type=LegalObservationType.ADVOCATE_CONTENTION,
            legal_status=LegalStatus.ASSERTED,
            certainty=LegalCertainty.ATTRIBUTED,
            raw_value=contention,
            attribution="Mr. Smith, counsel",
            speaker_name=parsed.turns[0].speaker_name,
            speaker_kind=parsed.turns[0].speaker_kind,
            identity_basis=parsed.turns[0].identity_basis,
            confidence=1,
            evidence=(
                ProposedEvidence(block_id=blocks[0].block_id, quote=contention),
            ),
            supersedes_observation_id=supersedes,
        ),
        ProposedLegalObservation(
            observation_type=LegalObservationType.JUSTICE_QUESTION,
            legal_status=LegalStatus.QUESTIONED,
            certainty=LegalCertainty.ATTRIBUTED,
            raw_value="Justice Kagan asked whether the rule fit the statutory text.",
            speaker_name=parsed.turns[1].speaker_name,
            speaker_kind=parsed.turns[1].speaker_kind,
            identity_basis=parsed.turns[1].identity_basis,
            confidence=1,
            evidence=(
                ProposedEvidence(
                    block_id=blocks[1].block_id,
                    quote="Does that rule fit the statutory text?",
                ),
            ),
        ),
    ]
    extraction_input = LegalExtractionInput(
        case_id=CASE_ID,
        argument_id=ARGUMENT_ID,
        blocks=(*blocks, docket),
        parser_versions=("official-transcript-fixture:1",),
        document_revision_ids=(TRANSCRIPT_ID, DOCKET_ID),
    )
    return LegalExtractionService(
        Extractor(proposals), InMemoryLegalObservationStore()
    ).process(extraction_input)


def make_candidate(observations, status):  # type: ignore[no-untyped-def]
    return BriefCandidate(
        case_id=CASE_ID,
        argument_id=ARGUMENT_ID,
        caption="Example v. Agency",
        primary_docket="25-100",
        case_status=status,
        official_transcript_complete=True,
        parser_complete=True,
        privacy_blocking_failure=False,
        argument_sessions=(
            CaseArgumentSession(
                argument_id=ARGUMENT_ID,
                argument_date=NOW,
                sequence=1,
                reargument=False,
                official_detail_url="https://www.supremecourt.gov/argument",
                official_transcript_url=(
                    "https://www.supremecourt.gov/official-transcript.pdf"
                ),
            ),
        ),
        observations=tuple(observations),
        document_urls={
            TRANSCRIPT_ID: "https://www.supremecourt.gov/official-transcript.pdf",
            DOCKET_ID: "https://www.supremecourt.gov/docket.html",
        },
        evaluated_at=NOW,
    )


def test_transcript_to_brief_projection_correction_and_failed_rollback() -> None:
    observations = run_analysis(
        "The statute does not authorize the agency's challenged action."
    )
    correlated = ScotusCorrelationEngine().correlate(
        CASE_ID, ScotusCaseStatus.DOCKETED, tuple(observations), NOW
    )
    assert correlated.aggregate.status is ScotusCaseStatus.ARGUED
    candidate = make_candidate(observations, correlated.aggregate.status)
    decision = evaluate_brief_candidate(candidate, minimum_confidence=0.85)
    assert decision.eligible
    brief_store = InMemoryBriefRevisionStore()
    generation = BriefGenerationService(Generator(), brief_store)
    first = generation.generate(candidate, decision, revision_number=1)
    first_public = build_public_case(
        term="2025",
        primary_docket="25-100",
        caption="Example v. Agency",
        argument_date=datetime(2026, 4, 20, tzinfo=UTC),
        case_status=correlated.aggregate.status,
        official_detail_url="https://www.supremecourt.gov/oral_arguments/audio/2025/25-100",
        revision=first,
        claims=decision.claims,
        argument_sessions=candidate.argument_sessions,
        case_history=(
            PublicCaseHistoryEvent(
                status=correlated.aggregate.status,
                changed_at=NOW,
                explanation="The Court heard oral argument.",
            ),
        ),
        revision_history=(
            PublicBriefRevisionSummary(
                revision_number=1,
                maturity=BriefMaturity.OFFICIAL_TRANSCRIPT,
                created_at=NOW,
            ),
        ),
    )
    projections = InMemoryScotusProjectionStore()
    safe = projections.activate(NOW, NOW, (first_public,))

    corrected_observations = run_analysis(
        "The statute does not categorically authorize the challenged action.",
        supersedes=observations[1].observation_id,
    )
    corrected_state = ScotusCorrelationEngine().correlate(
        CASE_ID,
        correlated.aggregate.status,
        tuple((*observations, *corrected_observations)),
        NOW + timedelta(hours=1),
    )
    assert corrected_state.aggregate.status is ScotusCaseStatus.CORRECTED
    corrected_candidate = make_candidate(
        (*observations, *corrected_observations), corrected_state.aggregate.status
    )
    corrected_candidate = BriefCandidate(
        **{
            **corrected_candidate.__dict__,
            "evaluated_at": NOW + timedelta(hours=1),
        }
    )
    corrected_decision = evaluate_brief_candidate(
        corrected_candidate, minimum_confidence=0.85
    )
    corrected = generation.generate(
        corrected_candidate,
        corrected_decision,
        revision_number=2,
        correction_note="Updated after the revised official transcript.",
    )
    corrected_public = build_public_case(
        term="2025",
        primary_docket="25-100",
        caption="Example v. Agency",
        argument_date=datetime(2026, 4, 20, tzinfo=UTC),
        case_status=ScotusCaseStatus.CORRECTED,
        official_detail_url="https://www.supremecourt.gov/oral_arguments/audio/2025/25-100",
        revision=corrected,
        claims=corrected_decision.claims,
        argument_sessions=corrected_candidate.argument_sessions,
        case_history=(
            PublicCaseHistoryEvent(
                status=ScotusCaseStatus.CORRECTED,
                changed_at=NOW + timedelta(hours=1),
                explanation="Official case material or analysis was corrected.",
            ),
        ),
        revision_history=(
            *first_public.revisions,
            PublicBriefRevisionSummary(
                revision_number=2,
                maturity=BriefMaturity.CORRECTED,
                created_at=NOW + timedelta(hours=1),
                correction_note="Updated after the revised official transcript.",
            ),
        ),
    )
    active = projections.activate(
        NOW + timedelta(hours=1), NOW + timedelta(hours=1), (corrected_public,)
    )
    assert active.cases[0].maturity is BriefMaturity.CORRECTED
    assert active.cases[0].revisions[-1].correction_note is not None

    projections.fail_activation = True
    with pytest.raises(RuntimeError, match="activation"):
        projections.activate(
            NOW + timedelta(hours=2), NOW + timedelta(hours=2), (first_public,)
        )
    assert projections.active_projection() is active
    assert projections.active_projection() is not safe
