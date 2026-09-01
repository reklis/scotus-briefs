from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from ragchew.api import create_app
from ragchew.config import MvpConfig, ServiceSettings
from ragchew.contracts import AudioDescriptor, CaptureEnvelope, DecoderMetadata
from ragchew.retention import RetentionService
from ragchew.storage import ObjectMetadata
from tests.fakes import FakeObjectStore, FakeRepository

TOKEN = "test-secret"


def envelope(
    capture_id: str = "capture_0123456789abcdef", digest: str = "a" * 64
) -> CaptureEnvelope:
    started = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    return CaptureEnvelope(
        capture_id=capture_id,
        receiver_id="dc-pi-01",
        system_id="dcfd",
        talkgroup_id=101,
        talkgroup_name="01 DISP",
        started_at=started,
        ended_at=started + timedelta(seconds=8),
        duration_ms=8000,
        frequency_hz=856_987_500,
        audio=AudioDescriptor(content_type="audio/wav", byte_count=16000, sha256=digest),
        decoder=DecoderMetadata(),
    )


def setup() -> tuple[TestClient, FakeRepository, FakeObjectStore, MvpConfig]:
    repository = FakeRepository()
    objects = FakeObjectStore()
    config = MvpConfig.from_yaml("config/mvp.yaml")
    settings = ServiceSettings(receiver_tokens='{"dc-pi-01":"test-secret"}')
    app = create_app(settings=settings, config=config, repository=repository, objects=objects)
    return TestClient(app), repository, objects, config


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_unauthorized_manifest_creates_nothing() -> None:
    client, repository, _, _ = setup()
    response = client.post(
        "/v1/receivers/dc-pi-01/captures", json=envelope().model_dump(mode="json")
    )
    assert response.status_code == 401
    assert repository.captures == {}


def test_malformed_manifest_is_rejected() -> None:
    client, repository, _, _ = setup()
    response = client.post(
        "/v1/receivers/dc-pi-01/captures",
        headers=auth(),
        json={"schema_version": "1.0", "receiver_id": "dc-pi-01"},
    )
    assert response.status_code == 422
    assert repository.captures == {}


def test_upload_commit_and_duplicate_are_idempotent() -> None:
    client, repository, objects, _ = setup()
    payload = envelope()
    response = client.post(
        "/v1/receivers/dc-pi-01/captures",
        headers=auth(),
        json=payload.model_dump(mode="json"),
    )
    assert response.status_code == 201
    ticket = response.json()
    assert ticket["duplicate"] is False
    objects.objects[ticket["object_key"]] = ObjectMetadata(16000, "audio/wav", "a" * 64)

    committed = client.post(
        f"/v1/receivers/dc-pi-01/captures/{payload.capture_id}/commit", headers=auth()
    )
    assert committed.status_code == 200
    assert committed.json()["status"] == "ready"
    assert len(repository.jobs) == 1

    duplicate = client.post(
        "/v1/receivers/dc-pi-01/captures",
        headers=auth(),
        json=payload.model_dump(mode="json"),
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["upload_url"] is None
    assert len(repository.jobs) == 1


def test_conflicting_duplicate_is_rejected() -> None:
    client, _, _, _ = setup()
    first = envelope()
    assert client.post(
        "/v1/receivers/dc-pi-01/captures",
        headers=auth(),
        json=first.model_dump(mode="json"),
    ).status_code == 201
    conflict = envelope(digest="b" * 64)
    response = client.post(
        "/v1/receivers/dc-pi-01/captures",
        headers=auth(),
        json=conflict.model_dump(mode="json"),
    )
    assert response.status_code == 409


def test_digest_size_and_content_type_are_validated() -> None:
    for metadata in (
        ObjectMetadata(16000, "audio/wav", "b" * 64),
        ObjectMetadata(15999, "audio/wav", "a" * 64),
        ObjectMetadata(16000, "audio/flac", "a" * 64),
    ):
        client, repository, objects, _ = setup()
        payload = envelope()
        ticket = client.post(
            "/v1/receivers/dc-pi-01/captures",
            headers=auth(),
            json=payload.model_dump(mode="json"),
        ).json()
        objects.objects[ticket["object_key"]] = metadata
        response = client.post(
            f"/v1/receivers/dc-pi-01/captures/{payload.capture_id}/commit",
            headers=auth(),
        )
        assert response.status_code == 422
        assert repository.rejections
        assert repository.jobs == []


def test_heartbeat_is_receiver_scoped() -> None:
    client, repository, _, _ = setup()
    heartbeat = {
        "receiver_id": "dc-pi-01",
        "observed_at": "2026-08-27T18:00:00Z",
        "software_version": "test",
        "config_version": "1",
        "rf_min_hz": 854000000,
        "rf_max_hz": 862000000,
        "control_messages_per_minute": 10,
        "spool_depth": 0,
        "oldest_spool_age_seconds": 0,
        "free_disk_bytes": 1000
    }
    response = client.post(
        "/v1/receivers/dc-pi-01/heartbeats", headers=auth(), json=heartbeat
    )
    assert response.status_code == 202
    assert len(repository.heartbeats) == 1


def test_retention_deletes_abandoned_and_expired_objects() -> None:
    _, repository, objects, config = setup()
    repository.abandoned = ["receivers/dc-pi-01/abandoned.wav"]
    repository.expired_audio = ["receivers/dc-pi-01/expired.wav"]
    repository.expired_transcript_count = 2
    service = RetentionService(repository, objects, config)
    result = service.run(datetime(2026, 8, 27, 18, tzinfo=UTC))
    assert result == {"abandoned_objects": 1, "audio_objects": 1, "transcripts": 2}
    assert set(objects.deleted) == set(repository.abandoned + repository.expired_audio)
