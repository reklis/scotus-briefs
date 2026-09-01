"""Authenticated edge uploader with at-least-once semantics."""

from __future__ import annotations

import httpx

from ragchew.config import MvpConfig
from ragchew.edge.spool import EdgeSpool, SpoolEntry


class PermanentUploadError(Exception):
    pass


class EdgeUploader:
    def __init__(
        self,
        base_url: str,
        receiver_id: str,
        token: str,
        spool: EdgeSpool,
        config: MvpConfig,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.receiver_id = receiver_id
        self.token = token
        self.spool = spool
        self.config = config
        self.client = client or httpx.Client(timeout=30)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def upload(self, entry: SpoolEntry) -> None:
        endpoint = f"{self.base_url}/v1/receivers/{self.receiver_id}/captures"
        response = self.client.post(
            endpoint,
            headers=self.headers,
            json=entry.envelope.model_dump(mode="json"),
        )
        if response.status_code == 409:
            raise PermanentUploadError(response.text)
        response.raise_for_status()
        ticket = response.json()
        upload_url = ticket.get("upload_url")
        if upload_url:
            with entry.audio_path.open("rb") as audio:
                uploaded = self.client.put(
                    upload_url,
                    headers={
                        "Content-Type": entry.envelope.audio.content_type,
                        "x-amz-meta-sha256": entry.envelope.audio.sha256,
                    },
                    content=audio,
                )
            uploaded.raise_for_status()
        committed = self.client.post(
            f"{endpoint}/{entry.capture_id}/commit",
            headers=self.headers,
        )
        if committed.status_code == 409:
            raise PermanentUploadError(committed.text)
        committed.raise_for_status()

    def process_one(self) -> bool:
        entry = self.spool.claim()
        if entry is None:
            return False
        try:
            self.upload(entry)
        except PermanentUploadError as error:
            self.spool.conflict(entry.capture_id, str(error))
        except (httpx.HTTPError, OSError) as error:
            delay = min(
                self.config.retry.maximum_delay_seconds,
                self.config.retry.base_delay_seconds * (2 ** max(0, entry.attempts)),
            )
            self.spool.retry(entry.capture_id, str(error), delay)
        else:
            self.spool.acknowledge(entry.capture_id)
        return True
