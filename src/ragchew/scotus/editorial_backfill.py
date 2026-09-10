"""Deterministic, sanitized editorial-backfill and canary policy helpers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ragchew.scotus.public_contracts import PublicCaseBrief, public_case_key
from ragchew.scotus.static_contracts import (
    CanaryAggregate,
    CanaryFailureCount,
    CanaryReviewerDecision,
    CanaryWarningCount,
    EditorialBackfillState,
    EditorialRolloutStage,
    EditorialWarningCode,
    PendingReason,
    PendingWork,
    RetryFailureCode,
)


class CanaryCaseKind(StrEnum):
    DISPOSITION_ONLY = "disposition_only"
    ARGUED = "argued"
    DECIDED_AFTER_ARGUMENT = "decided_after_argument"


@dataclass(frozen=True)
class EditorialCandidate:
    """Only public identity, activity, and coarse lifecycle data used for selection."""

    case_key: str
    authoritative_activity_date: datetime
    kind: CanaryCaseKind

    @classmethod
    def from_public_case(cls, case: PublicCaseBrief) -> EditorialCandidate:
        if case.latest_court_document_date is None:
            raise ValueError("editorial candidate requires authoritative Court activity")
        has_argument = bool(case.arguments)
        has_disposition = bool(case.dispositions or case.official_disposition_urls)
        if has_argument and has_disposition:
            kind = CanaryCaseKind.DECIDED_AFTER_ARGUMENT
        elif has_argument:
            kind = CanaryCaseKind.ARGUED
        elif has_disposition:
            kind = CanaryCaseKind.DISPOSITION_ONLY
        else:
            raise ValueError("editorial candidate has no argument or disposition")
        return cls(
            case_key=public_case_key(case.term, case.primary_docket),
            authoritative_activity_date=case.latest_court_document_date,
            kind=kind,
        )


def _ordered(candidates: Sequence[EditorialCandidate]) -> tuple[EditorialCandidate, ...]:
    by_key = {candidate.case_key: candidate for candidate in candidates}
    if len(by_key) != len(candidates):
        raise ValueError("editorial candidates must have unique case keys")
    return tuple(
        sorted(
            candidates,
            key=lambda item: (-item.authoritative_activity_date.timestamp(), item.case_key),
        )
    )


def deterministic_canary_manifest(
    candidates: Sequence[EditorialCandidate],
) -> tuple[EditorialCandidate, ...]:
    """Choose ten deterministically, preserving recency while covering each available kind.

    The newest ten are the baseline. If a required lifecycle kind exists only below that
    cut, its newest case replaces the oldest baseline case whose kind remains represented.
    No source text, generated prose, or model diagnostics participate in the manifest.
    """
    ordered = _ordered(candidates)
    if len(ordered) < EditorialRolloutStage.CANARY_10.limit:
        raise ValueError("a canary manifest requires at least ten eligible cases")
    selected = list(ordered[: EditorialRolloutStage.CANARY_10.limit])
    available = {item.kind for item in ordered}
    for kind in CanaryCaseKind:
        if kind not in available or any(item.kind is kind for item in selected):
            continue
        replacement = next(item for item in ordered if item.kind is kind)
        counts = Counter(item.kind for item in selected)
        replace_at = next(
            (
                index
                for index in range(len(selected) - 1, -1, -1)
                if counts[selected[index].kind] > 1
            ),
            None,
        )
        if replace_at is None:
            raise ValueError("canary lifecycle coverage cannot fit the manifest")
        selected[replace_at] = replacement
    rank = {item.case_key: index for index, item in enumerate(ordered)}
    return tuple(sorted(selected, key=lambda item: rank[item.case_key]))


def start_or_resume_backfill(
    *,
    candidates: Sequence[EditorialCandidate],
    processor_sha256: str,
    rollout_stage: EditorialRolloutStage,
    previous: EditorialBackfillState | None,
    advance_completed: bool = False,
) -> EditorialBackfillState:
    """Create a processor/stage-scoped slice or return its exact unfinished manifest."""
    ordered = _ordered(candidates)
    by_key = {item.case_key: item for item in ordered}
    same_processor = previous is not None and previous.processor_sha256 == processor_sha256
    if (
        same_processor
        and previous is not None
        and previous.rollout_stage is rollout_stage
        and not advance_completed
    ):
        if not set(previous.selected_case_keys) <= set(by_key):
            raise ValueError("active backfill selection is no longer discoverable")
        return previous

    rank_offset = 0
    if same_processor and previous is not None:
        previous_keys = set(previous.selected_case_keys)
        ordered = tuple(item for item in ordered if item.case_key not in previous_keys)
        rank_offset = previous.newest_first_rank_boundary

    if rollout_stage is EditorialRolloutStage.CANARY_10:
        selected = deterministic_canary_manifest(ordered)
    else:
        selected = ordered[: rollout_stage.limit]
    ranks = {item.case_key: rank_offset + index + 1 for index, item in enumerate(ordered)}
    boundary = max(
        (ranks[item.case_key] for item in selected),
        default=rank_offset,
    )
    return EditorialBackfillState(
        processor_sha256=processor_sha256,
        rollout_stage=rollout_stage,
        newest_first_rank_boundary=boundary,
        selected_case_keys=tuple(item.case_key for item in selected),
    )


def record_canary_review(
    report: CanaryAggregate,
    *,
    improved_count: int,
    factual_error_count: int = 0,
    status_error_count: int = 0,
    actor_error_count: int = 0,
    chronology_error_count: int = 0,
    prediction_error_count: int = 0,
    degraded_legacy_count: int = 0,
    privacy_validation_passed: bool,
    release_validation_passed: bool,
    reviewer_decision: CanaryReviewerDecision,
) -> CanaryAggregate:
    """Apply sanitized side-by-side review counts; contract validation is the gate."""
    return CanaryAggregate.model_validate(
        {
            **report.model_dump(mode="python"),
            "improved_count": improved_count,
            "factual_error_count": factual_error_count,
            "status_error_count": status_error_count,
            "actor_error_count": actor_error_count,
            "chronology_error_count": chronology_error_count,
            "prediction_error_count": prediction_error_count,
            "degraded_legacy_count": degraded_legacy_count,
            "privacy_validation_passed": privacy_validation_passed,
            "release_validation_passed": release_validation_passed,
            "reviewer_decision": reviewer_decision,
        }
    )


def qualification_failures(report: CanaryAggregate) -> tuple[str, ...]:
    """Return fixed, public-safe reasons that prevent stage advancement."""
    failures: list[str] = []
    if report.rollout_stage is EditorialRolloutStage.CANARY_10 and len(report.case_keys) != 10:
        failures.append("manifest_not_ten")
    required_improvements = (len(report.case_keys) * 4 + 4) // 5
    if report.improved_count < required_improvements:
        failures.append("insufficient_improved_rewrites")
    if report.attempted_count != len(report.case_keys):
        failures.append("manifest_incomplete")
    if report.candidate_sha256 is None:
        failures.append("candidate_unbound")
    if report.accepted_error_count:
        failures.append("accepted_editorial_error")
    if report.degraded_legacy_count:
        failures.append("degraded_legacy_page")
    if not report.privacy_validation_passed:
        failures.append("privacy_validation_failed")
    if not report.release_validation_passed:
        failures.append("release_validation_failed")
    return tuple(failures)


def require_stage_advancement(
    backfill: EditorialBackfillState,
    report: CanaryAggregate | None,
    requested: EditorialRolloutStage,
) -> None:
    """Fail unless a stage is unchanged or advances exactly one reviewed step."""
    if requested is backfill.rollout_stage:
        return
    transitions = {
        EditorialRolloutStage.CANARY_10: EditorialRolloutStage.BATCH_25,
        EditorialRolloutStage.BATCH_25: EditorialRolloutStage.BATCH_100,
    }
    if transitions.get(backfill.rollout_stage) is not requested:
        raise ValueError("editorial rollout stages must advance one measured step")
    if report is None or report.reviewer_decision is not CanaryReviewerDecision.APPROVED:
        raise ValueError("editorial rollout advancement requires reviewer approval")
    if report.processor_sha256 != backfill.processor_sha256:
        raise ValueError("editorial rollout report has a different processor")
    failures = qualification_failures(report)
    if failures:
        raise ValueError("editorial rollout did not qualify for advancement")


def aggregate_canary_report(
    *,
    backfill: EditorialBackfillState,
    pending_by_case: Mapping[str, PendingWork],
    accepted_case_keys: frozenset[str],
    runtime_seconds: int,
    model_call_count: int,
    candidate_sha256: str | None,
    warnings_by_case: Mapping[str, Sequence[EditorialWarningCode]] | None = None,
    previous: CanaryAggregate | None = None,
) -> CanaryAggregate:
    """Build/update an automatic report using only sanitized state and counters."""
    failed_keys: list[str] = []
    counts: Counter[RetryFailureCode] = Counter()
    reason_codes = {
        PendingReason.SOURCE_UNAVAILABLE: RetryFailureCode.SOURCE_UNAVAILABLE,
        PendingReason.SOURCE_INVALID: RetryFailureCode.SOURCE_INVALID,
        PendingReason.PROCESSING_FAILED: RetryFailureCode.PROCESSING_FAILED,
        PendingReason.VALIDATION_FAILED: RetryFailureCode.VALIDATION_FAILED,
        PendingReason.DATE_BACKFILL_UNMATCHED: RetryFailureCode.DATE_BACKFILL_UNMATCHED,
    }
    for key in backfill.selected_case_keys:
        if key in accepted_case_keys:
            continue
        pending = pending_by_case.get(key)
        if pending is None or pending.reason is PendingReason.BUDGET_EXHAUSTED:
            continue
        failed_keys.append(key)
        code = (
            pending.retry.failure_code
            if pending.retry is not None
            else reason_codes[pending.reason]
        )
        counts[code] += 1
    accepted_count = len(set(backfill.selected_case_keys) & accepted_case_keys)
    attempted_count = accepted_count + len(failed_keys)
    all_hard_failed = (
        attempted_count == len(backfill.selected_case_keys)
        and len(failed_keys) == len(backfill.selected_case_keys)
        and accepted_count == 0
    )
    warning_counts: Counter[EditorialWarningCode] = Counter()
    same_measurement = bool(
        previous is not None
        and previous.processor_sha256 == backfill.processor_sha256
        and previous.rollout_stage is backfill.rollout_stage
        and previous.case_keys == backfill.selected_case_keys
        and previous.accepted_count <= accepted_count
    )
    if same_measurement and previous is not None:
        warning_counts.update({item.code: item.count for item in previous.warning_code_counts})
    for key, warning_codes in (warnings_by_case or {}).items():
        if key not in accepted_case_keys:
            continue
        warning_counts.update(set(warning_codes))
    preserve_review = bool(
        previous is not None
        and previous.processor_sha256 == backfill.processor_sha256
        and previous.rollout_stage is backfill.rollout_stage
        and previous.candidate_sha256 == candidate_sha256
        and previous.case_keys == backfill.selected_case_keys
        and previous.attempted_count == attempted_count
        and previous.accepted_count == accepted_count
        and previous.failed_count == len(failed_keys)
    )
    return CanaryAggregate(
        processor_sha256=backfill.processor_sha256,
        rollout_stage=backfill.rollout_stage,
        candidate_sha256=None if all_hard_failed else candidate_sha256,
        case_keys=backfill.selected_case_keys,
        attempted_count=attempted_count,
        accepted_count=accepted_count,
        failed_count=len(failed_keys),
        failure_code_counts=tuple(
            CanaryFailureCount(code=code, count=counts[code])
            for code in sorted(counts, key=lambda item: item.value)
        ),
        warning_code_counts=tuple(
            CanaryWarningCount(code=code, count=min(accepted_count, warning_counts[code]))
            for code in sorted(warning_counts, key=lambda item: item.value)
        ),
        runtime_seconds=min(
            86_400,
            runtime_seconds + (previous.runtime_seconds if preserve_review and previous else 0),
        ),
        model_call_count=min(
            10_000,
            model_call_count + (previous.model_call_count if preserve_review and previous else 0),
        ),
        improved_count=previous.improved_count if preserve_review and previous else 0,
        factual_error_count=previous.factual_error_count if preserve_review and previous else 0,
        status_error_count=previous.status_error_count if preserve_review and previous else 0,
        actor_error_count=previous.actor_error_count if preserve_review and previous else 0,
        chronology_error_count=(
            previous.chronology_error_count if preserve_review and previous else 0
        ),
        prediction_error_count=(
            previous.prediction_error_count if preserve_review and previous else 0
        ),
        degraded_legacy_count=previous.degraded_legacy_count if preserve_review and previous else 0,
        privacy_validation_passed=(
            previous.privacy_validation_passed if preserve_review and previous else False
        ),
        release_validation_passed=(
            previous.release_validation_passed if preserve_review and previous else False
        ),
        reviewer_decision=(
            CanaryReviewerDecision.REJECTED
            if all_hard_failed
            else (
                previous.reviewer_decision
                if preserve_review and previous
                else CanaryReviewerDecision.PENDING
            )
        ),
    )
