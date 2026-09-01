from datetime import UTC, datetime, timedelta

import pytest

from ragchew.proceedings.contracts import (
    GovernmentAuthority,
    Jurisdiction,
    MediaKind,
    OfficialSource,
    ProceedingLifecycle,
    ProceedingType,
    SourceAccessMethod,
    SourceHealth,
)
from ragchew.proceedings.discovery import (
    ConditionalRequest,
    DiscoveredProceeding,
    DiscoveryCoordinator,
    InMemoryDiscoveryStore,
    MediaDescriptor,
    SourcePollResult,
)
from ragchew.proceedings.registry import (
    InMemorySourceRegistry,
    SourceAuthorizationError,
    SourceAuthorizer,
)

NOW = datetime(2026, 9, 1, 14, tzinfo=UTC)


def approved_source(**overrides: object) -> OfficialSource:
    values: dict[str, object] = {
        "source_id": "supreme_court",
        "authority": GovernmentAuthority.US_SUPREME_COURT,
        "jurisdiction": Jurisdiction.FEDERAL,
        "display_name": "Supreme Court",
        "official_index_url": "https://www.supremecourt.gov/oral_arguments/",
        "adapter": "supreme_court",
        "discovery_method": SourceAccessMethod.OFFICIAL_PAGE,
        "media_method": SourceAccessMethod.DOWNLOADABLE_FILE,
        "access_basis": "Reviewed official page and download access",
        "access_reviewed_at": NOW - timedelta(days=1),
        "access_reviewed_by": "reviewer@example.test",
        "access_review_expires_at": NOW + timedelta(days=365),
        "allowed_hosts": ("www.supremecourt.gov",),
        "poll_interval_seconds": 60,
        "expected_schedule": "Term calendar",
        "enabled": True,
        "health": SourceHealth.HEALTHY,
    }
    values.update(overrides)
    return OfficialSource.model_validate(values)


def proceeding(**overrides: object) -> DiscoveredProceeding:
    values: dict[str, object] = {
        "external_id": "24-123",
        "proceeding_type": ProceedingType.ORAL_ARGUMENT,
        "title": "Example v. Example",
        "official_url": "https://www.supremecourt.gov/oral_arguments/argument_transcripts/",
        "lifecycle": ProceedingLifecycle.SCHEDULED,
        "scheduled_start_at": NOW + timedelta(days=1),
        "source_updated_at": NOW,
    }
    values.update(overrides)
    return DiscoveredProceeding.model_validate(values)


class FakeAdapter:
    source_id = "supreme_court"

    def __init__(self, results: list[SourcePollResult | Exception]):
        self.results = results
        self.conditionals: list[ConditionalRequest] = []

    def poll(self, conditional: ConditionalRequest) -> SourcePollResult:
        self.conditionals.append(conditional)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def result(*items: DiscoveredProceeding, **overrides: object) -> SourcePollResult:
    values: dict[str, object] = {
        "source_id": "supreme_court",
        "endpoint_url": "https://www.supremecourt.gov/oral_arguments/",
        "access_method": SourceAccessMethod.OFFICIAL_PAGE,
        "retrieved_at": NOW,
        "proceedings": items,
        "etag": '"calendar-v1"',
    }
    values.update(overrides)
    return SourcePollResult.model_validate(values)


def setup(
    *results: SourcePollResult | Exception, source: OfficialSource | None = None
) -> tuple[DiscoveryCoordinator, InMemorySourceRegistry, InMemoryDiscoveryStore, FakeAdapter]:
    registry = InMemorySourceRegistry()
    registry.register(source or approved_source(), "initial review")
    store = InMemoryDiscoveryStore()
    adapter = FakeAdapter(list(results))
    coordinator = DiscoveryCoordinator(registry, store, {"supreme_court": adapter})
    return coordinator, registry, store, adapter


def test_disabled_and_expired_sources_fail_closed() -> None:
    disabled = approved_source(enabled=False, health=SourceHealth.DISABLED)
    registry = InMemorySourceRegistry()
    registry.register(disabled, "configured but not reviewed for launch")
    authorizer = SourceAuthorizer(registry)
    with pytest.raises(SourceAuthorizationError, match="disabled"):
        authorizer.require_source(disabled.source_id, NOW)

    expired = approved_source(access_review_expires_at=NOW)
    registry.register(expired, "review expired")
    with pytest.raises(SourceAuthorizationError, match="expired"):
        authorizer.require_source(expired.source_id, NOW)
    registered = registry.get(expired.source_id)
    assert registered is not None
    assert registered.health is SourceHealth.REVIEW_REQUIRED


def test_changed_method_and_redirect_host_require_review() -> None:
    registry = InMemorySourceRegistry()
    registry.register(approved_source(), "initial review")
    authorizer = SourceAuthorizer(registry)
    with pytest.raises(SourceAuthorizationError, match="method"):
        authorizer.authorize_url(
            "supreme_court",
            "https://www.supremecourt.gov/audio.mp3",
            SourceAccessMethod.OFFICIAL_HLS,
            NOW,
            media=True,
        )
    with pytest.raises(SourceAuthorizationError, match="allowlist"):
        authorizer.authorize_url(
            "supreme_court",
            "https://video-platform.example/audio.mp3",
            SourceAccessMethod.DOWNLOADABLE_FILE,
            NOW,
            media=True,
        )
    registered = registry.get("supreme_court")
    assert registered is not None
    assert registered.health is SourceHealth.REVIEW_REQUIRED


def test_discovery_is_idempotent_and_uses_conditional_requests() -> None:
    first = result(proceeding())
    second = result(not_modified=True, etag='"calendar-v1"')
    coordinator, _, store, adapter = setup(first, second)
    initial = coordinator.poll("supreme_court", NOW)
    duplicate = coordinator.poll("supreme_court", NOW + timedelta(seconds=60))
    assert (initial.discovered, initial.revisions) == (1, 1)
    assert (duplicate.discovered, duplicate.revisions) == (0, 0)
    assert len(store.proceedings) == 1
    assert len(store.revisions[("supreme_court", "24-123")]) == 1
    assert adapter.conditionals[1].etag == '"calendar-v1"'


def test_schedule_change_appends_revision_without_duplicate_proceeding() -> None:
    changed = proceeding(
        scheduled_start_at=NOW + timedelta(days=1, hours=1),
        source_updated_at=NOW + timedelta(hours=1),
    )
    coordinator, _, store, _ = setup(
        result(proceeding()),
        result(changed, retrieved_at=NOW + timedelta(hours=1), etag='"calendar-v2"'),
    )
    coordinator.poll("supreme_court", NOW)
    outcome = coordinator.poll("supreme_court", NOW + timedelta(hours=1))
    assert outcome.discovered == 0
    assert outcome.revisions == 1
    assert len(store.proceedings) == 1
    assert len(store.revisions[("supreme_court", "24-123")]) == 2


def test_cancelled_proceeding_creates_no_media_job() -> None:
    media = MediaDescriptor(
        external_id="argument-live",
        kind=MediaKind.LIVE,
        source_url="https://www.supremecourt.gov/audio.mp3",
        access_method=SourceAccessMethod.DOWNLOADABLE_FILE,
        content_type="audio/mpeg",
    )
    coordinator, _, store, _ = setup(
        result(proceeding(lifecycle=ProceedingLifecycle.CANCELLED, media=(media,)))
    )
    outcome = coordinator.poll("supreme_court", NOW)
    assert outcome.discovered == 1
    assert outcome.collection_jobs == 0
    assert store.jobs == set()


def test_unapproved_platform_embed_keeps_metadata_but_enqueues_no_media() -> None:
    embed = MediaDescriptor(
        external_id="platform-embed",
        kind=MediaKind.LIVE,
        source_url="https://platform.example/watch/123",
        access_method=SourceAccessMethod.DOWNLOADABLE_FILE,
        content_type="video/mp4",
    )
    coordinator, registry, store, _ = setup(result(proceeding(media=(embed,))))
    with pytest.raises(SourceAuthorizationError, match="allowlist"):
        coordinator.poll("supreme_court", NOW)
    assert ("supreme_court", "24-123") in store.proceedings
    assert store.jobs == set()
    registered = registry.get("supreme_court")
    assert registered is not None
    assert registered.health is SourceHealth.REVIEW_REQUIRED


def test_quiet_success_is_distinct_from_endpoint_failure_and_backoff() -> None:
    coordinator, registry, store, _ = setup(
        result(quiet=True), RuntimeError("endpoint timeout")
    )
    quiet = coordinator.poll("supreme_court", NOW)
    assert quiet.health is SourceHealth.QUIET
    assert store.get_poll_state("supreme_court").last_success_at == NOW

    failed = coordinator.poll("supreme_court", NOW + timedelta(seconds=60))
    assert failed.health is SourceHealth.DEGRADED
    assert failed.retry_after_seconds == 120
    registered = registry.get("supreme_court")
    assert registered is not None
    assert registered.health is SourceHealth.DEGRADED
    skipped = coordinator.poll("supreme_court", NOW + timedelta(seconds=90))
    assert not skipped.attempted
    assert skipped.retry_after_seconds == 90


def test_empty_poll_does_not_infer_proceeding_completion() -> None:
    coordinator, _, store, _ = setup(result(proceeding()), result())
    coordinator.poll("supreme_court", NOW)
    coordinator.poll("supreme_court", NOW + timedelta(seconds=60))
    saved = store.get_proceeding("supreme_court", "24-123")
    assert saved is not None
    assert saved.lifecycle is ProceedingLifecycle.SCHEDULED
