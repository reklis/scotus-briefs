"""Direct plain-text Citizen's Guide questions over official Court material."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from ragchew.scotus.contracts import (
    AdvocateRole,
    BriefMaturity,
    ScotusCaseStatus,
    ScotusDocumentKind,
    SpeakerKind,
)
from ragchew.scotus.extraction import LegalEvidenceBlock
from ragchew.scotus.public_contracts import PublicSourceLink

READER_QA_QUESTION_VERSION = "scotus-citizen-questions-v1"
READER_QA_PACKET_VERSION = "scotus-question-packets-v2"
READER_QA_PROMPT_VERSION = "scotus-plain-text-qa-v1"
READER_QA_SYNTHESIS_VERSION = "scotus-plain-text-synthesis-v1"
DEFAULT_PACKET_CHARACTERS = 30_000
DEFAULT_MAXIMUM_WINDOWS = 4
DEFAULT_OUTPUT_TOKENS = 500
_MAXIMUM_ANSWER_CHARACTERS_PER_TOKEN = 16


class ReaderQAError(ValueError):
    """A privacy-safe direct Guide failure."""

    def __init__(self, message: str, *, safe_code: str) -> None:
        super().__init__(message)
        self.safe_code = safe_code


class ReaderQuestionId(StrEnum):
    ABOUT = "about"
    SIDES = "sides"
    HISTORY = "history"
    COURT = "court"
    IMPORTANCE = "importance"


class ReaderSourceRole(StrEnum):
    DOCKET = "docket"
    REPORTER_SYLLABUS = "reporter_syllabus"
    CONTROLLING_OPINION = "controlling_opinion"
    ORDER = "order"
    TRANSCRIPT = "transcript"
    ADVOCATE = "advocate"
    SEPARATE_OPINION = "separate_opinion"


@dataclass(frozen=True, slots=True)
class ReaderQuestion:
    question_id: ReaderQuestionId
    wording: str
    heading: str


@dataclass(frozen=True, slots=True)
class ReaderSourceExcerpt:
    document_revision_id: UUID
    document_kind: ScotusDocumentKind
    official_url: str
    role: ReaderSourceRole
    page_label: str
    block_order: int
    text_private: str
    speaker_name: str | None = None
    advocate_role: AdvocateRole | None = None

    def __post_init__(self) -> None:
        if not self.official_url or not self.page_label or not self.text_private.strip():
            raise ValueError("reader source excerpt is incomplete")
        if self.block_order < 0:
            raise ValueError("reader source order cannot be negative")

    def public_link(self) -> PublicSourceLink:
        label = self.role.value.replace("_", " ")
        return PublicSourceLink(
            evidence_type=self.role.value,
            label=f"Official Supreme Court {label} — {self.page_label}",
            official_url=self.official_url,
            page_label=self.page_label,
        )


@dataclass(frozen=True, slots=True)
class ReaderSourcePacket:
    question: ReaderQuestion
    excerpts: tuple[ReaderSourceExcerpt, ...]
    maximum_characters: int = DEFAULT_PACKET_CHARACTERS
    maximum_windows: int = DEFAULT_MAXIMUM_WINDOWS

    def __post_init__(self) -> None:
        if not self.excerpts:
            raise ReaderQAError(
                "reader question has no authorized official material",
                safe_code="missing_question_source",
            )
        if self.maximum_characters < 1 or self.maximum_windows < 1:
            raise ValueError("reader packet bounds must be positive")

    @property
    def source_links(self) -> tuple[PublicSourceLink, ...]:
        links: list[PublicSourceLink] = []
        seen: set[tuple[str, str, str]] = set()
        for excerpt in self.excerpts:
            link = excerpt.public_link()
            key = (link.evidence_type, link.official_url, link.page_label)
            if key not in seen:
                seen.add(key)
                links.append(link)
        return tuple(links)


@dataclass(frozen=True, slots=True)
class ReaderAnswer:
    question: ReaderQuestion
    text: str
    source_ranges: tuple[ReaderSourceExcerpt, ...]

    def __post_init__(self) -> None:
        if not self.text.strip() or not self.source_ranges:
            raise ValueError("reader answer is incomplete")

    @property
    def sources(self) -> tuple[PublicSourceLink, ...]:
        links: list[PublicSourceLink] = []
        seen: set[tuple[str, str, str]] = set()
        for excerpt in self.source_ranges:
            link = excerpt.public_link()
            key = (link.evidence_type, link.official_url, link.page_label)
            if key not in seen:
                seen.add(key)
                links.append(link)
        return tuple(links)


@dataclass(frozen=True, slots=True)
class ReaderQAGuideDraft:
    title: str
    answers: tuple[ReaderAnswer, ...]
    maturity: BriefMaturity
    created_at: datetime
    model_name: str
    manual_review_required: bool = True

    def __post_init__(self) -> None:
        court_question = self.answers[3].question if len(self.answers) == 5 else None
        decided = court_question is not None and court_question.wording == _DECIDED_COURT
        expected = reader_questions(decided=decided)
        actual = tuple(answer.question for answer in self.answers)
        if actual != expected:
            raise ValueError("reader Guide must contain five exact ordered questions")
        if not self.title.strip() or not self.model_name:
            raise ValueError("reader Guide identity is incomplete")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("reader Guide time must be timezone-aware")
        if not self.manual_review_required:
            raise ValueError("generated reader Guide must require manual review")

    @property
    def about(self) -> ReaderAnswer:
        return self.answers[0]


_DECIDED_COURT = "What did the Supreme Court decide?"
_PENDING_COURT = "What is the Supreme Court being asked to decide?"


def reader_questions(*, decided: bool) -> tuple[ReaderQuestion, ...]:
    """Return the complete deterministic public question set."""
    court = _DECIDED_COURT if decided else _PENDING_COURT
    return (
        ReaderQuestion(
            ReaderQuestionId.ABOUT,
            "What is this case about?",
            "What is this case about?",
        ),
        ReaderQuestion(
            ReaderQuestionId.SIDES,
            "What does each side want?",
            "What does each side want?",
        ),
        ReaderQuestion(
            ReaderQuestionId.HISTORY,
            "What has happened in the case so far?",
            "What has happened in the case so far?",
        ),
        ReaderQuestion(ReaderQuestionId.COURT, court, court),
        ReaderQuestion(
            ReaderQuestionId.IMPORTANCE,
            "Why might this matter to ordinary people?",
            "Why might this matter to ordinary people?",
        ),
    )


def deterministic_status_and_maturity(
    *,
    primary_docket: str,
    has_disposition: bool,
    has_correction: bool,
    argument_count: int,
    has_reargument: bool,
) -> tuple[ScotusCaseStatus, BriefMaturity]:
    """Derive public Guide state without model observations."""
    if has_correction:
        return ScotusCaseStatus.CORRECTED, BriefMaturity.CORRECTED
    if has_disposition:
        if argument_count == 0 and "A" in primary_docket.upper():
            return ScotusCaseStatus.ORDER_ISSUED, BriefMaturity.POST_ORDER
        return ScotusCaseStatus.DECIDED, BriefMaturity.POST_OPINION
    if argument_count:
        return (
            ScotusCaseStatus.REARGUED if has_reargument else ScotusCaseStatus.ARGUED,
            BriefMaturity.OFFICIAL_TRANSCRIPT,
        )
    raise ReaderQAError(
        "reader Guide requires a dated argument or disposition",
        safe_code="missing_dated_activity",
    )


def _source_role(block: LegalEvidenceBlock) -> ReaderSourceRole:
    attribution = (block.attribution or "").casefold()
    if "dissent" in attribution or "concurr" in attribution or "separate opinion" in attribution:
        return ReaderSourceRole.SEPARATE_OPINION
    if block.document_kind is ScotusDocumentKind.DOCKET:
        return ReaderSourceRole.DOCKET
    if block.document_kind is ScotusDocumentKind.ORDER:
        return ReaderSourceRole.ORDER
    if block.document_kind is ScotusDocumentKind.TRANSCRIPT:
        return (
            ReaderSourceRole.ADVOCATE
            if block.speaker_kind is SpeakerKind.ADVOCATE
            else ReaderSourceRole.TRANSCRIPT
        )
    if "reporter syllabus" in attribution or (
        "syllabus" in block.text_private.casefold() and block.start_file_page <= 6
    ):
        return ReaderSourceRole.REPORTER_SYLLABUS
    return ReaderSourceRole.CONTROLLING_OPINION


def source_excerpts(blocks: Sequence[LegalEvidenceBlock]) -> tuple[ReaderSourceExcerpt, ...]:
    """Convert parsed blocks to ordered, role-labeled private excerpts."""
    ordered = sorted(
        blocks,
        key=lambda block: (
            block.document_kind.value,
            block.official_url,
            block.start_file_page,
            block.start_line,
            block.block_id,
        ),
    )
    return tuple(
        ReaderSourceExcerpt(
            document_revision_id=block.document_revision_id,
            document_kind=block.document_kind,
            official_url=block.official_url,
            role=_source_role(block),
            page_label=(
                f"page {block.start_file_page}, lines {block.start_line}-{block.end_line}"
            ),
            block_order=index,
            text_private=block.text_private,
            speaker_name=block.speaker_name,
            advocate_role=block.advocate_role,
        )
        for index, block in enumerate(ordered)
    )


_POSITION_TEXT = re.compile(
    r"\b(?:argu\w*|ask\w*|contend\w*|maintain\w*|petitioner\w*|respondent\w*|"
    r"applicant\w*|government|states?)\b",
    re.IGNORECASE,
)
_HISTORY_TEXT = re.compile(
    r"\b(?:appeal\w*|district court|court of appeals|enjoin\w*|injunction|filed|sued|"
    r"granted|denied|proceedings?|judgment)\b",
    re.IGNORECASE,
)
_OPERATIVE_TEXT = re.compile(
    r"\b(?:we (?:hold|conclude|grant|deny|affirm|reverse|vacate|stay)|"
    r"application is (?:granted|denied)|judgment is (?:affirmed|reversed|vacated)|"
    r"it is (?:so )?ordered)\b",
    re.IGNORECASE,
)
_IMPORTANCE_TEXT = re.compile(
    r"\b(?:because|means?|affect\w*|authority|power|rights?|requires?|prohibits?|"
    r"allows?|cannot|may not|therefore|thus)\b",
    re.IGNORECASE,
)
_QUESTION_TEXT = re.compile(
    r"\b(?:question presented|whether|asks? (?:the )?court|petition|application)\b",
    re.IGNORECASE,
)


def _role_items(
    excerpts: Sequence[ReaderSourceExcerpt], role: ReaderSourceRole
) -> tuple[ReaderSourceExcerpt, ...]:
    return tuple(item for item in excerpts if item.role is role)


def _matching(
    values: Sequence[ReaderSourceExcerpt], pattern: re.Pattern[str], *, fallback: int
) -> tuple[ReaderSourceExcerpt, ...]:
    """Keep every matching sentence and its immediate continuation, without page padding."""
    passages: list[ReaderSourceExcerpt] = []
    for item in values:
        sentences = tuple(
            value.strip()
            for value in re.split(r"(?<=[.!?])\s+", item.text_private)
            if value.strip()
        )
        matched = {index for index, sentence in enumerate(sentences) if pattern.search(sentence)}
        if not matched:
            continue
        selected = sorted(matched | {index + 1 for index in matched if index + 1 < len(sentences)})
        passages.append(
            replace(
                item,
                text_private=" ".join(sentences[index] for index in selected),
            )
        )
    return tuple(passages) or tuple(values[:fallback])


def _advocate_sides(
    values: Sequence[ReaderSourceExcerpt],
) -> tuple[ReaderSourceExcerpt, ...]:
    """Select bounded turns across distinct attributed advocates and known sides."""
    selected: list[ReaderSourceExcerpt] = []
    counts: dict[tuple[AdvocateRole, str], int] = {}
    for item in values:
        role = item.advocate_role or AdvocateRole.UNKNOWN
        speaker = (item.speaker_name or "unknown advocate").casefold()
        key = (role, speaker)
        if counts.get(key, 0) >= 2:
            continue
        counts[key] = counts.get(key, 0) + 1
        selected.append(item)
        if len(selected) == 8:
            break
    return tuple(selected)


def _question_excerpts(
    question: ReaderQuestion,
    excerpts: Sequence[ReaderSourceExcerpt],
    *,
    pending_case: bool,
) -> tuple[ReaderSourceExcerpt, ...]:
    """Select stable source roles and passages for one high-level question."""
    docket = _role_items(excerpts, ReaderSourceRole.DOCKET)
    syllabus = _role_items(excerpts, ReaderSourceRole.REPORTER_SYLLABUS)
    opinion = _role_items(excerpts, ReaderSourceRole.CONTROLLING_OPINION)
    orders = _role_items(excerpts, ReaderSourceRole.ORDER)
    transcript = _role_items(excerpts, ReaderSourceRole.TRANSCRIPT)
    advocates = _role_items(excerpts, ReaderSourceRole.ADVOCATE)
    pending_court = question.wording == _PENDING_COURT

    if question.question_id is ReaderQuestionId.ABOUT:
        selected = (*docket[:2], *syllabus[:4], *opinion[:4], *orders[:4], *transcript[:2])
    elif question.question_id is ReaderQuestionId.SIDES:
        selected = (
            *_matching(docket, _POSITION_TEXT, fallback=2),
            *syllabus[:4],
            *_matching(opinion, _POSITION_TEXT, fallback=4),
            *_matching(orders, _POSITION_TEXT, fallback=2),
            *_advocate_sides(advocates),
        )
    elif question.question_id is ReaderQuestionId.HISTORY:
        selected = (
            *_matching(docket, _HISTORY_TEXT, fallback=len(docket)),
            *syllabus[:4],
            *_matching(opinion, _HISTORY_TEXT, fallback=4),
            *_matching(orders, _HISTORY_TEXT, fallback=2),
        )
    elif question.question_id is ReaderQuestionId.COURT and pending_court:
        selected = (
            *_matching(docket, _QUESTION_TEXT, fallback=len(docket)),
            *transcript[:4],
            *_advocate_sides(advocates)[:4],
        )
    elif question.question_id is ReaderQuestionId.COURT:
        selected = (
            *docket[:1],
            *syllabus[:4],
            *_matching(opinion, _OPERATIVE_TEXT, fallback=4),
            *_matching(orders, _OPERATIVE_TEXT, fallback=4),
        )
    else:
        selected = (
            *syllabus[:4],
            *_matching(opinion, _IMPORTANCE_TEXT, fallback=4),
            *_matching(orders, _IMPORTANCE_TEXT, fallback=2),
            *(
                (
                    *_matching(docket, _IMPORTANCE_TEXT, fallback=2),
                    *transcript[:3],
                    *_advocate_sides(advocates)[:3],
                )
                if pending_case
                else ()
            ),
        )

    by_order = {item.block_order: item for item in selected}
    return tuple(by_order[key] for key in sorted(by_order))


class ReaderSourcePacketBuilder:
    """Build complete deterministic packets; never silently truncate blocks."""

    VERSION = READER_QA_PACKET_VERSION

    def __init__(
        self,
        *,
        maximum_characters: int = DEFAULT_PACKET_CHARACTERS,
        maximum_windows: int = DEFAULT_MAXIMUM_WINDOWS,
    ) -> None:
        if maximum_characters < 1 or maximum_windows < 1:
            raise ValueError("reader packet bounds must be positive")
        self.maximum_characters = maximum_characters
        self.maximum_windows = maximum_windows

    def build(
        self,
        questions: Sequence[ReaderQuestion],
        blocks: Sequence[LegalEvidenceBlock],
    ) -> tuple[ReaderSourcePacket, ...]:
        excerpts = tuple(
            item
            for item in source_excerpts(blocks)
            if item.role is not ReaderSourceRole.SEPARATE_OPINION
        )
        packets: list[ReaderSourcePacket] = []
        pending_case = any(question.wording == _PENDING_COURT for question in questions)
        if pending_case and any(
            item.role
            in {
                ReaderSourceRole.REPORTER_SYLLABUS,
                ReaderSourceRole.CONTROLLING_OPINION,
                ReaderSourceRole.ORDER,
            }
            for item in excerpts
        ):
            raise ReaderQAError(
                "pending case includes disposition material",
                safe_code="inconsistent_source_state",
            )
        for question in questions:
            selected = _question_excerpts(
                question,
                excerpts,
                pending_case=pending_case,
            )
            packet = ReaderSourcePacket(
                question=question,
                excerpts=selected,
                maximum_characters=self.maximum_characters,
                maximum_windows=self.maximum_windows,
            )
            # Validate completeness against the hard window bound now, before model work.
            packet_windows(packet)
            packets.append(packet)
        return tuple(packets)


def _formatted_excerpt(excerpt: ReaderSourceExcerpt, index: int) -> str:
    advocate = ""
    if excerpt.role is ReaderSourceRole.ADVOCATE:
        advocate_role = (excerpt.advocate_role or AdvocateRole.UNKNOWN).value
        speaker = excerpt.speaker_name or "unidentified advocate"
        advocate = f"; advocate_role={advocate_role}; speaker={speaker}"
    return (
        f"[Official source {index}: {excerpt.role.value}{advocate}; "
        f"{excerpt.page_label}]\n{excerpt.text_private}"
    )


def packet_windows(packet: ReaderSourcePacket) -> tuple[tuple[ReaderSourceExcerpt, ...], ...]:
    """Partition every excerpt into bounded windows or fail without truncation."""
    pieces: list[ReaderSourceExcerpt] = []
    for excerpt in packet.excerpts:
        # Derive capacity from the actual rendered metadata. A deliberately long
        # source index bounds digit growth when many small pieces share a window.
        probe = replace(excerpt, text_private="x")
        rendered_overhead = len(_formatted_excerpt(probe, 10**20)) - 1
        text_limit = packet.maximum_characters - rendered_overhead
        if text_limit < 1:
            raise ReaderQAError(
                "official source metadata exceeds the bounded question packet",
                safe_code="question_source_too_large",
            )
        text = excerpt.text_private
        for offset in range(0, len(text), text_limit):
            pieces.append(
                replace(
                    excerpt,
                    text_private=text[offset : offset + text_limit],
                )
            )
    windows: list[list[ReaderSourceExcerpt]] = []
    current: list[ReaderSourceExcerpt] = []
    current_size = 0
    for excerpt in pieces:
        rendered_size = len(_formatted_excerpt(excerpt, len(current) + 1)) + 2
        if rendered_size > packet.maximum_characters:
            raise ReaderQAError(
                "official source excerpt exceeds the bounded question packet",
                safe_code="question_source_too_large",
            )
        if current and current_size + rendered_size > packet.maximum_characters:
            windows.append(current)
            current = []
            current_size = 0
            rendered_size = len(_formatted_excerpt(excerpt, 1)) + 2
        current.append(excerpt)
        current_size += rendered_size
    if current:
        windows.append(current)
    if not windows or len(windows) > packet.maximum_windows:
        raise ReaderQAError(
            "official material exceeds the bounded question packet",
            safe_code="question_source_too_large",
        )
    return tuple(tuple(window) for window in windows)


class ReaderQARequestExecutor(Protocol):
    def __call__(
        self,
        request: dict[str, Any],
        *,
        question_id: ReaderQuestionId,
        phase: str,
        window_index: int,
    ) -> object: ...


class PlainTextReaderWriter:
    """Ask one ordinary-language question and retain only final plain text."""

    PROMPT_VERSION = READER_QA_PROMPT_VERSION
    SYNTHESIS_VERSION = READER_QA_SYNTHESIS_VERSION
    SYSTEM_PROMPT = (
        "You explain Supreme Court cases to people with no legal training. Answer only the "
        "question using the official Court material below. Use one to three short sentences "
        "and everyday words. Explain unavoidable legal terms. If the material does not answer "
        "the question, say so; do not guess. Return plain text only—no heading, bullets, "
        "citations, or JSON."
    )
    SYNTHESIS_PROMPT = (
        "Combine the draft answers into one answer to the question for a person with no legal "
        "training. You may use the union of facts in the drafts, but add no fact. Use one to "
        "three short sentences and everyday words. If the drafts do not answer the question, "
        "say so. Return plain text only—no heading, bullets, citations, or JSON."
    )

    def __init__(
        self,
        model_name: str,
        request_executor: ReaderQARequestExecutor,
        *,
        maximum_output_tokens: int = DEFAULT_OUTPUT_TOKENS,
    ) -> None:
        if not model_name or not 1 <= maximum_output_tokens <= 8_000:
            raise ValueError("plain-text reader writer configuration is invalid")
        self.model_name = model_name
        self.request_executor = request_executor
        self.maximum_output_tokens = maximum_output_tokens

    def _request(self, *, system: str, user: str) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "temperature": 0,
            "max_tokens": self.maximum_output_tokens,
            "reasoning_effort": "low",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

    def _final_text(self, completion: object) -> str:
        choices = (
            completion.get("choices", ())
            if isinstance(completion, Mapping)
            else getattr(completion, "choices", ())
        )
        if not choices:
            raise ReaderQAError("reader model returned no choice", safe_code="empty_choice")
        choice = choices[0]
        message = (
            choice.get("message")
            if isinstance(choice, Mapping)
            else getattr(choice, "message", None)
        )
        content = (
            message.get("content")
            if isinstance(message, Mapping)
            else getattr(message, "content", None)
        )
        if not isinstance(content, str) or not content.strip():
            raise ReaderQAError("reader model returned no final text", safe_code="empty_content")
        if len(content) > self.maximum_output_tokens * _MAXIMUM_ANSWER_CHARACTERS_PER_TOKEN:
            raise ReaderQAError(
                "reader model returned oversized final text",
                safe_code="output_too_large",
            )
        return content.strip()

    def _window_answer(
        self,
        packet: ReaderSourcePacket,
        excerpts: Sequence[ReaderSourceExcerpt],
        *,
        phase: str,
        window_index: int,
    ) -> str:
        material = "\n\n".join(
            _formatted_excerpt(excerpt, index)
            for index, excerpt in enumerate(excerpts, 1)
        )
        request = self._request(
            system=self.SYSTEM_PROMPT,
            user=f"Question: {packet.question.wording}\n\nOfficial Court material:\n\n{material}",
        )
        completion = self.request_executor(
            request,
            question_id=packet.question.question_id,
            phase=phase,
            window_index=window_index,
        )
        return self._final_text(completion)

    def answer(self, packet: ReaderSourcePacket) -> ReaderAnswer:
        windows = packet_windows(packet)
        if len(windows) == 1:
            text = self._window_answer(packet, windows[0], phase="direct", window_index=0)
        else:
            drafts = tuple(
                self._window_answer(packet, window, phase="map", window_index=index)
                for index, window in enumerate(windows)
            )
            joined = "\n\n".join(
                (
                    f"Draft {index} (source roles: "
                    f"{', '.join(dict.fromkeys(item.role.value for item in window))}; "
                    f"source ranges: {', '.join(item.page_label for item in window)}): "
                    f"{draft}"
                )
                for index, (window, draft) in enumerate(zip(windows, drafts, strict=True), 1)
            )
            request = self._request(
                system=self.SYNTHESIS_PROMPT,
                user=f"Question: {packet.question.wording}\n\nDraft answers:\n\n{joined}",
            )
            completion = self.request_executor(
                request,
                question_id=packet.question.question_id,
                phase="synthesis",
                window_index=len(windows),
            )
            text = self._final_text(completion)
        return ReaderAnswer(
            question=packet.question,
            text=text,
            source_ranges=packet.excerpts,
        )

    def generate(
        self,
        *,
        title: str,
        packets: Sequence[ReaderSourcePacket],
        maturity: BriefMaturity,
        created_at: datetime,
    ) -> ReaderQAGuideDraft:
        answers = tuple(self.answer(packet) for packet in packets)
        return ReaderQAGuideDraft(
            title=title,
            answers=answers,
            maturity=maturity,
            created_at=created_at,
            model_name=self.model_name,
        )
