"""Versioned contracts for transcript-first Supreme Court legal analysis."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self
from urllib.parse import urlparse
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from ragchew.contracts import StrictModel
from ragchew.proceedings.contracts import Sha256, UtcDatetime

SCOTUS_SCHEMA_VERSION = "1.0"


class ScotusCaseStatus(StrEnum):
    DOCKETED = "docketed"
    ARGUED = "argued"
    REARGUED = "reargued"
    ORDER_ISSUED = "order_issued"
    DECIDED = "decided"
    CORRECTED = "corrected"
    UNRESOLVED = "unresolved"


class ArgumentStatus(StrEnum):
    TRANSCRIPT_PENDING = "transcript_pending"
    TRANSCRIPT_READY = "transcript_ready"
    ANALYZED = "analyzed"
    PUBLISHED = "published"
    CORRECTED = "corrected"
    RETRACTED = "retracted"


class ScotusDocumentKind(StrEnum):
    TRANSCRIPT = "transcript"
    DOCKET = "docket"
    QUESTION_PRESENTED = "question_presented"
    ORDER = "order"
    OPINION = "opinion"
    OTHER_OFFICIAL = "other_official"


class DocumentRevisionStatus(StrEnum):
    DISCOVERED = "discovered"
    DOWNLOADING = "downloading"
    READY = "ready"
    QUARANTINED = "quarantined"
    PARSE_FAILED = "parse_failed"
    PARSED = "parsed"
    CONTENT_DELETED = "content_deleted"


class ParseStatus(StrEnum):
    COMPLETE = "complete"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class SpeakerKind(StrEnum):
    JUSTICE = "justice"
    ADVOCATE = "advocate"
    COURT_OFFICIAL = "court_official"
    UNKNOWN = "unknown"


class AdvocateRole(StrEnum):
    PETITIONER = "petitioner"
    RESPONDENT = "respondent"
    UNITED_STATES = "united_states"
    AMICUS = "amicus"
    UNKNOWN = "unknown"


class SpeakerIdentityBasis(StrEnum):
    ANONYMOUS = "anonymous"
    OFFICIAL_TRANSCRIPT_LABEL = "official_transcript_label"
    OFFICIAL_ARGUMENT_METADATA = "official_argument_metadata"
    EXPLICIT_INTRODUCTION = "explicit_introduction"


class LegalObservationType(StrEnum):
    CASE_BACKGROUND = "case_background"
    PROCEDURAL_POSTURE = "procedural_posture"
    QUESTION_PRESENTED = "question_presented"
    ADVOCATE_CONTENTION = "advocate_contention"
    JUSTICE_QUESTION = "justice_question"
    ANSWER = "answer"
    CONCESSION = "concession"
    DISPUTED_PREMISE = "disputed_premise"
    AUTHORITY_CITATION = "authority_citation"
    DOCTRINAL_THEME = "doctrinal_theme"
    REQUESTED_DISPOSITION = "requested_disposition"
    LOWER_COURT_ACTION = "lower_court_action"
    ORDER = "order"
    HOLDING = "holding"


class LegalStatus(StrEnum):
    DESCRIBED = "described"
    ASSERTED = "asserted"
    QUESTIONED = "questioned"
    ANSWERED = "answered"
    CONCEDED = "conceded"
    DISPUTED = "disputed"
    REQUESTED = "requested"
    LOWER_COURT_HELD = "lower_court_held"
    COURT_ORDERED = "court_ordered"
    COURT_HELD = "court_held"
    UNKNOWN = "unknown"


class LegalCertainty(StrEnum):
    DIRECT = "direct"
    ATTRIBUTED = "attributed"
    ANALYST_FORMULATION = "analyst_formulation"
    UNCERTAIN = "uncertain"


class ScotusSensitivity(StrEnum):
    NONE = "none"
    MINOR = "minor"
    VICTIM = "victim"
    MEDICAL = "medical"
    SEALED_OR_REDACTED = "sealed_or_redacted"
    HOME_ADDRESS = "home_address"
    PRIVATE_NAME = "private_name"


class BriefMaturity(StrEnum):
    OFFICIAL_TRANSCRIPT = "official_transcript"
    POST_ORDER = "post_order"
    POST_OPINION = "post_opinion"
    CORRECTED = "corrected"
    RETRACTED = "retracted"


class DocketReference(StrictModel):
    docket_id: UUID = Field(default_factory=uuid4)
    term: str = Field(pattern=r"^\d{4}$")
    docket_number: str = Field(min_length=1, max_length=40)
    normalized_docket: str = Field(min_length=1, max_length=40)
    official_url: str
    primary: bool = False

    @field_validator("official_url")
    @classmethod
    def official_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname != "www.supremecourt.gov":
            raise ValueError("official_url must be Court-hosted HTTPS")
        return value


class ScotusCase(StrictModel):
    schema_version: str = SCOTUS_SCHEMA_VERSION
    case_id: UUID = Field(default_factory=uuid4)
    term: str = Field(pattern=r"^\d{4}$")
    caption: str = Field(min_length=1, max_length=500)
    status: ScotusCaseStatus = ScotusCaseStatus.UNRESOLVED
    docket_ids: tuple[UUID, ...] = Field(min_length=1)
    primary_docket: str = Field(min_length=1, max_length=40)
    official_url: str
    first_observed_at: UtcDatetime
    updated_at: UtcDatetime


class ArgumentSession(StrictModel):
    argument_id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    term: str = Field(pattern=r"^\d{4}$")
    session_key: str = Field(min_length=1, max_length=200)
    argument_date: UtcDatetime
    sequence: int = Field(default=1, ge=1)
    reargument: bool = False
    status: ArgumentStatus = ArgumentStatus.TRANSCRIPT_PENDING
    official_detail_url: str
    transcript_document_id: UUID | None = None
    discovered_at: UtcDatetime
    updated_at: UtcDatetime

    @model_validator(mode="after")
    def require_transcript_for_ready_status(self) -> Self:
        ready = {
            ArgumentStatus.TRANSCRIPT_READY,
            ArgumentStatus.ANALYZED,
            ArgumentStatus.PUBLISHED,
            ArgumentStatus.CORRECTED,
        }
        if self.status in ready and self.transcript_document_id is None:
            raise ValueError("ready argument status requires a transcript document")
        return self


class ScotusDocumentRevision(StrictModel):
    document_revision_id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    argument_id: UUID | None = None
    kind: ScotusDocumentKind
    external_id: str = Field(min_length=1, max_length=500)
    revision_number: int = Field(ge=1)
    official_url: str
    status: DocumentRevisionStatus = DocumentRevisionStatus.DISCOVERED
    content_type: str = Field(min_length=3, max_length=200)
    byte_count: int | None = Field(default=None, gt=0)
    sha256: Sha256 | None = None
    object_key: str | None = Field(default=None, max_length=1_024)
    canonical: bool = False
    source_published_at: UtcDatetime | None = None
    observed_at: UtcDatetime
    ready_at: UtcDatetime | None = None
    delete_after: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_ready_revision(self) -> Self:
        ready = {DocumentRevisionStatus.READY, DocumentRevisionStatus.PARSED}
        if self.status in ready and (
            self.byte_count is None
            or self.sha256 is None
            or self.object_key is None
            or self.ready_at is None
        ):
            raise ValueError("ready document requires bytes, digest, object key, and ready time")
        if self.kind is ScotusDocumentKind.TRANSCRIPT and self.argument_id is None:
            raise ValueError("transcript revision requires an argument session")
        return self


class TranscriptLine(StrictModel):
    line_id: UUID = Field(default_factory=uuid4)
    parse_revision_id: UUID
    document_revision_id: UUID
    file_page: int = Field(ge=1)
    printed_page: int | None = Field(default=None, ge=1)
    line_number: int = Field(ge=1)
    raw_text_private: str = Field(min_length=1, max_length=2_000)
    normalized_text_private: str | None = Field(default=None, max_length=2_000)
    artifact: bool = False


class TranscriptTurn(StrictModel):
    turn_id: UUID = Field(default_factory=uuid4)
    parse_revision_id: UUID
    document_revision_id: UUID
    sequence: int = Field(ge=0)
    start_file_page: int = Field(ge=1)
    start_line: int = Field(ge=1)
    end_file_page: int = Field(ge=1)
    end_line: int = Field(ge=1)
    speaker_label_private: str | None = Field(default=None, max_length=300)
    speaker_name: str | None = Field(default=None, max_length=300)
    speaker_kind: SpeakerKind = SpeakerKind.UNKNOWN
    advocate_role: AdvocateRole | None = None
    identity_basis: SpeakerIdentityBasis = SpeakerIdentityBasis.ANONYMOUS
    text_private: str = Field(min_length=1, max_length=20_000)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_turn(self) -> Self:
        if (self.end_file_page, self.end_line) < (self.start_file_page, self.start_line):
            raise ValueError("turn end must not precede start")
        if self.speaker_name and self.identity_basis is SpeakerIdentityBasis.ANONYMOUS:
            raise ValueError("named speaker requires affirmative identity basis")
        if self.advocate_role and self.speaker_kind is not SpeakerKind.ADVOCATE:
            raise ValueError("advocate role requires advocate speaker kind")
        return self


class LegalEvidenceRange(StrictModel):
    document_revision_id: UUID
    document_kind: ScotusDocumentKind
    start_file_page: int = Field(ge=1)
    start_line: int = Field(ge=1)
    end_file_page: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote_private: str = Field(min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def ordered_range(self) -> Self:
        if (self.end_file_page, self.end_line) < (self.start_file_page, self.start_line):
            raise ValueError("evidence end must not precede start")
        return self


class LegalObservation(StrictModel):
    observation_id: UUID = Field(default_factory=uuid4)
    extraction_revision_id: UUID
    case_id: UUID
    argument_id: UUID | None = None
    observation_type: LegalObservationType
    legal_status: LegalStatus
    certainty: LegalCertainty
    raw_value_private: str = Field(min_length=1, max_length=8_000)
    normalized_value_private: str | None = Field(default=None, max_length=8_000)
    attribution: str | None = Field(default=None, max_length=500)
    speaker_name: str | None = Field(default=None, max_length=300)
    speaker_kind: SpeakerKind = SpeakerKind.UNKNOWN
    identity_basis: SpeakerIdentityBasis = SpeakerIdentityBasis.ANONYMOUS
    authority_citations: tuple[str, ...] = ()
    confidence: float = Field(ge=0, le=1)
    evidence: tuple[LegalEvidenceRange, ...] = Field(min_length=1)
    sensitivity: tuple[ScotusSensitivity, ...] = ()
    supersedes_observation_id: UUID | None = None

    @model_validator(mode="after")
    def enforce_evidence_status(self) -> Self:
        final_types = {LegalObservationType.ORDER, LegalObservationType.HOLDING}
        final_evidence = {ScotusDocumentKind.ORDER, ScotusDocumentKind.OPINION}
        if self.observation_type in final_types and not any(
            item.document_kind in final_evidence for item in self.evidence
        ):
            raise ValueError("an order or holding requires official order/opinion evidence")
        if self.speaker_name and self.identity_basis is SpeakerIdentityBasis.ANONYMOUS:
            raise ValueError("named speaker requires affirmative identity basis")
        attributed = {
            LegalObservationType.ADVOCATE_CONTENTION,
            LegalObservationType.ANSWER,
            LegalObservationType.CONCESSION,
            LegalObservationType.DISPUTED_PREMISE,
            LegalObservationType.REQUESTED_DISPOSITION,
        }
        if self.observation_type in attributed and not self.attribution:
            raise ValueError("attributed observation requires attribution")
        return self


class ScotusIssue(StrictModel):
    issue_id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    issue_key: str = Field(min_length=1, max_length=300)
    title_private: str = Field(min_length=1, max_length=500)
    authority_citations: tuple[str, ...] = ()
    observation_ids: tuple[UUID, ...] = ()
    first_observed_at: UtcDatetime
    updated_at: UtcDatetime
    correlation_version: str = Field(min_length=1, max_length=100)


class ScotusCaseAggregate(StrictModel):
    case_id: UUID
    status: ScotusCaseStatus
    issue_ids: tuple[UUID, ...] = ()
    observation_ids: tuple[UUID, ...] = ()
    updated_at: UtcDatetime
    correlation_version: str = Field(min_length=1, max_length=100)


class ScotusApprovedClaim(StrictModel):
    claim_id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    argument_id: UUID | None = None
    observation_type: LegalObservationType
    legal_status: LegalStatus
    certainty: LegalCertainty
    public_value: str = Field(min_length=1, max_length=2_000)
    attribution: str | None = Field(default=None, max_length=500)
    official_url: str
    public_source_label: str = Field(min_length=1, max_length=200)
    page_label: str = Field(min_length=1, max_length=100)
    source_observation_ids: tuple[UUID, ...] = Field(min_length=1)
    approved_at: UtcDatetime
    policy_version: str = Field(min_length=1, max_length=100)


class BriefSection(StrictModel):
    heading: str = Field(min_length=1, max_length=120)
    paragraphs: tuple[str, ...] = Field(min_length=1)
    claim_ids: tuple[UUID, ...] = Field(min_length=1)


class BriefArgumentAnalysis(StrictModel):
    argument_id: UUID
    sequence: int = Field(ge=1)
    argument_date: UtcDatetime
    reargument: bool = False
    heading: str = Field(min_length=1, max_length=120)
    paragraphs: tuple[str, ...] = Field(min_length=2)
    claim_ids: tuple[UUID, ...] = Field(min_length=1)


class LegalBriefRevision(StrictModel):
    schema_version: str = SCOTUS_SCHEMA_VERSION
    brief_id: UUID
    revision_id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    argument_id: UUID
    revision_number: int = Field(ge=1)
    maturity: BriefMaturity
    title: str = Field(min_length=1, max_length=180)
    title_claim_ids: tuple[UUID, ...] = Field(min_length=1)
    dek: str = Field(min_length=1, max_length=500)
    dek_claim_ids: tuple[UUID, ...] = Field(min_length=1)
    sections: tuple[BriefSection, ...] = Field(min_length=1)
    argument_analyses: tuple[BriefArgumentAnalysis, ...] = Field(min_length=1)
    claim_ids: tuple[UUID, ...] = Field(min_length=1)
    correction_note: str | None = Field(default=None, max_length=1_000)
    created_at: UtcDatetime
    generator_model: str = Field(min_length=1, max_length=200)


class PublicCaseProjection(StrictModel):
    schema_version: str = SCOTUS_SCHEMA_VERSION
    projection_id: UUID = Field(default_factory=uuid4)
    watermark: UtcDatetime
    cases: tuple[dict[str, Any], ...]
    generated_at: UtcDatetime
