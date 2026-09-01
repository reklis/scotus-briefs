"""Whisper-compatible transcription and immutable revision orchestration."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ragchew.analysis.audio import PrivateAudioRetriever, pcm_rms
from ragchew.analysis.hints import HintSet
from ragchew.contracts import TranscriptRevision, TranscriptStatus


@dataclass(frozen=True)
class CaptureForAnalysis:
    receiver_id: str
    capture_id: str
    object_key: str
    audio_bytes: int
    audio_sha256: str
    content_type: str
    talkgroup_name: str


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    confidence: float | None
    metadata: dict[str, Any]


class SpeechToText(Protocol):
    model_name: str

    def transcribe(self, audio_path: Path, prompt: str) -> TranscriptionResult: ...


class TranscriptStore(Protocol):
    def save_transcript(
        self,
        revision: TranscriptRevision,
        input_version: str,
        retention_days: int,
    ) -> TranscriptRevision: ...


class FasterWhisperAdapter:
    """Lazily loads faster-whisper so edge/API images do not require ML dependencies."""

    def __init__(
        self, model_name: str, device: str = "auto", compute_type: str = "default"
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]

            self._model = WhisperModel(
                self.model_name, device=self.device, compute_type=self.compute_type
            )
        return self._model

    def transcribe(self, audio_path: Path, prompt: str) -> TranscriptionResult:
        segments, info = self._load().transcribe(
            str(audio_path),
            language="en",
            initial_prompt=prompt,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        materialized = list(segments)
        text = " ".join(segment.text.strip() for segment in materialized).strip()
        probabilities = [math.exp(segment.avg_logprob) for segment in materialized]
        confidence = min(1.0, sum(probabilities) / len(probabilities)) if probabilities else None
        return TranscriptionResult(
            text=text,
            confidence=confidence,
            metadata={"language": info.language, "duration": info.duration},
        )


class TranscriptionService:
    def __init__(
        self,
        retriever: PrivateAudioRetriever,
        adapter: SpeechToText,
        store: TranscriptStore,
        hints: HintSet,
        retention_days: int,
        silence_rms_threshold: float = 0.001,
    ) -> None:
        self.retriever = retriever
        self.adapter = adapter
        self.store = store
        self.hints = hints
        self.retention_days = retention_days
        self.silence_rms_threshold = silence_rms_threshold

    def _config_hash(self, capture: CaptureForAnalysis) -> str:
        value = json.dumps(
            {
                "audio_sha256": capture.audio_sha256,
                "model": self.adapter.model_name,
                "hint_set": self.hints.version,
                "silence_rms_threshold": self.silence_rms_threshold,
            },
            sort_keys=True,
        ).encode()
        return hashlib.sha256(value).hexdigest()

    def process(self, capture: CaptureForAnalysis) -> TranscriptRevision:
        started = datetime.now(UTC)
        config_hash = self._config_hash(capture)
        with self.retriever.retrieve(
            capture.object_key,
            capture.audio_bytes,
            capture.audio_sha256,
            capture.content_type,
        ) as audio_path:
            if pcm_rms(audio_path) <= self.silence_rms_threshold:
                revision = TranscriptRevision(
                    capture_id=capture.capture_id,
                    status=TranscriptStatus.NON_TRANSCRIBABLE,
                    model=self.adapter.model_name,
                    model_config_hash=config_hash,
                    hint_set_version=self.hints.version,
                    started_at=started,
                    completed_at=datetime.now(UTC),
                )
            else:
                result = self.adapter.transcribe(
                    audio_path, self.hints.prompt(capture.talkgroup_name)
                )
                text = result.text.strip()
                revision = TranscriptRevision(
                    capture_id=capture.capture_id,
                    status=(
                        TranscriptStatus.COMPLETE
                        if text
                        else TranscriptStatus.NON_TRANSCRIBABLE
                    ),
                    text=text or None,
                    normalized_text=" ".join(text.split()) or None,
                    model=self.adapter.model_name,
                    model_config_hash=config_hash,
                    hint_set_version=self.hints.version,
                    confidence=result.confidence,
                    started_at=started,
                    completed_at=datetime.now(UTC),
                )
        return self.store.save_transcript(
            revision, capture.audio_sha256, self.retention_days
        )
