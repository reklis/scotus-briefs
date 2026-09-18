"""Canonical, versioned data contracts shared by ingestion and publication."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

SCHEMA_VERSION = "1.0.0"
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
StableCaseId = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
DocketNumber = Annotated[str, Field(pattern=r"^(?:\d{2,4}-\d+|\d{2,4}[A-Z]\d+|\d+O)$")]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DocumentType(StrEnum):
    TRANSCRIPT = "oral_argument_transcript"
    OPINION = "opinion"
    ORDER = "order"
    MERITS_BRIEF = "merits_brief"
    REPLY_BRIEF = "reply_brief"
    AMICUS_BRIEF = "amicus_brief"
    PETITION = "petition"
    RESPONSE = "response"
    APPENDIX = "appendix"
    OTHER = "other"
    UNKNOWN = "unknown"


class CaseAssociation(ContractModel):
    case_id: StableCaseId | None = None
    docket_numbers: list[DocketNumber] = Field(default_factory=list)
    historical_group: str | None = None

    @model_validator(mode="after")
    def has_identity(self) -> CaseAssociation:
        if not self.case_id and not self.docket_numbers and not self.historical_group:
            raise ValueError("a case association must have an identity")
        return self


class SourceIdentity(ContractModel):
    url: HttpUrl | None = None
    source_id: str | None = None
    page_url: HttpUrl | None = None
    official_filename: str | None = None
    import_path: str | None = None

    @model_validator(mode="after")
    def identifies_source(self) -> SourceIdentity:
        if not self.url and not self.import_path:
            raise ValueError("source identity requires an official URL or import path")
        return self


class DocumentManifestEntry(ContractModel):
    sha256: Sha256
    archive_path: Annotated[str, Field(pattern=r"^documents/[0-9a-f]{2}/[0-9a-f]{64}\.pdf$")]
    byte_size: Annotated[int, Field(gt=0)]
    document_type: DocumentType
    sources: Annotated[list[SourceIdentity], Field(min_length=1)]
    retrieved_at: datetime
    cases: list[CaseAssociation] = Field(default_factory=list)
    supersedes: Sha256 | None = None
    media_type: Literal["application/pdf"] = "application/pdf"

    @model_validator(mode="after")
    def validate_archive_metadata(self) -> DocumentManifestEntry:
        expected = f"documents/{self.sha256[:2]}/{self.sha256}.pdf"
        if PurePosixPath(self.archive_path).as_posix() != expected:
            raise ValueError(f"archive_path must be {expected}")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must include a timezone")
        if self.supersedes == self.sha256:
            raise ValueError("a document cannot supersede itself")
        return self


class DocumentManifest(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    documents: list[DocumentManifestEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_hashes(self) -> DocumentManifest:
        hashes = [item.sha256 for item in self.documents]
        if len(hashes) != len(set(hashes)):
            raise ValueError("manifest contains duplicate hashes")
        return self


class Lifecycle(StrEnum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    ARGUED = "argued"
    AWAITING_DECISION = "awaiting_decision"
    DECIDED = "decided"
    DISMISSED = "dismissed"
    UNRESOLVED = "unresolved"


class CaseDates(ContractModel):
    filed: date | None = None
    granted: date | None = None
    argument: date | None = None
    decision: date | None = None


class Party(ContractModel):
    name: str
    role: str | None = None


class MetadataProvenance(ContractModel):
    field: str
    source_url: HttpUrl | None = None
    document_hash: Sha256 | None = None
    observed_at: datetime | None = None
    method: Literal["official", "imported", "extracted", "manual"]
    confidence: Annotated[float, Field(ge=0, le=1)] = 1.0

    @model_validator(mode="after")
    def source_present(self) -> MetadataProvenance:
        if not self.source_url and not self.document_hash:
            raise ValueError("metadata provenance requires a URL or document hash")
        return self


class CaseDocumentReference(ContractModel):
    sha256: Sha256
    document_type: DocumentType
    current: bool = True


class NormalizedCase(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    case_id: StableCaseId
    slug: StableCaseId
    title: str
    term: Annotated[int, Field(ge=1789, le=2200)] | None = None
    docket_numbers: list[DocketNumber] = Field(default_factory=list)
    primary_docket: DocketNumber | None = None
    aliases: list[str] = Field(default_factory=list)
    lifecycle: Lifecycle
    dates: CaseDates = Field(default_factory=CaseDates)
    parties: list[Party] = Field(default_factory=list)
    provenance: list[MetadataProvenance] = Field(default_factory=list)
    documents: list[CaseDocumentReference] = Field(default_factory=list)
    unresolved_group: str | None = None

    @model_validator(mode="after")
    def identity_consistency(self) -> NormalizedCase:
        if self.primary_docket and self.primary_docket not in self.docket_numbers:
            raise ValueError("primary_docket must occur in docket_numbers")
        if not self.docket_numbers and not self.unresolved_group:
            raise ValueError("a case needs a docket number or unresolved group")
        if self.lifecycle == Lifecycle.DECIDED and not self.dates.decision:
            raise ValueError("decided cases require a decision date")
        return self


class EvidenceKind(StrEnum):
    FACT = "fact"
    ALLEGATION = "allegation"
    PARTY_ARGUMENT = "party_argument"
    AMICUS_ARGUMENT = "amicus_argument"
    PROCEDURAL_EVENT = "procedural_event"
    JUSTICE_QUESTION = "justice_question"
    HOLDING = "holding"
    CONCURRENCE = "concurrence"
    DISSENT = "dissent"
    STATED_CONSEQUENCE = "stated_consequence"


class EvidenceStatus(StrEnum):
    SUPPORTED = "supported"
    UNCERTAIN = "uncertain"
    UNAVAILABLE = "unavailable"


class PageRange(ContractModel):
    start: Annotated[int, Field(ge=1)]
    end: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def ascending(self) -> PageRange:
        if self.end < self.start:
            raise ValueError("page range end precedes start")
        return self


class EvidenceRecord(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    evidence_id: Annotated[str, Field(min_length=1)]
    case_id: str
    document_hash: Sha256
    pages: PageRange
    kind: EvidenceKind
    attribution: str
    text: str
    confidence: Annotated[float, Field(ge=0, le=1)]
    status: EvidenceStatus
    opinion_part: (
        Literal["majority", "plurality", "concurrence", "dissent", "per_curiam"] | None
    ) = None


class SectionStatus(StrEnum):
    COMPLETE = "complete"
    PENDING = "pending"
    SOURCE_LIMITED = "source_limited"
    NOT_APPLICABLE = "not_applicable"


class Citation(ContractModel):
    document_hash: Sha256
    pages: PageRange
    evidence_ids: list[str] = Field(default_factory=list)


class MaterialClaim(ContractModel):
    text: str
    attribution: str | None = None
    citations: Annotated[list[Citation], Field(min_length=1)]


class GuideSection(ContractModel):
    status: SectionStatus
    heading: str
    summary: str | None = None
    summary_citations: list[Citation] = Field(default_factory=list)
    claims: list[MaterialClaim] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete_has_content(self) -> GuideSection:
        if self.status == SectionStatus.COMPLETE and not (self.summary or self.claims):
            raise ValueError("complete sections require content")
        if self.summary and not self.summary_citations:
            raise ValueError("section summaries require citations")
        if self.status in {SectionStatus.PENDING, SectionStatus.NOT_APPLICABLE} and (
            self.claims or self.summary
        ):
            raise ValueError("pending/not-applicable sections cannot contain material content")
        return self


class GlossaryEntry(ContractModel):
    term: str
    definition: str
    citations: Annotated[list[Citation], Field(min_length=1)]


class ValidationState(StrEnum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ValidationResult(ContractModel):
    state: ValidationState
    checked_at: datetime
    checks: dict[str, bool]
    messages: list[str] = Field(default_factory=list)


class GenerationProvenance(ContractModel):
    source_hashes: Annotated[list[Sha256], Field(min_length=1)]
    model_name: str
    model_digest: str
    parameters: dict[str, Any]
    prompt_version: str
    schema_version: str
    extractor_version: str
    generated_at: datetime


class CitizenGuide(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    case_id: str
    lifecycle: Lifecycle
    overview: GuideSection
    background_and_question: GuideSection
    party_positions: list[GuideSection]
    oral_argument: GuideSection
    decision: GuideSection
    why_it_matters: GuideSection
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    sources: Annotated[list[Citation], Field(min_length=1)]
    generation: GenerationProvenance
    validation: ValidationResult

    @model_validator(mode="after")
    def lifecycle_rules(self) -> CitizenGuide:
        if self.lifecycle != Lifecycle.DECIDED and self.decision.status == SectionStatus.COMPLETE:
            raise ValueError("an undecided case cannot have a complete decision section")
        if self.validation.state == ValidationState.ACCEPTED and not all(
            self.validation.checks.values()
        ):
            raise ValueError("accepted guide has a failed validation check")
        return self


CANONICAL_MODELS: dict[str, type[BaseModel]] = {
    "document-manifest": DocumentManifest,
    "normalized-case": NormalizedCase,
    "evidence": EvidenceRecord,
    "citizen-guide": CitizenGuide,
}
