"""Typed non-secret policy and environment configuration."""

from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self
from urllib.parse import unquote, urlsplit

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from ragchew.proceedings.contracts import (
    GovernmentAuthority,
    Jurisdiction,
    SourceAccessMethod,
)


class ReceiverDefaults(BaseModel):
    receiver_id: str = "dc-pi-01"
    system_id: str = "dcfd"
    rf_center_hz: int
    rf_sample_rate_hz: int
    rf_min_hz: int
    rf_max_hz: int
    control_channels_hz: list[int]
    talkgroups: dict[int, str]
    spool_max_bytes: int
    acknowledged_grace_seconds: int
    heartbeat_seconds: int


class RetryDefaults(BaseModel):
    maximum_attempts: int
    base_delay_seconds: float
    maximum_delay_seconds: float
    job_lease_seconds: int
    abandoned_upload_seconds: int


class RetentionDefaults(BaseModel):
    audio_hours: int
    transcript_days: int
    failed_upload_hours: int


class PublicationDefaults(BaseModel):
    grace_minutes: int
    lookback_hours: int
    minimum_confidence: float = Field(ge=0, le=1)
    on_scene_weight: float
    dispatch_weight: float
    allowlist: list[str]
    mandatory_suppression: list[str]
    residential_location_precision: str


class MvpConfig(BaseModel):
    version: str
    receiver: ReceiverDefaults
    retry: RetryDefaults
    retention: RetentionDefaults
    publication: PublicationDefaults

    @classmethod
    def from_yaml(cls, path: str | Path) -> MvpConfig:
        with Path(path).open(encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle))


class ProceedingSourceDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[a-z0-9_-]{2,64}$")
    authority: GovernmentAuthority
    jurisdiction: Jurisdiction
    official_index_url: str
    adapter: str
    discovery_method: SourceAccessMethod = SourceAccessMethod.NONE
    media_method: SourceAccessMethod = SourceAccessMethod.NONE
    access_basis: str | None = None
    access_reviewed_at: datetime | None = None
    access_reviewed_by: str | None = None
    access_review_expires_at: datetime | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    poll_interval_seconds: int = Field(default=900, ge=30, le=86_400)
    expected_schedule: str
    enabled: bool = False

    @model_validator(mode="after")
    def require_review_before_enable(self) -> Self:
        if self.enabled and (
            self.discovery_method is SourceAccessMethod.NONE
            or not self.access_basis
            or not self.access_reviewed_at
            or not self.access_reviewed_by
            or not self.allowed_hosts
        ):
            raise ValueError("enabled proceeding source requires completed access review")
        return self


class ProceedingCollectionDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_duration_seconds: int = Field(ge=30, le=3_600)
    chunk_overlap_seconds: int = Field(ge=0, le=30)
    archive_wait_hours: int = Field(ge=1, le=720)
    stale_source_minutes: int = Field(ge=1)
    maximum_asset_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def require_bounded_overlap(self) -> Self:
        if self.chunk_overlap_seconds >= self.chunk_duration_seconds:
            raise ValueError("chunk overlap must be shorter than chunk duration")
        return self


class ProceedingRetentionDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_hours: int = Field(ge=1)
    transcript_days: int = Field(ge=1)
    document_extraction_days: int = Field(ge=1)
    failed_transfer_hours: int = Field(ge=1)


class ProceedingPublicationDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grace_minutes: int = Field(ge=0, le=180)
    lookback_hours: int = Field(ge=1)
    minimum_confidence: float = Field(ge=0, le=1)
    national_story_quota: int = Field(ge=0)
    district_story_quota: int = Field(ge=0)
    allowlist: list[str]
    mandatory_suppression: list[str]
    private_witnesses_named: bool = False
    public_transcript_quotes: bool = False


class ProceedingLaunchDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_private_preview_days: int = Field(ge=1)
    minimum_representative_proceedings_per_source: int = Field(ge=1)
    maximum_sensitive_leaks: int = Field(ge=0)
    maximum_status_upgrades: int = Field(ge=0)
    minimum_grounded_factual_element_rate: float = Field(ge=0, le=1)


class ProceedingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    sources: dict[str, ProceedingSourceDefaults]
    collection: ProceedingCollectionDefaults
    retention: ProceedingRetentionDefaults
    publication: ProceedingPublicationDefaults
    launch: ProceedingLaunchDefaults

    @model_validator(mode="after")
    def require_matching_source_keys(self) -> Self:
        mismatched = [key for key, source in self.sources.items() if key != source.source_id]
        if mismatched:
            raise ValueError(f"source mapping keys must match source_id: {mismatched}")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> ProceedingsConfig:
        with Path(path).open(encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle))


class ScotusDiscoveryDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terms: list[str] = Field(min_length=1)
    active_term: str = Field(pattern=r"^\d{4}$")
    recent_correction_lookback_days: int = Field(ge=1, le=3_650)
    recent_opinion_lookback_days: int = Field(ge=1, le=3_650)
    historical_rechecks_per_run: int = Field(ge=0, le=100)
    backfill_case_limit: int = Field(ge=0)
    backfill_lookback_days: int = Field(ge=1, le=3_650)
    backfill_priority: int = Field(ge=0)
    new_transcript_priority: int = Field(ge=0)
    poll_interval_seconds: int = Field(ge=60)
    crawl_delay_seconds: float = Field(ge=1)
    request_timeout_seconds: int = Field(default=60, ge=10, le=120)

    @model_validator(mode="after")
    def validate_terms_and_priority(self) -> Self:
        if any(len(term) != 4 or not term.isdigit() for term in self.terms):
            raise ValueError("SCOTUS terms must be four-digit years")
        if len(set(self.terms)) != len(self.terms):
            raise ValueError("SCOTUS terms must be unique")
        if self.active_term not in self.terms:
            raise ValueError("active SCOTUS term must be included in discovery terms")
        if self.new_transcript_priority >= self.backfill_priority:
            raise ValueError("new transcript work must have higher priority than backfill")
        return self


class ScotusDocumentDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maximum_pdf_bytes: int = Field(gt=0)
    maximum_pages: int = Field(gt=0)
    allowed_content_types: list[str] = Field(min_length=1)
    spool_memory_bytes: int = Field(gt=0)
    request_timeout_seconds: int = Field(default=60, ge=10, le=120)
    download_audio: bool = False
    stt_enabled: bool = False

    @model_validator(mode="after")
    def prohibit_audio_and_stt(self) -> Self:
        if self.download_audio or self.stt_enabled:
            raise ValueError("SCOTUS Legal Briefs is transcript-first; audio and STT are disabled")
        return self


class ScotusParserDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    minimum_line_coverage: float = Field(ge=0, le=1)
    maximum_ambiguous_pages: int = Field(ge=0)


class ScotusRetentionDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    copied_document_hours: int = Field(ge=1)
    extracted_text_days: int = Field(ge=1)
    failed_download_hours: int = Field(ge=1)


class ScotusGenerationDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["ollama"]
    model: Literal["qwen3.8:27b"]
    prompt_version: str
    brief_generation_enabled: bool = False
    maximum_brief_api_calls_per_run: int = Field(default=1, ge=1, le=100)
    maximum_brief_validation_attempts_per_case: int = Field(default=1, ge=1, le=5)
    stop_after_brief_validation_failure: bool = True
    audience: Literal["general_public"] = "general_public"
    maximum_context_characters: int = Field(gt=0)
    minimum_observation_confidence: float = Field(ge=0, le=1)
    maximum_sentence_words: int = Field(ge=10, le=40)
    maximum_paragraph_words: int = Field(ge=30, le=200)
    public_quotes: bool = False
    prohibit_vote_predictions: bool = True
    prohibit_personalized_legal_advice: bool = True


def _normalized_url_path(value: str) -> str:
    if any(character in value for character in "?#\\") or any(
        ord(character) < 32 for character in value
    ):
        raise ValueError("URL paths cannot contain query, fragment, backslash, or control data")
    decoded = unquote(value)
    if decoded != value or not re.fullmatch(r"/?[A-Za-z0-9._~/-]*", value):
        raise ValueError("URL paths must use plain URL-safe path characters")
    if not value.startswith("/"):
        value = f"/{value}"
    parts = [part for part in value.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("URL paths cannot contain dot segments")
    return "/" + "/".join(parts) + ("/" if parts else "")


def _safe_relative_path(value: str) -> str:
    raw_parts = value.split("/")
    path = PurePosixPath(value)
    if (
        "\\" in value
        or any(ord(character) < 32 for character in value)
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise ValueError("static output/state paths must be safe repository-relative paths")
    if path.parts[0] in {".git", ".github", "src", "tests", "config"}:
        raise ValueError("static output/state paths cannot overwrite source or repository metadata")
    return path.as_posix()


class ScotusRepositoryDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner: str = Field(pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
    name: str = Field(pattern=r"^[A-Za-z0-9._-]+$")


class ScotusStaticDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output_path: str
    generated_state_path: str
    generated_state_schema: Literal["1.0"] = "1.0"
    canonical_origin: str
    project_base_path: str
    section_path: str
    static_only: bool = True
    runtime_api_url: None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_paths(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for key in ("project_base_path", "section_path"):
            if key in normalized and isinstance(normalized[key], str):
                normalized[key] = _normalized_url_path(normalized[key])
        return normalized

    @model_validator(mode="after")
    def validate_static_boundary(self) -> Self:
        parsed = urlsplit(self.canonical_origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("canonical origin must be an HTTPS origin without path or credentials")
        output_path = _safe_relative_path(self.output_path)
        state_path = _safe_relative_path(self.generated_state_path)
        if (
            output_path == state_path
            or output_path.startswith(f"{state_path}/")
            or state_path.startswith(f"{output_path}/")
        ):
            raise ValueError("static output and generated state paths must not overlap")
        if not self.static_only or self.runtime_api_url is not None:
            raise ValueError("GitHub Pages publication cannot depend on a runtime API")
        object.__setattr__(self, "output_path", output_path)
        object.__setattr__(self, "generated_state_path", state_path)
        return self


class ScotusScheduleDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nightly_cron_utc: str

    @field_validator("nightly_cron_utc")
    @classmethod
    def require_daily_utc_cron(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[0-5]?\d) (?:[01]?\d|2[0-3]) \* \* \*", value):
            raise ValueError("nightly schedule must be a fixed daily UTC cron")
        return value


class ScotusBootstrapDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_terms_per_run: int = Field(ge=1, le=50)
    maximum_cases_per_run: int = Field(ge=1, le=1_000)
    maximum_requests_per_run: int = Field(ge=1)
    maximum_download_bytes_per_run: int = Field(ge=1)


class ScotusRunnerLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_cases_per_run: int = Field(ge=1, le=100)
    maximum_documents_per_run: int = Field(ge=1, le=1_000)
    maximum_http_requests_per_run: int = Field(ge=1)
    maximum_download_bytes_per_run: int = Field(ge=1)
    maximum_private_disk_bytes: int = Field(ge=1)
    maximum_runtime_seconds: int = Field(ge=60, le=86_400)


class ScotusModelBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_extraction_calls_per_run: int = Field(ge=0, le=1_000)
    maximum_brief_calls_per_run: int = Field(ge=0, le=100)
    maximum_total_calls_per_run: int = Field(ge=0, le=1_100)
    maximum_input_characters_per_run: int = Field(ge=0)
    maximum_input_tokens_per_run: int = Field(ge=0)
    maximum_output_tokens_per_run: int = Field(ge=0)
    maximum_input_tokens_per_call: int = Field(ge=0)
    maximum_output_tokens_per_call: int = Field(ge=0)
    input_cost_usd_per_million_tokens: Decimal = Field(ge=0)
    output_cost_usd_per_million_tokens: Decimal = Field(ge=0)
    maximum_estimated_cost_usd_per_run: Decimal = Field(ge=0)
    request_timeout_seconds: int = Field(ge=1, le=600)
    maximum_transport_attempts: int = Field(ge=1, le=5)

    @model_validator(mode="after")
    def enforce_theoretical_maximum(self) -> Self:
        requested_calls = self.maximum_extraction_calls_per_run + self.maximum_brief_calls_per_run
        if requested_calls > self.maximum_total_calls_per_run:
            raise ValueError("extraction and brief call maxima exceed the total model-call budget")
        if (
            self.maximum_input_tokens_per_run
            > self.maximum_input_tokens_per_call * self.maximum_total_calls_per_run
            or self.maximum_output_tokens_per_run
            > self.maximum_output_tokens_per_call * self.maximum_total_calls_per_run
        ):
            raise ValueError("aggregate model-token budget exceeds per-call theoretical maximum")
        maximum_token_cost = (
            Decimal(self.maximum_input_tokens_per_run)
            * self.input_cost_usd_per_million_tokens
            + Decimal(self.maximum_output_tokens_per_run)
            * self.output_cost_usd_per_million_tokens
        ) / Decimal(1_000_000)
        if maximum_token_cost > self.maximum_estimated_cost_usd_per_run:
            raise ValueError("theoretical model-token spend exceeds the run cost budget")
        return self


class ScotusLicensingDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code_and_documentation: Literal["Apache-2.0"]
    generated_briefs: Literal["CC-BY-4.0"]
    court_materials_excluded: Literal[True] = True


class ScotusPublicationApprovals(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_review_approved: bool = False
    licenses_approved: bool = False
    origin_approved: bool = False
    model_runtime_approved: bool = False
    launch_approved: bool = False

    def all_live_gates_approved(self) -> bool:
        return all(
            (
                self.source_review_approved,
                self.licenses_approved,
                self.origin_approved,
                self.model_runtime_approved,
                self.launch_approved,
            )
        )


class ScotusPublicationDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    dry_run: bool = True
    site_name: str
    base_path: str
    case_page_requires_official_transcript: bool = True
    event_driven: bool = True


class ScotusLaunchDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_private_preview_days: int = Field(ge=1)
    minimum_reviewed_cases: int = Field(ge=1)
    minimum_page_line_accuracy: float = Field(ge=0, le=1)
    minimum_grounded_factual_element_rate: float = Field(ge=0, le=1)
    maximum_status_upgrades: int = Field(ge=0)
    maximum_sensitive_leaks: int = Field(ge=0)


class ScotusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    product: str
    source_id: str
    enabled: bool = False
    discovery: ScotusDiscoveryDefaults
    documents: ScotusDocumentDefaults
    parser: ScotusParserDefaults
    retention: ScotusRetentionDefaults
    generation: ScotusGenerationDefaults
    repository: ScotusRepositoryDefaults
    static: ScotusStaticDefaults
    schedule: ScotusScheduleDefaults
    bootstrap: ScotusBootstrapDefaults
    runner_limits: ScotusRunnerLimits
    model_budget: ScotusModelBudget
    licensing: ScotusLicensingDefaults
    approvals: ScotusPublicationApprovals
    publication: ScotusPublicationDefaults
    launch: ScotusLaunchDefaults

    @model_validator(mode="after")
    def validate_product(self) -> Self:
        if self.product != "scotus_legal_briefs" or self.source_id != "supreme_court":
            raise ValueError("SCOTUS configuration must select the Supreme Court product/source")
        if self.generation.maximum_brief_api_calls_per_run > (
            self.model_budget.maximum_brief_calls_per_run
        ):
            raise ValueError("legacy brief-call limit exceeds the shared model budget")
        if self.runner_limits.maximum_cases_per_run > self.bootstrap.maximum_cases_per_run:
            raise ValueError("nightly case limit cannot exceed the manual bootstrap case limit")
        if (
            self.bootstrap.maximum_cases_per_run
            > self.model_budget.maximum_brief_calls_per_run
        ):
            raise ValueError("bootstrap case limit exceeds guaranteed brief-call capacity")
        if (
            self.bootstrap.maximum_cases_per_run
            > self.model_budget.maximum_extraction_calls_per_run
        ):
            raise ValueError("bootstrap case limit exceeds minimum extraction-call capacity")
        if not self.publication.enabled and not self.publication.dry_run:
            raise ValueError("publication must be enabled before dry-run mode can be disabled")
        if (
            self.publication.enabled
            and not self.publication.dry_run
            and (
                not self.enabled
                or not self.generation.brief_generation_enabled
                or not self.approvals.all_live_gates_approved()
            )
        ):
            raise ValueError(
                "live static publication requires source, license, origin, model-runtime, "
                "and launch approvals"
            )
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> ScotusConfig:
        with Path(path).open(encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle))


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAGCHEW_", env_file=".env", extra="ignore", case_sensitive=False
    )

    database_dsn: str = "postgresql://ragchew:ragchew@localhost/ragchew"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "ragchew-private"
    s3_region: str = "us-east-1"
    s3_access_key: str = "minioadmin"
    s3_secret_key: SecretStr = SecretStr("minioadmin")
    receiver_tokens: str = "{}"
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: SecretStr = Field(
        default=SecretStr("unused"),
        validation_alias=AliasChoices("RAGCHEW_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    llm_model: str = "gpt-5"
    stt_model: str = "small.en"
    config_path: str = "config/mvp.yaml"
    proceedings_config_path: str = "config/proceedings.yaml"
    scotus_config_path: str = "config/scotus.yaml"
    product_mode: str = "scotus_legal_briefs"
    source_user_agent: str = (
        "ragchew-scotus-briefs/1.0 "
        "(+https://github.com/reklis/scotus-briefs; contact=https://github.com/reklis)"
    )

    @field_validator("ollama_base_url")
    @classmethod
    def require_loopback_ollama_v1(cls, value: str) -> str:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if (
            parsed.scheme != "http"
            or hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is None
            or parsed.path.rstrip("/") != "/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Ollama base URL must be an HTTP loopback endpoint with port and /v1 path"
            )
        host = f"[{hostname}]" if hostname == "::1" else hostname
        return f"http://{host}:{parsed.port}/v1"

    @field_validator("source_user_agent")
    @classmethod
    def require_descriptive_source_user_agent(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized or "contact" not in normalized:
            raise ValueError("source user agent must include contact information")
        if "example.invalid" in normalized:
            raise ValueError("source user agent cannot use the example.invalid placeholder")
        return value.strip()

    def parsed_receiver_tokens(self) -> dict[str, str]:
        parsed: Any = json.loads(self.receiver_tokens)
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
        ):
            raise ValueError("receiver_tokens must be a JSON string-to-string object")
        return parsed
