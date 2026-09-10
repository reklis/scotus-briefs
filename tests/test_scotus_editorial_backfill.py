from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ragchew.scotus.editorial_backfill import (
    CanaryCaseKind,
    EditorialCandidate,
    aggregate_canary_report,
    deterministic_canary_manifest,
    qualification_failures,
    record_canary_review,
    require_stage_advancement,
    start_or_resume_backfill,
)
from ragchew.scotus.static_contracts import (
    CanaryAggregate,
    CanaryReviewerDecision,
    EditorialRolloutStage,
    EditorialWarningCode,
    PendingReason,
    PendingWork,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
PROCESSOR = "a" * 64


def candidates(count: int = 120) -> tuple[EditorialCandidate, ...]:
    kinds = (
        CanaryCaseKind.ARGUED,
        CanaryCaseKind.DECIDED_AFTER_ARGUMENT,
        CanaryCaseKind.DISPOSITION_ONLY,
    )
    return tuple(
        EditorialCandidate(
            case_key=f"2025-25-{index:03d}",
            authoritative_activity_date=NOW - timedelta(days=index),
            kind=kinds[index % len(kinds)],
        )
        for index in range(count)
    )


def test_canary_manifest_is_deterministic_newest_first_and_lifecycle_complete() -> None:
    manifest = deterministic_canary_manifest(tuple(reversed(candidates())))

    assert len(manifest) == 10
    assert {item.kind for item in manifest} == set(CanaryCaseKind)
    assert [item.case_key for item in manifest] == [f"2025-25-{index:03d}" for index in range(10)]


def test_canary_manifest_adds_missing_available_kind_deterministically() -> None:
    values = list(candidates(12))
    values[:10] = [
        EditorialCandidate(item.case_key, item.authoritative_activity_date, CanaryCaseKind.ARGUED)
        for item in values[:10]
    ]
    values[10] = EditorialCandidate(
        values[10].case_key,
        values[10].authoritative_activity_date,
        CanaryCaseKind.DISPOSITION_ONLY,
    )
    values[11] = EditorialCandidate(
        values[11].case_key,
        values[11].authoritative_activity_date,
        CanaryCaseKind.DECIDED_AFTER_ARGUMENT,
    )

    manifest = deterministic_canary_manifest(values)

    assert tuple(item.case_key for item in manifest[-2:]) == ("2025-25-010", "2025-25-011")
    assert {item.kind for item in manifest} == set(CanaryCaseKind)


def test_canary_fails_closed_without_exactly_ten_eligible_cases() -> None:
    with pytest.raises(ValueError, match="at least ten"):
        start_or_resume_backfill(
            candidates=candidates(9),
            processor_sha256=PROCESSOR,
            rollout_stage=EditorialRolloutStage.CANARY_10,
            previous=None,
        )


def test_backfill_slice_is_bounded_resumable_and_resets_for_processor() -> None:
    initial = start_or_resume_backfill(
        candidates=candidates(),
        processor_sha256=PROCESSOR,
        rollout_stage=EditorialRolloutStage.BATCH_25,
        previous=None,
    )
    resumed = start_or_resume_backfill(
        candidates=tuple(reversed(candidates())),
        processor_sha256=PROCESSOR,
        rollout_stage=EditorialRolloutStage.BATCH_25,
        previous=initial.model_copy(update={"attempted_count": 1, "accepted_count": 1}),
    )
    advanced = start_or_resume_backfill(
        candidates=candidates(),
        processor_sha256=PROCESSOR,
        rollout_stage=EditorialRolloutStage.BATCH_100,
        previous=resumed,
    )
    reset = start_or_resume_backfill(
        candidates=candidates(),
        processor_sha256="b" * 64,
        rollout_stage=EditorialRolloutStage.CANARY_10,
        previous=resumed,
    )

    assert len(initial.selected_case_keys) == 25
    assert initial.selected_case_keys == tuple(f"2025-25-{index:03d}" for index in range(25))
    assert resumed.attempted_count == 1
    assert initial.selected_case_keys[0] not in advanced.selected_case_keys
    assert advanced.selected_case_keys[0] == "2025-25-025"
    assert advanced.newest_first_rank_boundary == 120
    assert reset.processor_sha256 == "b" * 64
    assert len(reset.selected_case_keys) == 10


def test_automatic_report_excludes_runtime_deferred_from_attempts() -> None:
    backfill = start_or_resume_backfill(
        candidates=candidates(10),
        processor_sha256=PROCESSOR,
        rollout_stage=EditorialRolloutStage.CANARY_10,
        previous=None,
    )
    report = aggregate_canary_report(
        backfill=backfill,
        pending_by_case={
            backfill.selected_case_keys[1]: PendingWork(
                case_key=backfill.selected_case_keys[1],
                reason=PendingReason.VALIDATION_FAILED,
                attempts=1,
                first_seen_at=NOW,
                last_attempted_at=NOW,
            ),
            backfill.selected_case_keys[2]: PendingWork(
                case_key=backfill.selected_case_keys[2],
                reason=PendingReason.BUDGET_EXHAUSTED,
                attempts=0,
                first_seen_at=NOW,
            ),
        },
        accepted_case_keys=frozenset({backfill.selected_case_keys[0]}),
        runtime_seconds=60,
        model_call_count=2,
        candidate_sha256="c" * 64,
    )

    assert report.attempted_count == 2
    assert report.accepted_count == report.failed_count == 1
    assert [item.code.value for item in report.failure_code_counts] == ["validation_failed"]
    assert report.reviewer_decision is CanaryReviewerDecision.PENDING


def test_warning_bearing_candidate_and_all_hard_failed_report_are_retained() -> None:
    backfill = start_or_resume_backfill(
        candidates=candidates(10),
        processor_sha256=PROCESSOR,
        rollout_stage=EditorialRolloutStage.CANARY_10,
        previous=None,
    )
    accepted = aggregate_canary_report(
        backfill=backfill,
        pending_by_case={},
        accepted_case_keys=frozenset({backfill.selected_case_keys[0]}),
        runtime_seconds=10,
        model_call_count=1,
        candidate_sha256="c" * 64,
        warnings_by_case={
            backfill.selected_case_keys[0]: (
                EditorialWarningCode.PREFERRED_SENTENCE_LENGTH,
                EditorialWarningCode.PREFERRED_SENTENCE_LENGTH,
            )
        },
    )
    assert [(item.code.value, item.count) for item in accepted.warning_code_counts] == [
        ("preferred_sentence_length", 1)
    ]
    assert accepted.candidate_sha256 == "c" * 64

    pending = {
        key: PendingWork(
            case_key=key,
            reason=PendingReason.VALIDATION_FAILED,
            attempts=1,
            first_seen_at=NOW,
            last_attempted_at=NOW,
        )
        for key in backfill.selected_case_keys
    }
    failed = aggregate_canary_report(
        backfill=backfill.model_copy(update={"attempted_count": 10, "failed_count": 10}),
        pending_by_case=pending,
        accepted_case_keys=frozenset(),
        runtime_seconds=20,
        model_call_count=10,
        candidate_sha256="d" * 64,
    )
    assert failed.candidate_sha256 is None
    assert failed.reviewer_decision is CanaryReviewerDecision.REJECTED
    assert failed.case_keys == backfill.selected_case_keys


def test_advancement_requires_all_measured_canary_gates() -> None:
    backfill = start_or_resume_backfill(
        candidates=candidates(10),
        processor_sha256=PROCESSOR,
        rollout_stage=EditorialRolloutStage.CANARY_10,
        previous=None,
    ).model_copy(update={"attempted_count": 10, "accepted_count": 10})
    pending = CanaryAggregate(
        processor_sha256=PROCESSOR,
        rollout_stage=EditorialRolloutStage.CANARY_10,
        candidate_sha256="c" * 64,
        case_keys=backfill.selected_case_keys,
        attempted_count=10,
        accepted_count=10,
        failed_count=0,
        runtime_seconds=100,
        model_call_count=10,
        improved_count=8,
        privacy_validation_passed=True,
        release_validation_passed=True,
    )
    assert qualification_failures(pending) == ()
    with pytest.raises(ValueError, match="reviewer approval"):
        require_stage_advancement(backfill, pending, EditorialRolloutStage.BATCH_25)

    approved = record_canary_review(
        pending,
        improved_count=8,
        privacy_validation_passed=True,
        release_validation_passed=True,
        reviewer_decision=CanaryReviewerDecision.APPROVED,
    )
    require_stage_advancement(backfill, approved, EditorialRolloutStage.BATCH_25)
    with pytest.raises(ValueError, match="one measured step"):
        require_stage_advancement(backfill, approved, EditorialRolloutStage.BATCH_100)

    with pytest.raises(ValidationError, match="advancement thresholds"):
        CanaryAggregate.model_validate(
            {
                **pending.model_dump(),
                "improved_count": 7,
                "reviewer_decision": CanaryReviewerDecision.APPROVED,
            }
        )
