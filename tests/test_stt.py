from __future__ import annotations

import hashlib
import math
import struct
import wave
from pathlib import Path

import httpx
import pytest

from ragchew.analysis.audio import AudioValidationError, PrivateAudioRetriever
from ragchew.analysis.hints import HintSet
from ragchew.analysis.stt import (
    CaptureForAnalysis,
    TranscriptionResult,
    TranscriptionService,
)
from ragchew.contracts import TranscriptRevision, TranscriptStatus
from ragchew.storage import ObjectMetadata


class DownloadStore:
    def __init__(self) -> None:
        self.expires: list[int] = []

    def create_upload(self, key: str, content_type: str, sha256: str) -> str:
        raise NotImplementedError

    def head(self, key: str) -> ObjectMetadata:
        raise NotImplementedError

    def create_download(self, key: str, expires_seconds: int = 300) -> str:
        self.expires.append(expires_seconds)
        return f"https://private.invalid/{key}"

    def delete(self, key: str) -> None:
        raise NotImplementedError


class FakeAdapter:
    def __init__(self, text: str, model_name: str = "whisper-test") -> None:
        self.text = text
        self.model_name = model_name
        self.calls: list[tuple[Path, str]] = []

    def transcribe(self, audio_path: Path, prompt: str) -> TranscriptionResult:
        self.calls.append((audio_path, prompt))
        return TranscriptionResult(self.text, 0.88, {"test": True})


class MemoryTranscriptStore:
    def __init__(self) -> None:
        self.revisions: dict[tuple[str, str], TranscriptRevision] = {}

    def save_transcript(
        self, revision: TranscriptRevision, input_version: str, retention_days: int
    ) -> TranscriptRevision:
        key = (revision.capture_id, revision.model_config_hash)
        return self.revisions.setdefault(key, revision)


def wav_bytes(*, silent: bool = False, seconds: float = 0.25) -> bytes:
    from io import BytesIO

    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        samples = []
        for index in range(round(16000 * seconds)):
            value = 0 if silent else round(8000 * math.sin(2 * math.pi * 440 * index / 16000))
            samples.append(struct.pack("<h", value))
        audio.writeframes(b"".join(samples))
    return output.getvalue()


def service_for(
    payload: bytes,
    adapter: FakeAdapter,
    store: MemoryTranscriptStore,
) -> tuple[TranscriptionService, CaptureForAnalysis, DownloadStore]:
    digest = hashlib.sha256(payload).hexdigest()
    object_store = DownloadStore()
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    )
    retriever = PrivateAudioRetriever(object_store, client=client)
    hints = HintSet.load("resources/dcfd-hints-v1.yaml")
    service = TranscriptionService(retriever, adapter, store, hints, retention_days=30)
    capture = CaptureForAnalysis(
        receiver_id="dc-pi-01",
        capture_id="call_0123456789abcdef",
        object_key="receivers/dc-pi-01/call.wav",
        audio_bytes=len(payload),
        audio_sha256=digest,
        content_type="audio/wav",
        talkgroup_name="01 DISP",
    )
    return service, capture, object_store


def test_private_audio_uses_time_limited_url_and_validates_digest() -> None:
    payload = wav_bytes()
    adapter = FakeAdapter("Engine 10 responding.")
    service, capture, objects = service_for(payload, adapter, MemoryTranscriptStore())
    revision = service.process(capture)
    assert revision.status == TranscriptStatus.COMPLETE
    assert objects.expires == [300]

    bad = capture.__class__(**{**capture.__dict__, "audio_sha256": "b" * 64})
    with pytest.raises(AudioValidationError, match="digest"):
        service.process(bad)


def test_silent_audio_is_non_transcribable_and_never_sent_to_model() -> None:
    adapter = FakeAdapter("hallucinated content")
    service, capture, _ = service_for(wav_bytes(silent=True), adapter, MemoryTranscriptStore())
    revision = service.process(capture)
    assert revision.status == TranscriptStatus.NON_TRANSCRIBABLE
    assert revision.text is None
    assert adapter.calls == []


@pytest.mark.parametrize(
    "text",
    [
        "Engine 10 respond to H Street Northeast.",
        "Truck 5, clipped transmission",
        "No smoke and no fire showing.",
        "Battalion Chief 1 requests the working fire dispatch.",
        "Static but intelligible rescue squad traffic.",
    ],
)
def test_radio_transcripts_preserve_jargon_location_and_negation(text: str) -> None:
    adapter = FakeAdapter(text)
    service, capture, _ = service_for(wav_bytes(), adapter, MemoryTranscriptStore())
    revision = service.process(capture)
    assert revision.text == text
    assert revision.normalized_text == text
    assert "DC Fire and EMS" in adapter.calls[0][1]
    assert "H Street" in adapter.calls[0][1]
    assert "preserve negation" in adapter.calls[0][1]


def test_model_change_creates_distinct_immutable_revision() -> None:
    payload = wav_bytes()
    store = MemoryTranscriptStore()
    first_service, capture, _ = service_for(payload, FakeAdapter("first", "whisper-a"), store)
    second_service, _, _ = service_for(payload, FakeAdapter("second", "whisper-b"), store)
    first = first_service.process(capture)
    retry = first_service.process(capture)
    second = second_service.process(capture)
    assert retry.revision_id == first.revision_id
    assert second.revision_id != first.revision_id
    assert len(store.revisions) == 2
