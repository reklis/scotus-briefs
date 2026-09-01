"""SCOTUS document, parser, extraction, and correlation worker entrypoint."""

from __future__ import annotations

import logging
import os
import re
import socket
import tempfile
import time
from datetime import UTC, datetime
from typing import BinaryIO, cast
from uuid import UUID, uuid4

import httpx
from openai import OpenAI
from prometheus_client import start_http_server
from pypdf import PdfReader

from ragchew.config import MvpConfig, ScotusConfig, ServiceSettings
from ragchew.jobs import claim
from ragchew.logging_config import configure_logging
from ragchew.metrics import (
    JOB_BACKLOG,
    JOB_DURATION,
    JOB_OUTCOMES,
    SCOTUS_CASE_STATE_EVENTS,
    SCOTUS_DOCUMENT_OUTCOMES,
    SCOTUS_EXTRACTION_OUTCOMES,
    SCOTUS_PARSER_OUTCOMES,
)
from ragchew.proceedings.registry import PostgresSourceRegistry, SourceAuthorizer
from ragchew.repository import PostgresRepository
from ragchew.scotus.contracts import (
    AdvocateRole,
    ScotusDocumentKind,
    SpeakerIdentityBasis,
    SpeakerKind,
    TranscriptTurn,
)
from ragchew.scotus.correlation import PostgresScotusCorrelationStore, ScotusCorrelationEngine
from ragchew.scotus.documents import (
    PendingDocument,
    PostgresDocumentIngestionStore,
    ScotusDocumentCollector,
)
from ragchew.scotus.extraction import (
    DeterministicTranscriptObservationExtractor,
    LegalExtractionInput,
    LegalExtractionService,
    LegalObservationExtractor,
    OpenAILegalObservationExtractor,
    PostgresLegalObservationStore,
    bounded_contexts,
    transcript_turn_block,
)
from ragchew.scotus.transcript_parser import PypdfTextBackend, ScotusTranscriptParser
from ragchew.scotus.transcript_store import PostgresTranscriptParseStore
from ragchew.storage import S3ObjectStore

LOG = logging.getLogger("ragchew.scotus.worker")


def _pending_document(repository: PostgresRepository, revision_id: UUID) -> PendingDocument:
    with repository.pool.connection() as connection:
        row = connection.execute(
            """SELECT document_revision_id,case_id,argument_id,document_kind,external_id,
                      revision_number,official_url_private,content_type,observed_at
               FROM scotus_document_revisions WHERE document_revision_id=%s""",
            (revision_id,),
        ).fetchone()
    if row is None:
        raise ValueError("SCOTUS document revision not found")
    return PendingDocument(
        document_revision_id=row["document_revision_id"],
        case_id=row["case_id"],
        argument_id=row["argument_id"],
        kind=ScotusDocumentKind(row["document_kind"]),
        external_id=row["external_id"],
        revision_number=row["revision_number"],
        official_url=row["official_url_private"],
        expected_content_type=row["content_type"],
        observed_at=row["observed_at"],
    )


def _download_private(objects: S3ObjectStore, key: str, maximum: int) -> BinaryIO:
    url = objects.create_download(key, expires_seconds=300)
    file = cast(
        BinaryIO,
        tempfile.SpooledTemporaryFile(  # noqa: SIM115 - returned to parser caller
            max_size=8 * 1024 * 1024
        ),
    )
    received = 0
    with httpx.stream("GET", url, timeout=60) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            received += len(chunk)
            if received > maximum:
                file.close()
                raise ValueError("private document exceeds configured parser bound")
            file.write(chunk)
    file.seek(0)
    return file


def _opinion_names_docket(text: str, docket: str) -> bool:
    normalized = " ".join(
        text.replace("\N{EN DASH}", "-")
        .replace("\N{EM DASH}", "-")
        .upper()
        .split()
    )
    expected = " ".join(docket.upper().split())
    return (
        re.search(rf"(?<![0-9A-Z]){re.escape(expected)}(?![0-9A-Z])", normalized)
        is not None
    )


def _parse_document(
    repository: PostgresRepository,
    objects: S3ObjectStore,
    config: ScotusConfig,
    revision_id: UUID,
) -> UUID:
    with repository.pool.connection() as connection:
        row = connection.execute(
            """SELECT d.object_key,d.document_kind,d.status::text,d.case_id,
                      c.primary_docket
               FROM scotus_document_revisions d JOIN scotus_cases c USING(case_id)
               WHERE d.document_revision_id=%s""",
            (revision_id,),
        ).fetchone()
    if row is None or row["status"] not in {"ready", "parsed"} or not row["object_key"]:
        raise ValueError("ready private SCOTUS document not found")
    kind = ScotusDocumentKind(row["document_kind"])
    if kind is not ScotusDocumentKind.TRANSCRIPT:
        if kind is ScotusDocumentKind.OPINION:
            file = _download_private(
                objects, row["object_key"], config.documents.maximum_pdf_bytes
            )
            try:
                reader = PdfReader(file, strict=False)
                if reader.is_encrypted:
                    reader.decrypt("")
                text = " ".join(
                    (page.extract_text() or "") for page in reader.pages[:6]
                )
            finally:
                file.close()
            if not _opinion_names_docket(text, str(row["primary_docket"])):
                with repository.pool.connection() as connection:
                    connection.execute(
                        """UPDATE scotus_document_revisions SET status='quarantined',
                                  canonical=false,diagnostic_private=%s
                           WHERE document_revision_id=%s""",
                        (
                            "official opinion text does not name the correlated docket",
                            revision_id,
                        ),
                    )
                    connection.commit()
                return revision_id
        if kind in {ScotusDocumentKind.OPINION, ScotusDocumentKind.ORDER}:
            with repository.pool.connection() as connection, connection.transaction():
                case = connection.execute(
                    "SELECT status::text FROM scotus_cases WHERE case_id=%s FOR UPDATE",
                    (row["case_id"],),
                ).fetchone()
                if case is None:
                    raise ValueError("official document has no case")
                prior_status = case["status"]
                new_status = (
                    "decided"
                    if kind is ScotusDocumentKind.OPINION
                    else ("decided" if prior_status == "decided" else "order_issued")
                )
                if new_status != prior_status:
                    connection.execute(
                        "UPDATE scotus_cases SET status=%s,updated_at=now() WHERE case_id=%s",
                        (new_status, row["case_id"]),
                    )
                    connection.execute(
                        """INSERT INTO scotus_case_history
                           (case_id,prior_status,new_status,reason,evidence_ids,
                            correlation_version)
                           VALUES (%s,%s,%s,%s,%s::jsonb,'official-document-v1')""",
                        (
                            row["case_id"],
                            prior_status,
                            new_status,
                            f"validated official {kind.value} document",
                            f'["{revision_id}"]',
                        ),
                    )
        return revision_id
    file = _download_private(objects, row["object_key"], config.documents.maximum_pdf_bytes)
    try:
        parse_id = uuid4()
        result = ScotusTranscriptParser(PypdfTextBackend(), config.parser).parse(
            file,
            parse_revision_id=parse_id,
            document_revision_id=revision_id,
        )
    finally:
        file.close()
    PostgresTranscriptParseStore("", pool=repository.pool).save(
        result, config.retention.extracted_text_days
    )
    return parse_id


def _extract_parse(
    repository: PostgresRepository,
    settings: ServiceSettings,
    config: ScotusConfig,
    parse_id: UUID,
    *,
    deterministic: bool = False,
) -> str:
    with repository.pool.connection() as connection:
        metadata = connection.execute(
            """SELECT d.case_id,d.argument_id,d.document_revision_id,d.official_url_private,
                      p.parser,p.parser_version
               FROM scotus_document_parses p
               JOIN scotus_document_revisions d USING(document_revision_id)
               WHERE p.parse_revision_id=%s AND p.status='complete'""",
            (parse_id,),
        ).fetchone()
        rows = connection.execute(
            """SELECT * FROM scotus_transcript_turns
               WHERE parse_revision_id=%s AND text_private IS NOT NULL ORDER BY sequence""",
            (parse_id,),
        ).fetchall()
    if metadata is None or not rows:
        raise ValueError("complete private transcript parse not found")
    turns = tuple(
        TranscriptTurn(
            turn_id=row["turn_id"],
            parse_revision_id=row["parse_revision_id"],
            document_revision_id=row["document_revision_id"],
            sequence=row["sequence"],
            start_file_page=row["start_file_page"],
            start_line=row["start_line"],
            end_file_page=row["end_file_page"],
            end_line=row["end_line"],
            speaker_label_private=row["speaker_label_private"],
            speaker_name=row["speaker_name"],
            speaker_kind=SpeakerKind(row["speaker_kind"]),
            advocate_role=(
                AdvocateRole(row["advocate_role"]) if row["advocate_role"] else None
            ),
            identity_basis=SpeakerIdentityBasis(row["identity_basis"]),
            text_private=row["text_private"],
            confidence=row["confidence"],
        )
        for row in rows
    )
    blocks = tuple(
        transcript_turn_block(turn, metadata["official_url_private"]) for turn in turns
    )
    batches = bounded_contexts(blocks, config.generation.maximum_context_characters)
    extractor: LegalObservationExtractor
    if deterministic:
        extractor = DeterministicTranscriptObservationExtractor()
    else:
        llm = OpenAI(api_key=settings.openai_api_key.get_secret_value())
        extractor = OpenAILegalObservationExtractor(config.generation.model, llm)
    store = PostgresLegalObservationStore("", pool=repository.pool)
    output_ids: list[str] = []
    for index, batch in enumerate(batches):
        source = LegalExtractionInput(
            case_id=metadata["case_id"],
            argument_id=metadata["argument_id"],
            blocks=batch,
            parser_versions=(
                f"{metadata['parser']}:{metadata['parser_version']}",
                f"window:{index}",
            ),
            document_revision_ids=(metadata["document_revision_id"],),
        )
        observations = LegalExtractionService(extractor, store).process(source)
        output_ids.extend(str(item.observation_id) for item in observations)
    return output_ids[0] if output_ids else "no-observations"


def main() -> None:
    configure_logging(os.getenv("RAGCHEW_LOG_LEVEL", "INFO"))
    settings = ServiceSettings()
    scotus = ScotusConfig.from_yaml(settings.scotus_config_path)
    retry = MvpConfig.from_yaml(settings.config_path)
    repository = PostgresRepository(settings.database_dsn)
    objects = S3ObjectStore(settings)
    registry = PostgresSourceRegistry("", pool=repository.pool)
    document_store = PostgresDocumentIngestionStore("", pool=repository.pool)
    collector = ScotusDocumentCollector(
        SourceAuthorizer(registry),
        document_store,
        objects,
        scotus,
        user_agent=settings.source_user_agent,
    )
    correlation_store = PostgresScotusCorrelationStore("", pool=repository.pool)
    correlation = ScotusCorrelationEngine()
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    configured_stages = tuple(
        value.strip()
        for value in os.getenv("RAGCHEW_SCOTUS_WORKER_STAGES", "").split(",")
        if value.strip()
    )
    allowed_stages = {"collect", "parse", "extract", "correlate", "policy"}
    if any(stage not in allowed_stages for stage in configured_stages):
        raise ValueError("RAGCHEW_SCOTUS_WORKER_STAGES contains an unsupported stage")
    stage_filter = configured_stages or None
    deterministic_extraction = os.getenv(
        "RAGCHEW_SCOTUS_PRIVATE_DETERMINISTIC_EXTRACTION", ""
    ).lower() in {"1", "true", "yes"}
    if deterministic_extraction and scotus.publication.enabled:
        raise ValueError("private deterministic extraction requires disabled publication")
    start_http_server(int(os.getenv("RAGCHEW_METRICS_PORT", "9090")))

    while True:
        backlog = repository.job_backlog()
        for stage, count in backlog.items():
            JOB_BACKLOG.labels(stage).set(count)
        lease = claim(repository, worker_id, retry, stages=stage_filter)
        if lease is None:
            time.sleep(1)
            continue
        stage = lease.record.stage
        started = time.monotonic()
        try:
            input_id = UUID(lease.record.input_id)
            if stage == "collect" and lease.record.input_kind == "scotus_document":
                pending = _pending_document(repository, input_id)
                result = collector.collect(pending, datetime.now(UTC))
                SCOTUS_DOCUMENT_OUTCOMES.labels(pending.kind.value, result.status).inc()
                if result.status == "failed":
                    raise ValueError(result.diagnostic or "document collection failed")
                output_id = str(result.document_revision_id)
            elif stage == "parse" and lease.record.input_kind == "scotus_document":
                output_id = str(_parse_document(repository, objects, scotus, input_id))
                SCOTUS_PARSER_OUTCOMES.labels("complete").inc()
            elif stage == "extract" and lease.record.input_kind == "scotus_transcript_parse":
                output_id = _extract_parse(
                    repository,
                    settings,
                    scotus,
                    input_id,
                    deterministic=deterministic_extraction,
                )
                SCOTUS_EXTRACTION_OUTCOMES.labels("complete").inc()
            elif stage == "correlate" and lease.record.input_kind == "scotus_extraction":
                correlated = correlation_store.correlate_extraction(
                    input_id, correlation, datetime.now(UTC)
                )
                output_id = str(correlated.aggregate.case_id) if correlated else "no-case"
                if correlated:
                    SCOTUS_CASE_STATE_EVENTS.labels(correlated.aggregate.status.value).inc()
            elif stage == "policy" and lease.record.input_kind == "scotus_case":
                output_id = "deferred-to-scotus-publisher"
            else:
                raise ValueError(
                    f"unsupported SCOTUS worker job: {stage}/{lease.record.input_kind}"
                )
            lease.complete(output_id)
            JOB_OUTCOMES.labels(stage, "complete").inc()
        except Exception as error:
            LOG.exception(
                "SCOTUS job failed",
                extra={"stage": stage, "job_id": lease.record.job_id, "outcome": "failed"},
            )
            lease.fail(error)
            JOB_OUTCOMES.labels(stage, "failed").inc()
            if stage == "parse":
                SCOTUS_PARSER_OUTCOMES.labels("failed").inc()
            if stage == "extract":
                SCOTUS_EXTRACTION_OUTCOMES.labels("failed").inc()
        finally:
            JOB_DURATION.labels(stage).observe(time.monotonic() - started)
