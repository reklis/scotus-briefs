"""Strict sanitized public contracts for SCOTUS Legal Briefs."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ragchew.scotus.contracts import BriefMaturity, ScotusCaseStatus

STATIC_PUBLIC_SCHEMA_VERSION: Literal["1.1"] = "1.1"
LEGACY_STATIC_PUBLIC_SCHEMA_VERSION: Literal["1.0"] = "1.0"

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


def _require_aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
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

    @field_validator("argument_date")
    @classmethod
    def require_aware_argument_date(cls, value: datetime) -> datetime:
        return _require_aware(value, "argument date")


class PublicDisposition(BaseModel):
    """One dated Court disposition; values come only from reviewed official metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["opinion", "per_curiam", "decree"]
    official_url: str
    publication_date: datetime
    revision_date: datetime | None = None

    @field_validator("official_url")
    @classmethod
    def require_official_url(cls, value: str) -> str:
        try:
            return _require_official_court_url(value)
        except ValueError as error:
            raise ValueError("disposition link must point to the official Court host") from error

    @field_validator("publication_date", "revision_date")
    @classmethod
    def require_aware_dates(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware(value, "disposition date")

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if self.revision_date is not None and self.revision_date <= self.publication_date:
            raise ValueError("disposition revision date must follow publication")
        return self


class PublicBriefRevisionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_number: int = Field(ge=1)
    maturity: BriefMaturity
    created_at: datetime
    correction_note: str | None = None


def derive_latest_court_document_date(
    arguments: tuple[PublicArgumentAnalysis, ...],
    dispositions: tuple[PublicDisposition, ...],
    *,
    legacy_argument_date: datetime | None = None,
) -> datetime:
    """Derive activity only from official argument and disposition dates.

    ``legacy_argument_date`` is accepted solely for schema-1.0 compatibility. It is the
    deterministic fallback for an argued case whose URL-only disposition has no reviewed
    exact index match; build, retrieval, and article timestamps never participate.
    """
    values = [item.argument_date for item in arguments]
    values.extend(item.publication_date for item in dispositions)
    values.extend(
        item.revision_date for item in dispositions if item.revision_date is not None
    )
    if legacy_argument_date is not None:
        values.append(_require_aware(legacy_argument_date, "legacy argument date"))
    if not values:
        raise ValueError("public case has no dated official Court activity")
    return max(values)


class PublicCaseBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Schema 1.0 remains an explicit read-only compatibility shape. All constructors
    # default to 1.1 and current release validation requires 1.1.
    schema_version: Literal["1.0", "1.1"] = STATIC_PUBLIC_SCHEMA_VERSION
    slug: str = Field(pattern=r"^[a-z0-9-]+$")
    term: str = Field(pattern=r"^\d{4}$")
    primary_docket: str
    caption: str
    argument_date: datetime | None = None
    latest_court_document_date: datetime | None = None
    case_status: ScotusCaseStatus
    maturity: BriefMaturity
    title: str
    dek: str
    title_sources: tuple[PublicSourceLink, ...] = Field(min_length=1)
    dek_sources: tuple[PublicSourceLink, ...] = Field(min_length=1)
    sections: tuple[PublicBriefSection, ...] = Field(min_length=1)
    arguments: tuple[PublicArgumentAnalysis, ...] = ()
    case_history: tuple[PublicCaseHistoryEvent, ...] = Field(min_length=1)
    official_detail_url: str | None = None
    official_docket_url: str
    # URL-only values are retained for unmatched schema-1.0 migrations. Newly known
    # dispositions use ``dispositions`` and may not be duplicated here.
    official_disposition_urls: tuple[str, ...] = ()
    undated_disposition_date_fallback: Literal["latest_argument_date"] | None = None
    dispositions: tuple[PublicDisposition, ...] = ()
    revisions: tuple[PublicBriefRevisionSummary, ...] = Field(min_length=1)

    @field_validator("official_detail_url", "official_docket_url")
    @classmethod
    def require_official_case_urls(cls, value: str | None) -> str | None:
        if value is None:
            return None
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
        if len(values) != len(set(values)):
            raise ValueError("legacy disposition links must be unique")
        return values

    @field_validator("argument_date", "latest_court_document_date")
    @classmethod
    def require_aware_case_dates(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware(value, "Court activity date")

    updated_at: datetime
    topics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_identity_activity_and_revisions(self) -> Self:
        if not self.slug.startswith(f"{public_case_key(self.term, self.primary_docket)}-"):
            raise ValueError("public case slug must preserve the stable term/docket identity")
        numbers = tuple(revision.revision_number for revision in self.revisions)
        if numbers != tuple(range(1, len(numbers) + 1)):
            raise ValueError("public case revisions must be contiguous and immutable")
        sequences = tuple(argument.sequence for argument in self.arguments)
        if len(set(sequences)) != len(sequences):
            raise ValueError("public argument sequences must be unique")
        if sequences != tuple(sorted(sequences)):
            raise ValueError("public arguments must use deterministic sequence order")
        disposition_keys = tuple(
            (
                item.publication_date,
                item.revision_date or item.publication_date,
                item.kind,
                item.official_url,
            )
            for item in self.dispositions
        )
        if len({item.official_url for item in self.dispositions}) != len(self.dispositions):
            raise ValueError("public dispositions must have unique official URLs")
        if disposition_keys != tuple(sorted(disposition_keys)):
            raise ValueError("public dispositions must use deterministic date and URL order")
        structured_urls = {item.official_url for item in self.dispositions}
        if structured_urls & set(self.official_disposition_urls):
            raise ValueError("structured and legacy disposition links cannot overlap")

        if self.schema_version == LEGACY_STATIC_PUBLIC_SCHEMA_VERSION:
            if (
                self.dispositions
                or self.latest_court_document_date is not None
                or self.undated_disposition_date_fallback is not None
            ):
                raise ValueError("schema-1.0 cases cannot contain schema-1.1 activity fields")
            if (
                not self.arguments
                or self.argument_date is None
                or self.official_detail_url is None
            ):
                raise ValueError("legacy public cases require real argument metadata")
            return self

        if self.arguments:
            latest_argument = max(item.argument_date for item in self.arguments)
            if self.argument_date != latest_argument:
                raise ValueError("public argument date must equal the latest real argument date")
            if self.official_detail_url is None:
                raise ValueError("an argued case requires an official argument detail URL")
        elif self.argument_date is not None:
            raise ValueError("a zero-argument case cannot claim an argument date")

        if not self.arguments and not self.dispositions:
            raise ValueError("a zero-argument case requires a dated official disposition")
        if bool(self.official_disposition_urls) != bool(
            self.undated_disposition_date_fallback
        ):
            raise ValueError(
                "undated disposition links require the explicit migration fallback"
            )
        expected = derive_latest_court_document_date(
            self.arguments,
            self.dispositions,
            legacy_argument_date=(
                self.argument_date
                if self.undated_disposition_date_fallback is not None
                else None
            ),
        )
        if self.latest_court_document_date != expected:
            raise ValueError("latest Court document date does not match official activity")
        return self


class ScotusPublicProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0", "1.1"] = STATIC_PUBLIC_SCHEMA_VERSION
    watermark: datetime
    generated_at: datetime
    cases: tuple[PublicCaseBrief, ...]
    disclosure: str = DISCLOSURE
    site_name: str = "SCOTUS Legal Briefs"

    @model_validator(mode="after")
    def one_page_per_case(self) -> Self:
        identities = [public_case_key(case.term, case.primary_docket) for case in self.cases]
        if len(identities) != len(set(identities)):
            raise ValueError("public projection contains duplicate case pages")
        case_versions = {case.schema_version for case in self.cases}
        if self.schema_version == "1.0" and case_versions - {"1.0"}:
            raise ValueError("schema-1.0 projections may contain only schema-1.0 cases")
        if self.schema_version == "1.1" and case_versions - {"1.1"}:
            raise ValueError("current projections may contain only current public cases")
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
