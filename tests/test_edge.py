from __future__ import annotations

import json
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ragchew.config import MvpConfig
from ragchew.edge.capture import build_envelope, load_finalized_call
from ragchew.edge.health import build_heartbeat
from ragchew.edge.spool import EdgeSpool, SpoolFullError
from ragchew.edge.uploader import EdgeUploader


def make_wav(path: Path, frames: int = 8000) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * frames)


def metadata(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "talkgroupNum": 101,
        "time": "2026-08-27T18:00:00Z",
        "len": 1.0,
        "freq": 856987500,
        "encrypted": False,
        "emergency": False,
        "srcList": [{"src": 1102467}],
    }
    values.update(overrides)
    return values


@pytest.fixture()
def config() -> MvpConfig:
    return MvpConfig.from_yaml("config/mvp.yaml")


def test_finalized_call_filters_partial_encrypted_and_unselected(
    tmp_path: Path, config: MvpConfig
) -> None:
    audio = tmp_path / "call.wav"
    make_wav(audio)
    assert build_envelope(audio.with_suffix(".tmp"), metadata(), config) is None
    assert build_envelope(audio, metadata(encrypted=True), config) is None
    assert build_envelope(audio, metadata(talkgroupNum=9999), config) is None


def test_finalized_call_produces_stable_envelope(tmp_path: Path, config: MvpConfig) -> None:
    audio = tmp_path / "call.wav"
    make_wav(audio)
    sidecar = tmp_path / "call.json"
    sidecar.write_text(json.dumps(metadata(audioFile=audio.name)))
    first = load_finalized_call(sidecar, config)
    second = load_finalized_call(sidecar, config)
    assert first is not None and second is not None
    envelope, selected_audio = first
    assert envelope.capture_id == second[0].capture_id
    assert envelope.audio.byte_count == audio.stat().st_size
    assert envelope.audio.sample_rate_hz == 8000
    assert envelope.source_radio_ids == (1102467,)
    assert selected_audio == audio


def test_spool_survives_restart_and_recovers_upload(tmp_path: Path, config: MvpConfig) -> None:
    source = tmp_path / "source.wav"
    make_wav(source)
    envelope = build_envelope(source, metadata(), config)
    assert envelope is not None
    root = tmp_path / "spool"
    spool = EdgeSpool(root, max_bytes=1_000_000)
    assert spool.add(envelope, source)
    assert not spool.add(envelope, source)
    claimed = spool.claim()
    assert claimed is not None and claimed.state == "uploading"
    spool.close()

    reopened = EdgeSpool(root, max_bytes=1_000_000)
    assert reopened.recover_uploads() == 1
    recovered = reopened.claim()
    assert recovered is not None
    assert recovered.capture_id == envelope.capture_id
    assert recovered.audio_path.is_file()
    reopened.close()


def test_spool_preserves_unacknowledged_calls_at_capacity(
    tmp_path: Path, config: MvpConfig
) -> None:
    source = tmp_path / "source.wav"
    make_wav(source)
    envelope = build_envelope(source, metadata(), config)
    assert envelope is not None
    spool = EdgeSpool(tmp_path / "spool", max_bytes=envelope.audio.byte_count)
    assert spool.add(envelope, source)
    second = envelope.model_copy(update={"capture_id": "call_second_abcdefghijklmnop"})
    with pytest.raises(SpoolFullError):
        spool.add(second, source)
    assert spool.get(envelope.capture_id) is not None
    spool.close()


def test_acknowledged_call_is_removed_after_grace(tmp_path: Path, config: MvpConfig) -> None:
    source = tmp_path / "source.wav"
    make_wav(source)
    envelope = build_envelope(source, metadata(), config)
    assert envelope is not None
    spool = EdgeSpool(tmp_path / "spool", max_bytes=1_000_000)
    spool.add(envelope, source)
    assert spool.claim() is not None
    spool.acknowledge(envelope.capture_id)
    assert spool.cleanup_acknowledged(0, datetime.now(UTC) + timedelta(seconds=1)) == 1
    assert spool.get(envelope.capture_id) is None
    spool.close()


def test_uploader_retries_lost_acknowledgement_without_duplicate_upload(
    tmp_path: Path, config: MvpConfig
) -> None:
    source = tmp_path / "source.wav"
    make_wav(source)
    envelope = build_envelope(source, metadata(), config)
    assert envelope is not None
    spool = EdgeSpool(tmp_path / "spool", max_bytes=1_000_000)
    spool.add(envelope, source)
    calls = {"initiate": 0, "upload": 0, "commit": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            calls["upload"] += 1
            return httpx.Response(200)
        if request.url.path.endswith("/commit"):
            calls["commit"] += 1
            if calls["commit"] == 1:
                raise httpx.ReadError("acknowledgement lost", request=request)
            return httpx.Response(200, json={"status": "ready"})
        calls["initiate"] += 1
        if calls["initiate"] == 1:
            return httpx.Response(201, json={"upload_url": "https://object.invalid/upload"})
        return httpx.Response(201, json={"upload_url": None, "duplicate": True})

    retry_config = config.model_copy(
        update={
            "retry": config.retry.model_copy(
                update={"base_delay_seconds": 0, "maximum_delay_seconds": 0}
            )
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    uploader = EdgeUploader(
        "https://ingest.invalid", "dc-pi-01", "token", spool, retry_config, client
    )
    assert uploader.process_one()
    assert spool.get(envelope.capture_id).state == "retryable"  # type: ignore[union-attr]
    assert uploader.process_one()
    assert spool.get(envelope.capture_id).state == "acknowledged"  # type: ignore[union-attr]
    assert calls == {"initiate": 2, "upload": 1, "commit": 2}
    client.close()
    spool.close()


def test_conflicting_upload_stops_retry(tmp_path: Path, config: MvpConfig) -> None:
    source = tmp_path / "source.wav"
    make_wav(source)
    envelope = build_envelope(source, metadata(), config)
    assert envelope is not None
    spool = EdgeSpool(tmp_path / "spool", max_bytes=1_000_000)
    spool.add(envelope, source)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(409, text="conflict"))
    )
    uploader = EdgeUploader("https://ingest.invalid", "dc-pi-01", "token", spool, config, client)
    uploader.process_one()
    assert spool.get(envelope.capture_id).state == "conflicted"  # type: ignore[union-attr]
    client.close()
    spool.close()


def test_quiet_receiver_heartbeat_uses_control_activity(
    tmp_path: Path, config: MvpConfig
) -> None:
    spool = EdgeSpool(tmp_path / "spool", max_bytes=1_000_000)
    metrics = tmp_path / "radio.json"
    metrics.write_text(json.dumps({"control_messages_per_minute": 42, "dropped_samples": 0}))
    heartbeat = build_heartbeat(
        spool,
        config,
        disk_path=tmp_path,
        metrics_path=metrics,
        now=datetime(2026, 8, 27, 18, tzinfo=UTC),
    )
    assert heartbeat.control_messages_per_minute == 42
    assert heartbeat.last_finalized_call_at is None
    assert heartbeat.spool_depth == 0
    assert heartbeat.rf_min_hz == config.receiver.rf_min_hz
    spool.close()
