"""Strict sanitized public contracts for SCOTUS Legal Briefs."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ragchew.scotus.contracts import BriefMaturity, ScotusCaseStatus

DISCLOSURE = (
    "Automated and delayed legal analysis. Incomplete and non-authoritative; not an official "
    "Supreme Court record, not legal advice, and not a prediction of any justice's vote or case "
    "outcome. Always consult the linked official Court materials."
)


class PublicSourceLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_type: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    official_url: str
    page_label: str = Field(min_length=1, max_length=100)
    claim_ids: tuple[UUID, ...] = Field(min_length=1)

    @field_validator("official_url")
    @classmethod
    def require_court_host(cls, value: str) -> str:
        if not value.startswith("https://www.supremecourt.gov/"):
            raise ValueError("public source link must point to the official Court host")
        return value


class PublicBriefSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    heading: str
    paragraphs: tuple[str, ...]
    sources: tuple[PublicSourceLink, ...] = Field(min_length=1)


class PublicCaseHistoryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ScotusCaseStatus
    changed_at: datetime
    explanation: str = Field(min_length=1, max_length=300)


class PublicArgumentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    argument_date: datetime
    reargument: bool = False
    heading: str
    paragraphs: tuple[str, ...] = Field(min_length=2)
    official_detail_url: str
    official_transcript_url: str
    sources: tuple[PublicSourceLink, ...] = Field(min_length=1)

    @field_validator("official_detail_url", "official_transcript_url")
    @classmethod
    def require_official_urls(cls, value: str) -> str:
        if not value.startswith("https://www.supremecourt.gov/"):
            raise ValueError("argument links must point to the official Court host")
        return value


class PublicBriefRevisionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_number: int = Field(ge=1)
    maturity: BriefMaturity
    created_at: datetime
    correction_note: str | None = None


class PublicCaseBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(pattern=r"^[a-z0-9-]+$")
    term: str = Field(pattern=r"^\d{4}$")
    primary_docket: str
    caption: str
    argument_date: datetime
    case_status: ScotusCaseStatus
    maturity: BriefMaturity
    title: str
    dek: str
    title_sources: tuple[PublicSourceLink, ...] = Field(min_length=1)
    dek_sources: tuple[PublicSourceLink, ...] = Field(min_length=1)
    sections: tuple[PublicBriefSection, ...] = Field(min_length=1)
    arguments: tuple[PublicArgumentAnalysis, ...] = Field(min_length=1)
    case_history: tuple[PublicCaseHistoryEvent, ...] = Field(min_length=1)
    official_detail_url: str
    official_docket_url: str
    official_disposition_urls: tuple[str, ...] = ()
    revisions: tuple[PublicBriefRevisionSummary, ...] = Field(min_length=1)

    @field_validator("official_detail_url", "official_docket_url")
    @classmethod
    def require_official_case_urls(cls, value: str) -> str:
        if not value.startswith("https://www.supremecourt.gov/"):
            raise ValueError("case links must point to the official Court host")
        return value

    @field_validator("official_disposition_urls")
    @classmethod
    def require_official_disposition_urls(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.startswith("https://www.supremecourt.gov/") for value in values):
            raise ValueError("disposition links must point to the official Court host")
        return values
    updated_at: datetime
    topics: tuple[str, ...] = ()


class ScotusPublicProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    watermark: datetime
    generated_at: datetime
    cases: tuple[PublicCaseBrief, ...]
    disclosure: str = DISCLOSURE
    site_name: str = "SCOTUS Legal Briefs"

    @model_validator(mode="after")
    def one_page_per_case(self) -> ScotusPublicProjection:
        identities = [(case.term, case.primary_docket) for case in self.cases]
        if len(identities) != len(set(identities)):
            raise ValueError("public projection contains duplicate case pages")
        return self


def public_case_slug(term: str, primary_docket: str, caption: str) -> str:
    caption_slug = re.sub(r"[^a-z0-9]+", "-", caption.lower()).strip("-")[:80]
    docket_slug = re.sub(r"[^a-z0-9]+", "-", primary_docket.lower()).strip("-")
    return f"{term}-{docket_slug}-{caption_slug}"
