"""Strict sanitized public contracts for SCOTUS Legal Briefs."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ragchew.scotus.contracts import BriefMaturity, ScotusCaseStatus

STATIC_PUBLIC_SCHEMA_VERSION = "1.0"

DISCLOSURE = (
    "Automated and delayed legal analysis. Incomplete and non-authoritative; not an official "
    "Supreme Court record, not legal advice, and not a prediction of any justice's vote or case "
    "outcome. Always consult the linked official Court materials."
)


def _require_official_court_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.supremecourt.gov"
        or parsed.username
        or parsed.password
        or parsed.port is not None
    ):
        raise ValueError("public source link must point to the official Court host")
    return value


class PublicSourceLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_type: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    official_url: str
    page_label: str = Field(min_length=1, max_length=100)

    @field_validator("official_url")
    @classmethod
    def require_court_host(cls, value: str) -> str:
        return _require_official_court_url(value)


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
        try:
            return _require_official_court_url(value)
        except ValueError as error:
            raise ValueError("argument links must point to the official Court host") from error


class PublicBriefRevisionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_number: int = Field(ge=1)
    maturity: BriefMaturity
    created_at: datetime
    correction_note: str | None = None


class PublicCaseBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
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
        try:
            return _require_official_court_url(value)
        except ValueError as error:
            raise ValueError("case links must point to the official Court host") from error

    @field_validator("official_disposition_urls")
    @classmethod
    def require_official_disposition_urls(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        try:
            for value in values:
                _require_official_court_url(value)
        except ValueError as error:
            raise ValueError("disposition links must point to the official Court host") from error
        return values

    updated_at: datetime
    topics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_identity_and_revisions(self) -> PublicCaseBrief:
        if not self.slug.startswith(f"{public_case_key(self.term, self.primary_docket)}-"):
            raise ValueError("public case slug must preserve the stable term/docket identity")
        numbers = tuple(revision.revision_number for revision in self.revisions)
        if numbers != tuple(range(1, len(numbers) + 1)):
            raise ValueError("public case revisions must be contiguous and immutable")
        if len({argument.sequence for argument in self.arguments}) != len(self.arguments):
            raise ValueError("public argument sequences must be unique")
        return self


class ScotusPublicProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    watermark: datetime
    generated_at: datetime
    cases: tuple[PublicCaseBrief, ...]
    disclosure: str = DISCLOSURE
    site_name: str = "SCOTUS Legal Briefs"

    @model_validator(mode="after")
    def one_page_per_case(self) -> ScotusPublicProjection:
        identities = [public_case_key(case.term, case.primary_docket) for case in self.cases]
        if len(identities) != len(set(identities)):
            raise ValueError("public projection contains duplicate case pages")
        return self


def public_case_key(term: str, primary_docket: str) -> str:
    """Return stable public identity independent of caption or private UUIDs."""
    if not re.fullmatch(r"\d{4}", term):
        raise ValueError("case term must be a four-digit year")
    docket_slug = re.sub(r"[^a-z0-9]+", "-", primary_docket.casefold()).strip("-")
    if not docket_slug:
        raise ValueError("case docket must contain an alphanumeric character")
    return f"{term}-{docket_slug}"


def public_case_slug(term: str, primary_docket: str, caption: str) -> str:
    caption_slug = re.sub(r"[^a-z0-9]+", "-", caption.casefold()).strip("-")[:80]
    if not caption_slug:
        raise ValueError("case caption must contain an alphanumeric character")
    return f"{public_case_key(term, primary_docket)}-{caption_slug}"
