"""Deterministic compact planning and process-local repair for reader case guides.

This module is deliberately private-pipeline code.  Plans contain approved public claim
values, but they are not public-state contracts and must not be persisted.  The writer
receives only the bounded packets selected by :class:`ReaderGuidePlanner`; identity,
status, section ordering, session ordering, links, and citations remain deterministic.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, Self
from uuid import UUID

from pydantic import Field, model_validator

from ragchew.contracts import StrictModel
from ragchew.scotus.briefs import (
    BriefCandidate,
    BriefValidationError,
    CaseArgumentSession,
    DraftArgumentAnalysis,
    DraftSection,
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
from ragchew.scotus.reader_prose import load_reader_prose_policy

READER_GUIDE_PLAN_VERSION = "reader-guide-plan-v3"
MAX_PLAN_SECTIONS = 10
MAX_ARGUMENT_PACKETS = 10
MAX_PACKET_CLAIMS = 16
MAX_PACKET_CHARACTERS = 8_000
MAX_PLAN_CLAIMS = 64
MAX_PLAN_CHARACTERS = 32_000
MAX_ACTION_SLOTS = 8


class ReaderGuidePlanningError(ValueError):
    """A required reader purpose cannot be supported by the approved claims."""

    def __init__(self, message: str, *, safe_code: str) -> None:
        super().__init__(message)
        self.safe_code = safe_code


class ReaderGuideWritingError(ValueError):
    """A compact writer or repair response cannot be safely assembled."""

    def __init__(self, message: str, *, safe_code: str) -> None:
        super().__init__(message)
        self.safe_code = safe_code


class ReaderGuidePurpose(StrEnum):
    BACKGROUND = "background"
    PROCEDURAL_PATH = "procedural_path"
    LEGAL_ISSUE = "legal_issue"
    POSITIONS = "positions"
    JUSTICE_QUESTIONS = "justice_questions"
    COURT_ACTION = "court_action"
    COURT_REASONING = "court_reasoning"
    SEPARATE_OPINIONS = "separate_opinions"
    NEXT_KNOWN_STEP = "next_known_step"


class CanonicalActorRole(StrEnum):
    REQUESTING_PARTY = "requesting_party"
    LOWER_COURT = "lower_court"
    SUPREME_COURT = "supreme_court"


class CanonicalAction(StrEnum):
    HOLD = "hold"
    ORDER = "order"
    GRANT = "grant"
    DENY = "deny"
    AFFIRM = "affirm"
    REVERSE = "reverse"
    VACATE = "vacate"
    REMAND = "remand"
    DISMISS = "dismiss"
    STAY = "stay"
    BLOCK = "block"
    REQUIRE = "require"


class ActionEffect(StrEnum):
    INTERIM = "interim"
    FINAL = "final"
    UNSPECIFIED = "unspecified"


class ReaderGuideClaimPacket(StrictModel):
    """The compact, approved part of one claim that a writer may see."""

    claim_id: UUID
    argument_id: UUID | None = None
    observation_type: LegalObservationType
    legal_status: LegalStatus
    certainty: LegalCertainty
    public_value: str = Field(min_length=1, max_length=2_000)
    attribution: str | None = Field(default=None, max_length=500)
    position_group: AdvocateRole | None = None
    official_url: str = Field(min_length=1, max_length=2_000)
    public_source_label: str = Field(min_length=1, max_length=200)
    page_label: str = Field(min_length=1, max_length=100)


class CanonicalActionSlot(StrictModel):
    """One source-backed action whose semantics must survive ordinary wording."""

    claim_id: UUID
    actor_role: CanonicalActorRole
    actor: str = Field(min_length=1, max_length=500)
    action: CanonicalAction
    operative_object: str = Field(min_length=1, max_length=300)
    negated: bool = False
    effect: ActionEffect
    timing: str | None = Field(default=None, max_length=200)


class ReaderGuideSectionPacket(StrictModel):
    heading: str = Field(min_length=1, max_length=120)
    purpose: ReaderGuidePurpose
    reader_purpose: str = Field(min_length=1, max_length=300)
    allowed_observation_types: tuple[LegalObservationType, ...] = Field(min_length=1, max_length=14)
    required_observation_types: tuple[LegalObservationType, ...] = Field(min_length=1, max_length=8)
    allowed_legal_statuses: tuple[LegalStatus, ...] = Field(min_length=1, max_length=11)
    required_legal_statuses: tuple[LegalStatus, ...] = Field(min_length=1, max_length=8)
    claims: tuple[ReaderGuideClaimPacket, ...] = Field(min_length=1, max_length=MAX_PACKET_CLAIMS)
    action_slots: tuple[CanonicalActionSlot, ...] = Field(default=(), max_length=MAX_ACTION_SLOTS)
    plain_language_guidance: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def packet_is_role_appropriate_and_bounded(self) -> Self:
        claim_types = {claim.observation_type for claim in self.claims}
        statuses = {claim.legal_status for claim in self.claims}
        if not claim_types.issubset(self.allowed_observation_types):
            raise ValueError("section packet contains a claim type outside its reader purpose")
        if not statuses.issubset(self.allowed_legal_statuses):
            raise ValueError("section packet contains a legal status outside its reader purpose")
        if not set(self.required_observation_types).issubset(claim_types):
            raise ValueError("section packet lacks a required claim type")
        if not set(self.required_legal_statuses).issubset(statuses):
            raise ValueError("section packet lacks a required legal status")
        if sum(len(claim.public_value) for claim in self.claims) > MAX_PACKET_CHARACTERS:
            raise ValueError("section claim packet exceeds its character bound")
        claim_ids = {claim.claim_id for claim in self.claims}
        if any(slot.claim_id not in claim_ids for slot in self.action_slots):
            raise ValueError("action slot must resolve inside its section packet")
        return self


class ReaderGuidePositionPacket(StrictModel):
    role: AdvocateRole
    established: bool
    attribution: str | None = Field(default=None, max_length=500)
    claim_ids: tuple[UUID, ...] = Field(min_length=1, max_length=MAX_PACKET_CLAIMS)

    @model_validator(mode="after")
    def known_roles_are_established(self) -> Self:
        if self.established != (self.role is not AdvocateRole.UNKNOWN):
            raise ValueError("only an explicitly identified advocate role is established")
        if self.role is AdvocateRole.UNKNOWN and not self.attribution:
            raise ValueError("an unknown advocate packet retains its source attribution")
        return self


class ReaderGuideArgumentPacket(StrictModel):
    argument_id: UUID
    argument_date: datetime
    sequence: int = Field(ge=1)
    reargument: bool = False
    official_detail_url: str = Field(min_length=1, max_length=2_000)
    official_transcript_url: str = Field(min_length=1, max_length=2_000)
    claims: tuple[ReaderGuideClaimPacket, ...] = Field(min_length=1, max_length=MAX_PACKET_CLAIMS)
    positions: tuple[ReaderGuidePositionPacket, ...] = Field(default=(), max_length=8)
    justice_question_claim_ids: tuple[UUID, ...] = Field(default=(), max_length=8)
    action_slots: tuple[CanonicalActionSlot, ...] = Field(default=(), max_length=MAX_ACTION_SLOTS)
    plain_language_guidance: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def claims_belong_to_exact_session(self) -> Self:
        if any(claim.argument_id != self.argument_id for claim in self.claims):
            raise ValueError("argument packet mixes claims from different sessions")
        if sum(len(claim.public_value) for claim in self.claims) > MAX_PACKET_CHARACTERS:
            raise ValueError("argument claim packet exceeds its character bound")
        claim_ids = {claim.claim_id for claim in self.claims}
        referenced = {
            *(claim_id for position in self.positions for claim_id in position.claim_ids),
            *self.justice_question_claim_ids,
            *(slot.claim_id for slot in self.action_slots),
        }
        if not referenced.issubset(claim_ids):
            raise ValueError("argument coverage must resolve inside its session packet")
        return self


class ReaderGuidePlan(StrictModel):
    """Bounded private plan; this object must never enter generated public state."""

    plan_version: Literal["reader-guide-plan-v3"] = "reader-guide-plan-v3"
    case_id: UUID
    caption: str = Field(min_length=1, max_length=500)
    primary_docket: str = Field(min_length=1, max_length=40)
    case_status: ScotusCaseStatus
    maturity: BriefMaturity
    title_claim_ids: tuple[UUID, ...] = Field(min_length=1, max_length=4)
    summary_claims: tuple[ReaderGuideClaimPacket, ...] = Field(min_length=1, max_length=8)
    sections: tuple[ReaderGuideSectionPacket, ...] = Field(
        min_length=1, max_length=MAX_PLAN_SECTIONS
    )
    arguments: tuple[ReaderGuideArgumentPacket, ...] = Field(
        default=(), max_length=MAX_ARGUMENT_PACKETS
    )

    @model_validator(mode="after")
    def plan_is_consistent_and_bounded(self) -> Self:
        packets = (
            *self.summary_claims,
            *(claim for section in self.sections for claim in section.claims),
            *(claim for argument in self.arguments for claim in argument.claims),
        )
        by_id = {claim.claim_id: claim for claim in packets}
        if not set(self.title_claim_ids).issubset(by_id):
            raise ValueError("title support must resolve inside the plan")
        if len(packets) > MAX_PLAN_CLAIMS:
            raise ValueError("reader-guide plan exceeds its serialized claim bound")
        if sum(len(claim.public_value) for claim in packets) > MAX_PLAN_CHARACTERS:
            raise ValueError("reader-guide plan exceeds its serialized character bound")
        expected_arguments = tuple(
            packet.argument_id
            for packet in sorted(
                self.arguments,
                key=lambda item: (item.argument_date, item.sequence, str(item.argument_id)),
            )
        )
        if tuple(packet.argument_id for packet in self.arguments) != expected_arguments:
            raise ValueError("argument packets must be chronological")
        if len(expected_arguments) != len(set(expected_arguments)):
            raise ValueError("reader-guide plan repeats an argument session")
        return self


@dataclass(frozen=True, slots=True)
class ReaderGuidePlannerLimits:
    max_claims_per_section: int = 6
    max_characters_per_section: int = 4_000
    max_claims_per_argument: int = 12
    max_characters_per_argument: int = 6_000
    max_total_claims: int = 48
    max_total_characters: int = 24_000

    def __post_init__(self) -> None:
        pairs = (
            (self.max_claims_per_section, 1, MAX_PACKET_CLAIMS, "section claim"),
            (self.max_characters_per_section, 1, MAX_PACKET_CHARACTERS, "section character"),
            (self.max_claims_per_argument, 1, MAX_PACKET_CLAIMS, "argument claim"),
            (self.max_characters_per_argument, 1, MAX_PACKET_CHARACTERS, "argument character"),
            (self.max_total_claims, 1, MAX_PLAN_CLAIMS, "plan claim"),
            (self.max_total_characters, 1, MAX_PLAN_CHARACTERS, "plan character"),
        )
        for value, minimum, maximum, label in pairs:
            if not minimum <= value <= maximum:
                raise ValueError(f"{label} bound must be between {minimum} and {maximum}")


_CERTAINTY_ORDER = {
    LegalCertainty.DIRECT: 0,
    LegalCertainty.ATTRIBUTED: 1,
    LegalCertainty.ANALYST_FORMULATION: 2,
    LegalCertainty.UNCERTAIN: 3,
}
_TYPE_ORDER = {value: index for index, value in enumerate(LegalObservationType)}
_SPACE = re.compile(r"\s+")
_SEPARATE_OPINION_ATTRIBUTION = re.compile(
    r"^(?:Justice\s+[^,]+,\s*)?(?:dissenting|concurring)|^separate opinion\b",
    re.I,
)
_SEPARATE_OPINION_VALUE = re.compile(
    r"^(?:Justice\s+[^,]+(?:'s|\N{RIGHT SINGLE QUOTATION MARK}s)?\s+)?"
    r"(?:dissent|concurrence|separate opinion)\b|^The\s+(?:dissent|concurrence)\b",
    re.I,
)


def _position_group(attribution: str | None) -> AdvocateRole | None:
    if not attribution:
        return None
    lowered = attribution.casefold()
    if "united states" in lowered or "government" in lowered:
        return AdvocateRole.UNITED_STATES
    if "petitioner" in lowered:
        return AdvocateRole.PETITIONER
    if "respondent" in lowered:
        return AdvocateRole.RESPONDENT
    if "amicus" in lowered or "friend of the court" in lowered:
        return AdvocateRole.AMICUS
    return None


def _has_position(role: AdvocateRole) -> ClaimPredicate:
    def matches(claim: ScotusApprovedClaim) -> bool:
        return _position_group(claim.attribution) is role

    return matches


def _packet(claim: ScotusApprovedClaim) -> ReaderGuideClaimPacket:
    return ReaderGuideClaimPacket(
        claim_id=claim.claim_id,
        argument_id=claim.argument_id,
        observation_type=claim.observation_type,
        legal_status=claim.legal_status,
        certainty=claim.certainty,
        public_value=claim.public_value,
        attribution=claim.attribution,
        position_group=_position_group(claim.attribution),
        official_url=claim.official_url,
        public_source_label=claim.public_source_label,
        page_label=claim.page_label,
    )


def _claim_key(claim: ScotusApprovedClaim) -> tuple[int, int, int, str]:
    return (
        _CERTAINTY_ORDER[claim.certainty],
        _TYPE_ORDER[claim.observation_type],
        len(claim.public_value),
        str(claim.claim_id),
    )


def _deduplicate(claims: Iterable[ScotusApprovedClaim]) -> tuple[ScotusApprovedClaim, ...]:
    result: list[ScotusApprovedClaim] = []
    seen: set[str] = set()
    for claim in sorted(claims, key=_claim_key):
        normalized_value = _SPACE.sub(" ", claim.public_value).strip().casefold()
        normalized = "|".join(
            (
                str(claim.argument_id),
                claim.observation_type.value,
                claim.legal_status.value,
                (claim.attribution or "").strip().casefold(),
                normalized_value,
            )
        )
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(claim)
    return tuple(result)


ClaimPredicate = Callable[[ScotusApprovedClaim], bool]


def _select_claims(
    candidates: Iterable[ScotusApprovedClaim],
    required: tuple[ClaimPredicate, ...],
    *,
    maximum_claims: int,
    maximum_characters: int,
    safe_code: str,
) -> tuple[ScotusApprovedClaim, ...]:
    available = _deduplicate(candidates)
    selected: list[ScotusApprovedClaim] = []
    selected_ids: set[UUID] = set()
    characters = 0

    def add(claim: ScotusApprovedClaim) -> bool:
        nonlocal characters
        if claim.claim_id in selected_ids:
            return True
        if (
            len(selected) >= maximum_claims
            or characters + len(claim.public_value) > maximum_characters
        ):
            return False
        selected.append(claim)
        selected_ids.add(claim.claim_id)
        characters += len(claim.public_value)
        return True

    for predicate in required:
        match = next((claim for claim in available if predicate(claim)), None)
        if match is None or not add(match):
            raise ReaderGuidePlanningError(
                "approved claims cannot support a required reader purpose within bounds",
                safe_code=safe_code,
            )
    for claim in available:
        if not add(claim):
            continue
    if not selected:
        raise ReaderGuidePlanningError(
            "approved claims cannot support a required reader purpose",
            safe_code=safe_code,
        )
    return tuple(selected)


_ACTION_PATTERN = re.compile(
    r"\b(?P<action>sent\s+(?:the\s+)?case\s+back|return(?:ed)?\s+(?:the\s+)?case\s+to\s+"
    r"(?:a|the)\s+lower\s+court|held|holds?|order(?:ed)?|grant(?:ed)?|allow(?:ed)?|"
    r"deny|denied|reject(?:ed)?|affirm(?:ed)?|upheld|revers(?:e|ed)|vacat(?:e|ed)|"
    r"cancel(?:led|ed)?|remand(?:ed)?|dismiss(?:ed)?|stay(?:ed)?|paus(?:e|ed)|"
    r"enjoin(?:ed)?|block(?:ed)?|requir(?:e|ed))\b",
    re.I,
)
_ACTION_CANONICAL: tuple[tuple[re.Pattern[str], CanonicalAction], ...] = (
    (re.compile(r"sent .*case back|return.*case to .*lower court", re.I), CanonicalAction.REMAND),
    (re.compile(r"hold|held", re.I), CanonicalAction.HOLD),
    (re.compile(r"order", re.I), CanonicalAction.ORDER),
    (re.compile(r"grant|allow", re.I), CanonicalAction.GRANT),
    (re.compile(r"deny|denied|reject", re.I), CanonicalAction.DENY),
    (re.compile(r"affirm|upheld", re.I), CanonicalAction.AFFIRM),
    (re.compile(r"revers", re.I), CanonicalAction.REVERSE),
    (re.compile(r"vacat|cancel", re.I), CanonicalAction.VACATE),
    (re.compile(r"remand", re.I), CanonicalAction.REMAND),
    (re.compile(r"dismiss", re.I), CanonicalAction.DISMISS),
    (re.compile(r"stay|paus", re.I), CanonicalAction.STAY),
    (re.compile(r"enjoin|block", re.I), CanonicalAction.BLOCK),
    (re.compile(r"requir", re.I), CanonicalAction.REQUIRE),
)
_OBJECT = re.compile(
    r"\b(application|appeal|case|decree|execution|injunction|judgment|mandate|order|"
    r"petition|prosecution|release|removal|rule|sentence|writ)\b",
    re.I,
)
_NEGATION = re.compile(r"\b(?:not|never|did not|does not|declined to|refused to)\b", re.I)
_INTERIM = re.compile(r"\b(?:interim|temporar(?:y|ily)|pending|until|while .*appeal)\b", re.I)
_TIMING = re.compile(r"\b(?:pending|until|while)\b[^.!?]{0,160}", re.I)
_LOWER_COURT = re.compile(
    r"\b(?:the\s+)?(?:district court|court of appeals|appeals court|lower court|state court)\b",
    re.I,
)
_SUPREME_COURT = re.compile(r"\b(?:Supreme Court|The Court|the Court|Court)\b")
_NOUN_ACTION_PREFIX = re.compile(r"\b(?:a|an|the|for|of)\s+$", re.I)
_SENTENCES = re.compile(r"[^.!?]+[.!?]?")


def _canonical_action(value: str) -> CanonicalAction:
    for pattern, action in _ACTION_CANONICAL:
        if pattern.search(value):
            return action
    raise AssertionError("action pattern and canonical action map diverged")


def _explicit_actor(
    sentence: str,
    action_start: int,
) -> tuple[CanonicalActorRole, str] | None:
    preceding = sentence[:action_start]
    lower_matches = tuple(_LOWER_COURT.finditer(preceding))
    supreme_matches = tuple(
        match
        for match in _SUPREME_COURT.finditer(preceding)
        if not any(
            lower.start() <= match.start() and match.end() <= lower.end() for lower in lower_matches
        )
    )
    matches = (
        *(
            (match.start(), CanonicalActorRole.LOWER_COURT, match.group(0))
            for match in lower_matches
        ),
        *(
            (match.start(), CanonicalActorRole.SUPREME_COURT, match.group(0))
            for match in supreme_matches
        ),
    )
    if not matches:
        return None
    _, role, actor = max(matches, key=lambda item: item[0])
    return role, actor


def _actor_for_action(
    claim: ScotusApprovedClaim,
    sentence: str,
    action_start: int,
) -> tuple[CanonicalActorRole, str] | None:
    if claim.legal_status not in {
        LegalStatus.REQUESTED,
        LegalStatus.LOWER_COURT_HELD,
        LegalStatus.COURT_ORDERED,
        LegalStatus.COURT_HELD,
    }:
        return None
    if claim.legal_status is LegalStatus.REQUESTED:
        return CanonicalActorRole.REQUESTING_PARTY, claim.attribution or "requesting party"
    explicit = _explicit_actor(sentence, action_start)
    if explicit is not None:
        passive_court_action = bool(
            claim.legal_status in {LegalStatus.COURT_ORDERED, LegalStatus.COURT_HELD}
            and explicit[0] is CanonicalActorRole.LOWER_COURT
            and re.search(
                r"\b(?:application|petition|request)\b[^.!?]{0,120}\b(?:is|was)\s+"
                r"(?:granted|denied|dismissed)\b",
                sentence,
                re.I,
            )
        )
        if not passive_court_action:
            return explicit
    if claim.legal_status is LegalStatus.LOWER_COURT_HELD:
        return CanonicalActorRole.LOWER_COURT, "lower court"
    if claim.legal_status in {LegalStatus.COURT_ORDERED, LegalStatus.COURT_HELD}:
        return CanonicalActorRole.SUPREME_COURT, "Supreme Court"
    return None


def _operative_object(clause: str, match: re.Match[str], action: CanonicalAction) -> str:
    following = _OBJECT.search(clause, match.end())
    if following is not None:
        return following.group(0).casefold()
    if action is CanonicalAction.REMAND:
        return "case"
    preceding = tuple(_OBJECT.finditer(clause, 0, match.start()))
    if preceding:
        return preceding[-1].group(0).casefold()
    remainder = clause[match.end() :].strip(" ,:;-.")
    if remainder.casefold().startswith("that "):
        remainder = remainder[5:]
    return remainder[:300] or match.group("action").casefold()


def build_canonical_action_slots(
    claims: Iterable[ScotusApprovedClaim],
) -> tuple[CanonicalActionSlot, ...]:
    """Extract bounded typed actions without assigning one claim's actor to every verb."""
    slots: list[CanonicalActionSlot] = []
    seen: set[tuple[UUID, CanonicalActorRole, CanonicalAction, str, bool]] = set()
    for claim in sorted(claims, key=_claim_key):
        if _is_separate_claim(claim):
            continue
        for sentence_match in _SENTENCES.finditer(claim.public_value):
            sentence = sentence_match.group(0)
            for match in _ACTION_PATTERN.finditer(sentence):
                prefix = sentence[max(0, match.start() - 45) : match.start()]
                # Bare legal nouns are evidence values, not actions ("application for a stay").
                if _NOUN_ACTION_PREFIX.search(prefix):
                    continue
                actor = _actor_for_action(claim, sentence, match.start())
                if actor is None:
                    continue
                if match.group("action").casefold() == "order":
                    continue
                action = _canonical_action(match.group("action"))
                preceding_conjunctions = tuple(
                    re.finditer(r"\b(?:and|but)\b", sentence[: match.start()], re.I)
                )
                following_conjunction = re.search(r"\b(?:and|but)\b", sentence[match.end() :], re.I)
                clause_start = preceding_conjunctions[-1].end() if preceding_conjunctions else 0
                clause_end = (
                    match.end() + following_conjunction.start()
                    if following_conjunction
                    else len(sentence)
                )
                clause = sentence[clause_start:clause_end]
                clause_match = _ACTION_PATTERN.search(clause)
                if clause_match is None:
                    raise AssertionError("action disappeared from its containing clause")
                operative_object = _operative_object(clause, clause_match, action)
                negated = _NEGATION.search(clause[: clause_match.start()]) is not None
                interim = action is CanonicalAction.STAY or _INTERIM.search(clause) is not None
                effect = (
                    ActionEffect.INTERIM
                    if interim
                    else (
                        ActionEffect.FINAL
                        if claim.legal_status is LegalStatus.COURT_HELD
                        or action
                        in {
                            CanonicalAction.AFFIRM,
                            CanonicalAction.REVERSE,
                            CanonicalAction.VACATE,
                            CanonicalAction.REMAND,
                            CanonicalAction.DISMISS,
                        }
                        else ActionEffect.UNSPECIFIED
                    )
                )
                timing_match = _TIMING.search(clause)
                key = (claim.claim_id, actor[0], action, operative_object, negated)
                if key in seen:
                    continue
                seen.add(key)
                slots.append(
                    CanonicalActionSlot(
                        claim_id=claim.claim_id,
                        actor_role=actor[0],
                        actor=actor[1],
                        action=action,
                        operative_object=operative_object,
                        negated=negated,
                        effect=effect,
                        timing=timing_match.group(0).strip() if timing_match else None,
                    )
                )
                if len(slots) > MAX_ACTION_SLOTS:
                    raise ReaderGuidePlanningError(
                        "action coverage exceeds its packet bound",
                        safe_code="too_many_action_slots",
                    )
    return tuple(slots)


_PURPOSE_GUIDANCE: Mapping[ReaderGuidePurpose, str] = {
    ReaderGuidePurpose.BACKGROUND: "Explain the concrete dispute and who it affects.",
    ReaderGuidePurpose.PROCEDURAL_PATH: (
        "Explain how the case reached this Court without changing any actor or result."
    ),
    ReaderGuidePurpose.LEGAL_ISSUE: ("State the practical question the Court was asked to answer."),
    ReaderGuidePurpose.POSITIONS: (
        "Attribute each established side's requested result and reasoning."
    ),
    ReaderGuidePurpose.JUSTICE_QUESTIONS: (
        "Explain what assumptions the justices tested; do not imply votes."
    ),
    ReaderGuidePurpose.COURT_ACTION: (
        "State only the source-backed Supreme Court action and its effect."
    ),
    ReaderGuidePurpose.COURT_REASONING: (
        "Explain controlling reasoning separately from any party or separate opinion."
    ),
    ReaderGuidePurpose.SEPARATE_OPINIONS: (
        "Attribute each separate view to its author and not to the Court."
    ),
    ReaderGuidePurpose.NEXT_KNOWN_STEP: (
        "State only a source-backed next procedural step, never a prediction."
    ),
}


def _is_reasoning_claim(claim: ScotusApprovedClaim) -> bool:
    if claim.observation_type is LegalObservationType.DOCTRINAL_THEME:
        return True
    return (
        claim.observation_type is LegalObservationType.HOLDING
        and re.search(
            r"\b(?:because|based on|reasoned|concluded that|explained that)\b",
            claim.public_value,
            re.I,
        )
        is not None
    )


def _contains_term_guidance(claims: Iterable[ScotusApprovedClaim]) -> tuple[str, ...]:
    """Select reviewed ordinary-language guidance for terms present in a packet."""
    text = " ".join(claim.public_value for claim in claims)
    guidance: list[str] = []
    for term in load_reader_prose_policy().terms:
        if not any(re.search(pattern, text, re.IGNORECASE) for pattern in term.patterns):
            continue
        ordinary = term.ordinary_alternatives[0]
        label = term.label.replace("_", " ")
        guidance.append(
            f'Prefer ordinary wording such as "{ordinary}"; if "{label}" is necessary, '
            "explain that meaning in the same sentence."
        )
        if len(guidance) == 16:
            break
    return tuple(guidance)


def _required_section_claim_ids(packet: ReaderGuideSectionPacket) -> set[UUID]:
    required: set[UUID] = {slot.claim_id for slot in packet.action_slots}
    for observation_type in packet.required_observation_types:
        required.add(
            next(
                claim.claim_id
                for claim in packet.claims
                if claim.observation_type is observation_type
            )
        )
    for status in packet.required_legal_statuses:
        required.add(
            next(claim.claim_id for claim in packet.claims if claim.legal_status is status)
        )
    if packet.purpose is ReaderGuidePurpose.POSITIONS:
        established = {
            claim.position_group for claim in packet.claims if claim.position_group is not None
        }
        for role in established:
            required.add(
                next(claim.claim_id for claim in packet.claims if claim.position_group is role)
            )
        if not established:
            attributed = next((claim for claim in packet.claims if claim.attribution), None)
            if attributed is not None:
                required.add(attributed.claim_id)
    return required


def _required_argument_claim_ids(packet: ReaderGuideArgumentPacket) -> set[UUID]:
    required = {
        *(slot.claim_id for slot in packet.action_slots),
        *(position.claim_ids[0] for position in packet.positions),
    }
    if packet.justice_question_claim_ids:
        required.add(packet.justice_question_claim_ids[0])
    if not required:
        required.add(packet.claims[0].claim_id)
    return required


def _packet_removal_key(claim: ReaderGuideClaimPacket) -> tuple[int, int, int, str]:
    return (
        _CERTAINTY_ORDER[claim.certainty],
        _TYPE_ORDER[claim.observation_type],
        len(claim.public_value),
        str(claim.claim_id),
    )


def _trim_to_total_bounds(
    sections: list[ReaderGuideSectionPacket],
    arguments: tuple[ReaderGuideArgumentPacket, ...],
    summary: tuple[ReaderGuideClaimPacket, ...],
    limits: ReaderGuidePlannerLimits,
) -> tuple[list[ReaderGuideSectionPacket], tuple[ReaderGuideArgumentPacket, ...]]:
    """Drop only optional packet copies until the serialized model context is bounded."""
    mutable_arguments = list(arguments)

    def packets() -> tuple[ReaderGuideClaimPacket, ...]:
        return (
            *summary,
            *(claim for section in sections for claim in section.claims),
            *(claim for argument in mutable_arguments for claim in argument.claims),
        )

    while True:
        current = packets()
        if (
            len(current) <= limits.max_total_claims
            and sum(len(claim.public_value) for claim in current) <= limits.max_total_characters
        ):
            return sections, tuple(mutable_arguments)
        removable: list[tuple[tuple[int, int, int, str], str, int, UUID]] = []
        for index, section in enumerate(sections):
            protected = _required_section_claim_ids(section)
            removable.extend(
                (_packet_removal_key(claim), "section", index, claim.claim_id)
                for claim in section.claims
                if claim.claim_id not in protected and len(section.claims) > 1
            )
        for index, argument in enumerate(mutable_arguments):
            protected = _required_argument_claim_ids(argument)
            removable.extend(
                (_packet_removal_key(claim), "argument", index, claim.claim_id)
                for claim in argument.claims
                if claim.claim_id not in protected and len(argument.claims) > 1
            )
        if not removable:
            raise ReaderGuidePlanningError(
                "required reader-guide coverage exceeds the total plan bound",
                safe_code="reader_guide_plan_too_large",
            )
        _, kind, index, claim_id = max(removable)
        if kind == "section":
            section_packet = sections[index]
            remaining = tuple(
                claim for claim in section_packet.claims if claim.claim_id != claim_id
            )
            remaining_ids = {claim.claim_id for claim in remaining}
            sections[index] = section_packet.model_copy(
                update={
                    "claims": remaining,
                    "action_slots": tuple(
                        slot
                        for slot in section_packet.action_slots
                        if slot.claim_id in remaining_ids
                    ),
                }
            )
        else:
            argument_packet = mutable_arguments[index]
            remaining = tuple(
                claim for claim in argument_packet.claims if claim.claim_id != claim_id
            )
            remaining_ids = {claim.claim_id for claim in remaining}
            mutable_arguments[index] = argument_packet.model_copy(
                update={
                    "claims": remaining,
                    "positions": tuple(
                        position.model_copy(
                            update={
                                "claim_ids": tuple(
                                    value for value in position.claim_ids if value in remaining_ids
                                )
                            }
                        )
                        for position in argument_packet.positions
                        if any(value in remaining_ids for value in position.claim_ids)
                    ),
                    "justice_question_claim_ids": tuple(
                        value
                        for value in argument_packet.justice_question_claim_ids
                        if value in remaining_ids
                    ),
                    "action_slots": tuple(
                        slot
                        for slot in argument_packet.action_slots
                        if slot.claim_id in remaining_ids
                    ),
                }
            )


class ReaderGuidePlanner:
    def __init__(self, limits: ReaderGuidePlannerLimits | None = None) -> None:
        self.limits = limits or ReaderGuidePlannerLimits()

    def plan(
        self,
        candidate: BriefCandidate,
        claims: tuple[ScotusApprovedClaim, ...],
        maturity: BriefMaturity,
    ) -> ReaderGuidePlan:
        if not claims or any(claim.case_id != candidate.case_id for claim in claims):
            raise ReaderGuidePlanningError(
                "reader-guide claims must belong to one candidate case",
                safe_code="invalid_case_claims",
            )
        if len(candidate.caption) > 180:
            raise ReaderGuidePlanningError(
                "the exact official caption exceeds the public title bound",
                safe_code="unsupported_title_length",
            )
        sessions = sorted(
            candidate.argument_sessions,
            key=lambda item: (item.argument_date, item.sequence, str(item.argument_id)),
        )
        if len(sessions) > MAX_ARGUMENT_PACKETS or len(
            {item.argument_id for item in sessions}
        ) != len(sessions):
            raise ReaderGuidePlanningError(
                "argument session identity is invalid or exceeds its bound",
                safe_code="invalid_argument_sessions",
            )
        real_session_ids = {session.argument_id for session in sessions}
        if any(
            claim.argument_id is not None and claim.argument_id not in real_session_ids
            for claim in claims
        ):
            raise ReaderGuidePlanningError(
                "an approved claim names no real argument session",
                safe_code="unknown_argument_session",
            )

        controlling = tuple(claim for claim in claims if not _is_separate_claim(claim))
        separate = tuple(claim for claim in claims if _is_separate_claim(claim))
        expects_disposition = candidate.case_status in {
            ScotusCaseStatus.ORDER_ISSUED,
            ScotusCaseStatus.DECIDED,
        } or any(
            claim.observation_type
            in {
                LegalObservationType.HOLDING,
                LegalObservationType.ORDER,
            }
            and claim.legal_status
            in {
                LegalStatus.COURT_HELD,
                LegalStatus.COURT_ORDERED,
            }
            for claim in controlling
        )
        sections: list[ReaderGuideSectionPacket] = []

        def add_section(
            purpose: ReaderGuidePurpose,
            heading: str,
            allowed_types: tuple[LegalObservationType, ...],
            allowed_statuses: tuple[LegalStatus, ...],
            required: tuple[ClaimPredicate, ...],
            pool: Iterable[ScotusApprovedClaim],
        ) -> tuple[ScotusApprovedClaim, ...]:
            selected = _select_claims(
                (
                    claim
                    for claim in pool
                    if claim.observation_type in allowed_types
                    and claim.legal_status in allowed_statuses
                ),
                required,
                maximum_claims=self.limits.max_claims_per_section,
                maximum_characters=self.limits.max_characters_per_section,
                safe_code=f"unsupported_{purpose.value}",
            )
            sections.append(
                ReaderGuideSectionPacket(
                    heading=heading,
                    purpose=purpose,
                    reader_purpose=_PURPOSE_GUIDANCE[purpose],
                    allowed_observation_types=allowed_types,
                    required_observation_types=tuple(
                        dict.fromkeys(
                            next(claim.observation_type for claim in selected if predicate(claim))
                            for predicate in required
                        )
                    ),
                    allowed_legal_statuses=allowed_statuses,
                    required_legal_statuses=tuple(
                        dict.fromkeys(
                            next(claim.legal_status for claim in selected if predicate(claim))
                            for predicate in required
                        )
                    ),
                    claims=tuple(_packet(claim) for claim in selected),
                    action_slots=build_canonical_action_slots(selected),
                    plain_language_guidance=_contains_term_guidance(selected),
                )
            )
            return selected

        background_types = (
            LegalObservationType.CASE_BACKGROUND,
            LegalObservationType.PROCEDURAL_POSTURE,
        )
        background_required: ClaimPredicate = (
            (lambda claim: claim.observation_type is LegalObservationType.CASE_BACKGROUND)
            if expects_disposition and not sessions
            else (lambda claim: claim.observation_type in background_types)
        )
        background = add_section(
            ReaderGuidePurpose.BACKGROUND,
            "What this case is about",
            background_types,
            (LegalStatus.DESCRIBED,),
            (background_required,),
            controlling,
        )
        path_types = (
            LegalObservationType.PROCEDURAL_POSTURE,
            LegalObservationType.LOWER_COURT_ACTION,
            LegalObservationType.REQUESTED_DISPOSITION,
        )
        add_section(
            ReaderGuidePurpose.PROCEDURAL_PATH,
            (
                "Why this case reached the Court"
                if expects_disposition and not sessions
                else "How the case got here"
            ),
            path_types,
            (LegalStatus.DESCRIBED, LegalStatus.LOWER_COURT_HELD, LegalStatus.REQUESTED),
            (lambda claim: claim.observation_type in path_types,),
            controlling,
        )
        issue_types = (
            LegalObservationType.QUESTION_PRESENTED,
            LegalObservationType.DOCTRINAL_THEME,
            *((LegalObservationType.JUSTICE_QUESTION,) if sessions else ()),
        )
        issue_pool = _deduplicate(
            claim for claim in controlling if claim.observation_type in issue_types
        )
        issue_candidates = next(
            (
                tuple(claim for claim in issue_pool if claim.observation_type is preferred_type)[:1]
                for preferred_type in (
                    LegalObservationType.QUESTION_PRESENTED,
                    LegalObservationType.JUSTICE_QUESTION,
                    LegalObservationType.DOCTRINAL_THEME,
                )
                if any(claim.observation_type is preferred_type for claim in issue_pool)
            ),
            (),
        )
        issue = add_section(
            ReaderGuidePurpose.LEGAL_ISSUE,
            "The legal issue"
            if expects_disposition and not sessions
            else "The main legal question",
            issue_types,
            (
                LegalStatus.DESCRIBED,
                *((LegalStatus.QUESTIONED,) if sessions else ()),
            ),
            (lambda claim: claim.observation_type in issue_types,),
            issue_candidates[:1],
        )

        if sessions:
            position_claims = tuple(
                claim
                for claim in controlling
                if claim.observation_type
                in {
                    LegalObservationType.ADVOCATE_CONTENTION,
                    LegalObservationType.REQUESTED_DISPOSITION,
                    LegalObservationType.ANSWER,
                    LegalObservationType.CONCESSION,
                    LegalObservationType.DISPUTED_PREMISE,
                }
            )
            known_roles = tuple(
                sorted(
                    {
                        role
                        for claim in position_claims
                        if (role := _position_group(claim.attribution)) is not None
                    },
                    key=lambda item: item.value,
                )
            )
            position_required: tuple[ClaimPredicate, ...] = tuple(
                _has_position(role) for role in known_roles
            )
            if not position_required:
                position_required = (lambda claim: claim in position_claims,)
            add_section(
                ReaderGuidePurpose.POSITIONS,
                "What each side says",
                (
                    LegalObservationType.ADVOCATE_CONTENTION,
                    LegalObservationType.REQUESTED_DISPOSITION,
                    LegalObservationType.ANSWER,
                    LegalObservationType.CONCESSION,
                    LegalObservationType.DISPUTED_PREMISE,
                ),
                (
                    LegalStatus.ASSERTED,
                    LegalStatus.REQUESTED,
                    LegalStatus.ANSWERED,
                    LegalStatus.CONCEDED,
                    LegalStatus.DISPUTED,
                ),
                position_required,
                controlling,
            )

        if expects_disposition:
            action_types = (LegalObservationType.HOLDING, LegalObservationType.ORDER)
            add_section(
                ReaderGuidePurpose.COURT_ACTION,
                "What the Supreme Court did",
                action_types,
                (LegalStatus.COURT_HELD, LegalStatus.COURT_ORDERED),
                (lambda claim: claim.observation_type in action_types,),
                controlling,
            )
            reasoning_types = (
                LegalObservationType.DOCTRINAL_THEME,
                LegalObservationType.HOLDING,
            )
            issue_ids = {claim.claim_id for claim in issue}
            issue_values = {claim.public_value.casefold() for claim in issue}
            reasoning_pool = tuple(
                claim
                for claim in controlling
                if claim.claim_id not in issue_ids
                and claim.public_value.casefold() not in issue_values
            )
            add_section(
                ReaderGuidePurpose.COURT_REASONING,
                "Why the Court did it",
                reasoning_types,
                (LegalStatus.DESCRIBED, LegalStatus.COURT_HELD),
                (_is_reasoning_claim,),
                reasoning_pool,
            )
            if separate:
                add_section(
                    ReaderGuidePurpose.SEPARATE_OPINIONS,
                    "What separate opinions said",
                    (LegalObservationType.DOCTRINAL_THEME, LegalObservationType.HOLDING),
                    (LegalStatus.DESCRIBED, LegalStatus.COURT_HELD),
                    (lambda claim: _is_separate_claim(claim),),
                    separate,
                )

        next_claims = tuple(
            claim
            for claim in controlling
            if claim.observation_type
            in {
                LegalObservationType.ORDER,
                LegalObservationType.HOLDING,
                LegalObservationType.PROCEDURAL_POSTURE,
            }
            and re.search(
                r"\b(?:pending|remand|sent .* back|appeal continues|"
                r"further proceedings)\b",
                claim.public_value,
                re.I,
            )
        )
        if next_claims and sessions:
            next_types = tuple(
                value
                for value in (
                    LegalObservationType.ORDER,
                    LegalObservationType.HOLDING,
                    LegalObservationType.PROCEDURAL_POSTURE,
                )
                if any(claim.observation_type is value for claim in next_claims)
            )
            next_statuses = tuple(
                value
                for value in LegalStatus
                if any(claim.legal_status is value for claim in next_claims)
            )
            add_section(
                ReaderGuidePurpose.NEXT_KNOWN_STEP,
                "What happens next",
                next_types,
                next_statuses,
                (lambda claim: True,),
                next_claims,
            )

        argument_packets = tuple(self._argument_packet(session, claims) for session in sessions)
        summary_selected = _select_claims(
            (*background, *issue),
            (lambda claim: claim in background, lambda claim: claim in issue),
            maximum_claims=min(2, self.limits.max_claims_per_section),
            maximum_characters=self.limits.max_characters_per_section,
            safe_code="unsupported_summary",
        )
        summary_packets = tuple(_packet(claim) for claim in summary_selected)
        sections, argument_packets = _trim_to_total_bounds(
            sections,
            argument_packets,
            summary_packets,
            self.limits,
        )
        all_packets = (
            *summary_packets,
            *(claim for section in sections for claim in section.claims),
            *(claim for argument in argument_packets for claim in argument.claims),
        )
        if (
            len(all_packets) > self.limits.max_total_claims
            or sum(len(claim.public_value) for claim in all_packets)
            > self.limits.max_total_characters
        ):
            raise ReaderGuidePlanningError(
                "required reader-guide coverage exceeds the total plan bound",
                safe_code="reader_guide_plan_too_large",
            )
        title_claim = next(
            (
                claim
                for claim in summary_selected
                if claim.public_source_label.casefold() == "docket"
                or "/docket/" in claim.official_url.casefold()
                or candidate.primary_docket.casefold() in claim.public_value.casefold()
            ),
            summary_selected[0],
        )
        return ReaderGuidePlan(
            case_id=candidate.case_id,
            caption=candidate.caption,
            primary_docket=candidate.primary_docket,
            case_status=candidate.case_status,
            maturity=maturity,
            title_claim_ids=(title_claim.claim_id,),
            summary_claims=summary_packets,
            sections=tuple(sections),
            arguments=argument_packets,
        )

    def _argument_packet(
        self,
        session: CaseArgumentSession,
        claims: tuple[ScotusApprovedClaim, ...],
    ) -> ReaderGuideArgumentPacket:
        session_claims = tuple(
            claim
            for claim in claims
            if claim.argument_id == session.argument_id and not _is_separate_claim(claim)
        )
        position_types = {
            LegalObservationType.ADVOCATE_CONTENTION,
            LegalObservationType.REQUESTED_DISPOSITION,
            LegalObservationType.ANSWER,
            LegalObservationType.CONCESSION,
            LegalObservationType.DISPUTED_PREMISE,
        }
        position_claims = tuple(
            claim for claim in session_claims if claim.observation_type in position_types
        )
        roles = tuple(
            sorted(
                {
                    role
                    for claim in position_claims
                    if (role := _position_group(claim.attribution)) is not None
                },
                key=lambda item: item.value,
            )
        )
        required: list[ClaimPredicate] = [_has_position(role) for role in roles]
        if not roles and position_claims:
            required.append(lambda claim: claim in position_claims)
        if any(
            claim.observation_type is LegalObservationType.JUSTICE_QUESTION
            for claim in session_claims
        ):
            required.append(
                lambda claim: claim.observation_type is LegalObservationType.JUSTICE_QUESTION
            )
        required_predicates = tuple(required)
        if not required_predicates:
            raise ReaderGuidePlanningError(
                "argument session has no supported side or justice-question material",
                safe_code="unsupported_argument_session",
            )
        selected = _select_claims(
            session_claims,
            required_predicates,
            maximum_claims=self.limits.max_claims_per_argument,
            maximum_characters=self.limits.max_characters_per_argument,
            safe_code="unsupported_argument_session",
        )
        selected_ids = {claim.claim_id for claim in selected}
        positions: list[ReaderGuidePositionPacket] = []
        for role in roles:
            ids = tuple(
                claim.claim_id for claim in selected if _position_group(claim.attribution) is role
            )
            if ids:
                positions.append(
                    ReaderGuidePositionPacket(
                        role=role,
                        established=True,
                        attribution=next(
                            claim.attribution
                            for claim in selected
                            if claim.claim_id in ids and claim.attribution
                        ),
                        claim_ids=ids,
                    )
                )
        unknown_attributions = sorted(
            {
                claim.attribution
                for claim in selected
                if claim.observation_type in position_types
                and _position_group(claim.attribution) is None
                and claim.attribution
            },
            key=str.casefold,
        )
        for attribution in unknown_attributions[: max(0, 8 - len(positions))]:
            ids = tuple(
                claim.claim_id
                for claim in selected
                if claim.attribution == attribution and claim.observation_type in position_types
            )
            if ids:
                positions.append(
                    ReaderGuidePositionPacket(
                        role=AdvocateRole.UNKNOWN,
                        established=False,
                        attribution=attribution,
                        claim_ids=ids,
                    )
                )
        return ReaderGuideArgumentPacket(
            argument_id=session.argument_id,
            argument_date=session.argument_date,
            sequence=session.sequence,
            reargument=session.reargument,
            official_detail_url=session.official_detail_url,
            official_transcript_url=session.official_transcript_url,
            claims=tuple(_packet(claim) for claim in selected),
            positions=tuple(positions),
            justice_question_claim_ids=tuple(
                claim.claim_id
                for claim in selected
                if claim.observation_type is LegalObservationType.JUSTICE_QUESTION
                and claim.claim_id in selected_ids
            )[:8],
            action_slots=build_canonical_action_slots(selected),
            plain_language_guidance=_contains_term_guidance(selected),
        )


def _is_separate_claim(claim: ScotusApprovedClaim) -> bool:
    return bool(
        (
            claim.attribution
            and _SEPARATE_OPINION_ATTRIBUTION.search(claim.attribution)
        )
        or _SEPARATE_OPINION_VALUE.search(claim.public_value)
    )


def _writer_claim(claim: ReaderGuideClaimPacket) -> dict[str, object]:
    """Omit provenance/link metadata that is irrelevant to the translation task."""
    return {
        "id": str(claim.claim_id),
        "type": claim.observation_type.value,
        "status": claim.legal_status.value,
        "value": claim.public_value,
        **({"attribution": claim.attribution} if claim.attribution else {}),
        **({"position": claim.position_group.value} if claim.position_group else {}),
    }


def _writer_slot(slot: CanonicalActionSlot) -> dict[str, object]:
    return {
        "claim_id": str(slot.claim_id),
        "actor_role": slot.actor_role.value,
        "actor": slot.actor,
        "action": slot.action.value,
        "object": slot.operative_object,
        "negated": slot.negated,
        "effect": slot.effect.value,
        **({"timing": slot.timing} if slot.timing else {}),
    }


def compact_reader_guide_payload(plan: ReaderGuidePlan) -> dict[str, object]:
    """Return the complete and intentionally compact private writer payload."""
    return {
        "task": "translate approved facts into everyday language",
        "summary": [_writer_claim(claim) for claim in plan.summary_claims],
        "sections": [
            {
                "purpose": section.purpose.value,
                "guidance": section.reader_purpose,
                "claims": [_writer_claim(claim) for claim in section.claims],
                **(
                    {"actions": [_writer_slot(slot) for slot in section.action_slots]}
                    if section.action_slots
                    else {}
                ),
                **(
                    {"terms": list(section.plain_language_guidance)}
                    if section.plain_language_guidance
                    else {}
                ),
            }
            for section in plan.sections
        ],
        "arguments": [
            {
                "claims": [_writer_claim(claim) for claim in argument.claims],
                "positions": [
                    {
                        "role": position.role.value,
                        "established": position.established,
                        "claim_ids": [str(value) for value in position.claim_ids],
                    }
                    for position in argument.positions
                ],
                "justice_question_claim_ids": [
                    str(value) for value in argument.justice_question_claim_ids
                ],
                **(
                    {"actions": [_writer_slot(slot) for slot in argument.action_slots]}
                    if argument.action_slots
                    else {}
                ),
                **(
                    {"terms": list(argument.plain_language_guidance)}
                    if argument.plain_language_guidance
                    else {}
                ),
            }
            for argument in plan.arguments
        ],
    }


def compact_reader_guide_schema(plan: ReaderGuidePlan) -> dict[str, object]:
    paragraph = {"type": "string", "minLength": 1, "maxLength": 800}
    return {
        "type": "object",
        "properties": {
            "dek": {"type": "string", "minLength": 1, "maxLength": 500},
            "section_paragraphs": {
                "type": "array",
                "items": paragraph,
                "minItems": len(plan.sections),
                "maxItems": len(plan.sections),
            },
            "argument_paragraphs": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": paragraph,
                    "minItems": 2,
                    "maxItems": 2,
                },
                "minItems": len(plan.arguments),
                "maxItems": len(plan.arguments),
            },
        },
        "required": ["dek", "section_paragraphs", "argument_paragraphs"],
        "additionalProperties": False,
    }


class RequestExecutor(Protocol):
    def __call__(self, request: dict[str, Any]) -> object: ...


class CompactReaderGuideWriter:
    """Schema-constrained writer whose only discretionary output is prose."""

    PROMPT_VERSION = "scotus-reader-guide-compact-v1"

    def __init__(
        self,
        model_name: str,
        request_executor: RequestExecutor,
        *,
        maximum_output_tokens: int = 8_000,
    ) -> None:
        if not model_name or len(model_name) > 200:
            raise ValueError("writer model name is invalid")
        if not 1 <= maximum_output_tokens <= 100_000:
            raise ValueError("writer output-token bound is invalid")
        self.model_name = model_name
        self.request_executor = request_executor
        self.maximum_output_tokens = maximum_output_tokens

    def build_request(self, plan: ReaderGuidePlan) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "temperature": 0,
            "max_tokens": self.maximum_output_tokens,
            "reasoning_effort": "none",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "/no_think\nTranslate each supplied packet into direct everyday language. "
                        "Do not select facts, statuses, actors, actions, citations, headings, or "
                        "session order. Preserve every action slot, attribute positions, treat a "
                        "justice question only as a question, explain necessary legal terms in the "
                        "same sentence, make no prediction, and return only the requested JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        compact_reader_guide_payload(plan), separators=(",", ":")
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "compact_reader_guide",
                    "strict": True,
                    "schema": compact_reader_guide_schema(plan),
                },
            },
        }

    def generate(self, plan: ReaderGuidePlan) -> LegalBriefDraft:
        payload = _response_payload(self.request_executor(self.build_request(plan)))
        return _assemble_draft(plan, payload)


class PlannedReaderGuideGenerator:
    """Adapter matching the existing brief-generator boundary for later pipeline wiring."""

    def __init__(
        self,
        planner: ReaderGuidePlanner,
        writer: CompactReaderGuideWriter,
    ) -> None:
        self.planner = planner
        self.writer = writer
        self.model_name = writer.model_name

    def generate(
        self,
        candidate: BriefCandidate,
        claims: tuple[ScotusApprovedClaim, ...],
        maturity: BriefMaturity,
    ) -> LegalBriefDraft:
        return self.writer.generate(self.planner.plan(candidate, claims, maturity))


class ReaderGuideFieldKind(StrEnum):
    DEK = "dek"
    SECTION_PARAGRAPH = "section_paragraph"
    ARGUMENT_PARAGRAPH = "argument_paragraph"


class ReaderGuideFieldPath(StrictModel):
    kind: ReaderGuideFieldKind
    section_index: int | None = Field(default=None, ge=0, lt=MAX_PLAN_SECTIONS)
    argument_index: int | None = Field(default=None, ge=0, lt=MAX_ARGUMENT_PACKETS)
    paragraph_index: int | None = Field(default=None, ge=0, lt=6)

    @model_validator(mode="after")
    def indexes_match_kind(self) -> Self:
        expected = {
            ReaderGuideFieldKind.DEK: (False, False, False),
            ReaderGuideFieldKind.SECTION_PARAGRAPH: (True, False, True),
            ReaderGuideFieldKind.ARGUMENT_PARAGRAPH: (False, True, True),
        }[self.kind]
        actual = (
            self.section_index is not None,
            self.argument_index is not None,
            self.paragraph_index is not None,
        )
        if actual != expected:
            raise ValueError("repair path indexes do not match its field kind")
        return self


@dataclass(frozen=True, slots=True, repr=False)
class ProcessLocalFieldDiagnostic:
    """Detailed ephemeral feedback.  Only ``safe_code`` may cross the run boundary."""

    path: ReaderGuideFieldPath
    safe_code: str
    rule: str
    offending_term: str | None
    required_transformation: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z0-9_:-]{1,120}", self.safe_code) is None:
            raise ValueError("repair diagnostic safe code is invalid")
        for value, maximum, label in (
            (self.rule, 300, "rule"),
            (self.offending_term or "", 120, "offending term"),
            (self.required_transformation, 500, "required transformation"),
        ):
            if not value and label != "offending term":
                raise ValueError(f"repair diagnostic {label} is empty")
            if len(value) > maximum:
                raise ValueError(f"repair diagnostic {label} exceeds its bound")

    def __repr__(self) -> str:
        return f"ProcessLocalFieldDiagnostic(safe_code={self.safe_code!r})"


FieldValidator = Callable[[str, tuple[UUID, ...]], None]
GuideValidator = Callable[[LegalBriefDraft], None]


class TargetedReaderGuideRepairer:
    """Repair exactly one field, then validate the field and complete assembled guide."""

    PROMPT_VERSION = "scotus-reader-guide-field-repair-v2"

    def __init__(
        self,
        model_name: str,
        request_executor: RequestExecutor,
        *,
        maximum_output_tokens: int = 8_000,
    ) -> None:
        if not model_name or len(model_name) > 200:
            raise ValueError("repair model name is invalid")
        if not 1 <= maximum_output_tokens <= 100_000:
            raise ValueError("repair output-token bound is invalid")
        self.model_name = model_name
        self.request_executor = request_executor
        self.maximum_output_tokens = maximum_output_tokens

    def build_request(
        self,
        plan: ReaderGuidePlan,
        draft: LegalBriefDraft,
        diagnostic: ProcessLocalFieldDiagnostic,
    ) -> dict[str, Any]:
        rejected, claims, packet = _repair_context(plan, draft, diagnostic.path)
        maximum_length = _field_maximum(diagnostic.path)
        return {
            "model": self.model_name,
            "temperature": 0,
            "max_tokens": self.maximum_output_tokens,
            "reasoning_effort": "none",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "/no_think\nRewrite only the rejected field. Preserve its supported "
                        "meaning, "
                        "claim scope, actors, action, object, negation, and effect. Return no "
                        "commentary and do not refer to validation or processing."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "field_path": diagnostic.path.model_dump(mode="json"),
                            "rejected_text": rejected,
                            "support_packet": packet,
                            "diagnostic": {
                                "rule": diagnostic.rule,
                                **(
                                    {"offending_term": diagnostic.offending_term}
                                    if diagnostic.offending_term
                                    else {}
                                ),
                                "required_transformation": diagnostic.required_transformation,
                            },
                            "fixed_claim_ids": [str(value) for value in claims],
                        },
                        separators=(",", ":"),
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "reader_guide_field_repair",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": maximum_length,
                            }
                        },
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                },
            },
        }

    def repair(
        self,
        plan: ReaderGuidePlan,
        draft: LegalBriefDraft,
        diagnostic: ProcessLocalFieldDiagnostic,
        *,
        validate_field: FieldValidator,
        validate_guide: GuideValidator,
    ) -> LegalBriefDraft:
        rejected, claim_ids, _ = _repair_context(plan, draft, diagnostic.path)
        request = self.build_request(plan, draft, diagnostic)
        payload = _response_payload(self.request_executor(request))
        if set(payload) != {"text"}:
            raise ReaderGuideWritingError(
                "repair writer returned unexpected fields", safe_code="invalid_repair_schema"
            )
        text = payload.get("text")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > _field_maximum(diagnostic.path)
        ):
            raise ReaderGuideWritingError(
                "repair writer returned invalid field prose", safe_code="invalid_repair_schema"
            )
        repaired = _replace_field(draft, diagnostic.path, text)
        if _field_value(repaired, diagnostic.path) == rejected:
            raise ReaderGuideWritingError(
                "repair writer did not change the rejected field",
                safe_code="unchanged_repair_field",
            )
        _assert_only_target_changed(draft, repaired, diagnostic.path)
        try:
            validate_field(text, claim_ids)
            validate_guide(repaired)
        except BriefValidationError:
            raise
        except ValueError as error:
            raise ReaderGuideWritingError(
                "repaired guide failed deterministic validation",
                safe_code="repaired_guide_invalid",
            ) from error
        return repaired


def _response_payload(response: object) -> dict[str, object]:
    if isinstance(response, Mapping):
        payload: object = dict(response)
    elif isinstance(response, str):
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            raise ReaderGuideWritingError(
                "writer returned invalid JSON", safe_code="invalid_writer_schema"
            ) from None
    else:
        choices = getattr(response, "choices", ())
        content = (
            getattr(getattr(choices[0], "message", None), "content", None) if choices else None
        )
        if not isinstance(content, str):
            raise ReaderGuideWritingError(
                "writer returned no structured content", safe_code="empty_writer_content"
            )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            raise ReaderGuideWritingError(
                "writer returned invalid JSON", safe_code="invalid_writer_schema"
            ) from None
    if not isinstance(payload, dict):
        raise ReaderGuideWritingError(
            "writer returned a non-object response", safe_code="invalid_writer_schema"
        )
    return payload


def _assemble_draft(plan: ReaderGuidePlan, payload: Mapping[str, object]) -> LegalBriefDraft:
    if len(plan.caption) > 180:
        raise ReaderGuideWritingError(
            "the exact official caption exceeds the public title bound",
            safe_code="unsupported_title_length",
        )
    if set(payload) != {"dek", "section_paragraphs", "argument_paragraphs"}:
        raise ReaderGuideWritingError(
            "writer response has unexpected fields", safe_code="invalid_writer_schema"
        )
    dek = payload["dek"]
    section_values = payload["section_paragraphs"]
    argument_values = payload["argument_paragraphs"]
    if (
        not isinstance(dek, str)
        or not 0 < len(dek) <= 500
        or not isinstance(section_values, list)
        or len(section_values) != len(plan.sections)
        or not all(isinstance(value, str) and 0 < len(value) <= 800 for value in section_values)
        or not isinstance(argument_values, list)
        or len(argument_values) != len(plan.arguments)
        or not all(
            isinstance(value, list)
            and len(value) == 2
            and all(isinstance(paragraph, str) and 0 < len(paragraph) <= 800 for paragraph in value)
            for value in argument_values
        )
    ):
        raise ReaderGuideWritingError(
            "writer response violates the compact schema", safe_code="invalid_writer_schema"
        )
    return LegalBriefDraft(
        title=plan.caption,
        title_claim_ids=plan.title_claim_ids,
        dek=dek,
        dek_claim_ids=tuple(claim.claim_id for claim in plan.summary_claims),
        sections=tuple(
            DraftSection(
                heading=packet.heading,
                paragraphs=(paragraph,),
                claim_ids=tuple(claim.claim_id for claim in packet.claims),
            )
            for packet, paragraph in zip(plan.sections, section_values, strict=True)
        ),
        argument_analyses=tuple(
            DraftArgumentAnalysis(
                argument_id=packet.argument_id,
                heading=(
                    f"Reargument on {packet.argument_date.date().isoformat()}"
                    if packet.reargument
                    else f"Argument on {packet.argument_date.date().isoformat()}"
                ),
                paragraphs=tuple(paragraphs),
                claim_ids=tuple(claim.claim_id for claim in packet.claims),
            )
            for packet, paragraphs in zip(plan.arguments, argument_values, strict=True)
        ),
    )


def _repair_context(
    plan: ReaderGuidePlan,
    draft: LegalBriefDraft,
    path: ReaderGuideFieldPath,
) -> tuple[str, tuple[UUID, ...], dict[str, object]]:
    if path.kind is ReaderGuideFieldKind.DEK:
        claim_ids = tuple(claim.claim_id for claim in plan.summary_claims)
        if draft.dek_claim_ids != claim_ids:
            raise ReaderGuideWritingError(
                "draft summary citations differ from its plan",
                safe_code="invalid_repair_path",
            )
        return (
            draft.dek,
            claim_ids,
            {"claims": [_writer_claim(claim) for claim in plan.summary_claims]},
        )
    if path.kind is ReaderGuideFieldKind.SECTION_PARAGRAPH:
        assert path.section_index is not None and path.paragraph_index is not None
        try:
            section = draft.sections[path.section_index]
            packet = plan.sections[path.section_index]
            rejected = section.paragraphs[path.paragraph_index]
        except IndexError:
            raise ReaderGuideWritingError(
                "repair path is outside the planned guide", safe_code="invalid_repair_path"
            ) from None
        if section.heading != packet.heading:
            raise ReaderGuideWritingError(
                "draft section order differs from its plan", safe_code="invalid_repair_path"
            )
        claim_ids = tuple(claim.claim_id for claim in packet.claims)
        if section.claim_ids != claim_ids:
            raise ReaderGuideWritingError(
                "draft section citations differ from its plan", safe_code="invalid_repair_path"
            )
        return (
            rejected,
            claim_ids,
            {
                "purpose": packet.purpose.value,
                "guidance": packet.reader_purpose,
                "claims": [_writer_claim(claim) for claim in packet.claims],
                "actions": [_writer_slot(slot) for slot in packet.action_slots],
                "terms": list(packet.plain_language_guidance),
            },
        )
    assert path.argument_index is not None and path.paragraph_index is not None
    try:
        analysis = draft.argument_analyses[path.argument_index]
        argument_packet = plan.arguments[path.argument_index]
        rejected = analysis.paragraphs[path.paragraph_index]
    except IndexError:
        raise ReaderGuideWritingError(
            "repair path is outside the planned guide", safe_code="invalid_repair_path"
        ) from None
    if analysis.argument_id != argument_packet.argument_id:
        raise ReaderGuideWritingError(
            "draft argument identity differs from its plan", safe_code="invalid_repair_path"
        )
    claim_ids = tuple(claim.claim_id for claim in argument_packet.claims)
    if analysis.claim_ids != claim_ids:
        raise ReaderGuideWritingError(
            "draft argument citations differ from its plan", safe_code="invalid_repair_path"
        )
    return (
        rejected,
        claim_ids,
        {
            "claims": [_writer_claim(claim) for claim in argument_packet.claims],
            "positions": [
                position.model_dump(mode="json") for position in argument_packet.positions
            ],
            "actions": [_writer_slot(slot) for slot in argument_packet.action_slots],
            "terms": list(argument_packet.plain_language_guidance),
        },
    )


def _field_maximum(path: ReaderGuideFieldPath) -> int:
    return 500 if path.kind is ReaderGuideFieldKind.DEK else 800


def _field_value(draft: LegalBriefDraft, path: ReaderGuideFieldPath) -> str:
    if path.kind is ReaderGuideFieldKind.DEK:
        return draft.dek
    if path.kind is ReaderGuideFieldKind.SECTION_PARAGRAPH:
        assert path.section_index is not None and path.paragraph_index is not None
        return draft.sections[path.section_index].paragraphs[path.paragraph_index]
    assert path.argument_index is not None and path.paragraph_index is not None
    return draft.argument_analyses[path.argument_index].paragraphs[path.paragraph_index]


def _replace_field(
    draft: LegalBriefDraft, path: ReaderGuideFieldPath, replacement: str
) -> LegalBriefDraft:
    if path.kind is ReaderGuideFieldKind.DEK:
        return draft.model_copy(update={"dek": replacement})
    if path.kind is ReaderGuideFieldKind.SECTION_PARAGRAPH:
        assert path.section_index is not None and path.paragraph_index is not None
        sections = list(draft.sections)
        section = sections[path.section_index]
        paragraphs = list(section.paragraphs)
        paragraphs[path.paragraph_index] = replacement
        sections[path.section_index] = section.model_copy(update={"paragraphs": tuple(paragraphs)})
        return draft.model_copy(update={"sections": tuple(sections)})
    assert path.argument_index is not None and path.paragraph_index is not None
    analyses = list(draft.argument_analyses)
    analysis = analyses[path.argument_index]
    paragraphs = list(analysis.paragraphs)
    paragraphs[path.paragraph_index] = replacement
    analyses[path.argument_index] = analysis.model_copy(update={"paragraphs": tuple(paragraphs)})
    return draft.model_copy(update={"argument_analyses": tuple(analyses)})


def _assert_only_target_changed(
    before: LegalBriefDraft,
    after: LegalBriefDraft,
    path: ReaderGuideFieldPath,
) -> None:
    restored = _replace_field(after, path, _field_value(before, path))
    if restored.model_dump(mode="json") != before.model_dump(mode="json"):
        raise ReaderGuideWritingError(
            "field repair changed valid guide content", safe_code="repair_scope_violation"
        )
