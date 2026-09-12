from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from ragchew.config import ScotusConfig
from ragchew.scotus.editorial_backfill import (
    build_contemporaneous_canary_baseline,
    derive_canary_evidence_digest,
    derive_canary_protocol_digest,
    validate_candidate_against_baseline,
    validate_contemporaneous_canary_baseline,
)
from ragchew.scotus.static_contracts import (
    CanaryAggregate,
    CanaryFailureCount,
    CanaryReviewerDecision,
    ContentIntegrity,
    EditorialBackfillState,
    EditorialRolloutStage,
    LogicalDocumentState,
    ModelRetryStatus,
    PendingModelRetry,
    PendingReason,
    PendingWork,
    ProcessorFingerprint,
    PublicationState,
    RetryFailureCode,
    canonical_json_bytes,
)
from ragchew.scotus.static_state import GeneratedContent

NOW = datetime(2026, 9, 4, tzinfo=UTC)
QWEN_DIGEST = "22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643"
COGITO_DIGEST = "8f2632d0faa422ff60435bc0095575d032a8b4a0f728df034d90ea654ffb60bb"


def _config(role: str) -> ScotusConfig:
    values = ScotusConfig.from_yaml(Path("config/scotus.yaml")).model_dump()
    if role == "control":
        values["generation"].update(
            runtime_role="control", model="qwen3.8:27b", model_digest=QWEN_DIGEST
        )
    return ScotusConfig.model_validate(values)


def _arm(config: ScotusConfig, *, drift: bool = False) -> tuple[GeneratedContent, CanaryAggregate]:
    keys = config.editorial_backfill.replacement_canary_case_keys
    processor_digest = "a" * 64 if config.generation.runtime_role == "control" else "b" * 64
    backfill = EditorialBackfillState(
        processor_sha256=processor_digest,
        rollout_stage=EditorialRolloutStage.CANARY_10,
        newest_first_rank_boundary=10,
        selected_case_keys=keys,
        attempted_count=10,
        failed_count=10,
    )
    report = CanaryAggregate(
        processor_sha256=processor_digest,
        rollout_stage=EditorialRolloutStage.CANARY_10,
        case_keys=keys,
        attempted_count=10,
        accepted_count=0,
        failed_count=10,
        failure_code_counts=(
            CanaryFailureCount(code=RetryFailureCode.INVALID_SCHEMA, count=10),
        ),
        runtime_seconds=20,
        model_call_count=10,
        reviewer_decision=CanaryReviewerDecision.REJECTED,
    )
    documents = tuple(
        LogicalDocumentState(
            logical_key=f"{key}:docket:case",
            case_key=key,
            document_kind="docket",
            official_url=f"https://www.supremecourt.gov/docket/docketfiles/html/public/{index}.html",
            revision_number=1,
            integrity=ContentIntegrity(
                sha256=("f" * 64 if drift and index == 1 else f"{index:x}" * 64),
                byte_count=100 + index,
            ),
            checked_at=NOW,
        )
        for index, key in enumerate(keys, start=1)
    )
    pending = tuple(
        PendingWork(
            case_key=key,
            reason=PendingReason.VALIDATION_FAILED,
            attempts=1,
            first_seen_at=NOW,
            last_attempted_at=NOW,
            retry=PendingModelRetry(
                scope_sha256=f"{index:x}" * 64,
                stage="brief",
                completed_cycles=1,
                last_cycle_at=NOW,
                next_eligible_at=NOW + timedelta(hours=20),
                status=ModelRetryStatus.PENDING,
                failure_code=RetryFailureCode.INVALID_SCHEMA,
            ),
        )
        for index, key in enumerate(keys, start=1)
    )
    processor = ProcessorFingerprint(
        parser_version="p:1",
        extractor_version="e:1",
        policy_version="policy:1",
        model=(
            f"ollama:{config.generation.model}@sha256:"
            f"{config.generation.model_digest}@http://127.0.0.1:11434/v1"
        ),
        prompt_version="prompt:1",
        config_sha256="c" * 64,
        composite_sha256=processor_digest,
    )
    publication = PublicationState(
        updated_at=NOW,
        documents=tuple(sorted(documents, key=lambda item: item.logical_key)),
        pending_work=tuple(sorted(pending, key=lambda item: item.case_key)),
        processor=processor,
        editorial_backfill=backfill,
        canary_report=report,
    )
    return replace(GeneratedContent.empty(), publication=publication), report


def test_contemporaneous_baseline_is_sanitized_and_binds_complete_control() -> None:
    parent = GeneratedContent.empty()
    config = _config("control")
    control, report = _arm(config)

    baseline = build_contemporaneous_canary_baseline(
        parent=parent, control=control, report=report, config=config
    )

    assert baseline.case_keys == config.editorial_backfill.replacement_canary_case_keys
    assert baseline.document_count == 10
    assert baseline.model == "qwen3.8:27b"
    assert baseline.processor_sha256 == report.processor_sha256
    assert baseline.evidence_sha256 == derive_canary_evidence_digest(control, baseline.case_keys)
    serialized = canonical_json_bytes(baseline)
    assert b"source_text" not in serialized and b"prose" not in serialized
    validate_contemporaneous_canary_baseline(
        baseline, parent=parent, control=control, report=report, config=config
    )


def test_candidate_requires_same_parent_protocol_evidence_and_exact_cogito() -> None:
    parent = GeneratedContent.empty()
    control_config = _config("control")
    control, control_report = _arm(control_config)
    baseline = build_contemporaneous_canary_baseline(
        parent=parent, control=control, report=control_report, config=control_config
    )
    candidate_config = _config("production")
    candidate, candidate_report = _arm(candidate_config)

    assert derive_canary_protocol_digest(control_config) == derive_canary_protocol_digest(
        candidate_config
    )
    bound = validate_candidate_against_baseline(
        baseline,
        parent=parent,
        candidate=candidate,
        report=candidate_report,
        config=candidate_config,
    )
    assert bound.comparison_baseline_sha256 is not None
    assert bound.control_report_sha256 == baseline.control_report_sha256

    drifted, drifted_report = _arm(candidate_config, drift=True)
    with pytest.raises(ValueError, match="evidence does not match"):
        validate_candidate_against_baseline(
            baseline,
            parent=parent,
            candidate=drifted,
            report=drifted_report,
            config=candidate_config,
        )


def test_control_requires_retry_outcome_and_documents_for_every_manifest_case() -> None:
    config = _config("control")
    control, report = _arm(config)
    publication = control.publication.model_copy(
        update={"pending_work": control.publication.pending_work[:-1]}
    )
    incomplete = replace(control, publication=publication)
    with pytest.raises(ValueError, match="model-failure retry state"):
        build_contemporaneous_canary_baseline(
            parent=GeneratedContent.empty(), control=incomplete, report=report, config=config
        )

    publication = control.publication.model_copy(
        update={"documents": control.publication.documents[:-1]}
    )
    incomplete = replace(control, publication=publication)
    with pytest.raises(ValueError, match="complete documents"):
        build_contemporaneous_canary_baseline(
            parent=GeneratedContent.empty(), control=incomplete, report=report, config=config
        )


def test_approved_report_requires_both_comparison_bindings() -> None:
    config = _config("production")
    _, report = _arm(config)
    payload = {
        **report.model_dump(mode="python"),
        "candidate_sha256": "c" * 64,
        "accepted_count": 10,
        "failed_count": 0,
        "failure_code_counts": (),
        "improved_count": 8,
        "privacy_validation_passed": True,
        "release_validation_passed": True,
        "reviewer_decision": CanaryReviewerDecision.APPROVED,
    }
    with pytest.raises(ValidationError, match="advancement thresholds"):
        CanaryAggregate.model_validate(payload)

    payload.update(
        comparison_baseline_sha256="d" * 64,
        control_report_sha256="e" * 64,
    )
    approved = CanaryAggregate.model_validate(payload)
    assert approved.reviewer_decision is CanaryReviewerDecision.APPROVED
