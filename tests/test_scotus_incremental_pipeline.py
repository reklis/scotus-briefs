from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ragchew.config import ScotusConfig
from ragchew.proceedings.contracts import (
    DocumentType,
    ProceedingLifecycle,
    ProceedingType,
    SourceAccessMethod,
)
from ragchew.proceedings.discovery import (
    ConditionalRequest,
    DiscoveredProceeding,
    DocumentDescriptor,
    SourcePollResult,
)
from ragchew.scotus.discovery import (
    DiscoveryMode,
    ScotusArgumentCandidate,
    discover_once,
    select_discovery_resources,
    select_discovery_work,
    stable_candidate_fingerprint,
    transcript_logical_key,
)
from ragchew.scotus.documents import plan_document_revision
from ragchew.scotus.public_contracts import (
    ScotusPublicProjection,
    public_case_key,
    public_case_slug,
)
from ragchew.scotus.static_contracts import (
    ConditionalValidators,
    ContentIntegrity,
    CostLedger,
    LogicalDocumentState,
    LogicalSourceState,
    ModelAttemptOutcome,
    ModelAttemptReceipt,
)
from ragchew.scotus.static_pipeline import (
    BudgetExceeded,
    CaseProcessingResult,
    PublicationGateDenied,
    RunWorkspace,
    StaticBatchOrchestrator,
    StaticCaseWork,
    StaticDiscoveryResult,
    UnifiedRunBudget,
    call_with_bounded_transport_retries,
)
from ragchew.scotus.static_state import StaticStateStore
from ragchew.scotus.worker import WorkerMode, run_bounded_worker

NOW = datetime(2026, 8, 28, 2, tzinfo=UTC)
DIGEST = "a" * 64


def descriptor(docket: str) -> DocumentDescriptor:
    return DocumentDescriptor(
        external_id=f"2025:{docket}:transcript:first.pdf",
        document_type=DocumentType.OFFICIAL_TRANSCRIPT,
        official_url=(
            f"https://www.supremecourt.gov/oral_arguments/argument_transcripts/2025/{docket}.pdf"
        ),
        access_method=SourceAccessMethod.OFFICIAL_PAGE,
        content_type="application/pdf",
        source_updated_at=NOW,
    )


def candidate(
    docket: str = "25-1", *, term: str = "2025", days_ago: int = 1
) -> ScotusArgumentCandidate:
    return ScotusArgumentCandidate(
        term=term,
        primary_docket=docket,
        caption=f"Case {docket}",
        argument_date=NOW - timedelta(days=days_ago),
        official_detail_url=(f"https://www.supremecourt.gov/oral_arguments/audio/{term}/{docket}"),
        transcript=descriptor(docket),
        source_metadata={"source_updated_at": NOW.isoformat(), "transcript_available": True},
    )


def proceeding() -> DiscoveredProceeding:
    return DiscoveredProceeding(
        external_id="25-1",
        proceeding_type=ProceedingType.ORAL_ARGUMENT,
        title="Case 25-1",
        official_url="https://www.supremecourt.gov/oral_arguments/audio/2025/25-1",
        lifecycle=ProceedingLifecycle.COMPLETED,
        scheduled_start_at=NOW - timedelta(days=1),
        source_updated_at=NOW,
        documents=(descriptor("25-1"),),
        metadata={"term": "2025"},
    )


class Adapter:
    term = "2025"

    def __init__(self, result: SourcePollResult) -> None:
        self.result = result
        self.conditional: ConditionalRequest | None = None

    def poll(self, conditional: ConditionalRequest) -> SourcePollResult:
        self.conditional = conditional
        return self.result


def source_state() -> LogicalSourceState:
    return LogicalSourceState(
        logical_key="argument-index:2025",
        source_kind="argument_index",
        official_url=("https://www.supremecourt.gov/oral_arguments/argument_transcript/2025"),
        validators=ConditionalValidators(etag='"v1"'),
        integrity=ContentIntegrity(sha256=DIGEST, byte_count=1),
        checked_at=NOW - timedelta(days=1),
    )


def test_discover_once_sends_checkpoint_and_304_creates_no_work() -> None:
    adapter = Adapter(
        SourcePollResult(
            source_id="supreme_court",
            endpoint_url=source_state().official_url,
            access_method=SourceAccessMethod.OFFICIAL_PAGE,
            retrieved_at=NOW,
            not_modified=True,
            etag='"v1"',
        )
    )
    result = discover_once(
        adapter,
        resource_key="argument-index:2025",
        checkpoint=source_state(),
        now=NOW,
    )
    assert adapter.conditional == ConditionalRequest(etag='"v1"')
    assert result.not_modified and not result.changed and result.candidates == ()
    assert result.checkpoint.integrity == source_state().integrity


def test_stable_fingerprint_ignores_retrieval_times() -> None:
    first = candidate()
    changed_time = first.model_copy(
        update={
            "transcript": first.transcript.model_copy(
                update={"source_updated_at": NOW + timedelta(hours=1)}
            )
            if first.transcript
            else None,
            "source_metadata": {
                "source_updated_at": (NOW + timedelta(hours=1)).isoformat(),
                "transcript_available": True,
            },
        }
    )
    assert stable_candidate_fingerprint(first) == stable_candidate_fingerprint(changed_time)


def test_resource_selection_never_scans_every_historical_term() -> None:
    first = select_discovery_resources(
        ("2022", "2023", "2024", "2025"),
        active_term="2025",
        mode=DiscoveryMode.NIGHTLY,
        historical_limit=1,
        bootstrap_term_limit=3,
        now=NOW,
    )
    assert first.terms == ("2025", "2024")
    assert first.cursor is not None
    second = select_discovery_resources(
        ("2022", "2023", "2024", "2025"),
        active_term="2025",
        mode=DiscoveryMode.NIGHTLY,
        historical_limit=1,
        bootstrap_term_limit=3,
        now=NOW,
        cursor=first.cursor,
    )
    assert second.terms == ("2025", "2023")


def test_nightly_selection_prioritizes_new_and_rotates_historical() -> None:
    current = candidate("25-1")
    old = tuple(
        candidate(f"20-{number}", term="2020", days_ago=2_000 + number) for number in range(3)
    )
    first = select_discovery_work(
        (current, *old),
        mode=DiscoveryMode.NIGHTLY,
        now=NOW,
        active_term="2025",
        nightly_case_limit=2,
        new_transcript_priority=10,
        historical_priority=100,
        historical_limit=1,
        recent_lookback_days=30,
    )
    assert first.work[0].candidate == current
    assert first.work[0].priority == 10
    assert first.cursor is not None
    second = select_discovery_work(
        (current, *reversed(old)),
        mode=DiscoveryMode.NIGHTLY,
        now=NOW,
        active_term="2025",
        known_transcript_keys={transcript_logical_key(current)},
        nightly_case_limit=2,
        new_transcript_priority=10,
        historical_priority=100,
        historical_limit=1,
        recent_lookback_days=30,
        cursor=first.cursor,
    )
    assert second.work[-1].candidate != first.work[-1].candidate


def test_current_term_rechecks_rotate_when_all_transcripts_are_known() -> None:
    values = tuple(candidate(f"25-{number}") for number in range(1, 4))
    known = {transcript_logical_key(item) for item in values}
    first = select_discovery_work(
        values,
        mode=DiscoveryMode.NIGHTLY,
        now=NOW,
        active_term="2025",
        known_transcript_keys=known,
        nightly_case_limit=1,
        new_transcript_priority=10,
        historical_priority=100,
        historical_limit=0,
        recent_lookback_days=30,
    )
    second = select_discovery_work(
        values,
        mode=DiscoveryMode.NIGHTLY,
        now=NOW,
        active_term="2025",
        known_transcript_keys=known,
        nightly_case_limit=1,
        new_transcript_priority=10,
        historical_priority=100,
        historical_limit=0,
        recent_lookback_days=30,
        current_cursor=first.current_cursor,
    )
    assert first.work[0].candidate != second.work[0].candidate


def test_document_revision_plan_accepts_same_or_new_url_by_logical_identity() -> None:
    prior = LogicalDocumentState(
        logical_key="2025:25-1:transcript:2026-01-01:1",
        case_key="2025-25-1",
        document_kind="transcript",
        official_url="https://www.supremecourt.gov/pdfs/transcripts/2025/25-1.pdf",
        revision_number=1,
        integrity=ContentIntegrity(sha256=DIGEST, byte_count=100),
        checked_at=NOW,
    )
    unchanged = plan_document_revision(prior.logical_key, observed_sha256=DIGEST, prior=prior)
    revised = plan_document_revision(prior.logical_key, observed_sha256="b" * 64, prior=prior)
    assert (unchanged.changed, unchanged.revision_number) == (False, 1)
    assert (revised.changed, revised.revision_number) == (True, 2)


def live_config() -> ScotusConfig:
    config = ScotusConfig.from_yaml("config/scotus.yaml")
    return config.model_copy(
        update={
            "generation": config.generation.model_copy(update={"brief_generation_enabled": True}),
            "publication": config.publication.model_copy(update={"enabled": True}),
            "model_budget": config.model_budget.model_copy(
                update={
                    "maximum_brief_calls_per_run": 2,
                    "maximum_transport_attempts": 2,
                }
            ),
        }
    )


def test_workspace_is_private_and_always_removable(tmp_path: Path) -> None:
    workspace = RunWorkspace.create(tmp_path, run_id="test")
    assert workspace.root.is_relative_to(tmp_path)
    assert workspace.root.stat().st_mode & 0o077 == 0
    path = workspace.private_path("downloads", "case/document.pdf")
    path.parent.mkdir(mode=0o700)
    path.write_bytes(b"private")
    workspace.cleanup()
    assert not workspace.root.exists()
    workspace.cleanup()
    with pytest.raises(ValueError, match="safe"):
        RunWorkspace.create(tmp_path, run_id="!!!")


def test_unified_budget_gates_and_counts_every_transport_attempt() -> None:
    config = ScotusConfig.from_yaml("config/scotus.yaml")
    disabled = config.model_copy(
        update={"publication": config.publication.model_copy(update={"enabled": False})}
    )
    with pytest.raises(PublicationGateDenied):
        UnifiedRunBudget(disabled, CostLedger(updated_at=NOW)).authorize_model_request(
            stage="brief",
            document_digests=(DIGEST,),
            processor_versions={"parser": "1"},
            input_characters=1,
            input_tokens=1,
            output_tokens=1,
        )
    receipts: list[ModelAttemptReceipt] = []
    budget = UnifiedRunBudget(
        live_config(), CostLedger(updated_at=NOW), receipt_sink=receipts.append
    )
    permit = budget.authorize_model_request(
        stage="brief",
        document_digests=(DIGEST,),
        processor_versions={"parser": "1"},
        input_characters=10,
        input_tokens=10,
        output_tokens=10,
    )
    calls = 0

    def request() -> str:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise TimeoutError
        return "ok"

    assert (
        call_with_bounded_transport_retries(
            request,
            permit=permit,
            maximum_attempts=2,
            retryable=lambda error: isinstance(error, TimeoutError),
        )
        == "ok"
    )
    assert calls == budget.model_calls == budget.brief_calls == 2
    assert len(receipts) == 4  # attempted + terminal update for each transport
    terminal = [
        receipt
        for receipt in receipts
        if receipt.outcome in {ModelAttemptOutcome.FAILED, ModelAttemptOutcome.SUCCEEDED}
    ]
    assert [receipt.attempt_number for receipt in terminal] == [1, 2]
    assert [receipt.outcome for receipt in terminal] == [
        ModelAttemptOutcome.FAILED,
        ModelAttemptOutcome.SUCCEEDED,
    ]
    assert all(receipt.call_count == 1 for receipt in terminal)
    assert all(receipt.input_fingerprint == permit.fingerprint for receipt in terminal)
    ledger = CostLedger(updated_at=NOW, receipts=tuple(terminal))
    assert sum(receipt.call_count for receipt in ledger.receipts) == calls
    with pytest.raises(BudgetExceeded, match="brief call budgets"):
        budget.reserve_case()
    assert budget.extraction_calls == 0


def test_deferred_batch_work_cannot_be_hidden_by_advanced_checkpoints(
    tmp_path: Path,
) -> None:
    projection_payload = json.loads(
        Path("tests/fixtures/static/one-case.json").read_text(encoding="utf-8")
    )["projection"]
    public_case = ScotusPublicProjection.model_validate(projection_payload).cases[0]
    first_key = public_case_key(public_case.term, public_case.primary_docket)
    deferred_key = "2025-25-999"
    advanced = source_state().model_copy(update={"checked_at": NOW})

    class Discovery:
        def discover(self, **_kwargs: object) -> StaticDiscoveryResult:
            return StaticDiscoveryResult(
                work=(
                    StaticCaseWork(first_key, 1, (), "changed"),
                    StaticCaseWork(deferred_key, 2, (), "changed"),
                ),
                sources=(advanced,),
            )

    class Processor:
        def process(self, work: StaticCaseWork, **_kwargs: object) -> CaseProcessingResult:
            assert work.case_key == first_key
            return CaseProcessingResult(first_key, (), public_case)

    config = ScotusConfig.from_yaml("config/scotus.yaml")
    config = config.model_copy(
        update={
            "runner_limits": config.runner_limits.model_copy(
                update={"maximum_cases_per_run": 1}
            )
        }
    )
    result = StaticBatchOrchestrator(
        state_store=StaticStateStore(tmp_path / "empty"),
        discovery=Discovery(),
        processor=Processor(),
        config=config,
        runner_temp=tmp_path,
    ).run(now=NOW)
    assert result.changed_case_keys == (first_key,)
    assert result.pending_case_keys == (deferred_key,)
    assert result.content.publication.sources == ()


def test_case_validation_failure_does_not_block_later_complete_case(
    tmp_path: Path,
) -> None:
    projection_payload = json.loads(
        Path("tests/fixtures/static/one-case.json").read_text(encoding="utf-8")
    )["projection"]
    first_case = ScotusPublicProjection.model_validate(projection_payload).cases[0]
    first_key = public_case_key(first_case.term, first_case.primary_docket)
    second_docket = "25-998"
    second_key = public_case_key(first_case.term, second_docket)
    second_caption = "Second Synthetic Case v. Agency"
    second_case = first_case.model_copy(
        update={
            "primary_docket": second_docket,
            "caption": second_caption,
            "slug": public_case_slug(first_case.term, second_docket, second_caption),
        }
    )

    class Discovery:
        def discover(self, **_kwargs: object) -> StaticDiscoveryResult:
            return StaticDiscoveryResult(
                work=(
                    StaticCaseWork(first_key, 1, (), "changed"),
                    StaticCaseWork(second_key, 2, (), "changed"),
                )
            )

    class Processor:
        def process(self, work: StaticCaseWork, **_kwargs: object) -> CaseProcessingResult:
            if work.case_key == first_key:
                raise ValueError("synthetic validation failure")
            return CaseProcessingResult(second_key, (), second_case)

    result = StaticBatchOrchestrator(
        state_store=StaticStateStore(tmp_path / "empty"),
        discovery=Discovery(),
        processor=Processor(),
        config=ScotusConfig.from_yaml("config/scotus.yaml"),
        runner_temp=tmp_path,
    ).run(now=NOW)

    assert result.publishable
    assert result.changed_case_keys == (second_key,)
    assert result.pending_case_keys == (first_key,)
    assert result.content.projection is not None
    assert result.content.projection.cases == (second_case,)


def test_bounded_worker_empty_queue_drains_without_sleep() -> None:
    slept = False

    def sleep(_seconds: float) -> None:
        nonlocal slept
        slept = True

    result = run_bounded_worker(
        claim_next=lambda: None,
        process=lambda _lease: None,
        runnable_count=lambda: 0,
        active_lease_count=lambda: 0,
        mode=WorkerMode.DRAIN,
        maximum_idle_seconds=1,
        maximum_runtime_seconds=5,
        sleep=sleep,
    )
    assert result.drained
    assert result.processed == 0
    assert not slept
