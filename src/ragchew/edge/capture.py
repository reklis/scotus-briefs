"""Convert finalized Trunk Recorder artifacts into capture envelopes."""

from __future__ import annotations

import base64
import hashlib
import json
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ragchew.config import MvpConfig
from ragchew.contracts import AudioDescriptor, CaptureEnvelope, DecoderMetadata

AUDIO_TYPES = {".wav": "audio/wav", ".flac": "audio/flac", ".m4a": "audio/mp4"}
PARTIAL_SUFFIXES = {".tmp", ".part", ".partial"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deterministic_capture_id(
    receiver_id: str,
    system_id: str,
    talkgroup_id: int,
    started_at: datetime,
    frequency_hz: int,
    audio_sha256: str,
) -> str:
    canonical = "|".join(
        (
            receiver_id,
            system_id,
            str(talkgroup_id),
            started_at.isoformat(),
            str(frequency_hz),
            audio_sha256,
        )
    ).encode()
    encoded = base64.urlsafe_b64encode(hashlib.sha256(canonical).digest()).decode().rstrip("=")
    return f"call_{encoded}"


def _parse_time(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        # Trunk Recorder metadata commonly uses Unix seconds.
        return datetime.fromtimestamp(float(value), tz=UTC)
    if not isinstance(value, str):
        raise ValueError("call time is absent")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("call time must include timezone")
    return parsed.astimezone(UTC)


def _audio_properties(path: Path) -> tuple[int | None, int | None]:
    if path.suffix.lower() != ".wav":
        return None, None
    with wave.open(str(path), "rb") as audio:
        return audio.getframerate(), audio.getnchannels()


def _source_ids(metadata: dict[str, Any]) -> tuple[int, ...]:
    result: list[int] = []
    for item in metadata.get("srcList", []):
        value = item.get("src") if isinstance(item, dict) else item
        if isinstance(value, int) and value not in result:
            result.append(value)
    return tuple(result)


def build_envelope(
    audio_path: Path,
    metadata: dict[str, Any],
    config: MvpConfig,
) -> CaptureEnvelope | None:
    if (
        audio_path.suffix.lower() in PARTIAL_SUFFIXES
        or audio_path.suffix.lower() not in AUDIO_TYPES
    ):
        return None
    if not audio_path.is_file() or audio_path.stat().st_size <= 0:
        return None

    talkgroup_id = int(metadata.get("talkgroupNum", metadata.get("talkgroup", 0)))
    talkgroup_name = config.receiver.talkgroups.get(talkgroup_id)
    encrypted = bool(metadata.get("encrypted", False))
    if encrypted or talkgroup_name is None:
        return None

    started_at = _parse_time(metadata.get("time", metadata.get("startTime")))
    duration_seconds = float(metadata.get("len", metadata.get("duration", 0)))
    if duration_seconds <= 0:
        raise ValueError("call duration must be positive")
    ended_at = started_at + timedelta(seconds=duration_seconds)
    frequency_hz = int(metadata.get("freq", metadata.get("frequency", 0)))
    if frequency_hz <= 0:
        raise ValueError("call frequency must be positive")

    digest = sha256_file(audio_path)
    sample_rate, channels = _audio_properties(audio_path)
    capture_id = deterministic_capture_id(
        config.receiver.receiver_id,
        config.receiver.system_id,
        talkgroup_id,
        started_at,
        frequency_hz,
        digest,
    )
    return CaptureEnvelope(
        capture_id=capture_id,
        receiver_id=config.receiver.receiver_id,
        system_id=config.receiver.system_id,
        talkgroup_id=talkgroup_id,
        talkgroup_name=talkgroup_name,
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=round(duration_seconds * 1000),
        frequency_hz=frequency_hz,
        source_radio_ids=_source_ids(metadata),
        encrypted=False,
        emergency=bool(metadata.get("emergency", False)),
        audio=AudioDescriptor(
            content_type=AUDIO_TYPES[audio_path.suffix.lower()],
            byte_count=audio_path.stat().st_size,
            sha256=digest,
            sample_rate_hz=sample_rate,
            channels=channels,
        ),
        decoder=DecoderMetadata(
            signal_db=metadata.get("signalDb"),
            error_count=metadata.get("errors"),
            spike_count=metadata.get("spikes"),
            dropped_samples=metadata.get("droppedSamples"),
        ),
    )


def load_finalized_call(
    metadata_path: Path, config: MvpConfig
) -> tuple[CaptureEnvelope, Path] | None:
    if metadata_path.suffix != ".json" or not metadata_path.is_file():
        return None
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    configured = metadata.get("audioFile") or metadata.get("filename") or metadata.get("name")
    candidates = []
    if configured:
        candidates.append(metadata_path.parent / str(configured))
    candidates.extend(metadata_path.with_suffix(suffix) for suffix in AUDIO_TYPES)
    audio_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if audio_path is None:
        return None
    envelope = build_envelope(audio_path, metadata, config)
    return (envelope, audio_path) if envelope else None
