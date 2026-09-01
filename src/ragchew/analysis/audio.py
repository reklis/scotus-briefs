"""Private time-limited audio retrieval, validation, and conversion."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

from ragchew.storage import ObjectStore


class AudioValidationError(Exception):
    pass


class PrivateAudioRetriever:
    def __init__(
        self,
        objects: ObjectStore,
        client: httpx.Client | None = None,
        ffmpeg: str = "ffmpeg",
    ) -> None:
        self.objects = objects
        self.client = client or httpx.Client(timeout=60)
        self.ffmpeg = ffmpeg

    @contextmanager
    def retrieve(
        self,
        object_key: str,
        expected_bytes: int,
        expected_sha256: str,
        content_type: str,
    ) -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix="ragchew-audio-") as directory:
            extension = {"audio/wav": ".wav", "audio/flac": ".flac", "audio/mp4": ".m4a"}.get(
                content_type, ".audio"
            )
            source = Path(directory) / f"source{extension}"
            url = self.objects.create_download(object_key, expires_seconds=300)
            digest = hashlib.sha256()
            byte_count = 0
            with self.client.stream("GET", url) as response:
                response.raise_for_status()
                with source.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        byte_count += len(chunk)
                        if byte_count > expected_bytes:
                            raise AudioValidationError("download exceeds declared byte count")
                        digest.update(chunk)
                        handle.write(chunk)
            if byte_count != expected_bytes:
                raise AudioValidationError("download byte count does not match manifest")
            if digest.hexdigest() != expected_sha256:
                raise AudioValidationError("download digest does not match manifest")

            normalized = Path(directory) / "normalized.wav"
            if content_type == "audio/wav" and self._valid_wav(source):
                shutil.copyfile(source, normalized)
            else:
                result = subprocess.run(
                    [
                        self.ffmpeg,
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(source),
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        str(normalized),
                    ],
                    check=False,
                    capture_output=True,
                    timeout=60,
                )
                if result.returncode != 0 or not self._valid_wav(normalized):
                    raise AudioValidationError("ffmpeg could not produce valid mono PCM audio")
            yield normalized

    @staticmethod
    def _valid_wav(path: Path) -> bool:
        try:
            with wave.open(str(path), "rb") as audio:
                return (
                    audio.getnchannels() in {1, 2}
                    and audio.getsampwidth() == 2
                    and audio.getframerate() > 0
                    and audio.getnframes() > 0
                )
        except (OSError, EOFError, wave.Error):
            return False


def pcm_rms(path: Path) -> float:
    """Return normalized RMS for 16-bit PCM WAV without external numeric dependencies."""
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2:
            raise AudioValidationError("silence detector requires 16-bit PCM")
        frames = audio.readframes(audio.getnframes())
    if not frames:
        return 0.0
    count = len(frames) // 2
    total = 0
    for index in range(0, len(frames) - 1, 2):
        sample = int.from_bytes(frames[index : index + 2], "little", signed=True)
        total += sample * sample
    return float((total / count) ** 0.5 / 32768)
