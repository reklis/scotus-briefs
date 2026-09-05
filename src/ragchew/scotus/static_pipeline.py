"""Bounded, ephemeral orchestration primitives for static SCOTUS publication."""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from ragchew.config import ScotusConfig
from ragchew.proceedings.registry import SourceAuthorizationError
from ragchew.proceedings.sources.http import SourceFetchError
from ragchew.scotus.briefs import BriefPolicyError, BriefValidationError
from ragchew.scotus.discovery import DiscoveryMode
from ragchew.scotus.documents import DocumentCollectionError
from ragchew.scotus.extraction import LegalExtractionError
from ragchew.scotus.public_contracts import PublicCaseBrief, public_case_key
from ragchew.scotus.static_contracts import (
    CostLedger,
    CursorState,
    DispositionDiscoveryState,
    LogicalDocumentState,
    LogicalSourceState,
    ModelAttemptOutcome,
    ModelAttemptReceipt,
    PendingReason,
    PendingWork,
    ProcessorFingerprint,
    model_input_fingerprint,
)
from ragchew.scotus.static_state import GeneratedContent, StaticStateStore

LOG = logging.getLogger("ragchew.scotus.static_pipeline")


class BudgetExceeded(RuntimeError):
    """A configured non-safety run limit was reached."""


class RepeatedModelInput(RuntimeError):
    """A model input already has a durable attempted/blocked receipt."""


class PublicationGateDenied(RuntimeError):
    """Paid processing was requested while either publication gate was closed."""


class ProductionBatchUnavailable(RuntimeError):
    """No reviewed adapter is configured for Court/model processing."""


class FailureCategory(StrEnum):
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_INVALID = "source_invalid"
    BUDGET = "budget"
    VALIDATION = "validation"
    PROCESSING = "processing"
    INTERNAL = "internal"


def failure_category(error: BaseException) -> FailureCategory:
    """Map an exception to a coarse public category without serializing its message."""
    if isinstance(error, BudgetExceeded):
        return FailureCategory.BUDGET
    if isinstance(error, (TimeoutError, ConnectionError, SourceFetchError)):
        return FailureCategory.SOURCE_UNAVAILABLE
    if isinstance(error, DocumentCollectionError):
        return FailureCategory.SOURCE_INVALID
    if isinstance(
        error,
        (
            ValueError,
            SourceAuthorizationError,
            LegalExtractionError,
            BriefPolicyError,
            BriefValidationError,
            PublicationGateDenied,
            RepeatedModelInput,
        ),
    ):
        return FailureCategory.VALIDATION
    return FailureCategory.PROCESSING


def log_stage(
    logger: logging.Logger,
    *,
    case_key: str,
    stage: str,
    status: str,
    elapsed_seconds: float | None = None,
    category: FailureCategory | None = None,
    counts: Mapping[str, int] | None = None,
    digest: str | None = None,
) -> None:
    """Log allowlisted operational metadata only; never interpolate private errors."""
    extra: dict[str, object] = {
        "case_key": case_key,
        "stage": stage,
        "status": status,
    }
    if elapsed_seconds is not None:
        extra["elapsed_seconds"] = round(elapsed_seconds, 3)
    if category is not None:
        extra["failure_category"] = category.value
    if counts:
        extra["counts"] = {key: int(value) for key, value in sorted(counts.items())}
    if digest is not None:
        if not all(character in "0123456789abcdef" for character in digest) or len(digest) != 64:
            raise ValueError("logged digest must be SHA-256")
        extra["digest"] = digest
    logger.info("SCOTUS pipeline stage", extra=extra)


@dataclass
class RunWorkspace:
    """Permission-restricted run-scoped storage rooted below runner temp."""

    root: Path
    downloads: Path
    extracted_text: Path
    services: Path
    candidate: Path
    _cleaned: bool = False

    @classmethod
    def create(
        cls,
        runner_temp: str | Path | None = None,
        *,
        run_id: str | None = None,
    ) -> RunWorkspace:
        base = Path(runner_temp or os.environ.get("RUNNER_TEMP") or tempfile.gettempdir())
        base.mkdir(parents=True, exist_ok=True)
        if base.is_symlink() or not base.is_dir():
            raise ValueError("runner temporary root must be a real directory")
        safe_run_id = "".join(
            character for character in (run_id or "run") if character.isalnum() or character in "-_"
        )[:80]
        if not safe_run_id:
            raise ValueError("run ID has no safe characters")
        root = Path(tempfile.mkdtemp(prefix=f"ragchew-{safe_run_id}-", dir=base))
        os.chmod(root, 0o700)
        children = tuple(
            root / name for name in ("downloads", "extracted", "services", "candidate")
        )
        for child in children:
            child.mkdir(mode=0o700)
        downloads, extracted_text, services, candidate = children
        workspace = cls(
            root=root,
            downloads=downloads,
            extracted_text=extracted_text,
            services=services,
            candidate=candidate,
        )
        workspace.require_private_permissions()
        return workspace

    def private_path(
        self, area: Literal["downloads", "extracted_text", "services", "candidate"], name: str
    ) -> Path:
        if not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("workspace path must be safe and relative")
        parent = {
            "downloads": self.downloads,
            "extracted_text": self.extracted_text,
            "services": self.services,
            "candidate": self.candidate,
        }[area]
        path = parent / name
        if not path.resolve(strict=False).is_relative_to(self.root.resolve()):
            raise ValueError("workspace path escapes run root")
        return path

    def require_private_permissions(self) -> None:
        for path in (self.root, self.downloads, self.extracted_text, self.services, self.candidate):
            if path.stat().st_mode & 0o077:
                raise PermissionError("run workspace is accessible outside its owner")

    def disk_bytes(self) -> int:
        if not self.root.exists():
            return 0
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def cleanup(self) -> None:
        if not self._cleaned:
            shutil.rmtree(self.root, ignore_errors=True)
            self._cleaned = True

    def __enter__(self) -> RunWorkspace:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.cleanup()


class WorkspaceSignalCleanup:
    """Install temporary SIGINT/SIGTERM handlers that clean before propagating."""

    def __init__(self, workspace: RunWorkspace) -> None:
        self.workspace = workspace
        self.previous: dict[
            signal.Signals,
            signal.Handlers | Callable[[int, FrameType | None], Any] | int | None,
        ] = {}

    def __enter__(self) -> WorkspaceSignalCleanup:
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.previous[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle)
        return self

    def _handle(self, signum: int, _frame: FrameType | None) -> None:
        self.workspace.cleanup()
        raise SystemExit(128 + signum)

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)


@dataclass(frozen=True)
class ModelRequestPermit:
    budget: UnifiedRunBudget
    stage: Literal["extraction", "brief"]
    fingerprint: str
    input_tokens: int
    output_tokens: int

    def reserve_attempt(self) -> int:
        return self.budget._reserve_model_attempt(self)

    def complete_attempt(
        self,
        attempt_number: int,
        *,
        outcome: Literal[ModelAttemptOutcome.SUCCEEDED, ModelAttemptOutcome.FAILED],
        provider_input_tokens: int | None = None,
        provider_output_tokens: int | None = None,
    ) -> None:
        self.budget._complete_model_attempt(
            self,
            attempt_number,
            outcome=outcome,
            provider_input_tokens=provider_input_tokens,
            provider_output_tokens=provider_output_tokens,
        )


@dataclass
class UnifiedRunBudget:
    """One fail-closed accounting object shared by source and model stages."""

    config: ScotusConfig
    prior_ledger: CostLedger
    mode: DiscoveryMode = DiscoveryMode.NIGHTLY
    receipt_sink: Callable[[ModelAttemptReceipt], None] | None = None
    started_monotonic: float = field(default_factory=time.monotonic)
    selected_cases: int = 0
    selected_documents: int = 0
    http_requests: int = 0
    downloaded_bytes: int = 0
    extraction_calls: int = 0
    brief_calls: int = 0
    model_calls: int = 0
    input_characters: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
    _authorized: set[tuple[str, str]] = field(default_factory=set)
    _next_attempt_numbers: dict[tuple[str, str], int] = field(default_factory=dict)
    _attempt_receipts: dict[tuple[str, str, int], ModelAttemptReceipt] = field(default_factory=dict)

    def check_runtime(self) -> None:
        if (
            time.monotonic() - self.started_monotonic
            > self.config.runner_limits.maximum_runtime_seconds
        ):
            raise BudgetExceeded("runtime budget exhausted")

    def reserve_case(self, documents: int = 0) -> None:
        self.check_runtime()
        case_limit = (
            self.config.bootstrap.maximum_cases_per_run
            if self.mode is DiscoveryMode.BOOTSTRAP
            else self.config.runner_limits.maximum_cases_per_run
        )
        if self.selected_cases + 1 > case_limit:
            raise BudgetExceeded("case budget exhausted")
        if (
            self.selected_documents + documents
            > self.config.runner_limits.maximum_documents_per_run
        ):
            raise BudgetExceeded("document budget exhausted")
        if self.config.generation.brief_generation_enabled and self.config.publication.enabled:
            limits = self.config.model_budget
            # Do not start/download a case, and especially do not buy extraction,
            # unless at least one extraction and its required final brief can fit.
            if (
                self.extraction_calls >= limits.maximum_extraction_calls_per_run
                or self.brief_calls >= limits.maximum_brief_calls_per_run
                or self.model_calls + 2 > limits.maximum_total_calls_per_run
            ):
                raise BudgetExceeded("case cannot fit extraction and brief call budgets")
        self.selected_cases += 1
        self.selected_documents += documents

    def reserve_http_request(self) -> None:
        self.check_runtime()
        request_limit = (
            self.config.bootstrap.maximum_requests_per_run
            if self.mode is DiscoveryMode.BOOTSTRAP
            else self.config.runner_limits.maximum_http_requests_per_run
        )
        if self.http_requests + 1 > request_limit:
            raise BudgetExceeded("HTTP request budget exhausted")
        self.http_requests += 1

    def record_download(self, byte_count: int) -> None:
        if byte_count < 0:
            raise ValueError("download byte count cannot be negative")
        byte_limit = (
            self.config.bootstrap.maximum_download_bytes_per_run
            if self.mode is DiscoveryMode.BOOTSTRAP
            else self.config.runner_limits.maximum_download_bytes_per_run
        )
        if self.downloaded_bytes + byte_count > byte_limit:
            raise BudgetExceeded("download byte budget exhausted")
        self.downloaded_bytes += byte_count

    def check_private_disk(self, workspace: RunWorkspace) -> None:
        if workspace.disk_bytes() > self.config.runner_limits.maximum_private_disk_bytes:
            raise BudgetExceeded("private disk budget exhausted")

    def authorize_model_request(
        self,
        *,
        stage: Literal["extraction", "brief"],
        document_digests: tuple[str, ...],
        processor_versions: Mapping[str, str],
        input_characters: int,
        input_tokens: int,
        output_tokens: int,
        authorized_replay: bool = False,
    ) -> ModelRequestPermit:
        if not (
            self.config.generation.brief_generation_enabled and self.config.publication.enabled
        ):
            raise PublicationGateDenied("brief-generation and publication gates are required")
        if input_characters < 0 or input_tokens < 0 or output_tokens < 0:
            raise ValueError("model sizes cannot be negative")
        limits = self.config.model_budget
        if input_tokens > limits.maximum_input_tokens_per_call:
            raise BudgetExceeded(
                "model input token limit exceeded "
                f"(requested={input_tokens}, limit={limits.maximum_input_tokens_per_call})"
            )
        if output_tokens > limits.maximum_output_tokens_per_call:
            raise BudgetExceeded(
                "model output token limit exceeded "
                f"(requested={output_tokens}, limit={limits.maximum_output_tokens_per_call})"
            )
        if self.input_characters + input_characters > limits.maximum_input_characters_per_run:
            raise BudgetExceeded(
                "model input character budget exhausted "
                f"(requested={self.input_characters + input_characters}, "
                f"limit={limits.maximum_input_characters_per_run})"
            )
        fingerprint = model_input_fingerprint(document_digests, processor_versions)
        key = (stage, fingerprint)
        previous = {
            (receipt.stage, receipt.input_fingerprint) for receipt in self.prior_ledger.receipts
        }
        if key in self._authorized:
            raise RepeatedModelInput("model input was already authorized in this run")
        if key in previous and not authorized_replay:
            raise RepeatedModelInput("unchanged model input was already recorded")
        self.input_characters += input_characters
        self._authorized.add(key)
        return ModelRequestPermit(self, stage, fingerprint, input_tokens, output_tokens)

    def _reserve_model_attempt(self, permit: ModelRequestPermit) -> int:
        self.check_runtime()
        key = (permit.stage, permit.fingerprint)
        if key not in self._authorized:
            raise RepeatedModelInput("model request has no run authorization")
        limits = self.config.model_budget
        stage_calls = self.extraction_calls if permit.stage == "extraction" else self.brief_calls
        stage_maximum = (
            limits.maximum_extraction_calls_per_run
            if permit.stage == "extraction"
            else limits.maximum_brief_calls_per_run
        )
        if (
            stage_calls + 1 > stage_maximum
            or self.model_calls + 1 > limits.maximum_total_calls_per_run
        ):
            raise BudgetExceeded("model call budget exhausted")
        if permit.stage == "extraction" and (
            self.brief_calls >= limits.maximum_brief_calls_per_run
            or self.model_calls + 2 > limits.maximum_total_calls_per_run
        ):
            raise BudgetExceeded("extraction would consume the required brief call slot")
        if self.input_tokens + permit.input_tokens > limits.maximum_input_tokens_per_run:
            raise BudgetExceeded("aggregate model input-token budget exhausted")
        if self.output_tokens + permit.output_tokens > limits.maximum_output_tokens_per_run:
            raise BudgetExceeded("aggregate model output-token budget exhausted")
        cost = self._estimated_model_cost(permit.input_tokens, permit.output_tokens)
        if self.estimated_cost_usd + cost > limits.maximum_estimated_cost_usd_per_run:
            raise BudgetExceeded("estimated model spend budget exhausted")
        if permit.stage == "extraction":
            self.extraction_calls += 1
        else:
            self.brief_calls += 1
        self.model_calls += 1
        self.input_tokens += permit.input_tokens
        self.output_tokens += permit.output_tokens
        self.estimated_cost_usd += cost

        prior_maximum = max(
            (
                receipt.attempt_number
                for receipt in self.prior_ledger.receipts
                if (receipt.stage, receipt.input_fingerprint) == key
            ),
            default=0,
        )
        attempt_number = self._next_attempt_numbers.get(key, prior_maximum) + 1
        self._next_attempt_numbers[key] = attempt_number
        receipt = self.receipt(
            permit,
            attempt_number=attempt_number,
            outcome=ModelAttemptOutcome.ATTEMPTED,
            attempted_at=datetime.now(UTC),
        )
        attempt_key = (*key, attempt_number)
        self._attempt_receipts[attempt_key] = receipt
        if self.receipt_sink is not None:
            # The durable attempted record is written before transport. Each retry gets
            # its own identity so call count and estimated cost cannot be understated.
            self.receipt_sink(receipt)
        return attempt_number

    def _complete_model_attempt(
        self,
        permit: ModelRequestPermit,
        attempt_number: int,
        *,
        outcome: Literal[ModelAttemptOutcome.SUCCEEDED, ModelAttemptOutcome.FAILED],
        provider_input_tokens: int | None,
        provider_output_tokens: int | None,
    ) -> None:
        attempt_key = (permit.stage, permit.fingerprint, attempt_number)
        attempted = self._attempt_receipts.get(attempt_key)
        if attempted is None:
            raise RepeatedModelInput("model transport completion has no attempted receipt")
        provider_reported = (
            outcome is ModelAttemptOutcome.SUCCEEDED
            and provider_input_tokens is not None
            and provider_output_tokens is not None
        )
        input_tokens = provider_input_tokens if provider_reported else permit.input_tokens
        if input_tokens is None:  # narrowed separately from the paired usage check above
            raise ValueError("provider response omitted input token usage")
        output_tokens = (
            provider_output_tokens
            if provider_reported
            else (permit.output_tokens if outcome is ModelAttemptOutcome.SUCCEEDED else None)
        )
        estimated_cost = self._estimated_model_cost(
            input_tokens,
            output_tokens if output_tokens is not None else permit.output_tokens,
        )
        completed = ModelAttemptReceipt.model_validate(
            {
                **attempted.model_dump(),
                "outcome": outcome,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "token_count_source": (
                    "provider_reported" if provider_reported else "reserved_upper_bound"
                ),
                "estimated_cost_usd": estimated_cost,
            }
        )
        self._attempt_receipts[attempt_key] = completed
        if self.receipt_sink is not None:
            self.receipt_sink(completed)

    def _estimated_model_cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (
            Decimal(input_tokens) * self.config.model_budget.input_cost_usd_per_million_tokens
            + Decimal(output_tokens) * self.config.model_budget.output_cost_usd_per_million_tokens
        ) / Decimal(1_000_000)

    def receipt(
        self,
        permit: ModelRequestPermit,
        *,
        attempt_number: int = 1,
        outcome: ModelAttemptOutcome,
        attempted_at: datetime,
    ) -> ModelAttemptReceipt:
        return ModelAttemptReceipt(
            input_fingerprint=permit.fingerprint,
            stage=permit.stage,
            attempt_number=attempt_number,
            outcome=outcome,
            attempted_at=attempted_at,
            call_count=0 if outcome is ModelAttemptOutcome.BLOCKED else 1,
            input_tokens=(
                permit.input_tokens if outcome is not ModelAttemptOutcome.BLOCKED else None
            ),
            output_tokens=(
                permit.output_tokens if outcome is ModelAttemptOutcome.SUCCEEDED else None
            ),
            estimated_cost_usd=(
                Decimal("0")
                if outcome is ModelAttemptOutcome.BLOCKED
                else self._estimated_model_cost(permit.input_tokens, permit.output_tokens)
            ),
        )


@dataclass(frozen=True)
class ArgumentSessionWork:
    session_key: str
    document_keys: tuple[str, ...]


@dataclass(frozen=True)
class StaticCaseWork:
    case_key: str
    priority: int
    sessions: tuple[ArgumentSessionWork, ...]
    reason: str
    # Dockets and dispositions belong to the case. Only transcripts belong to an
    # argument session. Keeping these identities separate prevents a disposition-only
    # case from acquiring a fabricated session merely to enter the work queue.
    case_document_keys: tuple[str, ...] = ()

    @property
    def document_count(self) -> int:
        return len(
            {
                *self.case_document_keys,
                *(key for session in self.sessions for key in session.document_keys),
            }
        )


@dataclass(frozen=True)
class CaseProcessingResult:
    case_key: str
    processed_session_keys: tuple[str, ...]
    public_case: PublicCaseBrief | None
    failure: FailureCategory | None = None
    changed: bool = True
    documents: tuple[LogicalDocumentState, ...] = ()

    @property
    def complete(self) -> bool:
        return self.public_case is not None and self.failure is None


@dataclass(frozen=True)
class StaticDiscoveryResult:
    work: tuple[StaticCaseWork, ...] = ()
    sources: tuple[LogicalSourceState, ...] = ()
    documents: tuple[LogicalDocumentState, ...] = ()
    dispositions: tuple[DispositionDiscoveryState, ...] = ()
    cursors: tuple[CursorState, ...] = ()
    processor: ProcessorFingerprint | None = None
    # Cases discovered as changed but omitted by a bounded selection remain explicit
    # pending work instead of disappearing behind an unadvanced source checkpoint.
    deferred_case_keys: tuple[str, ...] = ()
    # An adapter may explicitly retire stale pending markers when it can prove they were
    # created only by an obsolete migration policy, not by unresolved source work.
    resolved_pending_case_keys: tuple[str, ...] = ()
    # A discovery adapter must set this false when its own selection limits omit
    # changed descriptors. Advancing a cursor/checkpoint in that case would hide work.
    checkpoint_safe: bool = True


class StaticDiscovery(Protocol):
    def discover(
        self,
        *,
        mode: DiscoveryMode,
        content: GeneratedContent,
        budget: UnifiedRunBudget,
        now: datetime,
    ) -> StaticDiscoveryResult: ...


class StaticCaseProcessor(Protocol):
    def process(
        self,
        work: StaticCaseWork,
        *,
        workspace: RunWorkspace,
        budget: UnifiedRunBudget,
        authorized_replay: bool,
    ) -> CaseProcessingResult: ...


class ProductionBatchAdapter(Protocol):
    """Reviewed live integration boundary; implementations may use Court/OpenAI I/O."""

    def run(
        self,
        *,
        state_store: StaticStateStore,
        config: ScotusConfig,
        mode: DiscoveryMode,
        runner_temp: str | Path,
        authorized_replay: bool,
    ) -> StaticBatchResult: ...


class FailClosedProductionBatchAdapter:
    """Concrete default that guarantees no source or model transport is attempted."""

    def run(
        self,
        *,
        state_store: StaticStateStore,
        config: ScotusConfig,
        mode: DiscoveryMode,
        runner_temp: str | Path,
        authorized_replay: bool,
    ) -> StaticBatchResult:
        del state_store, config, mode, runner_temp, authorized_replay
        raise ProductionBatchUnavailable(
            "production SCOTUS batch adapter is not configured; stopped before network/model use"
        )


class EphemeralServices(Protocol):
    def start(self, workspace: RunWorkspace) -> None: ...

    def stop(self) -> None: ...


class NoEphemeralServices:
    def start(self, workspace: RunWorkspace) -> None:
        del workspace

    def stop(self) -> None:
        return None


@dataclass(frozen=True)
class StaticBatchResult:
    content: GeneratedContent
    parent_release_id: str | None
    changed_case_keys: tuple[str, ...]
    pending_case_keys: tuple[str, ...]
    publishable: bool
    no_public_change: bool


class StaticBatchOrchestrator:
    """Coordinate public checkpoints and all-or-nothing changed-case processing."""

    def __init__(
        self,
        *,
        state_store: StaticStateStore,
        discovery: StaticDiscovery,
        processor: StaticCaseProcessor,
        config: ScotusConfig,
        services: EphemeralServices | None = None,
        runner_temp: str | Path | None = None,
        receipt_sink: Callable[[ModelAttemptReceipt], None] | None = None,
    ) -> None:
        self.state_store = state_store
        self.discovery = discovery
        self.processor = processor
        self.config = config
        self.services = services or NoEphemeralServices()
        self.runner_temp = runner_temp
        self.receipt_sink = receipt_sink

    def run(
        self,
        *,
        mode: DiscoveryMode = DiscoveryMode.NIGHTLY,
        now: datetime | None = None,
        authorized_replay: bool = False,
    ) -> StaticBatchResult:
        instant = now or datetime.now(UTC)
        original = self.state_store.load()
        budget = UnifiedRunBudget(
            self.config,
            original.cost_ledger,
            mode=mode,
            receipt_sink=self.receipt_sink,
        )
        workspace = RunWorkspace.create(self.runner_temp, run_id=str(int(instant.timestamp())))
        changed: list[str] = []
        pending: dict[str, PendingWork] = {
            item.case_key: item for item in original.publication.pending_work
        }
        working = original
        accepted_documents: dict[str, LogicalDocumentState] = {}
        failed = False
        checkpoints_safe = True
        try:
            with WorkspaceSignalCleanup(workspace):
                self.services.start(workspace)
                discovered = self.discovery.discover(
                    mode=mode,
                    content=original,
                    budget=budget,
                    now=instant,
                )
                checkpoints_safe = discovered.checkpoint_safe
                for resolved_key in discovered.resolved_pending_case_keys:
                    pending.pop(resolved_key, None)
                ordered = sorted(discovered.work, key=lambda item: (item.priority, item.case_key))
                selected_keys = {item.case_key for item in ordered}
                for deferred_key in discovered.deferred_case_keys:
                    if deferred_key not in selected_keys:
                        pending[deferred_key] = _pending(
                            pending.get(deferred_key),
                            case_key=deferred_key,
                            reason=PendingReason.BUDGET_EXHAUSTED,
                            now=instant,
                            attempted=False,
                        )
                for index, work in enumerate(ordered):
                    started = time.monotonic()
                    try:
                        budget.reserve_case(work.document_count)
                    except BudgetExceeded:
                        checkpoints_safe = False
                        for deferred in ordered[index:]:
                            pending[deferred.case_key] = _pending(
                                pending.get(deferred.case_key),
                                case_key=deferred.case_key,
                                reason=PendingReason.BUDGET_EXHAUSTED,
                                now=instant,
                                attempted=False,
                            )
                        break
                    try:
                        result = self.processor.process(
                            work,
                            workspace=workspace,
                            budget=budget,
                            authorized_replay=authorized_replay,
                        )
                        required = {session.session_key for session in work.sessions}
                        processed = set(result.processed_session_keys)
                        processed_documents = {item.logical_key for item in result.documents}
                        if (
                            not result.complete
                            or processed != required
                            or not set(work.case_document_keys).issubset(processed_documents)
                            or result.case_key != work.case_key
                            or result.public_case is None
                            or public_case_key(
                                result.public_case.term, result.public_case.primary_docket
                            )
                            != work.case_key
                        ):
                            raise ValueError(
                                "changed case did not complete every required session "
                                "and case document"
                            )
                        if result.changed:
                            working = self.state_store.merge_accepted_case(
                                working,
                                result.public_case,
                                watermark=instant,
                                generated_at=instant,
                                processor_sha256=(
                                    discovered.processor.composite_sha256
                                    if discovered.processor is not None
                                    else None
                                ),
                            )
                            changed.append(work.case_key)
                        accepted_documents.update(
                            {item.logical_key: item for item in result.documents}
                        )
                        pending.pop(work.case_key, None)
                        log_stage(
                            LOG,
                            case_key=work.case_key,
                            stage="case",
                            status="complete",
                            elapsed_seconds=time.monotonic() - started,
                            counts={"sessions": len(required), "documents": work.document_count},
                        )
                    except Exception as error:
                        category = failure_category(error)
                        # Source/disposition index checkpoints describe a complete,
                        # strictly parsed discovery response. A case-local document,
                        # extraction, or draft failure must remain pending without
                        # discarding that safe allowlisted discovery metadata.
                        if isinstance(error, BudgetExceeded):
                            checkpoints_safe = False
                        reason = {
                            FailureCategory.BUDGET: PendingReason.BUDGET_EXHAUSTED,
                            FailureCategory.SOURCE_UNAVAILABLE: PendingReason.SOURCE_UNAVAILABLE,
                            FailureCategory.SOURCE_INVALID: PendingReason.SOURCE_INVALID,
                            FailureCategory.VALIDATION: PendingReason.VALIDATION_FAILED,
                        }.get(category, PendingReason.PROCESSING_FAILED)
                        pending[work.case_key] = _pending(
                            pending.get(work.case_key),
                            case_key=work.case_key,
                            reason=reason,
                            now=instant,
                            attempted=True,
                        )
                        failed = True
                        log_stage(
                            LOG,
                            case_key=work.case_key,
                            stage="case",
                            status="failed",
                            elapsed_seconds=time.monotonic() - started,
                            category=category,
                        )
                        if isinstance(error, BudgetExceeded):
                            safe_detail = str(error)
                        elif isinstance(error, ValidationError):
                            safe_detail = (
                                "ValidationError["
                                + ",".join(
                                    f"{'.'.join(str(part) for part in item['loc'])}:{item['type']}"
                                    for item in error.errors(
                                        include_url=False,
                                        include_context=False,
                                        include_input=False,
                                    )[:10]
                                )
                                + "]"
                            )
                        else:
                            safe_code = getattr(error, "safe_code", None)
                            safe_detail = type(error).__name__
                            if isinstance(safe_code, str) and re.fullmatch(
                                r"[a-z0-9_:-]{1,80}", safe_code
                            ):
                                safe_detail += f"[{safe_code}]"
                        LOG.warning(
                            "SCOTUS bounded case failure; category=%s; detail=%s; cases=%d; "
                            "documents=%d; requests=%d; bytes=%d; extraction_calls=%d; "
                            "brief_calls=%d; total_model_calls=%d",
                            category.value,
                            safe_detail,
                            budget.selected_cases,
                            budget.selected_documents,
                            budget.http_requests,
                            budget.downloaded_bytes,
                            budget.extraction_calls,
                            budget.brief_calls,
                            budget.model_calls,
                        )
                        if isinstance(error, BudgetExceeded):
                            break
                    budget.check_private_disk(workspace)
                sources = {item.logical_key: item for item in original.publication.sources}
                documents = {item.logical_key: item for item in original.publication.documents}
                dispositions = {
                    item.logical_key: item for item in original.publication.dispositions
                }
                cursors = {item.cursor_key: item for item in original.publication.cursors}
                # Completed case checkpoints are safe independently of whether a later
                # case was deferred. Source/cursor checkpoints remain all-or-nothing so
                # they cannot hide omitted discovery work.
                documents.update(accepted_documents)
                if checkpoints_safe:
                    sources.update({item.logical_key: item for item in discovered.sources})
                    documents.update({item.logical_key: item for item in discovered.documents})
                    dispositions.update(
                        {item.logical_key: item for item in discovered.dispositions}
                    )
                    cursors.update({item.cursor_key: item for item in discovered.cursors})
                processor = original.publication.processor
                if discovered.processor is not None and checkpoints_safe:
                    target = discovered.processor.composite_sha256
                    # A global fingerprint is a claim about every active case, not the
                    # bounded subset selected in this run.
                    all_active_cases_migrated = all(
                        pointer.processor_sha256 == target for pointer in working.publication.cases
                    )
                    if all_active_cases_migrated:
                        processor = discovered.processor
                working = self.state_store.update_publication_state(
                    working,
                    updated_at=instant,
                    sources=tuple(sources[key] for key in sorted(sources)),
                    documents=tuple(documents[key] for key in sorted(documents)),
                    pending_work=tuple(pending[key] for key in sorted(pending)),
                    cursors=tuple(cursors[key] for key in sorted(cursors)),
                    processor=processor,
                    dispositions=tuple(dispositions[key] for key in sorted(dispositions)),
                )
        finally:
            try:
                self.services.stop()
            finally:
                workspace.cleanup()
        if changed:
            # Rendering owns construction of the content-derived candidate manifest.
            # Never leave a prior release manifest attached to changed projection bytes.
            working = replace(working, release=None)
        return StaticBatchResult(
            content=working,
            parent_release_id=original.publication.active_release_id,
            changed_case_keys=tuple(changed),
            pending_case_keys=tuple(sorted(pending)),
            publishable=bool(changed) or not failed,
            no_public_change=not changed,
        )


def _pending(
    previous: PendingWork | None,
    *,
    case_key: str,
    reason: PendingReason,
    now: datetime,
    attempted: bool,
) -> PendingWork:
    return PendingWork(
        case_key=case_key,
        reason=reason,
        attempts=(previous.attempts if previous else 0) + int(attempted),
        first_seen_at=previous.first_seen_at if previous else now,
        last_attempted_at=now if attempted else (previous.last_attempted_at if previous else None),
    )


def call_with_bounded_transport_retries[T](
    operation: Callable[[], T],
    *,
    permit: ModelRequestPermit,
    maximum_attempts: int,
    retryable: Callable[[BaseException], bool],
    response_usage: Callable[[T], tuple[int, int] | None] | None = None,
) -> T:
    """Retry transport only, accounting every provider attempt before it is sent."""
    if maximum_attempts < 1:
        raise ValueError("maximum attempts must be positive")
    if maximum_attempts > permit.budget.config.model_budget.maximum_transport_attempts:
        raise ValueError("maximum attempts exceed the configured transport limit")
    last_error: BaseException | None = None
    for attempt in range(maximum_attempts):
        attempt_number = permit.reserve_attempt()
        try:
            result = operation()
        except BaseException as error:
            permit.complete_attempt(attempt_number, outcome=ModelAttemptOutcome.FAILED)
            last_error = error
            if attempt + 1 >= maximum_attempts or not retryable(error):
                raise
        else:
            usage = response_usage(result) if response_usage is not None else None
            permit.complete_attempt(
                attempt_number,
                outcome=ModelAttemptOutcome.SUCCEEDED,
                provider_input_tokens=usage[0] if usage is not None else None,
                provider_output_tokens=usage[1] if usage is not None else None,
            )
            return result
    assert last_error is not None
    raise last_error


def processor_fingerprint(
    *,
    document_digests: Sequence[str],
    parser: str,
    extractor: str,
    policy: str,
    provider: str,
    endpoint: str,
    model: str,
    prompt: str,
    config_digest: str,
) -> str:
    """Stable composite fingerprint for all model-processing inputs and versions."""
    return model_input_fingerprint(
        tuple(document_digests),
        {
            "config": config_digest,
            "endpoint": endpoint,
            "extractor": extractor,
            "model": model,
            "provider": provider,
            "parser": parser,
            "policy": policy,
            "prompt": prompt,
        },
    )
