"""Strict process-private contracts for deterministic reader-guide planning.

These objects may contain approved claim values and internal identifiers.  They are
therefore deliberately separate from :mod:`static_contracts` and must never be
serialized into generated-content state or a static release.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ragchew.scotus.contracts import (
    LEGAL_STATUS_BY_OBSERVATION_TYPE,
    LegalObservationType,
    LegalStatus,
    ScotusCaseStatus,
)

READER_GUIDE_PLAN_SCHEMA_VERSION: Literal["1.0"] = "1.0"
MAX_PACKET_CLAIMS = 16
MAX_PACKET_CHARACTERS = 12_000
MAX_PLAN_CLAIMS = 64
MAX_PLAN_CHARACTERS = 40_000


class PrivateReaderContract(BaseModel):
    """Default-deny base for transient writer inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReaderSectionPurpose(StrEnum):
    TITLE = "title"
    SUMMARY = "summary"
    BACKGROUND = "background"
    PROCEDURAL_PATH = "procedural_path"
    LEGAL_ISSUE = "legal_issue"
    PARTY_POSITION = "party_position"
    JUSTICE_QUESTIONS = "justice_questions"
    COURT_ACTION = "court_action"
    COURT_REASONING = "court_reasoning"
    SEPARATE_OPINIONS = "separate_opinions"
    NEXT_KNOWN_STEP = "next_known_step"
    ARGUMENT_SESSION = "argument_session"


class CanonicalActorRole(StrEnum):
    REQUESTING_PARTY = "requesting_party"
    OTHER_PARTY = "other_party"
    LOWER_COURT = "lower_court"
    SUPREME_COURT = "supreme_court"


class CanonicalAction(StrEnum):
    REQUEST = "request"
    GRANT = "grant"
    DENY = "deny"
    AFFIRM = "affirm"
    REVERSE = "reverse"
    VACATE = "vacate"
    REMAND = "remand"
    DISMISS = "dismiss"
    STAY = "stay"
    ORDER = "order"
    HOLD = "hold"
    REQUIRE = "require"
    PROHIBIT = "prohibit"


class ActionEffect(StrEnum):
    REQUESTED = "requested"
    INTERIM = "interim"
    FINAL = "final"


class ReaderClaim(BaseModel):
    """The bounded approved-claim projection supplied to the prose writer."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    claim_id: UUID
    case_id: UUID
    observation_type: LegalObservationType
    legal_status: LegalStatus
    public_value: str = Field(min_length=1, max_length=2_000)
    attribution: str | None = Field(default=None, min_length=1, max_length=300)
    argument_id: UUID | None = None

    @model_validator(mode="after")
    def require_typed_legal_status(self) -> Self:
        if LEGAL_STATUS_BY_OBSERVATION_TYPE[self.observation_type] is not self.legal_status:
            raise ValueError("reader claim legal status does not match its observation type")
        return self


class PlainLanguageGuidance(PrivateReaderContract):
    term: str = Field(min_length=1, max_length=80, pattern=r"^[^\r\n]+$")
    ordinary_alternative: str = Field(min_length=1, max_length=240, pattern=r"^[^\r\n]+$")


class CanonicalActionSlot(PrivateReaderContract):
    """A semantic action the prose must preserve while changing its wording."""

    actor_role: CanonicalActorRole
    actor: str = Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")
    action: CanonicalAction
    operative_object: str = Field(min_length=1, max_length=500, pattern=r"^[^\r\n]+$")
    negated: bool = False
    effect: ActionEffect
    claim_ids: tuple[UUID, ...] = Field(min_length=1, max_length=MAX_PACKET_CLAIMS)

    @field_validator("claim_ids")
    @classmethod
    def unique_claim_ids(cls, values: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(values) != len(set(values)):
            raise ValueError("action-slot claim IDs must be unique")
        return values

    @model_validator(mode="after")
    def require_role_effect_pair(self) -> Self:
        if self.actor_role is CanonicalActorRole.SUPREME_COURT:
            if self.effect is ActionEffect.REQUESTED:
                raise ValueError("the Supreme Court cannot have a requested action effect")
        elif self.effect is not ActionEffect.REQUESTED and self.actor_role in {
            CanonicalActorRole.REQUESTING_PARTY,
            CanonicalActorRole.OTHER_PARTY,
        }:
            raise ValueError("a party action slot must describe requested effect")
        return self


def _validate_packet(
    *,
    allowed_observation_types: tuple[LegalObservationType, ...],
    required_observation_types: tuple[LegalObservationType, ...],
    allowed_legal_statuses: tuple[LegalStatus, ...],
    required_legal_statuses: tuple[LegalStatus, ...],
    claims: tuple[ReaderClaim, ...],
    action_slots: tuple[CanonicalActionSlot, ...],
    guidance: tuple[PlainLanguageGuidance, ...],
) -> None:
    collections = (
        (allowed_observation_types, "allowed observation types"),
        (required_observation_types, "required observation types"),
        (allowed_legal_statuses, "allowed legal statuses"),
        (required_legal_statuses, "required legal statuses"),
    )
    for values, label in collections:
        if len(values) != len(set(values)):
            raise ValueError(f"packet {label} must be unique")
    if not set(required_observation_types).issubset(allowed_observation_types):
        raise ValueError("required observation types must be allowed")
    if not set(required_legal_statuses).issubset(allowed_legal_statuses):
        raise ValueError("required legal statuses must be allowed")
    claim_ids = tuple(claim.claim_id for claim in claims)
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("packet claim IDs must be unique")
    if any(claim.observation_type not in allowed_observation_types for claim in claims):
        raise ValueError("packet contains a claim with a disallowed observation type")
    if any(claim.legal_status not in allowed_legal_statuses for claim in claims):
        raise ValueError("packet contains a claim with a disallowed legal status")
    present_types = {claim.observation_type for claim in claims}
    present_statuses = {claim.legal_status for claim in claims}
    if not set(required_observation_types).issubset(present_types):
        raise ValueError("packet does not support every required observation type")
    if not set(required_legal_statuses).issubset(present_statuses):
        raise ValueError("packet does not support every required legal status")
    claim_by_id = {claim.claim_id: claim for claim in claims}
    if any(not set(slot.claim_ids).issubset(claim_ids) for slot in action_slots):
        raise ValueError("action-slot claims must belong to the same packet")
    role_statuses = {
        CanonicalActorRole.REQUESTING_PARTY: {LegalStatus.REQUESTED},
        CanonicalActorRole.OTHER_PARTY: {LegalStatus.REQUESTED},
        CanonicalActorRole.LOWER_COURT: {LegalStatus.LOWER_COURT_HELD},
        CanonicalActorRole.SUPREME_COURT: {
            LegalStatus.COURT_ORDERED,
            LegalStatus.COURT_HELD,
        },
    }
    for slot in action_slots:
        if any(
            claim_by_id[claim_id].legal_status not in role_statuses[slot.actor_role]
            for claim_id in slot.claim_ids
        ):
            raise ValueError("action-slot actor role conflicts with its claim legal status")
    terms = tuple(item.term.casefold() for item in guidance)
    if len(terms) != len(set(terms)):
        raise ValueError("plain-language guidance terms must be unique")
    character_count = sum(len(claim.public_value) for claim in claims) + sum(
        len(item.term) + len(item.ordinary_alternative) for item in guidance
    )
    if character_count > MAX_PACKET_CHARACTERS:
        raise ValueError("reader packet exceeds its character bound")


class ReaderSectionPacket(PrivateReaderContract):
    heading: str = Field(min_length=1, max_length=120, pattern=r"^[^\r\n]+$")
    purpose: ReaderSectionPurpose
    reader_purpose: str = Field(min_length=1, max_length=300, pattern=r"^[^\r\n]+$")
    allowed_observation_types: tuple[LegalObservationType, ...] = Field(
        min_length=1, max_length=16
    )
    required_observation_types: tuple[LegalObservationType, ...] = Field(max_length=16)
    allowed_legal_statuses: tuple[LegalStatus, ...] = Field(min_length=1, max_length=11)
    required_legal_statuses: tuple[LegalStatus, ...] = Field(max_length=11)
    claims: tuple[ReaderClaim, ...] = Field(min_length=1, max_length=MAX_PACKET_CLAIMS)
    action_slots: tuple[CanonicalActionSlot, ...] = Field(default=(), max_length=4)
    plain_language_guidance: tuple[PlainLanguageGuidance, ...] = Field(
        default=(), max_length=16
    )

    @model_validator(mode="after")
    def validate_support(self) -> Self:
        _validate_packet(
            allowed_observation_types=self.allowed_observation_types,
            required_observation_types=self.required_observation_types,
            allowed_legal_statuses=self.allowed_legal_statuses,
            required_legal_statuses=self.required_legal_statuses,
            claims=self.claims,
            action_slots=self.action_slots,
            guidance=self.plain_language_guidance,
        )
        if self.purpose is ReaderSectionPurpose.ARGUMENT_SESSION:
            raise ValueError("case-level section purpose cannot identify an argument session")
        if any(claim.argument_id is not None for claim in self.claims):
            raise ValueError("case-level section packets cannot contain session claims")
        return self


class ReaderArgumentPacket(PrivateReaderContract):
    argument_id: UUID
    sequence: int = Field(ge=1, le=10)
    argument_date: datetime
    reargument: bool = False
    heading: str = Field(min_length=1, max_length=120, pattern=r"^[^\r\n]+$")
    purpose: ReaderSectionPurpose = ReaderSectionPurpose.ARGUMENT_SESSION
    reader_purpose: str = Field(min_length=1, max_length=300, pattern=r"^[^\r\n]+$")
    allowed_observation_types: tuple[LegalObservationType, ...] = Field(
        min_length=1, max_length=16
    )
    required_observation_types: tuple[LegalObservationType, ...] = Field(max_length=16)
    allowed_legal_statuses: tuple[LegalStatus, ...] = Field(min_length=1, max_length=11)
    required_legal_statuses: tuple[LegalStatus, ...] = Field(max_length=11)
    claims: tuple[ReaderClaim, ...] = Field(min_length=1, max_length=MAX_PACKET_CLAIMS)
    action_slots: tuple[CanonicalActionSlot, ...] = Field(default=(), max_length=4)
    plain_language_guidance: tuple[PlainLanguageGuidance, ...] = Field(
        default=(), max_length=16
    )

    @model_validator(mode="after")
    def validate_support(self) -> Self:
        if self.argument_date.tzinfo is None or self.argument_date.utcoffset() is None:
            raise ValueError("argument packet date must be timezone-aware")
        if self.purpose is not ReaderSectionPurpose.ARGUMENT_SESSION:
            raise ValueError("argument packet purpose must identify an argument session")
        _validate_packet(
            allowed_observation_types=self.allowed_observation_types,
            required_observation_types=self.required_observation_types,
            allowed_legal_statuses=self.allowed_legal_statuses,
            required_legal_statuses=self.required_legal_statuses,
            claims=self.claims,
            action_slots=self.action_slots,
            guidance=self.plain_language_guidance,
        )
        if any(claim.argument_id != self.argument_id for claim in self.claims):
            raise ValueError("argument packets may contain claims only from their own session")
        return self


class ReaderGuidePlan(PrivateReaderContract):
    schema_version: Literal["1.0"] = READER_GUIDE_PLAN_SCHEMA_VERSION
    case_id: UUID
    case_status: ScotusCaseStatus
    sections: tuple[ReaderSectionPacket, ...] = Field(min_length=1, max_length=8)
    arguments: tuple[ReaderArgumentPacket, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def validate_plan_bounds_and_order(self) -> Self:
        headings = tuple(section.heading for section in self.sections)
        if len(headings) != len(set(headings)):
            raise ValueError("reader-guide section headings must be unique")
        purpose_order = {
            purpose: index
            for index, purpose in enumerate(
                (
                    ReaderSectionPurpose.TITLE,
                    ReaderSectionPurpose.SUMMARY,
                    ReaderSectionPurpose.BACKGROUND,
                    ReaderSectionPurpose.PROCEDURAL_PATH,
                    ReaderSectionPurpose.LEGAL_ISSUE,
                    ReaderSectionPurpose.PARTY_POSITION,
                    ReaderSectionPurpose.JUSTICE_QUESTIONS,
                    ReaderSectionPurpose.COURT_ACTION,
                    ReaderSectionPurpose.COURT_REASONING,
                    ReaderSectionPurpose.SEPARATE_OPINIONS,
                    ReaderSectionPurpose.NEXT_KNOWN_STEP,
                )
            )
        }
        purposes = tuple(section.purpose for section in self.sections)
        if len(purposes) != len(set(purposes)):
            raise ValueError("reader-guide section purposes must be unique")
        if purposes != tuple(sorted(purposes, key=purpose_order.__getitem__)):
            raise ValueError("reader-guide sections must use deterministic purpose order")
        sequences = tuple(argument.sequence for argument in self.arguments)
        if sequences != tuple(range(1, len(self.arguments) + 1)):
            raise ValueError("argument packets must have contiguous sequence order")
        dates = tuple(argument.argument_date for argument in self.arguments)
        if dates != tuple(sorted(dates)):
            raise ValueError("argument packets must use chronological order")
        argument_ids = tuple(argument.argument_id for argument in self.arguments)
        if len(argument_ids) != len(set(argument_ids)):
            raise ValueError("argument packet identities must be unique")
        claims = tuple(
            claim for packet in self.sections for claim in packet.claims
        ) + tuple(claim for packet in self.arguments for claim in packet.claims)
        if any(claim.case_id != self.case_id for claim in claims):
            raise ValueError("reader-guide claims must belong to the planned case")
        if len(claims) > MAX_PLAN_CLAIMS:
            raise ValueError("reader-guide plan exceeds its transmitted-claim bound")
        claims_by_id: dict[UUID, ReaderClaim] = {}
        for claim in claims:
            prior = claims_by_id.setdefault(claim.claim_id, claim)
            if prior != claim:
                raise ValueError("a claim ID cannot have conflicting packet values")
        packet_text = sum(
            len(packet.heading)
            + len(packet.reader_purpose)
            + sum(
                len(claim.public_value) + len(claim.attribution or "")
                for claim in packet.claims
            )
            + sum(
                len(slot.actor) + len(slot.operative_object)
                for slot in packet.action_slots
            )
            + sum(
                len(item.term) + len(item.ordinary_alternative)
                for item in packet.plain_language_guidance
            )
            for packet in self.sections
        ) + sum(
            len(packet.heading)
            + len(packet.reader_purpose)
            + sum(
                len(claim.public_value) + len(claim.attribution or "")
                for claim in packet.claims
            )
            + sum(
                len(slot.actor) + len(slot.operative_object)
                for slot in packet.action_slots
            )
            + sum(
                len(item.term) + len(item.ordinary_alternative)
                for item in packet.plain_language_guidance
            )
            for packet in self.arguments
        )
        if packet_text > MAX_PLAN_CHARACTERS:
            raise ValueError("reader-guide plan exceeds its character bound")
        return self
