"""Durable Kubernetes analysis worker entrypoint."""

from __future__ import annotations

import logging
import os
import socket
import time
from uuid import UUID

import httpx
from openai import OpenAI
from prometheus_client import start_http_server

from ragchew.analysis.audio import PrivateAudioRetriever
from ragchew.analysis.extraction import ExtractionService, OpenAIObservationExtractor
from ragchew.analysis.hints import HintSet
from ragchew.analysis.observation_store import PostgresObservationStore
from ragchew.analysis.store import PostgresTranscriptStore
from ragchew.analysis.stt import FasterWhisperAdapter, TranscriptionService
from ragchew.config import MvpConfig, ServiceSettings
from ragchew.contracts import JobStage
from ragchew.correlation.engine import CorrelationEngine, CorrelationRules
from ragchew.correlation.store import PostgresIncidentStore
from ragchew.jobs import claim
from ragchew.logging_config import configure_logging
from ragchew.metrics import (
    EXTRACTION_FAILURES,
    INCIDENT_STATE_EVENTS,
    JOB_BACKLOG,
    JOB_DURATION,
    JOB_OUTCOMES,
    STT_DURATION,
)
from ragchew.repository import PostgresRepository
from ragchew.storage import S3ObjectStore

LOG = logging.getLogger("ragchew.worker")


def main() -> None:
    configure_logging(os.getenv("RAGCHEW_LOG_LEVEL", "INFO"))
    settings = ServiceSettings()
    config = MvpConfig.from_yaml(settings.config_path)
    repository = PostgresRepository(settings.database_dsn)
    objects = S3ObjectStore(settings)
    transcript_store = PostgresTranscriptStore("", pool=repository.pool)
    observation_store = PostgresObservationStore("", pool=repository.pool)
    incident_store = PostgresIncidentStore("", pool=repository.pool)
    stt = TranscriptionService(
        PrivateAudioRetriever(objects, client=httpx.Client(timeout=60)),
        FasterWhisperAdapter(settings.stt_model),
        transcript_store,
        HintSet.load("resources/dcfd-hints-v1.yaml"),
        config.retention.transcript_days,
    )
    llm = OpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key.get_secret_value(),
    )
    extraction = ExtractionService(
        OpenAIObservationExtractor(settings.llm_model, llm), observation_store
    )
    correlation = CorrelationEngine(
        CorrelationRules(
            lookback_hours=config.publication.lookback_hours,
            publishable_threshold=config.publication.minimum_confidence,
        )
    )
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    start_http_server(int(os.getenv("RAGCHEW_METRICS_PORT", "9090")))

    while True:
        backlog = repository.job_backlog()
        for job_stage in JobStage:
            JOB_BACKLOG.labels(job_stage.value).set(backlog.get(job_stage.value, 0))
        lease = claim(repository, worker_id, config)
        if lease is None:
            time.sleep(1)
            continue
        stage = lease.record.stage
        started = time.monotonic()
        try:
            if stage == JobStage.TRANSCRIBE:
                capture_source = transcript_store.get_capture(lease.record.input_id)
                if capture_source is None:
                    raise ValueError("ready source capture not found")
                stt_started = time.monotonic()
                transcript_output = stt.process(capture_source)
                STT_DURATION.observe(time.monotonic() - stt_started)
                output_id = str(transcript_output.revision_id)
            elif stage == JobStage.EXTRACT:
                transcript_source = observation_store.get_transcript(
                    UUID(lease.record.input_id)
                )
                if transcript_source is None:
                    raise ValueError("complete transcript revision not found")
                observations = extraction.process(transcript_source)
                output_id = (
                    str(observations[0].observation_id) if observations else "no-observations"
                )
            elif stage == JobStage.CORRELATE:
                incident_output = incident_store.correlate_extraction(
                    UUID(lease.record.input_id), correlation
                )
                output_id = (
                    str(incident_output.incident.incident_id)
                    if incident_output
                    else "routine"
                )
                if incident_output:
                    INCIDENT_STATE_EVENTS.labels(
                        incident_output.incident.state.value
                    ).inc()
            else:
                raise ValueError(f"unsupported worker stage: {stage}")
            lease.complete(output_id)
            JOB_OUTCOMES.labels(stage, "complete").inc()
        except Exception as error:
            LOG.exception(
                "job failed",
                extra={"stage": stage, "job_id": lease.record.job_id, "outcome": "failed"},
            )
            lease.fail(error)
            JOB_OUTCOMES.labels(stage, "failed").inc()
            if stage == JobStage.EXTRACT:
                EXTRACTION_FAILURES.inc()
        finally:
            JOB_DURATION.labels(stage).observe(time.monotonic() - started)
