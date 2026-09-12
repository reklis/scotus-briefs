"""Deterministic, sanitized editorial-backfill and canary policy helpers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ragchew.config import (
    SCOTUS_CONTROL_MODEL,
    SCOTUS_CONTROL_MODEL_DIGEST,
    SCOTUS_PRODUCTION_MODEL,
    SCOTUS_PRODUCTION_MODEL_DIGEST,
    ScotusConfig,
)
from ragchew.scotus.public_contracts import PublicCaseBrief, public_case_key
from ragchew.scotus.static_contracts import (
    CanaryAggregate,
    CanaryFailureCount,
    CanaryReviewerDecision,
    CanaryWarningCount,
    ContemporaneousCanaryBaseline,
    EditorialBackfillState,
    EditorialRolloutStage,
    EditorialWarningCode,
    PendingReason,
    PendingWork,
    RetryFailureCode,
    canonical_json_bytes,
    contract_digest,
    sha256_hex,
)
from ragchew.scotus.static_state import GeneratedContent, generated_public_content_digest


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
    replacement_canary_case_keys: Sequence[str] = (),
) -> EditorialBackfillState:
    """Create a processor/stage-scoped slice or return its exact unfinished manifest."""
    ordered = _ordered(candidates)
    by_key = {item.case_key: item for item in ordered}
    same_processor = previous is not None and previous.processor_sha256 == processor_sha256
    expected_replacement_manifest = tuple(replacement_canary_case_keys)
    if previous is None and expected_replacement_manifest:
        if (
            rollout_stage is not EditorialRolloutStage.CANARY_10
            or len(expected_replacement_manifest) != EditorialRolloutStage.CANARY_10.limit
        ):
            raise ValueError("a replacement processor requires the reviewed prior canary manifest")
        if not set(expected_replacement_manifest) <= set(by_key):
            raise ValueError("replacement canary manifest is no longer discoverable")
        return EditorialBackfillState(
            processor_sha256=processor_sha256,
            rollout_stage=rollout_stage,
            newest_first_rank_boundary=len(ordered),
            selected_case_keys=expected_replacement_manifest,
        )
    if (
        same_processor
        and previous is not None
        and previous.rollout_stage is rollout_stage
        and not advance_completed
    ):
        if not set(previous.selected_case_keys) <= set(by_key):
            raise ValueError("active backfill selection is no longer discoverable")
        return previous

    if previous is not None and not same_processor:
        expected_manifest = expected_replacement_manifest
        if (
            rollout_stage is not EditorialRolloutStage.CANARY_10
            or previous.rollout_stage is not EditorialRolloutStage.CANARY_10
            or len(previous.selected_case_keys) != EditorialRolloutStage.CANARY_10.limit
            or previous.selected_case_keys != expected_manifest
        ):
            raise ValueError("a replacement processor requires the reviewed prior canary manifest")
        if not set(expected_manifest) <= set(by_key):
            raise ValueError("replacement canary manifest is no longer discoverable")
        return EditorialBackfillState(
            processor_sha256=processor_sha256,
            rollout_stage=rollout_stage,
            newest_first_rank_boundary=previous.newest_first_rank_boundary,
            selected_case_keys=previous.selected_case_keys,
        )

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


def derive_canary_protocol_digest(config: ScotusConfig) -> str:
    """Hash the complete paired-arm protocol, excluding only model role/identity."""
    payload = config.model_dump(mode="json")
    generation = payload["generation"]
    for field in ("runtime_role", "model", "model_digest"):
        generation.pop(field)
    return sha256_hex(canonical_json_bytes(payload, privacy_check=False))


def _canary_evidence_payload(
    content: GeneratedContent,
    case_keys: Sequence[str],
) -> tuple[dict[str, object], int]:
    ordered_keys = tuple(case_keys)
    if len(ordered_keys) != 10 or len(set(ordered_keys)) != 10:
        raise ValueError("paired canary evidence requires exactly ten ordered case keys")
    documents_by_case = {
        key: tuple(
            sorted(
                (item for item in content.publication.documents if item.case_key == key),
                key=lambda item: item.logical_key,
            )
        )
        for key in ordered_keys
    }
    missing = tuple(key for key, documents in documents_by_case.items() if not documents)
    if missing:
        raise ValueError("paired canary evidence requires complete documents for every case")
    dispositions_by_case = {
        key: tuple(
            sorted(
                (item for item in content.publication.dispositions if item.case_key == key),
                key=lambda item: item.logical_key,
            )
        )
        for key in ordered_keys
    }
    cases: list[dict[str, object]] = []
    document_count = 0
    for key in ordered_keys:
        documents = documents_by_case[key]
        document_count += len(documents)
        cases.append(
            {
                "case_key": key,
                "documents": tuple(
                    {
                        "identity": item.logical_key,
                        "official_url": item.official_url,
                        "kind": item.document_kind,
                        "sha256": item.integrity.sha256,
                        "byte_count": item.integrity.byte_count,
                    }
                    for item in documents
                ),
                "dispositions": tuple(
                    {
                        "identity": item.logical_key,
                        "metadata_sha256": item.metadata_sha256,
                    }
                    for item in dispositions_by_case[key]
                ),
            }
        )
    return {"cases": tuple(cases)}, document_count


def derive_canary_evidence_digest(
    content: GeneratedContent,
    case_keys: Sequence[str],
) -> str:
    """Hash only normalized public document integrity and disposition metadata."""
    payload, _ = _canary_evidence_payload(content, case_keys)
    return sha256_hex(canonical_json_bytes(payload, privacy_check=False))


def _require_exact_arm_identity(
    *,
    content: GeneratedContent,
    report: CanaryAggregate,
    config: ScotusConfig,
    runtime_role: str,
) -> None:
    expected_model, expected_digest = {
        "control": (SCOTUS_CONTROL_MODEL, SCOTUS_CONTROL_MODEL_DIGEST),
        "production": (SCOTUS_PRODUCTION_MODEL, SCOTUS_PRODUCTION_MODEL_DIGEST),
    }[runtime_role]
    if (
        config.generation.runtime_role != runtime_role
        or config.generation.model != expected_model
        or config.generation.model_digest != expected_digest
    ):
        raise ValueError(f"paired canary requires exact {runtime_role} model identity")
    processor = content.publication.processor
    expected_identity_prefix = (
        f"ollama:{expected_model}@sha256:{expected_digest}@"
    )
    if (
        processor is None
        or processor.composite_sha256 != report.processor_sha256
        or not processor.model.startswith(expected_identity_prefix)
    ):
        raise ValueError(f"paired canary report requires exact {runtime_role} processor identity")


def _require_complete_arm(
    *,
    content: GeneratedContent,
    report: CanaryAggregate,
    config: ScotusConfig,
    runtime_role: str,
) -> tuple[str, int]:
    manifest = config.editorial_backfill.replacement_canary_case_keys
    if (
        len(manifest) != 10
        or report.rollout_stage is not EditorialRolloutStage.CANARY_10
        or report.case_keys != manifest
        or report.attempted_count != 10
        or report.attempted_count != report.accepted_count + report.failed_count
    ):
        raise ValueError("paired canary must account for the exact ordered ten-case manifest")
    _require_exact_arm_identity(
        content=content,
        report=report,
        config=config,
        runtime_role=runtime_role,
    )
    backfill = content.publication.editorial_backfill
    embedded_report = content.publication.canary_report
    if embedded_report != report:
        raise ValueError("paired canary report is not the arm's retained report")
    if backfill is None or (
        backfill.selected_case_keys != manifest
        or backfill.processor_sha256 != report.processor_sha256
        or backfill.rollout_stage is not report.rollout_stage
        or backfill.attempted_count != report.attempted_count
        or backfill.accepted_count != report.accepted_count
        or backfill.failed_count != report.failed_count
    ):
        raise ValueError("paired canary backfill does not match its report")

    if report.accepted_count:
        if content.projection is None or report.candidate_sha256 != sha256_hex(
            canonical_json_bytes(content.projection)
        ):
            raise ValueError("paired canary report is not bound to its candidate projection")
    elif report.candidate_sha256 is not None:
        raise ValueError("an all-failed paired canary cannot bind a candidate projection")

    accepted = {
        item.case_key
        for item in content.publication.cases
        if item.case_key in manifest and item.processor_sha256 == report.processor_sha256
    }
    if len(accepted) != report.accepted_count:
        raise ValueError("paired canary accepted outcomes do not match its report")
    pending_by_case = {item.case_key: item for item in content.publication.pending_work}
    non_model_codes = {
        RetryFailureCode.SOURCE_UNAVAILABLE,
        RetryFailureCode.SOURCE_INVALID,
        RetryFailureCode.PROCESSING_FAILED,
        RetryFailureCode.VALIDATION_FAILED,
        RetryFailureCode.DATE_BACKFILL_UNMATCHED,
    }
    retry_counts: Counter[RetryFailureCode] = Counter()
    for key in manifest:
        if key in accepted:
            continue
        pending = pending_by_case.get(key)
        if (
            pending is None
            or pending.retry is None
            or pending.retry.failure_code in non_model_codes
        ):
            raise ValueError("paired canary nonaccepted outcomes require model-failure retry state")
        retry_counts[pending.retry.failure_code] += 1
    report_counts = Counter({item.code: item.count for item in report.failure_code_counts})
    if retry_counts != report_counts:
        raise ValueError("paired canary retry outcomes do not match its report")
    payload, document_count = _canary_evidence_payload(content, manifest)
    evidence_sha256 = sha256_hex(canonical_json_bytes(payload, privacy_check=False))
    return evidence_sha256, document_count


def build_contemporaneous_canary_baseline(
    *,
    parent: GeneratedContent,
    control: GeneratedContent,
    report: CanaryAggregate,
    config: ScotusConfig,
) -> ContemporaneousCanaryBaseline:
    """Build a source-text-free binding for a complete exact-Qwen control run."""
    if (
        config.editorial_backfill.control_model != SCOTUS_CONTROL_MODEL
        or config.editorial_backfill.control_model_digest != SCOTUS_CONTROL_MODEL_DIGEST
    ):
        raise ValueError("editorial control identity is not the reviewed Qwen content")
    evidence_sha256, document_count = _require_complete_arm(
        content=control,
        report=report,
        config=config,
        runtime_role="control",
    )
    return ContemporaneousCanaryBaseline(
        parent_public_content_sha256=generated_public_content_digest(parent),
        case_keys=report.case_keys,
        runtime_role="control",
        model=SCOTUS_CONTROL_MODEL,
        model_digest=SCOTUS_CONTROL_MODEL_DIGEST,
        protocol_sha256=derive_canary_protocol_digest(config),
        evidence_sha256=evidence_sha256,
        document_count=document_count,
        processor_sha256=report.processor_sha256,
        control_report_sha256=contract_digest(report),
    )


def validate_contemporaneous_canary_baseline(
    baseline: ContemporaneousCanaryBaseline,
    *,
    parent: GeneratedContent,
    control: GeneratedContent,
    report: CanaryAggregate,
    config: ScotusConfig,
) -> None:
    """Rebuild and compare every control baseline binding."""
    rebuilt = build_contemporaneous_canary_baseline(
        parent=parent,
        control=control,
        report=report,
        config=config,
    )
    if rebuilt != baseline:
        raise ValueError("contemporaneous control baseline binding does not match")


def validate_candidate_against_baseline(
    baseline: ContemporaneousCanaryBaseline,
    *,
    parent: GeneratedContent,
    candidate: GeneratedContent,
    report: CanaryAggregate,
    config: ScotusConfig,
) -> CanaryAggregate:
    """Validate the independent Cogito arm and return its comparison-bound report."""
    if generated_public_content_digest(parent) != baseline.parent_public_content_sha256:
        raise ValueError("paired canary candidate has a different generated-content parent")
    if report.case_keys != baseline.case_keys:
        raise ValueError("paired canary candidate has a different manifest")
    if derive_canary_protocol_digest(config) != baseline.protocol_sha256:
        raise ValueError("paired canary candidate has a different protocol")
    evidence_sha256, document_count = _require_complete_arm(
        content=candidate,
        report=report,
        config=config,
        runtime_role="production",
    )
    if (
        evidence_sha256 != baseline.evidence_sha256
        or document_count != baseline.document_count
    ):
        raise ValueError("paired canary candidate evidence does not match control")
    baseline_sha256 = contract_digest(baseline)
    if report.comparison_baseline_sha256 not in (None, baseline_sha256):
        raise ValueError("candidate report has a different comparison baseline binding")
    if report.control_report_sha256 not in (None, baseline.control_report_sha256):
        raise ValueError("candidate report has a different control report binding")
    return CanaryAggregate.model_validate(
        {
            **report.model_dump(mode="python"),
            "comparison_baseline_sha256": baseline_sha256,
            "control_report_sha256": baseline.control_report_sha256,
        }
    )


# Explicit paired-canary names kept as small aliases for orchestration callers.
derive_paired_canary_protocol_digest = derive_canary_protocol_digest
derive_paired_canary_evidence_digest = derive_canary_evidence_digest
build_control_canary_baseline = build_contemporaneous_canary_baseline
validate_control_canary_baseline = validate_contemporaneous_canary_baseline
validate_canary_candidate = validate_candidate_against_baseline


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
        comparison_baseline_sha256=(
            previous.comparison_baseline_sha256 if preserve_review and previous else None
        ),
        control_report_sha256=(
            previous.control_report_sha256 if preserve_review and previous else None
        ),
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
            runtime_seconds + (previous.runtime_seconds if same_measurement and previous else 0),
        ),
        model_call_count=min(
            10_000,
            model_call_count + (previous.model_call_count if same_measurement and previous else 0),
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
