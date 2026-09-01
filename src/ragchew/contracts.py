"""Versioned contracts shared by edge and cluster services."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaptureStatus(StrEnum):
    CREATED = "created"
    UPLOADING = "uploading"
    READY = "ready"
    REJECTED = "rejected"
    EXPIRED = "expired"
    AUDIO_DELETED = "audio_deleted"


class JobStage(StrEnum):
    TRANSCRIBE = "transcribe"
    EXTRACT = "extract"
    CORRELATE = "correlate"
    PUBLISH = "publish"


class TranscriptStatus(StrEnum):
    COMPLETE = "complete"
    NON_TRANSCRIBABLE = "non_transcribable"
    FAILED = "failed"


class ObservationType(StrEnum):
    LOCATION = "location"
    INCIDENT_TYPE = "incident_type"
    REPORTED_EVENT = "reported_event"
    DISPATCH = "dispatch"
    RESPONSE = "response"
    ARRIVAL = "arrival"
    ON_SCENE = "on_scene"
    ESCALATION = "escalation"
    CANCELLATION = "cancellation"
    CONTAINMENT = "containment"
    RESOLUTION = "resolution"
    CORRECTION = "correction"
    UNIT_ASSIGNMENT = "unit_assignment"
    INJURY_MENTION = "injury_mention"
    ROUTINE = "routine"
    PRIVACY = "privacy"


class EpistemicStatus(StrEnum):
    REPORTED = "reported"
    DISPATCHED = "dispatched"
    RESPONDING = "responding"
    ON_SCENE_REPORTED = "on_scene_reported"
    CONFIRMED = "confirmed"
    NEGATED = "negated"
    UNCERTAIN = "uncertain"
    CORRECTED = "corrected"


class SensitivityLabel(StrEnum):
    NONE = "none"
    MEDICAL = "medical"
    PERSONAL_IDENTIFIER = "personal_identifier"
    EXACT_RESIDENTIAL_UNIT = "exact_residential_unit"
    BEHAVIORAL_HEALTH = "behavioral_health"
    SUICIDE = "suicide"
    OVERDOSE = "overdose"
    JUVENILE = "juvenile"
    ENCRYPTED = "encrypted"


class IncidentState(StrEnum):
    CANDIDATE = "candidate"
    CORROBORATING = "corroborating"
    PUBLISHABLE = "publishable"
    ACTIVE = "active"
    RESOLVED = "resolved"
    CORRECTED = "corrected"
    RETRACTED = "retracted"
    SUPPRESSED = "suppressed"


class Certainty(StrEnum):
    REPORTED = "reported"
    DISPATCHED = "dispatched"
    ON_SCENE_REPORTED = "on_scene_reported"
    CONFIRMED = "confirmed"


class AudioDescriptor(StrictModel):
    content_type: str = Field(pattern=r"^audio/")
    byte_count: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_rate_hz: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, ge=1, le=2)


class DecoderMetadata(StrictModel):
    signal_db: float | None = None
    error_count: int | None = Field(default=None, ge=0)
    spike_count: int | None = Field(default=None, ge=0)
    dropped_samples: int | None = Field(default=None, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)


class CaptureEnvelope(StrictModel):
    schema_version: str = SCHEMA_VERSION
    capture_id: str = Field(min_length=16, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    receiver_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    system_id: str = Field(min_length=1, max_length=32)
    talkgroup_id: int = Field(gt=0)
    talkgroup_name: str = Field(min_length=1, max_length=128)
    started_at: datetime
    ended_at: datetime
    duration_ms: int = Field(gt=0)
    frequency_hz: int = Field(gt=0)
    source_radio_ids: tuple[int, ...] = ()
    encrypted: bool = False
    emergency: bool = False
    audio: AudioDescriptor
    decoder: DecoderMetadata = Field(default_factory=DecoderMetadata)

    @field_validator("started_at", "ended_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("timestamp must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if self.ended_at <= self.started_at:
            raise ValueError("ended_at must be after started_at")
        actual_ms = int((self.ended_at - self.started_at).total_seconds() * 1000)
        if abs(actual_ms - self.duration_ms) > 1_500:
            raise ValueError("duration_ms does not match timestamps")
        return self


class EvidenceRange(StrictModel):
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self


class TranscriptRevision(StrictModel):
    revision_id: UUID = Field(default_factory=uuid4)
    capture_id: str
    status: TranscriptStatus
    text: str | None = None
    normalized_text: str | None = None
    model: str
    model_config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hint_set_version: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    started_at: datetime
    completed_at: datetime


class Observation(StrictModel):
    observation_id: UUID = Field(default_factory=uuid4)
    transcript_revision_id: UUID
    capture_id: str
    type: ObservationType
    raw_value: str
    normalized_value: str | None = None
    confidence: float = Field(ge=0, le=1)
    epistemic_status: EpistemicStatus
    evidence: EvidenceRange
    occurred_at: datetime
    sensitivity: tuple[SensitivityLabel, ...] = ()
    routine: bool = False
    supersedes_observation_id: UUID | None = None


class Incident(StrictModel):
    incident_id: UUID = Field(default_factory=uuid4)
    state: IncidentState = IncidentState.CANDIDATE
    incident_type: str | None = None
    public_location: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    sensitivity: tuple[SensitivityLabel, ...] = ()
    observation_ids: tuple[UUID, ...] = ()
    first_observed_at: datetime
    updated_at: datetime
    correlation_version: str


class ApprovedPublicClaim(StrictModel):
    claim_id: UUID = Field(default_factory=uuid4)
    incident_id: UUID
    claim_type: ObservationType
    public_value: str
    certainty: Certainty
    source_observation_ids: tuple[UUID, ...] = Field(min_length=1)
    approved_at: datetime
    policy_version: str


class StoryTimelineItem(StrictModel):
    occurred_at: datetime
    text: str = Field(min_length=1, max_length=500)
    claim_ids: tuple[UUID, ...] = Field(min_length=1)


class StoryRevision(StrictModel):
    story_id: UUID
    revision_id: UUID = Field(default_factory=uuid4)
    incident_id: UUID
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1, max_length=2_000)
    status: IncidentState
    claim_ids: tuple[UUID, ...] = Field(min_length=1)
    timeline: tuple[StoryTimelineItem, ...] = ()
    created_at: datetime
    generator_model: str


class EdgeHeartbeat(StrictModel):
    schema_version: str = SCHEMA_VERSION
    receiver_id: str
    observed_at: datetime
    software_version: str
    config_version: str
    rf_min_hz: int = Field(gt=0)
    rf_max_hz: int = Field(gt=0)
    control_messages_per_minute: float = Field(ge=0)
    last_finalized_call_at: datetime | None = None
    last_acknowledged_call_at: datetime | None = None
    spool_depth: int = Field(ge=0)
    oldest_spool_age_seconds: float = Field(ge=0)
    free_disk_bytes: int = Field(ge=0)
    dropped_samples: int | None = Field(default=None, ge=0)
    clock_offset_seconds: float | None = None
    cpu_temperature_c: float | None = None
    out_of_range_calls: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_rf_window(self) -> Self:
        if self.rf_max_hz <= self.rf_min_hz:
            raise ValueError("rf_max_hz must exceed rf_min_hz")
        return self
