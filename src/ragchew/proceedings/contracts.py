"""Versioned private and public contracts for official government proceedings."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Self
from urllib.parse import urlparse
from uuid import UUID, uuid4

from pydantic import AfterValidator, Field, field_validator, model_validator

from ragchew.contracts import StrictModel

PROCEEDINGS_SCHEMA_VERSION = "1.0"


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SourceKey = Annotated[str, Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_-]+$")]
ExternalId = Annotated[str, Field(min_length=1, max_length=256)]


class Jurisdiction(StrEnum):
    FEDERAL = "federal"
    DISTRICT_OF_COLUMBIA = "district_of_columbia"


class GovernmentAuthority(StrEnum):
    US_SUPREME_COURT = "us_supreme_court"
    US_HOUSE = "us_house"
    DC_COUNCIL = "dc_council"
    DC_MAYOR = "dc_mayor"


class SourceAccessMethod(StrEnum):
    NONE = "none"
    DOCUMENTED_API = "documented_api"
    OFFICIAL_FEED = "official_feed"
    OFFICIAL_PAGE = "official_page"
    DOWNLOADABLE_FILE = "downloadable_file"
    OFFICIAL_HLS = "official_hls"
    AUTHORITATIVE_CAPTIONS = "authoritative_captions"


class SourceHealth(StrEnum):
    DISABLED = "disabled"
    HEALTHY = "healthy"
    QUIET = "quiet"
    DEGRADED = "degraded"
    REVIEW_REQUIRED = "review_required"


class ProceedingLifecycle(StrEnum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    DELAYED = "delayed"
    COMPLETED = "completed"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    ARCHIVE_PENDING = "archive_pending"
    UNAVAILABLE = "unavailable"


class ProceedingType(StrEnum):
    ORAL_ARGUMENT = "oral_argument"
    HOUSE_FLOOR = "house_floor"
    HEARING = "hearing"
    LEGISLATIVE_MEETING = "legislative_meeting"
    MAYORAL_BRIEFING = "mayoral_briefing"
    OTHER = "other"


class MediaKind(StrEnum):
    LIVE = "live"
    ARCHIVE = "archive"


class MediaStatus(StrEnum):
    DISCOVERED = "discovered"
    COLLECTING = "collecting"
    READY = "ready"
    INCOMPLETE = "incomplete"
    REJECTED = "rejected"
    EXPIRED = "expired"
    DELETED = "deleted"


class DocumentType(StrEnum):
    AGENDA = "agenda"
    DOCKET = "docket"
    LEGISLATION = "legislation"
    AMENDMENT = "amendment"
    VOTE_RECORD = "vote_record"
    ORDER = "order"
    OPINION = "opinion"
    RELEASE = "release"
    PARTICIPANT_ROSTER = "participant_roster"
    OFFICIAL_TRANSCRIPT = "official_transcript"
    OTHER_OFFICIAL_DOCUMENT = "other_official_document"


class EvidenceKind(StrEnum):
    SPOKEN_MEDIA = "spoken_media"
    OFFICIAL_TRANSCRIPT = "official_transcript"
    AGENDA = "agenda"
    DOCKET = "docket"
    LEGISLATION = "legislation"
    AMENDMENT = "amendment"
    VOTE_RECORD = "vote_record"
    ORDER = "order"
    OPINION = "opinion"
    RELEASE = "release"
    PARTICIPANT_ROSTER = "participant_roster"
    OTHER_OFFICIAL_DOCUMENT = "other_official_document"


class ParticipantRole(StrEnum):
    PUBLIC_OFFICIAL = "public_official"
    STAFF = "staff"
    EXPERT_WITNESS = "expert_witness"
    PRIVATE_WITNESS = "private_witness"
    MEMBER_OF_PUBLIC = "member_of_public"
    UNKNOWN = "unknown"


class SpeakerIdentityBasis(StrEnum):
    ANONYMOUS = "anonymous"
    OFFICIAL_ROSTER_AND_TURN = "official_roster_and_turn"
    AUTHORITATIVE_CAPTION = "authoritative_caption"
    OFFICIAL_TRANSCRIPT = "official_transcript"
    EXPLICIT_INTRODUCTION = "explicit_introduction"


class TranscriptSegmentStatus(StrEnum):
    COMPLETE = "complete"
    GAP = "gap"
    UNINTELLIGIBLE = "unintelligible"
    SILENCE = "silence"


class StatementType(StrEnum):
    QUESTION = "question"
    ARGUMENT = "argument"
    TESTIMONY = "testimony"
    PROPOSAL = "proposal"
    ANNOUNCEMENT = "announcement"
    INTRODUCTION = "introduction"
    DEBATE = "debate"
    CORRECTION = "correction"
    PROCEDURAL = "procedural"
    OTHER = "other"


class ActionType(StrEnum):
    NONE = "none"
    SCHEDULE = "schedule"
    INTRODUCE = "introduce"
    AMEND = "amend"
    MOVE = "move"
    VOTE = "vote"
    ADOPT = "adopt"
    RULE = "rule"
    ORDER = "order"
    SIGN = "sign"
    IMPLEMENT = "implement"
    DENY = "deny"
    WITHDRAW = "withdraw"
    CORRECT = "correct"


class GovernmentActionStatus(StrEnum):
    UNKNOWN = "unknown"
    SCHEDULED = "scheduled"
    QUESTIONED = "questioned"
    ARGUED = "argued"
    TESTIFIED = "testified"
    PROPOSED = "proposed"
    ANNOUNCED = "announced"
    INTRODUCED = "introduced"
    AMENDED = "amended"
    MOVED = "moved"
    ADVANCED = "advanced"
    ADOPTED = "adopted"
    PASSED_ONE_CHAMBER = "passed_one_chamber"
    ORDERED = "ordered"
    SIGNED = "signed"
    EFFECTIVE = "effective"
    IMPLEMENTED = "implemented"
    DENIED = "denied"
    WITHDRAWN = "withdrawn"
    CORRECTED = "corrected"


class GovernmentEventKind(StrEnum):
    COURT_CASE = "court_case"
    LEGISLATION = "legislation"
    OVERSIGHT = "oversight"
    BUDGET = "budget"
    AGENCY_ACTION = "agency_action"
    POLICY_ANNOUNCEMENT = "policy_announcement"
    OTHER = "other"


class OfficialSource(StrictModel):
    schema_version: str = PROCEEDINGS_SCHEMA_VERSION
    source_id: SourceKey
    authority: GovernmentAuthority
    jurisdiction: Jurisdiction
    display_name: str = Field(min_length=1, max_length=160)
    official_index_url: str
    adapter: str = Field(min_length=1, max_length=100)
    discovery_method: SourceAccessMethod
    media_method: SourceAccessMethod = SourceAccessMethod.NONE
    access_basis: str | None = Field(default=None, max_length=4_000)
    access_reviewed_at: UtcDatetime | None = None
    access_reviewed_by: str | None = Field(default=None, max_length=200)
    access_review_expires_at: UtcDatetime | None = None
    allowed_hosts: tuple[str, ...] = ()
    poll_interval_seconds: int = Field(ge=30, le=86_400)
    expected_schedule: str = Field(min_length=1, max_length=500)
    enabled: bool = False
    health: SourceHealth = SourceHealth.DISABLED

    @field_validator("official_index_url")
    @classmethod
    def require_official_https_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("official_index_url must be an HTTPS URL without credentials")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(host.lower().rstrip(".") for host in value)
        if any(not host or "/" in host or ":" in host for host in normalized):
            raise ValueError("allowed_hosts must contain hostnames only")
        if len(set(normalized)) != len(normalized):
            raise ValueError("allowed_hosts must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_authorization(self) -> Self:
        if self.access_review_expires_at and not self.access_reviewed_at:
            raise ValueError("access review expiry requires a review timestamp")
        if (
            self.access_review_expires_at
            and self.access_reviewed_at
            and self.access_review_expires_at <= self.access_reviewed_at
        ):
            raise ValueError("access review expiry must follow review timestamp")
        if self.enabled and (
            self.discovery_method is SourceAccessMethod.NONE
            or not self.access_basis
            or not self.access_reviewed_at
            or not self.access_reviewed_by
            or not self.allowed_hosts
        ):
            raise ValueError("enabled source requires an approved access basis and host allowlist")
        if self.media_method is not SourceAccessMethod.NONE and not self.allowed_hosts:
            raise ValueError("media access requires a host allowlist")
        return self


class Proceeding(StrictModel):
    schema_version: str = PROCEEDINGS_SCHEMA_VERSION
    proceeding_id: UUID = Field(default_factory=uuid4)
    source_id: SourceKey
    authority: GovernmentAuthority
    jurisdiction: Jurisdiction
    external_id: ExternalId
    proceeding_type: ProceedingType
    title: str = Field(min_length=1, max_length=500)
    official_url: str
    lifecycle: ProceedingLifecycle
    scheduled_start_at: UtcDatetime
    scheduled_end_at: UtcDatetime | None = None
    actual_start_at: UtcDatetime | None = None
    actual_end_at: UtcDatetime | None = None
    discovered_at: UtcDatetime
    updated_at: UtcDatetime

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if self.scheduled_end_at and self.scheduled_end_at <= self.scheduled_start_at:
            raise ValueError("scheduled_end_at must follow scheduled_start_at")
        if self.actual_end_at and not self.actual_start_at:
            raise ValueError("actual_end_at requires actual_start_at")
        if (
            self.actual_end_at
            and self.actual_start_at
            and self.actual_end_at <= self.actual_start_at
        ):
            raise ValueError("actual_end_at must follow actual_start_at")
        return self


class ProceedingMetadataRevision(StrictModel):
    revision_id: UUID = Field(default_factory=uuid4)
    proceeding_id: UUID
    revision_number: int = Field(ge=1)
    source_updated_at: UtcDatetime | None = None
    observed_at: UtcDatetime
    payload: dict[str, Any]
    payload_sha256: Sha256


class ProceedingMediaAsset(StrictModel):
    media_asset_id: UUID = Field(default_factory=uuid4)
    proceeding_id: UUID
    kind: MediaKind
    revision_number: int = Field(ge=1)
    source_url: str
    source_external_id: str = Field(min_length=1, max_length=500)
    content_type: str = Field(pattern=r"^(audio|video)/")
    status: MediaStatus = MediaStatus.DISCOVERED
    byte_count: int | None = Field(default=None, gt=0)
    sha256: Sha256 | None = None
    duration_ms: int | None = Field(default=None, gt=0)
    object_key: str | None = None
    canonical: bool = False
    discovered_at: UtcDatetime
    ready_at: UtcDatetime | None = None
    delete_after: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_ready_asset(self) -> Self:
        if self.status is MediaStatus.READY and (
            not self.byte_count or not self.sha256 or not self.object_key or not self.ready_at
        ):
            raise ValueError("ready media requires bytes, digest, object key, and ready time")
        return self


class ProceedingMediaChunk(StrictModel):
    chunk_id: UUID = Field(default_factory=uuid4)
    media_asset_id: UUID
    sequence: int = Field(ge=0)
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    overlap_ms: int = Field(default=0, ge=0)
    content_type: str = Field(pattern=r"^(audio|video)/")
    byte_count: int = Field(gt=0)
    sha256: Sha256
    object_key: str = Field(min_length=1, max_length=1_024)
    discontinuity_before: bool = False
    captured_at: UtcDatetime

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.source_end_ms <= self.source_start_ms:
            raise ValueError("source_end_ms must exceed source_start_ms")
        if self.overlap_ms >= self.source_end_ms - self.source_start_ms:
            raise ValueError("overlap_ms must be shorter than the chunk")
        return self


class OfficialDocument(StrictModel):
    document_id: UUID = Field(default_factory=uuid4)
    proceeding_id: UUID | None = None
    source_id: SourceKey
    document_type: DocumentType
    external_id: ExternalId
    revision_number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    official_url: str
    published_at: UtcDatetime | None = None
    observed_at: UtcDatetime
    content_type: str = Field(min_length=3, max_length=200)
    byte_count: int | None = Field(default=None, gt=0)
    sha256: Sha256
    object_key: str | None = Field(default=None, max_length=1_024)
    delete_after: UtcDatetime | None = None


class ProceedingParticipant(StrictModel):
    participant_id: UUID = Field(default_factory=uuid4)
    proceeding_id: UUID
    display_name_private: str | None = Field(default=None, max_length=300)
    public_name: str | None = Field(default=None, max_length=300)
    role: ParticipantRole
    official_role: str | None = Field(default=None, max_length=300)
    identity_basis: SpeakerIdentityBasis = SpeakerIdentityBasis.ANONYMOUS
    identity_evidence_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def validate_public_identity(self) -> Self:
        if self.public_name and self.role is not ParticipantRole.PUBLIC_OFFICIAL:
            raise ValueError("only supported public officials may have a public_name")
        if self.public_name and self.identity_basis is SpeakerIdentityBasis.ANONYMOUS:
            raise ValueError("public_name requires affirmative identity evidence")
        if (
            self.identity_basis is not SpeakerIdentityBasis.ANONYMOUS
            and not self.identity_evidence_ids
        ):
            raise ValueError("identified participant requires identity evidence")
        return self


class ProceedingTranscriptSegment(StrictModel):
    segment_id: UUID = Field(default_factory=uuid4)
    transcript_revision_id: UUID
    media_asset_id: UUID
    chunk_id: UUID | None = None
    sequence: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    status: TranscriptSegmentStatus
    text_private: str | None = None
    normalized_text_private: str | None = None
    speaker_label: str | None = Field(default=None, max_length=100)
    participant_id: UUID | None = None
    identity_basis: SpeakerIdentityBasis = SpeakerIdentityBasis.ANONYMOUS
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_segment_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def validate_segment(self) -> Self:
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must exceed start_ms")
        if self.status is TranscriptSegmentStatus.COMPLETE and not self.text_private:
            raise ValueError("complete segment requires text")
        if self.status is not TranscriptSegmentStatus.COMPLETE and self.text_private:
            raise ValueError("gap, silence, and unintelligible segments cannot contain text")
        if self.participant_id and self.identity_basis is SpeakerIdentityBasis.ANONYMOUS:
            raise ValueError("identified segment requires affirmative identity basis")
        return self


class ProceedingEvidenceReference(StrictModel):
    evidence_kind: EvidenceKind
    source_id: UUID
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    page: int | None = Field(default=None, ge=1)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, gt=0)
    locator: str | None = Field(default=None, max_length=500)
    quote_private: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def validate_locator(self) -> Self:
        if self.evidence_kind is EvidenceKind.SPOKEN_MEDIA:
            if self.start_ms is None or self.end_ms is None or self.end_ms <= self.start_ms:
                raise ValueError("spoken media evidence requires an ordered time range")
        elif not any((self.page, self.locator, self.start_char is not None)):
            raise ValueError("document evidence requires a page, character range, or locator")
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError("document character range requires both bounds")
        if (
            self.start_char is not None
            and self.end_char is not None
            and self.end_char <= self.start_char
        ):
            raise ValueError("end_char must exceed start_char")
        return self


class ProceedingObservation(StrictModel):
    observation_id: UUID = Field(default_factory=uuid4)
    extraction_revision_id: UUID
    proceeding_id: UUID
    jurisdiction: Jurisdiction
    authority: GovernmentAuthority
    body: str = Field(min_length=1, max_length=300)
    topic_hint: str | None = Field(default=None, max_length=500)
    participant_id: UUID | None = None
    speaker_label: str | None = Field(default=None, max_length=100)
    identity_basis: SpeakerIdentityBasis = SpeakerIdentityBasis.ANONYMOUS
    statement_type: StatementType
    action_type: ActionType
    action_status: GovernmentActionStatus
    raw_value_private: str = Field(min_length=1, max_length=8_000)
    normalized_value_private: str | None = Field(default=None, max_length=8_000)
    target_identifier: str | None = Field(default=None, max_length=300)
    vote_yes: int | None = Field(default=None, ge=0)
    vote_no: int | None = Field(default=None, ge=0)
    vote_other: int | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=1)
    occurred_at: UtcDatetime
    evidence: tuple[ProceedingEvidenceReference, ...] = Field(min_length=1)
    sensitive: bool = False
    supersedes_observation_id: UUID | None = None

    @model_validator(mode="after")
    def validate_identity_and_votes(self) -> Self:
        if self.participant_id and self.identity_basis is SpeakerIdentityBasis.ANONYMOUS:
            raise ValueError("identified observation requires affirmative identity basis")
        has_vote = any(
            value is not None for value in (self.vote_yes, self.vote_no, self.vote_other)
        )
        has_vote_record = any(
            item.evidence_kind is EvidenceKind.VOTE_RECORD for item in self.evidence
        )
        if has_vote and not has_vote_record:
            raise ValueError("vote totals require official vote-record evidence")
        return self


class ProceedingTopic(StrictModel):
    topic_id: UUID = Field(default_factory=uuid4)
    proceeding_id: UUID
    title_private: str = Field(min_length=1, max_length=500)
    official_identifier: str | None = Field(default=None, max_length=300)
    observation_ids: tuple[UUID, ...] = ()
    first_observed_at: UtcDatetime
    updated_at: UtcDatetime
    correlation_version: str = Field(min_length=1, max_length=100)


class GovernmentEvent(StrictModel):
    event_id: UUID = Field(default_factory=uuid4)
    jurisdiction: Jurisdiction
    authority: GovernmentAuthority
    event_kind: GovernmentEventKind
    official_identifier: str | None = Field(default=None, max_length=300)
    title_private: str = Field(min_length=1, max_length=500)
    current_status: GovernmentActionStatus
    proceeding_ids: tuple[UUID, ...] = ()
    topic_ids: tuple[UUID, ...] = ()
    observation_ids: tuple[UUID, ...] = ()
    first_observed_at: UtcDatetime
    updated_at: UtcDatetime
    correlation_version: str = Field(min_length=1, max_length=100)


class ProceedingApprovedClaim(StrictModel):
    claim_id: UUID = Field(default_factory=uuid4)
    event_id: UUID
    jurisdiction: Jurisdiction
    authority: GovernmentAuthority
    official_url: str
    proceeding_at: UtcDatetime
    evidence_kind: EvidenceKind
    statement_type: StatementType
    action_status: GovernmentActionStatus
    public_value: str = Field(min_length=1, max_length=2_000)
    public_official_name: str | None = Field(default=None, max_length=300)
    source_observation_ids: tuple[UUID, ...] = Field(min_length=1)
    approved_at: UtcDatetime
    policy_version: str = Field(min_length=1, max_length=100)


class ProceedingStoryTimelineItem(StrictModel):
    occurred_at: UtcDatetime
    text: str = Field(min_length=1, max_length=500)
    claim_ids: tuple[UUID, ...] = Field(min_length=1)


class ProceedingPublicStory(StrictModel):
    schema_version: str = PROCEEDINGS_SCHEMA_VERSION
    story_id: UUID
    revision_id: UUID = Field(default_factory=uuid4)
    event_id: UUID
    jurisdiction: Jurisdiction
    authority: GovernmentAuthority
    official_url: str
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1, max_length=2_000)
    status: GovernmentActionStatus
    claim_ids: tuple[UUID, ...] = Field(min_length=1)
    timeline: tuple[ProceedingStoryTimelineItem, ...] = ()
    correction_note: str | None = Field(default=None, max_length=1_000)
    created_at: UtcDatetime
    generator_model: str = Field(min_length=1, max_length=200)
