from datetime import UTC, datetime, timedelta

from ragchew.proceedings.contracts import (
    DocumentType,
    GovernmentAuthority,
    Jurisdiction,
    OfficialSource,
    SourceAccessMethod,
    SourceHealth,
)
from ragchew.proceedings.discovery import DocumentDescriptor
from ragchew.proceedings.registry import InMemorySourceRegistry
from ragchew.scotus.contracts import ScotusDocumentKind
from ragchew.scotus.discovery import (
    InMemoryScotusDiscoveryStore,
    ScotusArgumentCandidate,
    ScotusDiscoveryCoordinator,
    deterministic_argument_id,
    deterministic_case_id,
)

NOW = datetime(2026, 8, 28, 2, tzinfo=UTC)


def source() -> OfficialSource:
    return OfficialSource(
        source_id="supreme_court",
        authority=GovernmentAuthority.US_SUPREME_COURT,
        jurisdiction=Jurisdiction.FEDERAL,
        display_name="Supreme Court",
        official_index_url="https://www.supremecourt.gov/oral_arguments/argument_audio.aspx",
        adapter="supreme_court",
        discovery_method=SourceAccessMethod.OFFICIAL_PAGE,
        media_method=SourceAccessMethod.DOWNLOADABLE_FILE,
        access_basis="reviewed Court-hosted pages",
        access_reviewed_at=NOW - timedelta(days=1),
        access_reviewed_by="project-source-access-review",
        access_review_expires_at=NOW + timedelta(days=365),
        allowed_hosts=("www.supremecourt.gov",),
        poll_interval_seconds=900,
        expected_schedule="Court term",
        enabled=True,
        health=SourceHealth.HEALTHY,
    )


def transcript(docket: str = "25-466", suffix: str = "ec8f") -> DocumentDescriptor:
    return DocumentDescriptor(
        external_id=f"2025:{docket}:transcript:{suffix}.pdf",
        document_type=DocumentType.OFFICIAL_TRANSCRIPT,
        official_url=(
            "https://www.supremecourt.gov/oral_arguments/argument_transcripts/"
            f"2025/{docket}_{suffix}.pdf"
        ),
        access_method=SourceAccessMethod.OFFICIAL_PAGE,
        content_type="application/pdf",
    )


def docket_document(docket: str = "25-466") -> DocumentDescriptor:
    return DocumentDescriptor(
        external_id=f"{docket}:docket",
        document_type=DocumentType.DOCKET,
        official_url=(
            "https://www.supremecourt.gov/docket/docketfiles/html/public/"
            f"{docket}.html"
        ),
        access_method=SourceAccessMethod.OFFICIAL_PAGE,
        content_type="text/html",
    )


def candidate(**overrides: object) -> ScotusArgumentCandidate:
    values: dict[str, object] = {
        "term": "2025",
        "primary_docket": "25-466",
        "caption": "Sripetch v. SEC",
        "argument_date": datetime(2026, 4, 20, tzinfo=UTC),
        "official_detail_url": (
            "https://www.supremecourt.gov/oral_arguments/audio/2025/25-466"
        ),
        "transcript": transcript(),
        "docket_documents": (docket_document(),),
        "source_metadata": {"audio_available": True, "audio_collection": "disabled"},
    }
    values.update(overrides)
    return ScotusArgumentCandidate.model_validate(values)


def setup() -> tuple[ScotusDiscoveryCoordinator, InMemoryScotusDiscoveryStore]:
    registry = InMemorySourceRegistry()
    registry.register(source(), "approved Court source")
    store = InMemoryScotusDiscoveryStore()
    return ScotusDiscoveryCoordinator(registry, store), store


def test_transcript_discovery_is_idempotent_and_never_queues_audio() -> None:
    coordinator, store = setup()
    first = coordinator.apply(candidate(), NOW, priority=10)
    duplicate = coordinator.apply(candidate(), NOW, priority=10)
    assert (first.cases_created, first.arguments_created, first.transcript_jobs) == (1, 1, 1)
    assert (duplicate.cases_created, duplicate.arguments_created, duplicate.transcript_jobs) == (
        0,
        0,
        0,
    )
    assert len(store.jobs) == 1
    assert {document.kind for document in store.documents.values()} == {
        ScotusDocumentKind.TRANSCRIPT,
        ScotusDocumentKind.DOCKET,
    }
    assert all("audio" not in job.external_id for job in store.jobs)


def test_audio_only_argument_remains_pending_without_collection_job() -> None:
    coordinator, store = setup()
    result = coordinator.apply(candidate(transcript=None), NOW, priority=10)
    assert result.cases_created == 1
    assert result.transcript_jobs == 0
    assert store.jobs == set()


def test_caption_change_appends_revision_without_duplicate_case() -> None:
    coordinator, store = setup()
    item = candidate()
    coordinator.apply(item, NOW, priority=10)
    changed = item.model_copy(update={"caption": "Sripetch v. Securities and Exchange Commission"})
    result = coordinator.apply(changed, NOW + timedelta(minutes=15), priority=10)
    case_id = deterministic_case_id("2025", "25-466")
    assert result.cases_created == 0
    assert result.metadata_revisions == 1
    assert len(store.case_revisions[case_id]) == 2


def test_consolidated_and_reargued_sessions_have_stable_distinct_ids() -> None:
    consolidated = candidate(
        primary_docket="24-101",
        consolidated_dockets=("24-102",),
        transcript=transcript("24-101"),
        docket_documents=(docket_document("24-101"), docket_document("24-102")),
    )
    case_id = deterministic_case_id("2025", "24-101")
    first = deterministic_argument_id(case_id, consolidated.argument_date)
    reargued = deterministic_argument_id(
        case_id,
        consolidated.argument_date + timedelta(days=30),
        sequence=2,
        reargument=True,
    )
    assert first != reargued
    assert case_id == deterministic_case_id("2025", "24-101")


def test_backfill_is_bounded_checkpointed_and_uses_low_priority() -> None:
    coordinator, store = setup()
    candidates = tuple(
        candidate(
            primary_docket=f"25-{number}",
            caption=f"Case {number}",
            transcript=transcript(f"25-{number}"),
            docket_documents=(docket_document(f"25-{number}"),),
            official_detail_url=(
                "https://www.supremecourt.gov/oral_arguments/audio/2025/"
                f"25-{number}"
            ),
        )
        for number in (1, 2, 3)
    )
    result = coordinator.backfill(
        "2025", candidates, NOW, case_limit=2, priority=100
    )
    assert result.cases_created == 2
    assert result.transcript_jobs == 2
    assert {job.priority for job in store.jobs} == {100}
    checkpoint = store.get_backfill_checkpoint("2025")
    assert checkpoint.examined == 2
    assert checkpoint.queued == 2
    assert not checkpoint.complete
