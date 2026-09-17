from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ragchew.scotus.contracts import (
    AdvocateRole,
    BriefMaturity,
    ScotusCaseStatus,
    ScotusDocumentKind,
    SpeakerIdentityBasis,
    SpeakerKind,
)
from ragchew.scotus.extraction import LegalEvidenceBlock
from ragchew.scotus.reader_qa import (
    PlainTextReaderWriter,
    ReaderQAError,
    ReaderQAGuideDraft,
    ReaderQuestion,
    ReaderQuestionId,
    ReaderSourcePacketBuilder,
    ReaderSourceRole,
    deterministic_status_and_maturity,
    packet_windows,
    reader_questions,
    source_excerpts,
)

NOW = datetime(2026, 9, 17, tzinfo=UTC)
OPINION_URL = "https://www.supremecourt.gov/opinions/25pdf/test.pdf"
DOCKET_URL = "https://www.supremecourt.gov/docket/docketfiles/html/public/25-1.html"
TRANSCRIPT_URL = "https://www.supremecourt.gov/oral_arguments/argument_transcripts/2025/25-1.pdf"


def block(
    kind: ScotusDocumentKind,
    text: str,
    *,
    page: int = 1,
    url: str = OPINION_URL,
    attribution: str | None = None,
    speaker_kind: SpeakerKind = SpeakerKind.UNKNOWN,
    speaker_name: str | None = None,
    advocate_role: AdvocateRole | None = None,
) -> LegalEvidenceBlock:
    return LegalEvidenceBlock(
        block_id=f"{kind.value}-{page}-{abs(hash(text))}",
        document_revision_id=uuid4(),
        document_kind=kind,
        official_url=url,
        start_file_page=page,
        start_line=1,
        end_file_page=page,
        end_line=2,
        text_private=text,
        speaker_name=speaker_name,
        speaker_kind=speaker_kind,
        advocate_role=advocate_role,
        identity_basis=SpeakerIdentityBasis.ANONYMOUS,
        attribution=attribution,
    )


def completion(text: str | None, *, reasoning: str = "discard me") -> object:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, reasoning=reasoning))]
    )


def corpus() -> tuple[LegalEvidenceBlock, ...]:
    return (
        block(
            ScotusDocumentKind.DOCKET,
            "The applicant asks for a stay and the respondent opposes it.",
            url=DOCKET_URL,
        ),
        block(
            ScotusDocumentKind.OPINION,
            "Syllabus. The dispute concerns a federal rule.",
            page=1,
            attribution="Opinion of the Court",
        ),
        block(
            ScotusDocumentKind.OPINION,
            "The Court grants the application because the applicant is likely to succeed.",
            page=2,
            attribution="Opinion of the Court",
        ),
        block(
            ScotusDocumentKind.OPINION,
            "Justice Example, dissenting, would deny the application.",
            page=3,
            attribution="Justice Example, dissenting",
        ),
        block(
            ScotusDocumentKind.TRANSCRIPT,
            "Counsel says the rule exceeds the agency's authority.",
            url=TRANSCRIPT_URL,
            speaker_kind=SpeakerKind.ADVOCATE,
            speaker_name="Ms. Counsel",
            advocate_role=AdvocateRole.PETITIONER,
        ),
    )


def test_questions_are_fixed_and_pending_wording_never_implies_decision() -> None:
    decided = reader_questions(decided=True)
    pending = reader_questions(decided=False)

    assert tuple(item.question_id for item in decided) == tuple(ReaderQuestionId)
    assert len(decided) == len(pending) == 5
    assert decided[3].wording == "What did the Supreme Court decide?"
    assert pending[3].wording == "What is the Supreme Court being asked to decide?"
    assert not pending[3].wording.startswith("What did")


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            dict(
                primary_docket="26A124",
                has_disposition=True,
                has_correction=False,
                argument_count=0,
                has_reargument=False,
            ),
            (ScotusCaseStatus.ORDER_ISSUED, BriefMaturity.POST_ORDER),
        ),
        (
            dict(
                primary_docket="24-43",
                has_disposition=True,
                has_correction=False,
                argument_count=1,
                has_reargument=False,
            ),
            (ScotusCaseStatus.DECIDED, BriefMaturity.POST_OPINION),
        ),
        (
            dict(
                primary_docket="24-1",
                has_disposition=False,
                has_correction=False,
                argument_count=2,
                has_reargument=True,
            ),
            (ScotusCaseStatus.REARGUED, BriefMaturity.OFFICIAL_TRANSCRIPT),
        ),
        (
            dict(
                primary_docket="24-1",
                has_disposition=True,
                has_correction=True,
                argument_count=1,
                has_reargument=False,
            ),
            (ScotusCaseStatus.CORRECTED, BriefMaturity.CORRECTED),
        ),
    ],
)
def test_status_and_maturity_are_observation_independent(
    kwargs: dict[str, object], expected: tuple[ScotusCaseStatus, BriefMaturity]
) -> None:
    assert deterministic_status_and_maturity(**kwargs) == expected  # type: ignore[arg-type]


def test_packets_label_sources_and_exclude_separate_opinions() -> None:
    excerpts = source_excerpts(corpus())
    assert {item.role for item in excerpts} == {
        ReaderSourceRole.DOCKET,
        ReaderSourceRole.REPORTER_SYLLABUS,
        ReaderSourceRole.CONTROLLING_OPINION,
        ReaderSourceRole.SEPARATE_OPINION,
        ReaderSourceRole.ADVOCATE,
    }

    packets = ReaderSourcePacketBuilder(maximum_characters=10_000).build(
        reader_questions(decided=True), corpus()
    )
    assert len(packets) == 5
    assert all(
        excerpt.role is not ReaderSourceRole.SEPARATE_OPINION
        for packet in packets
        for excerpt in packet.excerpts
    )
    assert all(packet.source_links for packet in packets)

    boundary_roles = source_excerpts(
        (
            block(
                ScotusDocumentKind.OPINION,
                "Continuation of the reporter's summary.",
                page=2,
                attribution="Reporter syllabus",
            ),
            block(
                ScotusDocumentKind.ORDER,
                "Justice Example, dissenting from the order.",
                attribution="Justice Example, dissenting",
            ),
        )
    )
    assert {item.role for item in boundary_roles} == {
        ReaderSourceRole.REPORTER_SYLLABUS,
        ReaderSourceRole.SEPARATE_OPINION,
    }


def test_question_specific_packets_cover_realistic_source_roles_and_late_action() -> None:
    blocks = [
        block(
            ScotusDocumentKind.DOCKET,
            "The applicant filed after the district court entered an injunction.",
            url=DOCKET_URL,
        ),
        block(
            ScotusDocumentKind.OPINION,
            "Syllabus. The applicant argues for relief and the respondent opposes it.",
            page=1,
            attribution="Opinion of the Court",
        ),
        block(
            ScotusDocumentKind.OPINION,
            "The district court granted an injunction and the government appealed.",
            page=2,
            attribution="Opinion of the Court",
        ),
        block(
            ScotusDocumentKind.OPINION,
            "Because the statute limits agency power, the rule cannot stand.",
            page=18,
            attribution="Opinion of the Court",
        ),
        block(
            ScotusDocumentKind.OPINION,
            "We reverse the judgment and vacate the injunction.",
            page=20,
            attribution="Opinion of the Court",
        ),
        block(
            ScotusDocumentKind.OPINION,
            "Justice Example, dissenting, would affirm.",
            page=21,
            attribution="Justice Example, dissenting",
        ),
        block(
            ScotusDocumentKind.TRANSCRIPT,
            "Counsel for applicant asks the Court to reverse.",
            url=TRANSCRIPT_URL,
            speaker_kind=SpeakerKind.ADVOCATE,
            speaker_name="Ms. Petitioner",
            advocate_role=AdvocateRole.PETITIONER,
        ),
        block(
            ScotusDocumentKind.TRANSCRIPT,
            "Counsel for respondent asks the Court to affirm.",
            page=2,
            url=TRANSCRIPT_URL,
            speaker_kind=SpeakerKind.ADVOCATE,
            speaker_name="Mr. Respondent",
            advocate_role=AdvocateRole.RESPONDENT,
        ),
    ]
    decided = ReaderSourcePacketBuilder(maximum_characters=30_000).build(
        reader_questions(decided=True), blocks
    )
    by_id = {packet.question.question_id: packet for packet in decided}

    assert any("We reverse" in item.text_private for item in by_id[ReaderQuestionId.COURT].excerpts)
    assert any(
        item.role is ReaderSourceRole.ADVOCATE
        for item in by_id[ReaderQuestionId.SIDES].excerpts
    )
    assert any(
        "district court" in item.text_private
        for item in by_id[ReaderQuestionId.HISTORY].excerpts
    )
    assert any(
        "Because" in item.text_private
        for item in by_id[ReaderQuestionId.IMPORTANCE].excerpts
    )
    assert all(
        item.role is not ReaderSourceRole.SEPARATE_OPINION
        for packet in decided
        for item in packet.excerpts
    )

    pending_blocks = tuple(
        item
        for item in blocks
        if item.document_kind in {ScotusDocumentKind.DOCKET, ScotusDocumentKind.TRANSCRIPT}
    )
    pending = ReaderSourcePacketBuilder(maximum_characters=30_000).build(
        reader_questions(decided=False), pending_blocks
    )
    pending_court = pending[3]
    assert pending_court.question.wording == (
        "What is the Supreme Court being asked to decide?"
    )
    assert any(item.role is ReaderSourceRole.ADVOCATE for item in pending_court.excerpts)
    sides = pending[1]
    assert {item.advocate_role for item in sides.excerpts} >= {
        AdvocateRole.PETITIONER,
        AdvocateRole.RESPONDENT,
    }


def test_pending_packet_rejects_inconsistent_disposition_material() -> None:
    with pytest.raises(ReaderQAError) as caught:
        ReaderSourcePacketBuilder(maximum_characters=30_000).build(
            reader_questions(decided=False), corpus()
        )
    assert caught.value.safe_code == "inconsistent_source_state"


def test_packet_windows_process_every_character_or_fail() -> None:
    material = (
        block(ScotusDocumentKind.OPINION, "a" * 700, attribution="Opinion of the Court"),
    )
    packet = ReaderSourcePacketBuilder(
        maximum_characters=500, maximum_windows=4
    ).build((reader_questions(decided=True)[0],), material)[0]
    windows = packet_windows(packet)
    reconstructed = "".join(item.text_private for window in windows for item in window)
    assert reconstructed == "a" * 700

    with pytest.raises(ReaderQAError) as caught:
        ReaderSourcePacketBuilder(maximum_characters=500, maximum_windows=1).build(
            (reader_questions(decided=True)[0],), material
        )
    assert caught.value.safe_code == "question_source_too_large"


def test_packet_bound_includes_rendered_advocate_metadata() -> None:
    oversized_metadata = block(
        ScotusDocumentKind.TRANSCRIPT,
        "A",
        url=TRANSCRIPT_URL,
        speaker_kind=SpeakerKind.ADVOCATE,
        speaker_name="X" * 300,
        advocate_role=AdvocateRole.PETITIONER,
    )
    with pytest.raises(ReaderQAError) as caught:
        ReaderSourcePacketBuilder(maximum_characters=100).build(
            (reader_questions(decided=True)[1],),
            (oversized_metadata,),
        )
    assert caught.value.safe_code == "question_source_too_large"


def test_writer_uses_plain_text_requests_and_preserves_json_looking_answer() -> None:
    requests: list[dict[str, object]] = []

    def execute(
        request: dict[str, object], **metadata: object
    ) -> object:
        requests.append({**request, "metadata": metadata})
        return completion('  {"awkward": "but reviewable"}  ')

    packet = ReaderSourcePacketBuilder(maximum_characters=10_000).build(
        (reader_questions(decided=True)[1],), corpus()
    )[0]
    answer = PlainTextReaderWriter("local-test", execute).answer(packet)

    assert answer.text == '{"awkward": "but reviewable"}'
    assert "response_format" not in requests[0]
    assert requests[0]["metadata"] == {
        "question_id": ReaderQuestionId.SIDES,
        "phase": "direct",
        "window_index": 0,
    }
    messages = requests[0]["messages"]
    assert messages[1]["content"].startswith("Question: What does each side want?")
    assert "advocate_role=petitioner; speaker=Ms. Counsel" in messages[1]["content"]
    assert "Official Court material:" in messages[1]["content"]
    assert "discard me" not in answer.text
    assert answer.source_ranges == packet.excerpts


@pytest.mark.parametrize("value", [None, "", "   "])
def test_writer_rejects_missing_or_blank_final_content(value: str | None) -> None:
    packet = ReaderSourcePacketBuilder(maximum_characters=10_000).build(
        (reader_questions(decided=True)[0],), corpus()
    )[0]
    writer = PlainTextReaderWriter("local-test", lambda request, **metadata: completion(value))

    with pytest.raises(ReaderQAError) as caught:
        writer.answer(packet)
    assert caught.value.safe_code == "empty_content"


def test_long_packet_maps_then_synthesizes_without_persisting_drafts() -> None:
    calls: list[tuple[str, int]] = []
    synthesis_user = ""

    def execute(request: dict[str, object], **metadata: object) -> object:
        nonlocal synthesis_user
        phase = str(metadata["phase"])
        calls.append((phase, int(metadata["window_index"])))
        if phase == "synthesis":
            synthesis_user = request["messages"][1]["content"]
        return completion("Final answer." if phase == "synthesis" else "Window fact.")

    material = (
        block(ScotusDocumentKind.OPINION, "a" * 700, attribution="Opinion of the Court"),
    )
    packet = ReaderSourcePacketBuilder(maximum_characters=500, maximum_windows=4).build(
        (reader_questions(decided=True)[0],), material
    )[0]
    answer = PlainTextReaderWriter("local-test", execute).answer(packet)

    assert answer.text == "Final answer."
    assert calls == [("map", 0), ("map", 1), ("synthesis", 2)]
    assert "source roles: controlling_opinion" in synthesis_user
    assert "source ranges: page 1, lines 1-2" in synthesis_user


def test_writer_rejects_output_beyond_the_transport_character_bound() -> None:
    packet = ReaderSourcePacketBuilder(maximum_characters=10_000).build(
        (reader_questions(decided=True)[0],), corpus()
    )[0]
    writer = PlainTextReaderWriter(
        "local-test",
        lambda request, **metadata: completion("x" * 17),
        maximum_output_tokens=1,
    )

    with pytest.raises(ReaderQAError) as caught:
        writer.answer(packet)
    assert caught.value.safe_code == "output_too_large"


def test_guide_requires_exactly_five_ordered_answers() -> None:
    packets = ReaderSourcePacketBuilder(maximum_characters=10_000).build(
        reader_questions(decided=True), corpus()
    )
    writer = PlainTextReaderWriter(
        "local-test", lambda request, **metadata: completion("A plain answer.")
    )
    guide = writer.generate(
        title="Example v. Agency",
        packets=packets,
        maturity=BriefMaturity.POST_OPINION,
        created_at=NOW,
    )

    assert isinstance(guide, ReaderQAGuideDraft)
    assert len(guide.answers) == 5
    assert guide.manual_review_required

    changed_question = ReaderQuestion(
        ReaderQuestionId.ABOUT,
        "Tell me anything.",
        "Model-selected heading",
    )
    with pytest.raises(ValueError, match="five exact ordered questions"):
        replace(
            guide,
            answers=(
                replace(guide.answers[0], question=changed_question),
                *guide.answers[1:],
            ),
        )
