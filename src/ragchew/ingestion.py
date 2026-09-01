"""Capture ingestion orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from ragchew.auth import ReceiverAuthenticator
from ragchew.config import MvpConfig
from ragchew.contracts import CaptureEnvelope
from ragchew.repository import CaptureRecord, Repository
from ragchew.storage import ObjectStore


class IngestionConflict(Exception):
    pass


class IngestionNotFound(Exception):
    pass


class IntegrityFailure(Exception):
    pass


@dataclass(frozen=True)
class UploadTicket:
    capture_id: str
    status: str
    object_key: str
    upload_url: str | None
    duplicate: bool


class IngestionService:
    def __init__(
        self,
        repository: Repository,
        objects: ObjectStore,
        authenticator: ReceiverAuthenticator,
        config: MvpConfig,
    ) -> None:
        self.repository = repository
        self.objects = objects
        self.authenticator = authenticator
        self.config = config

    def _object_key(self, envelope: CaptureEnvelope) -> str:
        extension = {
            "audio/wav": "wav",
            "audio/flac": "flac",
            "audio/mp4": "m4a",
        }.get(envelope.audio.content_type, "audio")
        prefix = self.authenticator.object_prefix(envelope.receiver_id)
        return f"{prefix}calls/{envelope.started_at:%Y/%m/%d}/{envelope.capture_id}.{extension}"

    def initiate(self, receiver_id: str, envelope: CaptureEnvelope) -> UploadTicket:
        if envelope.receiver_id != receiver_id:
            raise IngestionConflict("manifest receiver does not match authenticated receiver")
        existing = self.repository.get_capture(receiver_id, envelope.capture_id)
        if existing:
            if existing.audio_sha256 != envelope.audio.sha256:
                raise IngestionConflict("capture identifier already exists with different content")
            if existing.status in {"ready", "audio_deleted"}:
                return UploadTicket(
                    envelope.capture_id, existing.status, existing.object_key, None, True
                )
            upload_url = self.objects.create_upload(
                existing.object_key, envelope.audio.content_type, envelope.audio.sha256
            )
            return UploadTicket(
                envelope.capture_id, existing.status, existing.object_key, upload_url, True
            )

        object_key = self._object_key(envelope)
        record = self.repository.create_capture(envelope, object_key)
        if record.audio_sha256 != envelope.audio.sha256:
            raise IngestionConflict("capture identifier raced with different content")
        upload_url = self.objects.create_upload(
            record.object_key, envelope.audio.content_type, envelope.audio.sha256
        )
        return UploadTicket(envelope.capture_id, record.status, object_key, upload_url, False)

    def commit(self, receiver_id: str, capture_id: str) -> CaptureRecord:
        record = self.repository.get_capture(receiver_id, capture_id)
        if not record:
            raise IngestionNotFound("capture not found")
        if record.status in {"ready", "audio_deleted"}:
            return record

        metadata = self.objects.head(record.object_key)
        if metadata.sha256 != record.audio_sha256:
            self.repository.reject_capture(
                receiver_id, capture_id, "audio sha256 metadata mismatch"
            )
            raise IntegrityFailure("audio digest does not match manifest")
        if metadata.byte_count != record.audio_bytes:
            self.repository.reject_capture(receiver_id, capture_id, "audio byte count mismatch")
            raise IntegrityFailure("audio byte count does not match manifest")
        if metadata.content_type != record.content_type:
            self.repository.reject_capture(receiver_id, capture_id, "audio content type mismatch")
            raise IntegrityFailure("audio content type does not match manifest")

        if not self.repository.commit_capture(
            receiver_id, capture_id, self.config.retention.audio_hours
        ):
            raise IngestionConflict("capture cannot transition to ready")
        ready = self.repository.get_capture(receiver_id, capture_id)
        if ready is None:
            raise RuntimeError("committed capture disappeared")
        return ready
