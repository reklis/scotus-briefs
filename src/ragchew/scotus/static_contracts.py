"""Versioned contracts for public generated-content state and static releases."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import format_datetime, parsedate_to_datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ragchew.scotus.public_contracts import (
    PublicCaseBrief,
    ScotusPublicProjection,
    public_case_key,
)

STATE_SCHEMA_VERSION: Literal["1.1"] = "1.1"
RELEASE_SCHEMA_VERSION: Literal["1.0"] = "1.0"
COST_SCHEMA_VERSION: Literal["1.0"] = "1.0"
CASE_REVISION_SCHEMA_VERSION: Literal["1.1"] = "1.1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_KEY_PATTERN = r"^[a-z0-9][a-z0-9._:-]{0,199}$"
_RELEASE_ID_PATTERN = _SHA256_PATTERN
_UUID_PATTERN = re.compile(
    r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])"
)


class StaticContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ConditionalValidators(StaticContract):
    etag: str | None = Field(default=None, min_length=2, max_length=512)
    last_modified: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("etag")
    @classmethod
    def validate_etag(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r'(?:W/)?"[^"\r\n]*"', value):
            raise ValueError("ETag must be a quoted HTTP entity tag")
        return value

    @field_validator("last_modified")
    @classmethod
    def validate_last_modified(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError) as error:
            raise ValueError("Last-Modified must be an RFC 7231 HTTP date") from error
        if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
            raise ValueError("Last-Modified must use GMT")
        if format_datetime(parsed, usegmt=True) != value:
            raise ValueError("Last-Modified must use canonical RFC 7231 format")
        return value


class ContentIntegrity(StaticContract):
    sha256: str = Field(pattern=_SHA256_PATTERN)
    byte_count: int = Field(ge=0)


class LogicalSourceState(StaticContract):
    logical_key: str = Field(pattern=_KEY_PATTERN)
    source_kind: Literal["argument_index", "case_detail", "docket", "orders", "opinions"]
    official_url: str
    validators: ConditionalValidators = ConditionalValidators()
    integrity: ContentIntegrity | None = None
    checked_at: datetime

    @field_validator("official_url")
    @classmethod
    def require_official_url(cls, value: str) -> str:
        # Reuse the strict public source URL validator without exporting its implementation.
        from ragchew.scotus.public_contracts import PublicSourceLink

        PublicSourceLink(
            evidence_type="Source",
            label="Official Supreme Court source",
            official_url=value,
            page_label="resource",
        )
        return value


class LogicalDocumentState(StaticContract):
    logical_key: str = Field(pattern=_KEY_PATTERN)
    case_key: str = Field(pattern=_KEY_PATTERN)
    document_kind: Literal["transcript", "docket", "order", "opinion"]
    official_url: str
    revision_number: int = Field(ge=1)
    validators: ConditionalValidators = ConditionalValidators()
    integrity: ContentIntegrity
    checked_at: datetime

    @field_validator("official_url")
    @classmethod
    def require_official_url(cls, value: str) -> str:
        LogicalSourceState.require_official_url(value)
        return value


class CaseRevisionPointer(StaticContract):
    case_key: str = Field(pattern=_KEY_PATTERN)
    term: str = Field(pattern=r"^\d{4}$")
    primary_docket: str = Field(min_length=1, max_length=100)
    active_revision: int = Field(ge=1)
    active_slug: str = Field(pattern=r"^[a-z0-9-]+$")
    active_case_sha256: str = Field(pattern=_SHA256_PATTERN)
    # Per-case provenance lets a bounded processor/config migration resume without
    # falsely promoting the global fingerprint after only one selected case.
    processor_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    legacy_slugs: tuple[str, ...] = ()

    @field_validator("legacy_slugs")
    @classmethod
    def unique_sorted_legacy_slugs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(r"[a-z0-9-]+", value) for value in values):
            raise ValueError("legacy slugs must be URL-safe")
        return tuple(sorted(set(values)))

    @model_validator(mode="after")
    def validate_case_key(self) -> Self:
        if self.case_key != public_case_key(self.term, self.primary_docket):
            raise ValueError("case key must derive from normalized term and docket")
        if self.active_slug in self.legacy_slugs:
            raise ValueError("active slug cannot also be a legacy slug")
        return self


class PendingReason(StrEnum):
    BUDGET_EXHAUSTED = "budget_exhausted"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_INVALID = "source_invalid"
    PROCESSING_FAILED = "processing_failed"
    VALIDATION_FAILED = "validation_failed"
    DATE_BACKFILL_UNMATCHED = "date_backfill_unmatched"


class PendingWork(StaticContract):
    case_key: str = Field(pattern=_KEY_PATTERN)
    reason: PendingReason
    attempts: int = Field(ge=0)
    first_seen_at: datetime
    last_attempted_at: datetime | None = None


class DispositionDiscoveryState(StaticContract):
    """Allowlisted durable metadata for one independently discovered slip disposition."""

    logical_key: str = Field(pattern=_KEY_PATTERN)
    case_key: str = Field(pattern=_KEY_PATTERN)
    term: str = Field(pattern=r"^\d{4}$")
    primary_docket: str = Field(min_length=1, max_length=100)
    consolidated_dockets: tuple[str, ...] = ()
    caption: str = Field(min_length=1, max_length=500)
    release_number: str = Field(pattern=r"^(?:D)?\d+$")
    kind: Literal["opinion", "per_curiam", "decree"]
    official_url: str
    publication_date: datetime
    revision_date: datetime | None = None
    revision_reference_url: str | None = None
    metadata_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_metadata(self) -> Self:
        if len(set(self.consolidated_dockets)) != len(self.consolidated_dockets):
            raise ValueError("disposition consolidated dockets must be unique")
        if self.primary_docket in self.consolidated_dockets:
            raise ValueError("disposition primary docket cannot be consolidated")
        if self.revision_date is not None and self.revision_date <= self.publication_date:
            raise ValueError("disposition revision date must follow publication")
        if (self.revision_date is None) != (self.revision_reference_url is None):
            raise ValueError("disposition revision date and reference must appear together")
        LogicalSourceState.require_official_url(self.official_url)
        urls = (self.official_url,) + (
            (self.revision_reference_url,) if self.revision_reference_url is not None else ()
        )
        expected_path = rf"https://www\.supremecourt\.gov/opinions/{self.term[-2:]}pdf/[^/?#]+\.pdf"
        if any(re.fullmatch(expected_path, url, re.IGNORECASE) is None for url in urls):
            raise ValueError("disposition URL is outside its term's slip-opinion path")
        if self.revision_reference_url is not None:
            LogicalSourceState.require_official_url(self.revision_reference_url)
            if "diff" not in self.revision_reference_url.rsplit("/", 1)[-1].casefold():
                raise ValueError("disposition revision reference must be a diff PDF")
        normalized_docket = " ".join(
            self.primary_docket.replace("\N{EN DASH}", "-").strip().casefold().split()
        )
        docket_key = re.sub(r"[^a-z0-9]+", "-", normalized_docket).strip("-")
        expected_key = f"slip:{self.term}:{docket_key}:{self.release_number.casefold()}"
        if self.logical_key != expected_key:
            raise ValueError("disposition logical key does not match its official identity")
        payload = self.model_dump(
            mode="json",
            exclude={"logical_key", "case_key", "metadata_sha256"},
        )
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(encoded).hexdigest() != self.metadata_sha256:
            raise ValueError("disposition metadata digest does not match its fields")
        return self


class CursorState(StaticContract):
    cursor_key: str = Field(pattern=_KEY_PATTERN)
    position: int = Field(ge=0)
    wrapped_count: int = Field(default=0, ge=0)
    updated_at: datetime


class ProcessorFingerprint(StaticContract):
    parser_version: str = Field(min_length=1, max_length=200)
    extractor_version: str = Field(min_length=1, max_length=200)
    policy_version: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=200)
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    composite_sha256: str = Field(pattern=_SHA256_PATTERN)


class PublicationState(StaticContract):
    # Schema 1.0 is accepted only so an immutable generated-content parent can be
    # validated and passed to the explicit activity-contract migration.
    schema_version: Literal["1.0", "1.1"] = STATE_SCHEMA_VERSION
    active_release_id: str | None = Field(default=None, pattern=_RELEASE_ID_PATTERN)
    updated_at: datetime
    sources: tuple[LogicalSourceState, ...] = ()
    documents: tuple[LogicalDocumentState, ...] = ()
    dispositions: tuple[DispositionDiscoveryState, ...] = ()
    undated_disposition_case_keys: tuple[str, ...] = ()
    cases: tuple[CaseRevisionPointer, ...] = ()
    pending_work: tuple[PendingWork, ...] = ()
    cursors: tuple[CursorState, ...] = ()
    processor: ProcessorFingerprint | None = None

    @model_validator(mode="after")
    def require_stable_collections(self) -> Self:
        _require_unique_sorted(self.sources, lambda value: value.logical_key, "source")
        _require_unique_sorted(self.documents, lambda value: value.logical_key, "document")
        _require_unique_sorted(
            self.dispositions, lambda value: value.logical_key, "disposition"
        )
        if self.undated_disposition_case_keys != tuple(
            sorted(set(self.undated_disposition_case_keys))
        ):
            raise ValueError("undated disposition case keys must be unique and sorted")
        if any(
            re.fullmatch(_KEY_PATTERN, key) is None
            for key in self.undated_disposition_case_keys
        ):
            raise ValueError("undated disposition case key is invalid")
        _require_unique_sorted(self.cases, lambda value: value.case_key, "case")
        _require_unique_sorted(self.pending_work, lambda value: value.case_key, "pending case")
        _require_unique_sorted(self.cursors, lambda value: value.cursor_key, "cursor")
        return self


class PublicCaseRevisionRecord(StaticContract):
    # Historical 1.0 records remain valid at their original path and exact bytes.
    schema_version: Literal["1.0", "1.1"] = CASE_REVISION_SCHEMA_VERSION
    case_key: str = Field(pattern=_KEY_PATTERN)
    revision_number: int = Field(ge=1)
    accepted_at: datetime
    case_sha256: str = Field(pattern=_SHA256_PATTERN)
    previous_case_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    case: PublicCaseBrief

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.schema_version != self.case.schema_version:
            raise ValueError("revision record and public case schema versions differ")
        if self.case_key != public_case_key(self.case.term, self.case.primary_docket):
            raise ValueError("revision case key does not match the public case")
        if self.case.revisions[-1].revision_number != self.revision_number:
            raise ValueError("revision record number does not match public case history")
        if sha256_hex(canonical_json_bytes(self.case, privacy_check=False)) != self.case_sha256:
            raise ValueError("revision record case digest does not match its payload")
        if self.revision_number == 1 and self.previous_case_sha256 is not None:
            raise ValueError("first case revision cannot have a previous digest")
        if self.revision_number > 1 and self.previous_case_sha256 is None:
            raise ValueError("later case revision requires the previous digest")
        return self


class ModelAttemptOutcome(StrEnum):
    ATTEMPTED = "attempted"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ModelAttemptReceipt(StaticContract):
    input_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    stage: Literal["extraction", "brief"]
    # One receipt represents exactly one transport attempt. The default preserves
    # compatibility with pre-retry ledgers, whose sole attempt was implicitly first.
    attempt_number: int = Field(default=1, ge=1)
    outcome: ModelAttemptOutcome
    attempted_at: datetime
    call_count: int = Field(ge=0, le=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    token_count_source: Literal["reserved_upper_bound", "provider_reported"] = (
        "reserved_upper_bound"
    )
    estimated_cost_usd: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.outcome is ModelAttemptOutcome.BLOCKED and self.call_count != 0:
            raise ValueError("blocked model receipts cannot record a provider call")
        if self.outcome is not ModelAttemptOutcome.BLOCKED and self.call_count != 1:
            raise ValueError("attempted model receipts must record exactly one call")
        if self.outcome is ModelAttemptOutcome.BLOCKED and (
            self.input_tokens is not None
            or self.output_tokens is not None
            or self.estimated_cost_usd != 0
        ):
            raise ValueError("blocked model receipts cannot record token usage or cost")
        if self.outcome is not ModelAttemptOutcome.BLOCKED and self.input_tokens is None:
            raise ValueError("transport attempt receipts require an input token count")
        if self.outcome is ModelAttemptOutcome.SUCCEEDED and self.output_tokens is None:
            raise ValueError("successful transport receipts require an output token count")
        if self.outcome in {ModelAttemptOutcome.ATTEMPTED, ModelAttemptOutcome.FAILED} and (
            self.output_tokens is not None
        ):
            raise ValueError("unfinished transport receipts cannot claim output token usage")
        if self.token_count_source == "provider_reported" and (
            self.outcome is not ModelAttemptOutcome.SUCCEEDED
            or self.input_tokens is None
            or self.output_tokens is None
        ):
            raise ValueError("provider token counts require a successful response with usage")
        return self


def _receipt_key(receipt: ModelAttemptReceipt) -> tuple[str, str, int]:
    return receipt.stage, receipt.input_fingerprint, receipt.attempt_number


def _require_unique_receipts(receipts: tuple[ModelAttemptReceipt, ...]) -> None:
    keys = [_receipt_key(receipt) for receipt in receipts]
    if len(keys) != len(set(keys)):
        raise ValueError("cost ledger contains a repeated transport attempt")
    if keys != sorted(keys):
        raise ValueError("cost receipts must use deterministic attempt order")


class CostLedger(StaticContract):
    schema_version: Literal["1.0"] = "1.0"
    revision: int = Field(default=0, ge=0)
    updated_at: datetime
    receipts: tuple[ModelAttemptReceipt, ...] = ()

    @model_validator(mode="after")
    def unique_attempts(self) -> Self:
        _require_unique_receipts(self.receipts)
        return self


class CostReceiptBundle(StaticContract):
    """Privacy-scannable upload format for receipts isolated from active state."""

    schema_version: Literal["1.0"] = "1.0"
    receipts: tuple[ModelAttemptReceipt, ...]

    @model_validator(mode="after")
    def unique_attempts(self) -> Self:
        _require_unique_receipts(self.receipts)
        return self


class StaticSearchEntry(StaticContract):
    """The complete and deliberately minimal client-side search record."""

    path: str = Field(pattern=r"^/[^?#]*?/$")
    title: str = Field(min_length=1, max_length=300)
    caption: str = Field(min_length=1, max_length=300)
    docket: str = Field(min_length=1, max_length=100)
    term: str = Field(pattern=r"^\d{4}$")
    latest_court_document_date: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"
    )
    argument_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: str = Field(min_length=1, max_length=100)
    topics: tuple[str, ...] = ()

    @field_validator("topics")
    @classmethod
    def normalize_topics(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 100 for value in values):
            raise ValueError("search topics must be short non-empty labels")
        if tuple(sorted(set(values), key=lambda value: (value.casefold(), value))) != values:
            raise ValueError("search topics must be unique and deterministically sorted")
        return values


class StaticSearchIndex(StaticContract):
    schema_version: Literal["1.0", "1.1"] = "1.1"
    cases: tuple[StaticSearchEntry, ...]

    @model_validator(mode="after")
    def unique_paths(self) -> Self:
        paths = tuple(item.path for item in self.cases)
        if len(paths) != len(set(paths)):
            raise ValueError("search index contains duplicate case paths")
        if self.schema_version == "1.0":
            if any(
                item.latest_court_document_date is not None
                or item.argument_date is None
                for item in self.cases
            ):
                raise ValueError("schema-1.0 search entries require legacy argument dates only")
        elif any(item.latest_court_document_date is None for item in self.cases):
            raise ValueError("current search entries require latest Court document dates")
        return self


class ReleaseFile(StaticContract):
    path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)
    byte_count: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def require_safe_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("release file path must be safe and relative")
        return path.as_posix()


class ReleaseManifest(StaticContract):
    schema_version: Literal["1.0"] = "1.0"
    release_id: str = Field(pattern=_RELEASE_ID_PATTERN)
    previous_release_id: str | None = Field(default=None, pattern=_RELEASE_ID_PATTERN)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    tool_version: str = Field(min_length=1, max_length=100)
    generated_at: datetime
    files: tuple[ReleaseFile, ...]
    case_count: int = Field(ge=0)
    page_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_files(self) -> Self:
        _require_unique_sorted(self.files, lambda value: value.path, "release file")
        if self.release_id == self.previous_release_id:
            raise ValueError("release ID must differ from its previous release")
        return self


class ReleasePointers(StaticContract):
    schema_version: Literal["1.0"] = "1.0"
    active_release_id: str | None = Field(default=None, pattern=_RELEASE_ID_PATTERN)
    previous_release_id: str | None = Field(default=None, pattern=_RELEASE_ID_PATTERN)

    @model_validator(mode="after")
    def distinct_pointers(self) -> Self:
        if (
            self.active_release_id is not None
            and self.active_release_id == self.previous_release_id
        ):
            raise ValueError("active and previous release pointers must differ")
        return self


def _require_unique_sorted(items: tuple[Any, ...], key: Any, label: str) -> None:
    keys = [key(item) for item in items]
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate {label} state")
    if keys != sorted(keys):
        raise ValueError(f"{label} state must use deterministic key order")


def _json_value(value: Any) -> Any:
    if isinstance(value, ScotusPublicProjection):
        payload = value.model_dump(mode="python")
        payload["cases"] = sorted(
            payload["cases"],
            key=lambda case: public_case_key(case["term"], case["primary_docket"]),
        )
        return _json_value(payload)
    if isinstance(value, PublicCaseBrief):
        payload = value.model_dump(mode="python")
        payload["topics"] = sorted(set(payload["topics"]), key=str.casefold)
        payload["official_disposition_urls"] = sorted(set(payload["official_disposition_urls"]))
        if value.schema_version == "1.0":
            # Compatibility serialization is deliberately version-aware so loading and
            # carrying an accepted V1 revision cannot rewrite one historical byte.
            payload.pop("dispositions")
            payload.pop("latest_court_document_date")
            payload.pop("undated_disposition_date_fallback")
        return _json_value(payload)
    if isinstance(value, PublicationState):
        payload = value.model_dump(mode="python")
        if value.schema_version == "1.0":
            if not value.dispositions:
                # Preserve canonical pre-discovery schema-1.0 state bytes.
                payload.pop("dispositions")
            payload.pop("undated_disposition_case_keys")
        return _json_value(payload)
    if isinstance(value, CaseRevisionPointer):
        payload = value.model_dump(mode="python")
        if value.processor_sha256 is None:
            # Keep pre-provenance schema-1.0 pointers byte-canonical while treating
            # the omitted provenance as stale during migration.
            payload.pop("processor_sha256")
        return _json_value(payload)
    if isinstance(value, StaticSearchIndex):
        payload = value.model_dump(mode="python")
        if value.schema_version == "1.0":
            for entry in payload["cases"]:
                entry.pop("latest_court_document_date")
        return _json_value(payload)
    if isinstance(value, ModelAttemptReceipt):
        payload = value.model_dump(mode="python")
        if value.attempt_number == 1:
            payload.pop("attempt_number")
        if value.token_count_source == "reserved_upper_bound":
            payload.pop("token_count_source")
        return _json_value(payload)
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("static timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        mapping_items = dict(value)
        if (
            mapping_items.get("schema_version") == "1.0"
            and {"primary_docket", "arguments", "revisions"}.issubset(mapping_items)
        ):
            # Nested V1 cases (notably inside immutable revision records) need the
            # same version-aware omission as a top-level PublicCaseBrief.
            mapping_items.pop("dispositions", None)
            mapping_items.pop("latest_court_document_date", None)
            mapping_items.pop("undated_disposition_date_fallback", None)
        if {
            "case_key",
            "active_revision",
            "active_case_sha256",
            "processor_sha256",
        }.issubset(mapping_items) and mapping_items["processor_sha256"] is None:
            mapping_items.pop("processor_sha256")
        if {"input_fingerprint", "stage", "attempt_number", "outcome"}.issubset(
            mapping_items
        ):
            if mapping_items["attempt_number"] == 1:
                mapping_items.pop("attempt_number")
            if mapping_items.get("token_count_source") == "reserved_upper_bound":
                mapping_items.pop("token_count_source")
        normalized = {str(key): _json_value(item) for key, item in mapping_items.items()}
        for key in (
            "dek_sources",
            "official_disposition_urls",
            "sources",
            "title_sources",
            "topics",
        ):
            items = normalized.get(key)
            if isinstance(items, list):
                normalized[key] = sorted(
                    items,
                    key=lambda item: json.dumps(
                        item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).casefold(),
                )
        arguments = normalized.get("arguments")
        if isinstance(arguments, list):
            normalized["arguments"] = sorted(arguments, key=lambda item: item["sequence"])
        revisions = normalized.get("revisions")
        if isinstance(revisions, list) and all(
            isinstance(item, dict) and "revision_number" in item for item in revisions
        ):
            normalized["revisions"] = sorted(revisions, key=lambda item: item["revision_number"])
        return normalized
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def canonical_json_bytes(
    value: BaseModel | Mapping[str, Any], *, privacy_check: bool = True
) -> bytes:
    """Serialize public data as canonical UTF-8 JSON with one trailing newline."""
    payload = _json_value(value)
    if privacy_check:
        assert_public_payload(payload)
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def contract_digest(value: BaseModel | Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json_bytes(value))


def model_input_fingerprint(
    document_digests: tuple[str, ...], processor_versions: Mapping[str, str]
) -> str:
    """Create an opaque stable key without retaining evidence or prompt content."""
    if not document_digests or any(
        not re.fullmatch(_SHA256_PATTERN, item) for item in document_digests
    ):
        raise ValueError("model fingerprints require valid document SHA-256 digests")
    if not processor_versions or any(
        not key or not value for key, value in processor_versions.items()
    ):
        raise ValueError("model fingerprints require non-empty processor versions")
    payload = {
        "document_digests": sorted(set(document_digests)),
        "processor_versions": dict(sorted(processor_versions.items())),
    }
    return sha256_hex(canonical_json_bytes(payload, privacy_check=False))


_FORBIDDEN_KEYS = {
    "approvedclaim",
    "approvedclaims",
    "claimid",
    "claimids",
    "claimledger",
    "credential",
    "credentials",
    "documentid",
    "documentrevisionid",
    "evidencewindow",
    "extractedtext",
    "internalid",
    "objectkey",
    "observation",
    "observations",
    "observationid",
    "prompt",
    "prompts",
    "modeloutput",
    "modelpayload",
    "modelresponse",
    "rawmodeloutput",
    "rawresponse",
    "responsebody",
    "rawvalueprivate",
    "signedurl",
    "sourcebody",
    "sourcehtml",
    "sourcepayload",
    "sourcetext",
    "stacktrace",
    "textprivate",
    "privatepayload",
    "privatetext",
    "transcriptbody",
    "transcriptexcerpt",
    "transcriptpayload",
    "transcripttext",
}
_FORBIDDEN_TEXT = (
    "%PDF-",
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "s3://",
)
_SECRET_PATTERN = re.compile(r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})")


def assert_public_payload(payload: Any) -> None:
    """Recursively reject private field names, UUIDs, credentials, and copied payload markers."""

    def walk(value: Any, location: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
                forbidden_identifier = normalized.endswith("id") and normalized not in {
                    "activereleaseid",
                    "previousreleaseid",
                    "releaseid",
                }
                forbidden_family = (
                    "private" in normalized
                    or "observation" in normalized
                    or "claim" in normalized
                    or ("prompt" in normalized and normalized != "promptversion")
                )
                if (
                    normalized in _FORBIDDEN_KEYS
                    or normalized.endswith("uuid")
                    or forbidden_identifier
                    or forbidden_family
                ):
                    raise ValueError(f"forbidden public field at {location}.{key}")
                walk(item, f"{location}.{key}")
        elif isinstance(value, (tuple, list)):
            for index, item in enumerate(value):
                walk(item, f"{location}[{index}]")
        elif isinstance(value, str):
            if _UUID_PATTERN.search(value):
                raise ValueError(f"internal UUID is forbidden at {location}")
            if _SECRET_PATTERN.search(value) or any(marker in value for marker in _FORBIDDEN_TEXT):
                raise ValueError(f"private or credential-like text is forbidden at {location}")

    walk(payload, "$")


def validate_projection_payload(payload: Mapping[str, Any]) -> ScotusPublicProjection:
    """Read either reviewed public schema explicitly; callers decide when to migrate V1."""
    projection = ScotusPublicProjection.model_validate(payload, strict=False)
    assert_public_payload(projection.model_dump(mode="python"))
    return projection


def validate_search_payload(payload: Mapping[str, Any]) -> StaticSearchIndex:
    """Read the legacy/current search contract without silently changing its version."""
    search = StaticSearchIndex.model_validate(payload, strict=False)
    assert_public_payload(search.model_dump(mode="python"))
    return search
