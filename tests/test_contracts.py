from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ragchew.contracts import AudioDescriptor, CaptureEnvelope, DecoderMetadata, EdgeHeartbeat


def capture(**overrides: object) -> CaptureEnvelope:
    started = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "capture_id": "capture_0123456789abcdef",
        "receiver_id": "dc-pi-01",
        "system_id": "dcfd",
        "talkgroup_id": 101,
        "talkgroup_name": "01 DISP",
        "started_at": started,
        "ended_at": started + timedelta(seconds=8),
        "duration_ms": 8000,
        "frequency_hz": 856_987_500,
        "source_radio_ids": (1102467,),
        "audio": AudioDescriptor(
            content_type="audio/wav", byte_count=16000, sha256="a" * 64
        ),
        "decoder": DecoderMetadata(error_count=0),
    }
    values.update(overrides)
    return CaptureEnvelope.model_validate(values)


def test_capture_envelope_accepts_valid_call() -> None:
    assert capture().schema_version == "1.0"


def test_capture_envelope_requires_utc() -> None:
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        capture(started_at=datetime(2026, 8, 27, 18, 0))


def test_capture_envelope_rejects_bad_digest() -> None:
    with pytest.raises(ValidationError):
        capture(audio={"content_type": "audio/wav", "byte_count": 1, "sha256": "bad"})


def test_capture_envelope_rejects_mismatched_duration() -> None:
    with pytest.raises(ValidationError, match="duration_ms"):
        capture(duration_ms=100)


def test_heartbeat_requires_ordered_rf_window() -> None:
    with pytest.raises(ValidationError, match="rf_max_hz"):
        EdgeHeartbeat(
            receiver_id="dc-pi-01",
            observed_at=datetime.now(UTC),
            software_version="test",
            config_version="1",
            rf_min_hz=862_000_000,
            rf_max_hz=854_000_000,
            control_messages_per_minute=1,
            spool_depth=0,
            oldest_spool_age_seconds=0,
            free_disk_bytes=1,
        )
