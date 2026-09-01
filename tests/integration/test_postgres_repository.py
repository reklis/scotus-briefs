from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from ragchew.analysis.extraction import ExtractionService
from ragchew.analysis.observation_store import PostgresObservationStore
from ragchew.analysis.store import PostgresTranscriptStore
from ragchew.config import MvpConfig
from ragchew.contracts import TranscriptRevision, TranscriptStatus
from ragchew.correlation.engine import CorrelationEngine
from ragchew.correlation.store import PostgresIncidentStore
from ragchew.policy import PublicationPolicy
from ragchew.policy_store import PostgresPolicyStore
from ragchew.repository import PostgresRepository
from ragchew.scotus.publisher import _build_candidate, _candidate_rows
from tests.test_extraction import FakeExtractor
from tests.test_ingestion import envelope

DSN = os.getenv("RAGCHEW_TEST_DATABASE_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="RAGCHEW_TEST_DATABASE_DSN is not configured")


@pytest.fixture()
def repository() -> PostgresRepository:
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        connection.execute(Path("migrations/001_initial.sql").read_text())
        connection.execute(Path("migrations/002_roles.sql").read_text())
        connection.execute(Path("migrations/003_proceedings.sql").read_text())
        connection.execute(Path("migrations/004_scotus_legal_briefs.sql").read_text())
        connection.execute(Path("migrations/005_scotus_whole_case_briefs.sql").read_text())
        connection.execute(
            Path("migrations/006_scotus_generation_cost_controls.sql").read_text()
        )
        connection.execute(
            """INSERT INTO receivers(receiver_id,object_prefix,token_hash)
               VALUES ('dc-pi-01','receivers/dc-pi-01/','test')"""
        )
    repo = PostgresRepository(DSN)
    yield repo
    repo.pool.close()


def test_public_role_can_read_projection_view_but_not_private_tables(
    repository: PostgresRepository,
) -> None:
    with repository.pool.connection() as connection:
        with connection.transaction():
            connection.execute("SET ROLE ragchew_public")
            connection.execute("SELECT * FROM active_public_projection").fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute("SELECT * FROM captures").fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute("SELECT * FROM proceedings").fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute("SELECT * FROM scotus_document_revisions").fetchall()
        with connection.transaction():
            connection.execute("SELECT * FROM active_proceeding_public_projection").fetchall()
            connection.execute("SELECT * FROM active_scotus_public_projection").fetchall()
            connection.execute("RESET ROLE")


def test_commit_creates_one_job_and_delivery_is_idempotent(
    repository: PostgresRepository,
) -> None:
    item = envelope()
    key = f"receivers/dc-pi-01/calls/{item.capture_id}.wav"
    first = repository.create_capture(item, key)
    duplicate = repository.create_capture(item, key)
    assert first == duplicate
    assert repository.commit_capture(item.receiver_id, item.capture_id, 24)
    assert repository.commit_capture(item.receiver_id, item.capture_id, 24)
    with repository.pool.connection() as connection:
        assert connection.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 1


def test_expired_worker_lease_is_reclaimed(repository: PostgresRepository) -> None:
    item = envelope()
    repository.create_capture(item, f"receivers/dc-pi-01/{item.capture_id}.wav")
    repository.commit_capture(item.receiver_id, item.capture_id, 24)
    first = repository.claim_job("worker-a", 300)
    assert first is not None
    assert repository.claim_job("worker-b", 300) is None
    with repository.pool.connection() as connection:
        connection.execute(
            "UPDATE jobs SET lease_expires_at=now() - interval '1 second' WHERE job_id=%s",
            (first.job_id,),
        )
        connection.commit()
    reclaimed = repository.claim_job("worker-b", 300)
    assert reclaimed is not None
    assert reclaimed.job_id == first.job_id
    assert not repository.complete_job(reclaimed.job_id, "worker-a", "wrong")
    assert repository.complete_job(reclaimed.job_id, "worker-b", "transcript-1")


def test_transcript_revisions_are_immutable_and_enqueue_extraction(
    repository: PostgresRepository,
) -> None:
    item = envelope()
    repository.create_capture(item, f"receivers/dc-pi-01/{item.capture_id}.wav")
    repository.commit_capture(item.receiver_id, item.capture_id, 24)
    store = PostgresTranscriptStore("", pool=repository.pool)
    capture = store.get_capture(item.capture_id)
    assert capture is not None and capture.talkgroup_name == "01 DISP"
    now = datetime.now(UTC)
    revision = TranscriptRevision(
        capture_id=item.capture_id,
        status=TranscriptStatus.COMPLETE,
        text="Engine 10 responding.",
        normalized_text="Engine 10 responding.",
        model="whisper-test",
        model_config_hash="c" * 64,
        hint_set_version="dcfd-v1",
        confidence=0.9,
        started_at=now,
        completed_at=now,
    )
    first = store.save_transcript(revision, item.audio.sha256, 30)
    duplicate = store.save_transcript(
        revision.model_copy(update={"text": "must not overwrite"}), item.audio.sha256, 30
    )
    assert duplicate.revision_id == first.revision_id
    assert duplicate.text == "Engine 10 responding."
    with repository.pool.connection() as connection:
        stages = connection.execute(
            "SELECT stage,count(*) AS n FROM jobs GROUP BY stage ORDER BY stage"
        ).fetchall()
    assert stages == [{"stage": "extract", "n": 1}, {"stage": "transcribe", "n": 1}]


def test_extraction_revisions_are_immutable_and_enqueue_correlation(
    repository: PostgresRepository,
) -> None:
    item = envelope(capture_id="capture_extract_123456789")
    repository.create_capture(item, f"receivers/dc-pi-01/{item.capture_id}.wav")
    repository.commit_capture(item.receiver_id, item.capture_id, 24)
    transcript_store = PostgresTranscriptStore("", pool=repository.pool)
    now = datetime.now(UTC)
    revision = transcript_store.save_transcript(
        TranscriptRevision(
            capture_id=item.capture_id,
            status=TranscriptStatus.COMPLETE,
            text="Engine 10 respond to 1400 H Street Northeast.",
            normalized_text="Engine 10 respond to 1400 H Street Northeast.",
            model="whisper-test",
            model_config_hash="d" * 64,
            hint_set_version="dcfd-v1",
            confidence=0.9,
            started_at=now,
            completed_at=now,
        ),
        item.audio.sha256,
        30,
    )
    observation_store = PostgresObservationStore("", pool=repository.pool)
    source = observation_store.get_transcript(revision.revision_id)
    assert source is not None
    service = ExtractionService(FakeExtractor(), observation_store)
    first = service.process(source)
    duplicate = service.process(source)
    assert len(first) >= 2
    assert [item.observation_id for item in duplicate] == [item.observation_id for item in first]
    with repository.pool.connection() as connection:
        assert connection.execute(
            "SELECT count(*) AS n FROM jobs WHERE stage='correlate'"
        ).fetchone()["n"] == 1
        extraction_id = connection.execute(
            "SELECT extraction_revision_id FROM extraction_revisions"
        ).fetchone()["extraction_revision_id"]
    incident_store = PostgresIncidentStore("", pool=repository.pool)
    engine = CorrelationEngine()
    first_incident = incident_store.correlate_extraction(extraction_id, engine)
    retried_incident = incident_store.correlate_extraction(extraction_id, engine)
    assert first_incident is not None and retried_incident is not None
    assert first_incident.incident.incident_id == retried_incident.incident.incident_id
    with repository.pool.connection() as connection:
        assert connection.execute(
            "SELECT count(*) AS n FROM incident_observations"
        ).fetchone()["n"] == len(first_incident.contexts)
        assert connection.execute(
            "SELECT count(*) AS n FROM incident_state_history"
        ).fetchone()["n"] == 1
    decision = PublicationPolicy(
        MvpConfig.from_yaml("config/mvp.yaml").publication
    ).evaluate(retried_incident)
    policy_store = PostgresPolicyStore("", pool=repository.pool)
    assert policy_store.save(decision) == policy_store.save(decision)
    with repository.pool.connection() as connection:
        assert connection.execute(
            "SELECT count(*) AS n FROM policy_decisions"
        ).fetchone()["n"] == 1


def test_abandoned_upload_and_audio_retention(repository: PostgresRepository) -> None:
    item = envelope()
    key = f"receivers/dc-pi-01/{item.capture_id}.wav"
    repository.create_capture(item, key)
    with repository.pool.connection() as connection:
        connection.execute(
            "UPDATE captures SET created_at=now() - interval '2 hours' WHERE capture_id=%s",
            (item.capture_id,),
        )
        connection.commit()
    assert repository.expire_abandoned_uploads(datetime.now(UTC) - timedelta(hours=1)) == [key]

    second = envelope(capture_id="capture_second_123456789")
    second_key = f"receivers/dc-pi-01/{second.capture_id}.wav"
    repository.create_capture(second, second_key)
    repository.commit_capture(second.receiver_id, second.capture_id, 0)
    job = repository.claim_job("worker", 60)
    assert job is not None
    assert repository.complete_job(job.job_id, "worker", "done")
    assert repository.expire_audio(datetime.now(UTC) + timedelta(seconds=1)) == [second_key]


def test_scotus_whole_case_candidate_waits_for_every_historical_parse(
    repository: PostgresRepository,
) -> None:
    case_id = UUID("10000000-0000-0000-0000-000000000001")
    first_argument = UUID("10000000-0000-0000-0000-000000000002")
    second_argument = UUID("10000000-0000-0000-0000-000000000003")
    first_document = UUID("10000000-0000-0000-0000-000000000004")
    second_document = UUID("10000000-0000-0000-0000-000000000005")
    with repository.pool.connection() as connection, connection.transaction():
        connection.execute(
            """INSERT INTO scotus_cases
               (case_id,schema_version,term,caption_private,primary_docket,official_url,
                status,first_observed_at,updated_at)
               VALUES (%s,'1.0','2025','Example v. Agency','25-100',
                       'https://www.supremecourt.gov/example','reargued',now(),now())""",
            (case_id,),
        )
        for argument_id, sequence, days, reargument in (
            (first_argument, 1, 0, False),
            (second_argument, 2, 30, True),
        ):
            connection.execute(
                """INSERT INTO scotus_argument_sessions
                   (argument_id,case_id,term,session_key,argument_date,sequence,reargument,
                    status,official_detail_url,discovered_at,updated_at)
                   VALUES (%s,%s,'2025',%s,now()+(%s * interval '1 day'),%s,%s,
                           'transcript_ready',%s,now(),now())""",
                (
                    argument_id,
                    case_id,
                    f"session-{sequence}",
                    days,
                    sequence,
                    reargument,
                    f"https://www.supremecourt.gov/argument-{sequence}",
                ),
            )
        for document_id, argument_id, sequence in (
            (first_document, first_argument, 1),
            (second_document, second_argument, 2),
        ):
            connection.execute(
                """INSERT INTO scotus_document_revisions
                   (document_revision_id,case_id,argument_id,document_kind,external_id,
                    revision_number,official_url_private,status,content_type,byte_count,
                    sha256,object_key,canonical,observed_at,ready_at)
                   VALUES (%s,%s,%s,'transcript',%s,1,%s,'parsed','application/pdf',100,
                           %s,%s,true,now(),now())""",
                (
                    document_id,
                    case_id,
                    argument_id,
                    f"transcript-{sequence}",
                    f"https://www.supremecourt.gov/transcript-{sequence}.pdf",
                    str(sequence) * 64,
                    f"official/case/transcript/{sequence}.pdf",
                ),
            )
            connection.execute(
                """UPDATE scotus_argument_sessions
                   SET transcript_document_revision_id=%s WHERE argument_id=%s""",
                (document_id, argument_id),
            )
            connection.execute(
                """INSERT INTO scotus_document_parses
                   (parse_revision_id,document_revision_id,parser,parser_version,
                    config_hash,status,page_count)
                   VALUES (gen_random_uuid(),%s,'pypdf','1',%s,'complete',10)""",
                (document_id, str(sequence + 2) * 64),
            )
    rows = _candidate_rows(repository)
    assert len(rows) == 1
    candidate = _build_candidate(repository, rows[0], datetime.now(UTC))
    assert [session.argument_id for session in candidate.argument_sessions] == [
        first_argument,
        second_argument,
    ]
    with repository.pool.connection() as connection:
        connection.execute(
            "DELETE FROM scotus_document_parses WHERE document_revision_id=%s",
            (second_document,),
        )
        connection.commit()
    assert _candidate_rows(repository) == []
