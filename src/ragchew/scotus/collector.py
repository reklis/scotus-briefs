"""One-shot reviewed Supreme Court transcript discovery command."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from ragchew.config import ProceedingsConfig, ScotusConfig, ServiceSettings
from ragchew.logging_config import configure_logging
from ragchew.metrics import SCOTUS_SOURCE_LAST_SUCCESS, SCOTUS_TRANSCRIPT_WAIT_AGE
from ragchew.proceedings.discovery import ConditionalRequest
from ragchew.proceedings.registry import PostgresSourceRegistry
from ragchew.proceedings.sources.http import HttpxSourceFetcher
from ragchew.proceedings.sources.supreme_court import SupremeCourtAdapter
from ragchew.scotus.discovery import ScotusDiscoveryCoordinator, candidate_from_proceeding
from ragchew.scotus.discovery_store import PostgresScotusDiscoveryStore

LOG = logging.getLogger("ragchew.scotus.collector")


def run_once(
    settings: ServiceSettings,
    scotus: ScotusConfig,
    proceedings: ProceedingsConfig,
    now: datetime,
) -> int:
    registry = PostgresSourceRegistry(settings.database_dsn)
    source = registry.get("supreme_court")
    if source is None:
        raise RuntimeError(
            "supreme_court registry entry must be provisioned by an access-review operator"
        )
    configured = proceedings.sources["supreme_court"]
    if source.adapter != configured.adapter:
        raise RuntimeError("Supreme Court registry adapter differs from reviewed configuration")
    if not source.enabled or not scotus.enabled:
        LOG.warning("SCOTUS discovery disabled pending launch approval")
        return 0
    store = PostgresScotusDiscoveryStore("", pool=registry.pool)
    coordinator = ScotusDiscoveryCoordinator(registry, store)
    fetcher = HttpxSourceFetcher(
        user_agent=settings.source_user_agent,
        maximum_bytes=scotus.documents.maximum_pdf_bytes,
        minimum_interval_seconds=scotus.discovery.crawl_delay_seconds,
    )
    queued = 0
    for term in scotus.discovery.terms:
        adapter = SupremeCourtAdapter(
            fetcher,
            term=term,
            clock=lambda: now,
            detail_lookback_days=scotus.discovery.backfill_lookback_days,
            maximum_detail_requests=scotus.discovery.backfill_case_limit,
            transcript_archive=True,
        )
        result = adapter.poll(ConditionalRequest())
        candidates = tuple(
            candidate_from_proceeding(item, term)
            for item in result.proceedings
            if item.scheduled_start_at is not None
        )
        pending_dates = [
            candidate.argument_date
            for candidate in candidates
            if candidate.transcript is None
        ]
        if pending_dates:
            SCOTUS_TRANSCRIPT_WAIT_AGE.set(
                max(0, (now - min(pending_dates)).total_seconds())
            )
        else:
            SCOTUS_TRANSCRIPT_WAIT_AGE.set(0)
        applied = coordinator.backfill(
            term,
            candidates,
            now,
            case_limit=scotus.discovery.backfill_case_limit,
            priority=scotus.discovery.backfill_priority,
        )
        queued += applied.transcript_jobs
    SCOTUS_SOURCE_LAST_SUCCESS.set(now.timestamp())
    LOG.info("SCOTUS discovery complete", extra={"outcome": "complete"})
    return queued


def main() -> None:
    configure_logging(os.getenv("RAGCHEW_LOG_LEVEL", "INFO"))
    settings = ServiceSettings()
    run_once(
        settings,
        ScotusConfig.from_yaml(settings.scotus_config_path),
        ProceedingsConfig.from_yaml(settings.proceedings_config_path),
        datetime.now(UTC),
    )
