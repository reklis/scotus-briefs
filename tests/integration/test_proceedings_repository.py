from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.proceedings.contracts import (
    DocumentType,
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
    DocumentDescriptor,
    MediaDescriptor,
    PostgresDiscoveryStore,
    SourcePollResult,
)
from ragchew.proceedings.registry import PostgresSourceRegistry
from ragchew.scotus.contracts import (
    LegalCertainty,
    LegalObservationType,
    LegalStatus,
    ParseStatus,
    ScotusDocumentKind,
    SpeakerIdentityBasis,
    SpeakerKind,
    TranscriptLine,
    TranscriptTurn,
)
from ragchew.scotus.correlation import (
    PostgresScotusCorrelationStore,
    ScotusCorrelationEngine,
)
from ragchew.scotus.discovery import ScotusArgumentCandidate, ScotusDiscoveryCoordinator
from ragchew.scotus.discovery_store import PostgresScotusDiscoveryStore
from ragchew.scotus.documents import AcceptedDocument, PostgresDocumentIngestionStore
from ragchew.scotus.extraction import (
    LegalEvidenceBlock,
    LegalExtractionBatch,
    LegalExtractionInput,
    LegalExtractionService,
    PostgresLegalObservationStore,
    ProposedEvidence,
    ProposedLegalObservation,
)
from ragchew.scotus.transcript_parser import TranscriptParseResult
from ragchew.scotus.transcript_store import PostgresTranscriptParseStore

DSN = os.getenv("RAGCHEW_TEST_DATABASE_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="RAGCHEW_TEST_DATABASE_DSN is not configured")
NOW = datetime(2026, 9, 1, 14, tzinfo=UTC)


@pytest.fixture()
def pool() -> ConnectionPool:  # type: ignore[type-arg]
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        for migration in (
            "migrations/001_initial.sql",
            "migrations/002_roles.sql",
            "migrations/003_proceedings.sql",
            "migrations/004_scotus_legal_briefs.sql",
            "migrations/005_scotus_whole_case_briefs.sql",
            "migrations/006_scotus_generation_cost_controls.sql",
        ):
            connection.execute(Path(migration).read_text())
    result = ConnectionPool(conninfo=DSN, kwargs={"row_factory": dict_row}, open=True)
    yield result
    result.close()


def source(*, approved: bool) -> OfficialSource:
    return OfficialSource(
        source_id="supreme_court",
        authority=GovernmentAuthority.US_SUPREME_COURT,
        jurisdiction=Jurisdiction.FEDERAL,
        display_name="Supreme Court",
        official_index_url="https://www.supremecourt.gov/oral_arguments/",
        adapter="supreme_court",
        discovery_method=(
            SourceAccessMethod.OFFICIAL_PAGE if approved else SourceAccessMethod.NONE
        ),
        media_method=(
            SourceAccessMethod.DOWNLOADABLE_FILE if approved else SourceAccessMethod.NONE
        ),
        access_basis="reviewed official access" if approved else None,
        access_reviewed_at=NOW if approved else None,
        access_reviewed_by="reviewer@example.test" if approved else None,
        access_review_expires_at=NOW + timedelta(days=365) if approved else None,
        allowed_hosts=("www.supremecourt.gov",),
        poll_interval_seconds=60,
        expected_schedule="term calendar",
        enabled=approved,
        health=SourceHealth.HEALTHY if approved else SourceHealth.DISABLED,
    )


class Adapter:
    source_id = "supreme_court"

    def __init__(self) -> None:
        self.calls = 0

    def poll(self, conditional: ConditionalRequest) -> SourcePollResult:
        self.calls += 1
        if self.calls > 1:
            return SourcePollResult(
                source_id=self.source_id,
                endpoint_url="https://www.supremecourt.gov/oral_arguments/",
                access_method=SourceAccessMethod.OFFICIAL_PAGE,
                retrieved_at=NOW + timedelta(minutes=1),
                not_modified=True,
                etag='"v1"',
            )
        media = MediaDescriptor(
            external_id="24-123-audio",
            kind=MediaKind.ARCHIVE,
            source_url="https://www.supremecourt.gov/media/audio.mp3",
            access_method=SourceAccessMethod.DOWNLOADABLE_FILE,
            content_type="audio/mpeg",
        )
        item = DiscoveredProceeding(
            external_id="24-123",
            proceeding_type=ProceedingType.ORAL_ARGUMENT,
            title="Example v. Example",
            official_url="https://www.supremecourt.gov/oral_arguments/",
            lifecycle=ProceedingLifecycle.ARCHIVE_PENDING,
            scheduled_start_at=NOW,
            media=(media,),
        )
        return SourcePollResult(
            source_id=self.source_id,
            endpoint_url="https://www.supremecourt.gov/oral_arguments/",
            access_method=SourceAccessMethod.OFFICIAL_PAGE,
            retrieved_at=NOW,
            proceedings=(item,),
            etag='"v1"',
        )


def test_source_approval_is_audited_and_unreviewed_source_remains_disabled(
    pool: ConnectionPool,  # type: ignore[type-arg]
) -> None:
    registry = PostgresSourceRegistry("", pool=pool)
    registry.register(source(approved=False), "initial disabled configuration")
    with pool.connection() as connection:
        count = connection.execute(
            "SELECT count(*) AS count FROM official_source_approval_history"
        ).fetchone()["count"]
    assert count == 0

    registry.register(source(approved=True), "automation and reuse reviewed")
    saved = registry.get("supreme_court")
    assert saved is not None and saved.enabled
    with pool.connection() as connection:
        count = connection.execute(
            "SELECT count(*) AS count FROM official_source_approval_history"
        ).fetchone()["count"]
    assert count == 1


def test_postgres_discovery_is_idempotent_and_enqueues_one_collection_job(
    pool: ConnectionPool,  # type: ignore[type-arg]
) -> None:
    registry = PostgresSourceRegistry("", pool=pool)
    registry.register(source(approved=True), "automation and reuse reviewed")
    store = PostgresDiscoveryStore("", pool=pool)
    adapter = Adapter()
    coordinator = DiscoveryCoordinator(registry, store, {"supreme_court": adapter})

    first = coordinator.poll("supreme_court", NOW)
    second = coordinator.poll("supreme_court", NOW + timedelta(minutes=1))
    assert (first.discovered, first.revisions, first.collection_jobs) == (1, 1, 1)
    assert (second.discovered, second.revisions, second.collection_jobs) == (0, 0, 0)
    with pool.connection() as connection:
        counts = connection.execute(
            """SELECT
                 (SELECT count(*) FROM proceedings) AS proceedings,
                 (SELECT count(*) FROM proceeding_revisions) AS revisions,
                 (SELECT count(*) FROM jobs WHERE stage='collect') AS jobs"""
        ).fetchone()
    assert counts == {"proceedings": 1, "revisions": 1, "jobs": 1}


def test_scotus_transcript_discovery_persists_one_priority_job(
    pool: ConnectionPool,  # type: ignore[type-arg]
) -> None:
    registry = PostgresSourceRegistry("", pool=pool)
    registry.register(source(approved=True), "automation and reuse reviewed")
    store = PostgresScotusDiscoveryStore("", pool=pool)
    coordinator = ScotusDiscoveryCoordinator(registry, store)
    transcript = DocumentDescriptor(
        external_id="2025:25-466:transcript:25-466_ec8f.pdf",
        document_type=DocumentType.OFFICIAL_TRANSCRIPT,
        official_url=(
            "https://www.supremecourt.gov/oral_arguments/argument_transcripts/"
            "2025/25-466_ec8f.pdf"
        ),
        access_method=SourceAccessMethod.OFFICIAL_PAGE,
        content_type="application/pdf",
    )
    candidate = ScotusArgumentCandidate(
        term="2025",
        primary_docket="25-466",
        caption="Sripetch v. SEC",
        argument_date=NOW,
        official_detail_url="https://www.supremecourt.gov/oral_arguments/audio/2025/25-466",
        transcript=transcript,
    )
    first = coordinator.apply(candidate, NOW, priority=10)
    duplicate = coordinator.apply(candidate, NOW + timedelta(minutes=15), priority=10)
    assert first.transcript_jobs == 1
    assert duplicate.transcript_jobs == 0
    with pool.connection() as connection:
        counts = connection.execute(
            """SELECT
                 (SELECT count(*) FROM scotus_cases) AS cases,
                 (SELECT count(*) FROM scotus_argument_sessions) AS arguments,
                 (SELECT count(*) FROM scotus_document_revisions) AS documents,
                 (SELECT count(*) FROM jobs WHERE input_kind='scotus_document') AS jobs,
                 (SELECT min(priority) FROM jobs WHERE input_kind='scotus_document') AS priority"""
        ).fetchone()
    assert counts == {"cases": 1, "arguments": 1, "documents": 1, "jobs": 1, "priority": 10}


def test_scotus_document_parse_is_immutable_and_private_text_expires(
    pool: ConnectionPool,  # type: ignore[type-arg]
) -> None:
    registry = PostgresSourceRegistry("", pool=pool)
    registry.register(source(approved=True), "automation and reuse reviewed")
    discovery_store = PostgresScotusDiscoveryStore("", pool=pool)
    coordinator = ScotusDiscoveryCoordinator(registry, discovery_store)
    transcript = DocumentDescriptor(
        external_id="2025:25-466:transcript:25-466_ec8f.pdf",
        document_type=DocumentType.OFFICIAL_TRANSCRIPT,
        official_url=(
            "https://www.supremecourt.gov/oral_arguments/argument_transcripts/"
            "2025/25-466_ec8f.pdf"
        ),
        access_method=SourceAccessMethod.OFFICIAL_PAGE,
        content_type="application/pdf",
    )
    candidate = ScotusArgumentCandidate(
        term="2025",
        primary_docket="25-466",
        caption="Sripetch v. SEC",
        argument_date=NOW,
        official_detail_url="https://www.supremecourt.gov/oral_arguments/audio/2025/25-466",
        transcript=transcript,
    )
    coordinator.apply(candidate, NOW, priority=10)
    with pool.connection() as connection:
        row = connection.execute(
            """SELECT document_revision_id,case_id,argument_id
               FROM scotus_document_revisions"""
        ).fetchone()
    document_store = PostgresDocumentIngestionStore("", pool=pool)
    accepted = AcceptedDocument(
        document_revision_id=row["document_revision_id"],
        case_id=row["case_id"],
        kind=ScotusDocumentKind.TRANSCRIPT,
        external_id=transcript.external_id,
        revision_number=1,
        official_url=transcript.official_url,
        content_type="application/pdf",
        byte_count=100,
        sha256="a" * 64,
        object_key="official/us_supreme_court/test.pdf",
        page_count=1,
        ready_at=NOW,
    )
    assert document_store.commit(accepted, NOW + timedelta(hours=1), priority=10)
    parse_id = uuid4()
    line = TranscriptLine(
        parse_revision_id=parse_id,
        document_revision_id=accepted.document_revision_id,
        file_page=1,
        printed_page=1,
        line_number=1,
        raw_text_private="JUSTICE KAGAN: What is your rule?",
        normalized_text_private="JUSTICE KAGAN: What is your rule?",
    )
    turn = TranscriptTurn(
        parse_revision_id=parse_id,
        document_revision_id=accepted.document_revision_id,
        sequence=0,
        start_file_page=1,
        start_line=1,
        end_file_page=1,
        end_line=1,
        speaker_label_private="JUSTICE KAGAN",
        speaker_name="Justice Kagan",
        speaker_kind=SpeakerKind.JUSTICE,
        identity_basis=SpeakerIdentityBasis.OFFICIAL_TRANSCRIPT_LABEL,
        text_private="What is your rule?",
        confidence=1,
    )
    parsed = TranscriptParseResult(
        parse_revision_id=parse_id,
        document_revision_id=accepted.document_revision_id,
        parser_name="fixture",
        parser_version="1",
        config_hash="b" * 64,
        status=ParseStatus.COMPLETE,
        page_count=1,
        line_coverage=1,
        lines=(line,),
        turns=(turn,),
    )
    parse_store = PostgresTranscriptParseStore("", pool=pool)
    assert parse_store.save(parsed, retention_days=1)
    assert not parse_store.save(parsed, retention_days=1)

    evidence_block = LegalEvidenceBlock(
        block_id="turn-1",
        document_revision_id=accepted.document_revision_id,
        document_kind=ScotusDocumentKind.TRANSCRIPT,
        official_url=accepted.official_url,
        start_file_page=1,
        start_line=1,
        end_file_page=1,
        end_line=1,
        text_private="What is your rule?",
        speaker_name="Justice Kagan",
        speaker_kind=SpeakerKind.JUSTICE,
        identity_basis=SpeakerIdentityBasis.OFFICIAL_TRANSCRIPT_LABEL,
        attribution="Justice Kagan",
    )
    proposed = ProposedLegalObservation(
        observation_type=LegalObservationType.JUSTICE_QUESTION,
        legal_status=LegalStatus.QUESTIONED,
        certainty=LegalCertainty.ATTRIBUTED,
        raw_value="Justice Kagan asked what rule counsel proposed.",
        speaker_name="Justice Kagan",
        speaker_kind=SpeakerKind.JUSTICE,
        identity_basis=SpeakerIdentityBasis.OFFICIAL_TRANSCRIPT_LABEL,
        confidence=1,
        evidence=(ProposedEvidence(block_id="turn-1", quote="What is your rule?"),),
    )

    class Extractor:
        model_name = "fixture"
        PROMPT_VERSION = "fixture-v1"

        def extract(self, source: LegalExtractionInput) -> LegalExtractionBatch:
            return LegalExtractionBatch(observations=[proposed])

    extraction_input = LegalExtractionInput(
        case_id=row["case_id"],
        argument_id=row["argument_id"],
        blocks=(evidence_block,),
        parser_versions=("fixture:1",),
        document_revision_ids=(accepted.document_revision_id,),
    )
    extraction_store = PostgresLegalObservationStore("", pool=pool)
    extraction_service = LegalExtractionService(Extractor(), extraction_store)
    first_observations = extraction_service.process(extraction_input)
    retried_observations = extraction_service.process(extraction_input)
    assert [item.observation_id for item in retried_observations] == [
        item.observation_id for item in first_observations
    ]
    with pool.connection() as connection:
        extraction_id = connection.execute(
            "SELECT extraction_revision_id FROM scotus_extraction_revisions"
        ).fetchone()["extraction_revision_id"]
    correlation_store = PostgresScotusCorrelationStore("", pool=pool)
    engine = ScotusCorrelationEngine()
    first_correlation = correlation_store.correlate_extraction(extraction_id, engine, NOW)
    retried_correlation = correlation_store.correlate_extraction(extraction_id, engine, NOW)
    assert first_correlation is not None and retried_correlation is not None
    assert first_correlation.aggregate == retried_correlation.aggregate
    with pool.connection() as connection:
        correlated_counts = connection.execute(
            """SELECT
                 (SELECT count(*) FROM scotus_case_observations) AS case_links,
                 (SELECT count(*) FROM scotus_issue_observations) AS issue_links,
                 (SELECT count(*) FROM scotus_case_history) AS history"""
        ).fetchone()
    assert correlated_counts == {"case_links": 1, "issue_links": 1, "history": 1}
    assert parse_store.expire_extracted_text(NOW + timedelta(days=2)) == 1
    with pool.connection() as connection:
        values = connection.execute(
            """SELECT l.raw_text_private,t.text_private,a.status::text
               FROM scotus_transcript_lines l
               JOIN scotus_transcript_turns t ON t.parse_revision_id=l.parse_revision_id
               JOIN scotus_document_revisions d
                 ON d.document_revision_id=l.document_revision_id
               JOIN scotus_argument_sessions a ON a.argument_id=d.argument_id"""
        ).fetchone()
    assert values == {
        "raw_text_private": None,
        "text_private": None,
        "status": "transcript_ready",
    }
