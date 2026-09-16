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
CITIZENS_GUIDE_SCHEMA_VERSION = "scotus-citizens-guide-schema-v1"
MAX_CITIZENS_GUIDE_WORDS = 180
MIN_FIELD_SENTENCES = 1
MAX_FIELD_SENTENCES = 2
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


class CitizensGuideField(StrEnum):
    """The only conceptual prose fields exposed to the compact writer."""

    WHAT_IT_IS_ABOUT = "what_it_is_about"
    WHAT_THE_SIDES_SAY = "what_the_sides_say"
    WHAT_THE_COURT_DID = "what_the_court_did"
    WHY_IT_MATTERS = "why_it_matters"


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
_GUIDE_WORD = re.compile(
    r"\b[\w]+(?:[\N{RIGHT SINGLE QUOTATION MARK}'-][\w]+)*\b", re.UNICODE
)
_GUIDE_ACRONYM = re.compile(r"\b(?:[A-Za-z]\.){2,}")
_GUIDE_ABBREVIATION = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|No|Inc|Ltd|Co|v)\.", re.I
)
_GUIDE_SENTENCE_END = re.compile(r"[.!?]+(?=(?:[\"')\]]*)?(?:\s|$))")


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
    ReaderGuidePurpose.BACKGROUND: (
        "Explain only the concrete dispute and approved practical impact; do not predict effects."
    ),
    ReaderGuidePurpose.PROCEDURAL_PATH: (
        "Explain only essential history and name the lower court for every lower-court action."
    ),
    ReaderGuidePurpose.LEGAL_ISSUE: (
        "State the practical question, not an answer or predicted result."
    ),
    ReaderGuidePurpose.POSITIONS: (
        "Name each side and use argues, says, asks, or wants; never turn a request into a ruling."
    ),
    ReaderGuidePurpose.JUSTICE_QUESTIONS: (
        "Describe only the issue tested, without naming individual justices or implying votes."
    ),
    ReaderGuidePurpose.COURT_ACTION: (
        "Name the Supreme Court and state only its supplied action, object, and effect."
    ),
    ReaderGuidePurpose.COURT_REASONING: (
        "Explain only the approved controlling reason or impact; do not infer consequences."
    ),
    ReaderGuidePurpose.SEPARATE_OPINIONS: (
        "Attribute each separate view to its author and never present it as the Court's ruling."
    ),
    ReaderGuidePurpose.NEXT_KNOWN_STEP: (
        "State only a supplied next procedural step and its actor, never a prediction."
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
            action_candidates = tuple(
                claim
                for claim in controlling
                if claim.observation_type in action_types
                and claim.legal_status in {LegalStatus.COURT_HELD, LegalStatus.COURT_ORDERED}
            )
            action_claims: tuple[ScotusApprovedClaim, ...] = ()
            if action_candidates:
                action_claims = add_section(
                    ReaderGuidePurpose.COURT_ACTION,
                    "What the Supreme Court did",
                    action_types,
                    (LegalStatus.COURT_HELD, LegalStatus.COURT_ORDERED),
                    (lambda claim: claim.observation_type in action_types,),
                    action_candidates,
                )
            reasoning_types = (
                LegalObservationType.DOCTRINAL_THEME,
                LegalObservationType.HOLDING,
            )
            issue_ids = {claim.claim_id for claim in issue}
            issue_values = {claim.public_value.casefold() for claim in issue}
            action_ids = {claim.claim_id for claim in action_claims}
            reasoning_pool = tuple(
                claim
                for claim in controlling
                if claim.claim_id not in issue_ids | action_ids
                and claim.public_value.casefold() not in issue_values
                and claim.observation_type in reasoning_types
                and claim.legal_status in {LegalStatus.DESCRIBED, LegalStatus.COURT_HELD}
                and _is_reasoning_claim(claim)
            )
            if reasoning_pool:
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


def _field_packet(
    *,
    purpose: str,
    guidance: str,
    claims: tuple[ReaderGuideClaimPacket, ...],
    action_slots: tuple[CanonicalActionSlot, ...] = (),
    terms: tuple[str, ...] = (),
) -> dict[str, object]:
    """Build one evidence boundary; no claim or action from another field is included."""
    claim_ids = {claim.claim_id for claim in claims}
    return {
        "purpose": purpose,
        "guidance": guidance,
        "claims": [_writer_claim(claim) for claim in claims],
        **(
            {
                "actions": [
                    _writer_slot(slot) for slot in action_slots if slot.claim_id in claim_ids
                ]
            }
            if any(slot.claim_id in claim_ids for slot in action_slots)
            else {}
        ),
        **({"terms": list(terms)} if terms else {}),
    }


@dataclass(frozen=True, slots=True)
class _CitizensGuideFieldPacket:
    name: CitizensGuideField
    heading: str | None
    guidance: str
    claims: tuple[ReaderGuideClaimPacket, ...]
    action_slots: tuple[CanonicalActionSlot, ...] = ()
    terms: tuple[str, ...] = ()


def _unique_claims(
    claims: Iterable[ReaderGuideClaimPacket],
) -> tuple[ReaderGuideClaimPacket, ...]:
    by_id: dict[UUID, ReaderGuideClaimPacket] = {}
    for claim in claims:
        by_id.setdefault(claim.claim_id, claim)
    return tuple(by_id.values())


def _unique_slots(slots: Iterable[CanonicalActionSlot]) -> tuple[CanonicalActionSlot, ...]:
    result: list[CanonicalActionSlot] = []
    seen: set[tuple[UUID, CanonicalActorRole, CanonicalAction, str, bool]] = set()
    for slot in slots:
        key = (
            slot.claim_id,
            slot.actor_role,
            slot.action,
            slot.operative_object,
            slot.negated,
        )
        if key not in seen:
            seen.add(key)
            result.append(slot)
    return tuple(result)


def _citizens_guide_fields(plan: ReaderGuidePlan) -> tuple[_CitizensGuideFieldPacket, ...]:
    """Project the detailed deterministic plan onto the high-level public shape."""
    fields = [
        _CitizensGuideFieldPacket(
            name=CitizensGuideField.WHAT_IT_IS_ABOUT,
            heading=None,
            guidance=(
                "Explain only the supplied issue and essential background in ordinary language."
            ),
            claims=plan.summary_claims,
        )
    ]

    position_sections = tuple(
        section for section in plan.sections if section.purpose is ReaderGuidePurpose.POSITIONS
    )
    if position_sections:
        position_claims = _unique_claims(
            claim
            for section in position_sections
            for claim in section.claims
            if claim.position_group is not None
        )
        position_ids = {claim.claim_id for claim in position_claims}
        position_slots = _unique_slots(
            slot
            for section in position_sections
            for slot in section.action_slots
            if slot.claim_id in position_ids
        )
        position_terms = tuple(
            dict.fromkeys(
                term for section in position_sections for term in section.plain_language_guidance
            )
        )
    else:
        # A sparse docket may establish an attributed request without an argument
        # session. That is still safe side evidence; unattributed history is not.
        procedural = tuple(
            section
            for section in plan.sections
            if section.purpose is ReaderGuidePurpose.PROCEDURAL_PATH
        )
        position_claims = _unique_claims(
            claim
            for section in procedural
            for claim in section.claims
            if claim.attribution
            and claim.observation_type
            in {
                LegalObservationType.ADVOCATE_CONTENTION,
                LegalObservationType.REQUESTED_DISPOSITION,
                LegalObservationType.ANSWER,
                LegalObservationType.CONCESSION,
                LegalObservationType.DISPUTED_PREMISE,
            }
        )
        position_ids = {claim.claim_id for claim in position_claims}
        position_slots = _unique_slots(
            slot
            for section in procedural
            for slot in section.action_slots
            if slot.claim_id in position_ids
        )
        position_terms = ()
    if position_claims:
        fields.append(
            _CitizensGuideFieldPacket(
                name=CitizensGuideField.WHAT_THE_SIDES_SAY,
                heading="What the sides say",
                guidance=(
                    "State only the attributed party positions or requests. Name each side and "
                    "use argues, says, asks, or wants; never turn a request into a ruling."
                ),
                claims=position_claims,
                action_slots=position_slots,
                terms=position_terms,
            )
        )

    action = next(
        (
            section
            for section in plan.sections
            if section.purpose is ReaderGuidePurpose.COURT_ACTION
        ),
        None,
    )
    if action is not None:
        fields.append(
            _CitizensGuideFieldPacket(
                name=CitizensGuideField.WHAT_THE_COURT_DID,
                heading="What the Supreme Court did",
                guidance=(
                    "State only the supplied Supreme Court action, object, polarity, timing, "
                    "and effect. Do not add lower-court history or a party request."
                ),
                claims=action.claims,
                action_slots=action.action_slots,
                terms=action.plain_language_guidance,
            )
        )
    else:
        status_section = next(
            (
                section
                for section in plan.sections
                if section.purpose is ReaderGuidePurpose.PROCEDURAL_PATH
            ),
            None,
        )
        if status_section is not None:
            status_claims = tuple(
                claim
                for claim in status_section.claims
                if claim.observation_type
                not in {
                    LegalObservationType.ADVOCATE_CONTENTION,
                    LegalObservationType.REQUESTED_DISPOSITION,
                    LegalObservationType.ANSWER,
                    LegalObservationType.CONCESSION,
                    LegalObservationType.DISPUTED_PREMISE,
                }
            )
            if not status_claims and not position_claims:
                status_claims = status_section.claims
            if status_claims:
                status_ids = {claim.claim_id for claim in status_claims}
                fields.append(
                    _CitizensGuideFieldPacket(
                        name=CitizensGuideField.WHAT_THE_COURT_DID,
                        heading="Where the case stands",
                        guidance=(
                            "State only the supplied procedural status. Identify any acting lower "
                            "court or requesting party, and do not imply a Supreme Court outcome."
                        ),
                        claims=status_claims,
                        action_slots=tuple(
                            slot
                            for slot in status_section.action_slots
                            if slot.claim_id in status_ids
                        ),
                        terms=status_section.plain_language_guidance,
                    )
                )

    impact = next(
        (
            section
            for section in plan.sections
            if section.purpose is ReaderGuidePurpose.COURT_REASONING
        ),
        None,
    )
    if impact is not None:
        fields.append(
            _CitizensGuideFieldPacket(
                name=CitizensGuideField.WHY_IT_MATTERS,
                heading="Why it matters",
                guidance=(
                    "Explain only the approved reason or practical impact. Do not predict or "
                    "infer consequences that the supplied claims do not state."
                ),
                claims=impact.claims,
                action_slots=impact.action_slots,
                terms=impact.plain_language_guidance,
            )
        )
    return tuple(fields)


def compact_reader_guide_payload(plan: ReaderGuidePlan) -> dict[str, object]:
    """Return only applicable, mutually isolated conceptual evidence packets."""
    return {
        "task": "write a concise Citizen's Guide from only each field's packet",
        "schema_version": CITIZENS_GUIDE_SCHEMA_VERSION,
        "limits": {
            "total_words": MAX_CITIZENS_GUIDE_WORDS,
            "sentences_per_field": [MIN_FIELD_SENTENCES, MAX_FIELD_SENTENCES],
        },
        "fields": {
            field.name.value: _field_packet(
                purpose=field.name.value,
                guidance=field.guidance,
                claims=field.claims,
                action_slots=field.action_slots,
                terms=field.terms,
            )
            for field in _citizens_guide_fields(plan)
        },
    }


def compact_reader_guide_schema(plan: ReaderGuidePlan) -> dict[str, object]:
    fields = _citizens_guide_fields(plan)
    properties = {
        field.name.value: {
            "type": "string",
            "minLength": 1,
            "maxLength": 500 if field.heading is None else 800,
            "description": (
                "One or two ordinary-language sentences from only the matching evidence packet."
            ),
        }
        for field in fields
    }
    return {
        "type": "object",
        "description": (
            f"{CITIZENS_GUIDE_SCHEMA_VERSION}: schema-only Citizen's Guide prose; all fields "
            f"together contain at most {MAX_CITIZENS_GUIDE_WORDS} words."
        ),
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


class RequestExecutor(Protocol):
    def __call__(self, request: dict[str, Any]) -> object: ...


class CompactReaderGuideWriter:
    """Schema-constrained Citizen's Guide writer; only prose is model-selected."""

    PROMPT_VERSION = "scotus-gpt-oss-citizens-guide-v2"
    SCHEMA_VERSION = CITIZENS_GUIDE_SCHEMA_VERSION

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
            "reasoning_effort": "low",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only final strict-schema JSON, with no reasoning, analysis, "
                        "markdown, or wrapper text in the final content. "
                        "Write one or two short, ordinary-language sentences for every output "
                        "field and no more than 180 words "
                        "across all output fields. Use only the matching field packet; never move "
                        "evidence or actions between fields. Do not select or repeat claim IDs, "
                        "action-slot IDs, legal status, citations, sources, identity, headings, or "
                        "session order. Name every actor explicitly: use argues, says, asks, or "
                        "wants for a party position or request, and reserve ruled, granted, "
                        "denied, affirmed, reversed, or sent back for the court identified by a "
                        "supplied action slot. Never say a party or agency issued a court order. "
                        "Preserve every supplied actor, role, action, object, negation, timing, "
                        "uncertainty, and "
                        "interim or final effect. Do not use ambiguous pronouns or phrases such as "
                        "'the Court agreed,' 'it ordered,' or 'that decision.' Do not name "
                        "lawyers, give justice-by-justice or per-session argument detail, add "
                        "unnecessary procedural history, infer an unavailable outcome or impact, "
                        "predict events, or use unexplained legal jargon; explain any unavoidable "
                        "legal term immediately "
                        "in the same "
                        "sentence. Omit nonessential detail rather than inventing or conflating it."
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
                    "name": "scotus_citizens_guide_v2",
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


def assembled_draft_action_slots(
    plan: ReaderGuidePlan,
) -> dict[tuple[ReaderGuideFieldPath, str], tuple[CanonicalActionSlot, ...]]:
    """Map each assembled field path and deterministic heading to its exact action slots.

    Production validation can use this projection without reconstructing the detailed
    planner sections. ``dek`` is used as the heading marker for the title-adjacent field.
    """
    result: dict[tuple[ReaderGuideFieldPath, str], tuple[CanonicalActionSlot, ...]] = {}
    section_index = 0
    for field in _citizens_guide_fields(plan):
        if field.heading is None:
            path = ReaderGuideFieldPath(kind=ReaderGuideFieldKind.DEK)
            heading = "dek"
        else:
            path = ReaderGuideFieldPath(
                kind=ReaderGuideFieldKind.SECTION_PARAGRAPH,
                section_index=section_index,
                paragraph_index=0,
            )
            heading = field.heading
            section_index += 1
        result[(path, heading)] = field.action_slots
    return result


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

    PROMPT_VERSION = "scotus-guide-repair-v4-low"

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
            "reasoning_effort": "low",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only final strict-schema JSON, with no reasoning or wrapper text "
                        "in the final content. Rewrite only the rejected field as one or two "
                        "short ordinary-language sentences so the complete guide remains at most "
                        "180 words. Use only its support packet. Preserve claim scope and every "
                        "supported actor, role, action, object, negation, timing, and effect. Name "
                        "actors explicitly; use asks or wants for requests and court-action verbs "
                        "only for the acting court. Do not name lawyers, add justice-by-justice "
                        "detail, unnecessary history, prediction, or unexplained jargon. Do not "
                        "refer to validation or processing."
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
        _validate_citizens_guide_fields(_draft_prose_fields(repaired))
        try:
            validate_field(text, claim_ids)
            validate_guide(repaired)
        except BriefValidationError as error:
            raise BriefValidationError(
                str(error),
                safe_code=error.safe_code,
                draft=repaired,
            ) from None
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


def _field_sentence_count(text: str) -> int:
    normalized = _GUIDE_ACRONYM.sub(lambda match: match.group(0).replace(".", ""), text)
    normalized = _GUIDE_ABBREVIATION.sub(lambda match: match.group(0)[:-1], normalized)
    endings = tuple(_GUIDE_SENTENCE_END.finditer(normalized))
    if not endings:
        return 1
    tail = normalized[endings[-1].end() :].strip(" \t\r\n\"')]")
    return len(endings) + bool(tail)


def _validate_citizens_guide_fields(fields: Iterable[str]) -> None:
    values = tuple(fields)
    if any(
        not value.strip()
        or not MIN_FIELD_SENTENCES <= _field_sentence_count(value) <= MAX_FIELD_SENTENCES
        for value in values
    ):
        raise ReaderGuideWritingError(
            "a Citizen's Guide field must contain one or two sentences",
            safe_code="citizens_guide_sentence_limit",
        )
    if sum(len(_GUIDE_WORD.findall(value)) for value in values) > MAX_CITIZENS_GUIDE_WORDS:
        raise ReaderGuideWritingError(
            "the Citizen's Guide exceeds its total word limit",
            safe_code="citizens_guide_word_limit",
        )


def _draft_prose_fields(draft: LegalBriefDraft) -> tuple[str, ...]:
    return (
        draft.dek,
        *(paragraph for section in draft.sections for paragraph in section.paragraphs),
        *(
            paragraph
            for analysis in draft.argument_analyses
            for paragraph in analysis.paragraphs
        ),
    )


def _assemble_draft(plan: ReaderGuidePlan, payload: Mapping[str, object]) -> LegalBriefDraft:
    if len(plan.caption) > 180:
        raise ReaderGuideWritingError(
            "the exact official caption exceeds the public title bound",
            safe_code="unsupported_title_length",
        )
    fields = _citizens_guide_fields(plan)
    expected = {field.name.value for field in fields}
    if set(payload) != expected:
        raise ReaderGuideWritingError(
            "writer response has unexpected fields", safe_code="invalid_writer_schema"
        )
    values = tuple(payload[field.name.value] for field in fields)
    if any(
        not isinstance(value, str)
        or not value
        or len(value) > (500 if field.heading is None else 800)
        for field, value in zip(fields, values, strict=True)
    ):
        raise ReaderGuideWritingError(
            "writer response violates the compact schema", safe_code="invalid_writer_schema"
        )
    prose = tuple(value for value in values if isinstance(value, str))
    _validate_citizens_guide_fields(prose)
    about = fields[0]
    section_fields = fields[1:]
    return LegalBriefDraft(
        title=plan.caption,
        title_claim_ids=plan.title_claim_ids,
        dek=prose[0],
        dek_claim_ids=tuple(claim.claim_id for claim in about.claims),
        sections=tuple(
            DraftSection(
                heading=field.heading or "",
                paragraphs=(paragraph,),
                claim_ids=tuple(claim.claim_id for claim in field.claims),
            )
            for field, paragraph in zip(section_fields, prose[1:], strict=True)
        ),
        argument_analyses=(),
    )


def _repair_context(
    plan: ReaderGuidePlan,
    draft: LegalBriefDraft,
    path: ReaderGuideFieldPath,
) -> tuple[str, tuple[UUID, ...], dict[str, object]]:
    fields = _citizens_guide_fields(plan)
    if path.kind is ReaderGuideFieldKind.DEK:
        field = fields[0]
        claim_ids = tuple(claim.claim_id for claim in field.claims)
        if draft.dek_claim_ids != claim_ids:
            raise ReaderGuideWritingError(
                "draft summary citations differ from its plan",
                safe_code="invalid_repair_path",
            )
        return (
            draft.dek,
            claim_ids,
            _field_packet(
                purpose=field.name.value,
                guidance=field.guidance,
                claims=field.claims,
                action_slots=field.action_slots,
                terms=field.terms,
            ),
        )
    if path.kind is ReaderGuideFieldKind.SECTION_PARAGRAPH:
        assert path.section_index is not None and path.paragraph_index is not None
        try:
            section = draft.sections[path.section_index]
            field = fields[path.section_index + 1]
            rejected = section.paragraphs[path.paragraph_index]
        except IndexError:
            raise ReaderGuideWritingError(
                "repair path is outside the planned guide", safe_code="invalid_repair_path"
            ) from None
        if section.heading != field.heading:
            raise ReaderGuideWritingError(
                "draft section order differs from its plan", safe_code="invalid_repair_path"
            )
        claim_ids = tuple(claim.claim_id for claim in field.claims)
        if section.claim_ids != claim_ids:
            raise ReaderGuideWritingError(
                "draft section citations differ from its plan", safe_code="invalid_repair_path"
            )
        return (
            rejected,
            claim_ids,
            _field_packet(
                purpose=field.name.value,
                guidance=field.guidance,
                claims=field.claims,
                action_slots=field.action_slots,
                terms=field.terms,
            ),
        )
    raise ReaderGuideWritingError(
        "high-level Citizen's Guides have no argument fields",
        safe_code="invalid_repair_path",
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
