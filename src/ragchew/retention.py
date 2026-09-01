"""Private source-material lifecycle cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ragchew.config import MvpConfig
from ragchew.repository import Repository
from ragchew.storage import ObjectStore


class RetentionService:
    def __init__(self, repository: Repository, objects: ObjectStore, config: MvpConfig) -> None:
        self.repository = repository
        self.objects = objects
        self.config = config

    def run(self, now: datetime | None = None) -> dict[str, int]:
        current = now or datetime.now(UTC)
        abandoned_before = current - timedelta(
            seconds=self.config.retry.abandoned_upload_seconds
        )
        abandoned = self.repository.expire_abandoned_uploads(abandoned_before)
        audio = self.repository.expire_audio(current)
        for key in {*abandoned, *audio}:
            self.objects.delete(key)
        transcripts = self.repository.expire_transcripts(current)
        return {
            "abandoned_objects": len(abandoned),
            "audio_objects": len(audio),
            "transcripts": transcripts,
        }
