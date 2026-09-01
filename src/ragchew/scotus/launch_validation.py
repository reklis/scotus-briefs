"""Deterministic evaluation of the checked-in SCOTUS private-preview ledger."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ragchew.config import ScotusConfig


class ValidationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    docket: str
    profile: str
    features: tuple[str, ...]
    review: Literal["pass", "fail"]
    note: str = Field(min_length=10)


class PreviewCycle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    status: Literal["pass", "fail"]


class Measurements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewed_cases: int
    publication_candidates_reviewed: int
    page_line_accuracy: float
    speaker_identity_precision: float
    citation_precision: float
    grounded_factual_element_rate: float
    issue_grouping_precision: float
    issue_grouping_recall: float
    legal_status_upgrades: int
    sensitive_public_leaks: int
    private_boundary_leaks: int
    failed_cycle_projection_changes: int


class LiveDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_transcript_observed: bool
    court_calendar_permitted: bool
    status: Literal["validated", "unvalidated"]
    transcript_publication_latency_hours: float | None


class LaunchState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_enabled: bool
    public_site_enabled: bool
    reason: str


class ValidationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    term: str
    source: str
    cases: tuple[ValidationCase, ...]
    preview_cycles: tuple[PreviewCycle, ...]
    measurements: Measurements
    live_discovery: LiveDiscovery
    launch: LaunchState

    @model_validator(mode="after")
    def validate_consistency(self) -> ValidationManifest:
        dockets = [item.docket for item in self.cases]
        if len(dockets) != len(set(dockets)):
            raise ValueError("validation dockets must be unique")
        dates = sorted(item.date for item in self.preview_cycles)
        if dates and dates != [dates[0] + timedelta(days=index) for index in range(len(dates))]:
            raise ValueError("preview cycles must be consecutive")
        if (
            self.live_discovery.court_calendar_permitted
            and not self.live_discovery.new_transcript_observed
        ):
            raise ValueError("a permitted live transcript must be observed")
        if (
            not self.live_discovery.new_transcript_observed
            and self.live_discovery.status != "unvalidated"
        ):
            raise ValueError("missing live transcript must remain explicitly unvalidated")
        return self

    @classmethod
    def from_json(cls, path: str | Path) -> ValidationManifest:
        return cls.model_validate(json.loads(Path(path).read_text()))


def fixture_gates_pass(manifest: ValidationManifest, config: ScotusConfig) -> bool:
    """Return whether deterministic fixture/private-boundary gates pass."""
    required_features = {
        "consolidated",
        "reargument",
        "technical_terminology",
        "multiple_advocates",
        "sensitive_facts",
        "later_order",
        "later_opinion",
    }
    observed = {feature for item in manifest.cases for feature in item.features}
    measurements = manifest.measurements
    return all(
        (
            len(manifest.cases) >= config.launch.minimum_reviewed_cases,
            len(manifest.cases) <= config.discovery.backfill_case_limit,
            len(manifest.preview_cycles) >= config.launch.minimum_private_preview_days,
            all(item.review == "pass" for item in manifest.cases),
            all(item.status == "pass" for item in manifest.preview_cycles),
            required_features <= observed,
            measurements.reviewed_cases == len(manifest.cases),
            measurements.publication_candidates_reviewed == len(manifest.cases),
            measurements.page_line_accuracy >= config.launch.minimum_page_line_accuracy,
            measurements.grounded_factual_element_rate
            >= config.launch.minimum_grounded_factual_element_rate,
            measurements.legal_status_upgrades <= config.launch.maximum_status_upgrades,
            measurements.sensitive_public_leaks <= config.launch.maximum_sensitive_leaks,
            measurements.private_boundary_leaks == 0,
            measurements.failed_cycle_projection_changes == 0,
        )
    )


def launch_must_remain_disabled(
    manifest: ValidationManifest, config: ScotusConfig
) -> bool:
    """Fail closed while the recorded live-discovery gate is unvalidated."""
    return (
        fixture_gates_pass(manifest, config)
        and manifest.live_discovery.status == "unvalidated"
        and not manifest.launch.source_enabled
        and not manifest.launch.public_site_enabled
        and not config.enabled
        and not config.publication.enabled
    )
