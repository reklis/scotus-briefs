"""One-shot SCOTUS private document and extracted-text retention command."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from ragchew.config import ServiceSettings
from ragchew.logging_config import configure_logging
from ragchew.metrics import SCOTUS_RETENTION_OUTCOMES
from ragchew.repository import PostgresRepository
from ragchew.scotus.transcript_store import PostgresTranscriptParseStore
from ragchew.storage import S3ObjectStore


def run_once(settings: ServiceSettings, now: datetime) -> dict[str, int]:
    repository = PostgresRepository(settings.database_dsn)
    store = PostgresTranscriptParseStore("", pool=repository.pool)
    objects = S3ObjectStore(settings)
    keys = store.expire_documents(now)
    deleted = 0
    for key in keys:
        objects.delete(key)
        deleted += 1
        SCOTUS_RETENTION_OUTCOMES.labels("document", "deleted").inc()
    text = store.expire_extracted_text(now)
    if text:
        SCOTUS_RETENTION_OUTCOMES.labels("extracted_text", "deleted").inc(text)
    repository.pool.close()
    return {"documents": deleted, "extracted_text_revisions": text}


def main() -> None:
    configure_logging(os.getenv("RAGCHEW_LOG_LEVEL", "INFO"))
    settings = ServiceSettings()
    run_once(settings, datetime.now(UTC))
