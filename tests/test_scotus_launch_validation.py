import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from ragchew.config import ScotusConfig
from ragchew.scotus.launch_validation import (
    ValidationManifest,
    fixture_gates_pass,
    launch_must_remain_disabled,
)

MANIFEST = Path("tests/fixtures/scotus_validation_manifest.json")
CONFIG = ScotusConfig.from_yaml("config/scotus.yaml")


def test_bounded_representative_fixture_backfill_passes_all_gates() -> None:
    manifest = ValidationManifest.from_json(MANIFEST)
    assert len(manifest.cases) == 20
    assert len(manifest.cases) <= CONFIG.discovery.backfill_case_limit
    assert fixture_gates_pass(manifest, CONFIG)


def test_preview_ledger_has_seven_consecutive_passing_cycles() -> None:
    manifest = ValidationManifest.from_json(MANIFEST)
    assert len(manifest.preview_cycles) == CONFIG.launch.minimum_private_preview_days
    assert all(cycle.status == "pass" for cycle in manifest.preview_cycles)
    assert manifest.measurements.publication_candidates_reviewed == len(manifest.cases)


def test_unvalidated_live_discovery_requires_disabled_launch_configuration() -> None:
    manifest = ValidationManifest.from_json(MANIFEST)
    assert not manifest.live_discovery.court_calendar_permitted
    assert manifest.live_discovery.status == "unvalidated"
    disabled = CONFIG.model_copy(
        update={
            "enabled": False,
            "publication": CONFIG.publication.model_copy(update={"enabled": False}),
        }
    )
    assert launch_must_remain_disabled(manifest, disabled)


def test_calendar_permitted_without_new_transcript_fails_manifest() -> None:
    raw = deepcopy(json.loads(MANIFEST.read_text()))
    raw["live_discovery"]["court_calendar_permitted"] = True
    with pytest.raises(ValidationError, match="permitted live transcript"):
        ValidationManifest.model_validate(raw)


def test_any_failed_review_closes_fixture_gate() -> None:
    raw = deepcopy(json.loads(MANIFEST.read_text()))
    raw["cases"][0]["review"] = "fail"
    assert not fixture_gates_pass(ValidationManifest.model_validate(raw), CONFIG)
