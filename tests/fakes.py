from __future__ import annotations

from datetime import datetime
from typing import BinaryIO
from uuid import UUID, uuid4

from ragchew.contracts import CaptureEnvelope, EdgeHeartbeat
from ragchew.repository import CaptureRecord, JobRecord
from ragchew.storage import ObjectMetadata


class FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, ObjectMetadata] = {}
        self.deleted: list[str] = []

    def create_upload(self, key: str, content_type: str, sha256: str) -> str:
        return f"https://private.invalid/upload/{key}?sha256={sha256}&type={content_type}"

    def head(self, key: str) -> ObjectMetadata:
        return self.objects[key]

    def create_download(self, key: str, expires_seconds: int = 300) -> str:
        return f"https://private.invalid/download/{key}?expires={expires_seconds}"

    def put_file(
        self, key: str, file: BinaryIO, content_type: str, sha256: str
    ) -> None:
        file.seek(0, 2)
        self.objects[key] = ObjectMetadata(file.tell(), content_type, sha256)
        file.seek(0)

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


class FakeRepository:
    def __init__(self) -> None:
        self.captures: dict[tuple[str, str], CaptureRecord] = {}
        self.manifests: dict[tuple[str, str], CaptureEnvelope] = {}
        self.jobs: list[JobRecord] = []
        self.heartbeats: list[EdgeHeartbeat] = []
        self.rejections: list[str] = []
        self.abandoned: list[str] = []
        self.expired_audio: list[str] = []
        self.expired_transcript_count = 0

    def get_capture(self, receiver_id: str, capture_id: str) -> CaptureRecord | None:
        return self.captures.get((receiver_id, capture_id))

    def create_capture(self, envelope: CaptureEnvelope, object_key: str) -> CaptureRecord:
        key = (envelope.receiver_id, envelope.capture_id)
        record = self.captures.setdefault(
            key,
            CaptureRecord(
                receiver_id=envelope.receiver_id,
                capture_id=envelope.capture_id,
                audio_sha256=envelope.audio.sha256,
                audio_bytes=envelope.audio.byte_count,
                content_type=envelope.audio.content_type,
                object_key=object_key,
                status="uploading",
            ),
        )
        self.manifests[key] = envelope
        return record

    def commit_capture(self, receiver_id: str, capture_id: str, audio_hours: int) -> bool:
        key = (receiver_id, capture_id)
        record = self.captures.get(key)
        if not record or record.status in {"rejected", "expired"}:
            return False
        if record.status != "ready":
            self.captures[key] = CaptureRecord(
                receiver_id=record.receiver_id,
                capture_id=record.capture_id,
                audio_sha256=record.audio_sha256,
                audio_bytes=record.audio_bytes,
                content_type=record.content_type,
                object_key=record.object_key,
                status="ready",
            )
            self.jobs.append(
                JobRecord(uuid4(), "transcribe", "capture", capture_id, record.audio_sha256, 0)
            )
        return True

    def reject_capture(self, receiver_id: str, capture_id: str, diagnostic: str) -> None:
        self.rejections.append(diagnostic)
        record = self.captures[(receiver_id, capture_id)]
        self.captures[(receiver_id, capture_id)] = CaptureRecord(
            receiver_id=record.receiver_id,
            capture_id=record.capture_id,
            audio_sha256=record.audio_sha256,
            audio_bytes=record.audio_bytes,
            content_type=record.content_type,
            object_key=record.object_key,
            status="rejected",
        )

    def record_heartbeat(self, heartbeat: EdgeHeartbeat) -> None:
        self.heartbeats.append(heartbeat)

    def claim_job(
        self,
        worker_id: str,
        lease_seconds: int,
        stages: tuple[str, ...] | None = None,
    ) -> JobRecord | None:
        for index, record in enumerate(self.jobs):
            if stages is None or record.stage in stages:
                return self.jobs.pop(index)
        return None

    def complete_job(self, job_id: UUID, worker_id: str, output_id: str) -> bool:
        return True

    def fail_job(
        self,
        job_id: UUID,
        worker_id: str,
        error: str,
        maximum_attempts: int,
        delay_seconds: float,
    ) -> bool:
        return True

    def expire_abandoned_uploads(self, older_than: datetime) -> list[str]:
        return self.abandoned

    def expire_audio(self, now: datetime) -> list[str]:
        return self.expired_audio

    def expire_transcripts(self, now: datetime) -> int:
        return self.expired_transcript_count

    def job_backlog(self) -> dict[str, int]:
        return {}
