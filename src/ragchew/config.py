"""Typed non-secret policy and environment configuration."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, model_validator
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
    backfill_case_limit: int = Field(ge=0)
    backfill_lookback_days: int = Field(ge=1, le=3_650)
    backfill_priority: int = Field(ge=0)
    new_transcript_priority: int = Field(ge=0)
    poll_interval_seconds: int = Field(ge=60)
    crawl_delay_seconds: float = Field(ge=1)

    @model_validator(mode="after")
    def validate_terms_and_priority(self) -> Self:
        if any(len(term) != 4 or not term.isdigit() for term in self.terms):
            raise ValueError("SCOTUS terms must be four-digit years")
        if len(set(self.terms)) != len(self.terms):
            raise ValueError("SCOTUS terms must be unique")
        if self.new_transcript_priority >= self.backfill_priority:
            raise ValueError("new transcript work must have higher priority than backfill")
        return self


class ScotusDocumentDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maximum_pdf_bytes: int = Field(gt=0)
    maximum_pages: int = Field(gt=0)
    allowed_content_types: list[str] = Field(min_length=1)
    spool_memory_bytes: int = Field(gt=0)
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

    provider: Literal["openai"]
    model: str = Field(pattern=r"^(?:gpt-|o[134]-).+")
    prompt_version: str
    brief_generation_enabled: bool = False
    maximum_brief_api_calls_per_run: int = Field(default=1, ge=1, le=100)
    stop_after_brief_validation_failure: bool = True
    audience: Literal["general_public"] = "general_public"
    maximum_context_characters: int = Field(gt=0)
    minimum_observation_confidence: float = Field(ge=0, le=1)
    maximum_sentence_words: int = Field(ge=10, le=40)
    maximum_paragraph_words: int = Field(ge=30, le=200)
    public_quotes: bool = False
    prohibit_vote_predictions: bool = True
    prohibit_personalized_legal_advice: bool = True


class ScotusPublicationDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
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
    publication: ScotusPublicationDefaults
    launch: ScotusLaunchDefaults

    @model_validator(mode="after")
    def validate_product(self) -> Self:
        if self.product != "scotus_legal_briefs" or self.source_id != "supreme_court":
            raise ValueError("SCOTUS configuration must select the Supreme Court product/source")
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
    source_user_agent: str = "SCOTUS-Legal-Briefs/0.1 contact=operator@example.invalid"

    def parsed_receiver_tokens(self) -> dict[str, str]:
        parsed: Any = json.loads(self.receiver_tokens)
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
        ):
            raise ValueError("receiver_tokens must be a JSON string-to-string object")
        return parsed
