"""Bounded HTTP retrieval for source-specific adapters."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from ragchew.proceedings.discovery import ConditionalRequest


class SourceFetchError(RuntimeError):
    """Raised when an official endpoint violates its reviewed HTTP contract."""


@dataclass(frozen=True)
class SourceResponse:
    status_code: int
    url: str
    headers: dict[str, str]
    content: bytes

    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.rsplit("charset=", 1)[1].split(";", 1)[0].strip()
        return self.content.decode(charset, "replace")


class SourceFetcher(Protocol):
    def get(
        self, url: str, conditional: ConditionalRequest | None = None
    ) -> SourceResponse: ...


class HttpxSourceFetcher:
    """HTTP client with no redirects, response bounds, and host-local crawl delay."""

    def __init__(
        self,
        *,
        user_agent: str,
        maximum_bytes: int = 16 * 1024 * 1024,
        minimum_interval_seconds: float = 1.0,
        timeout_seconds: float = 30,
        client: httpx.Client | None = None,
    ) -> None:
        if not user_agent.strip() or "contact" not in user_agent.lower():
            raise ValueError("source user agent must include contact information")
        self.user_agent = user_agent
        self.maximum_bytes = maximum_bytes
        self.minimum_interval_seconds = minimum_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.client = client or httpx.Client(follow_redirects=False)
        self._last_request_at: float | None = None

    def get(
        self, url: str, conditional: ConditionalRequest | None = None
    ) -> SourceResponse:
        if self._last_request_at is not None:
            remaining = self.minimum_interval_seconds - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}
        if conditional and conditional.etag:
            headers["If-None-Match"] = conditional.etag
        if conditional and conditional.last_modified:
            headers["If-Modified-Since"] = conditional.last_modified
        try:
            with self.client.stream(
                "GET", url, headers=headers, timeout=self.timeout_seconds
            ) as response:
                self._last_request_at = time.monotonic()
                if 300 <= response.status_code < 400:
                    location = response.headers.get("location", "missing")
                    raise SourceFetchError(f"unexpected redirect to {location}")
                if response.status_code not in {200, 304}:
                    raise SourceFetchError(
                        f"official endpoint returned HTTP {response.status_code}"
                    )
                announced = response.headers.get("content-length")
                if announced and int(announced) > self.maximum_bytes:
                    raise SourceFetchError("official response exceeds configured byte limit")
                chunks: list[bytes] = []
                received = 0
                for chunk in response.iter_bytes():
                    received += len(chunk)
                    if received > self.maximum_bytes:
                        raise SourceFetchError("official response exceeds configured byte limit")
                    chunks.append(chunk)
                return SourceResponse(
                    status_code=response.status_code,
                    url=str(response.url),
                    headers={key.lower(): value for key, value in response.headers.items()},
                    content=b"".join(chunks),
                )
        except httpx.HTTPError as error:
            raise SourceFetchError(f"official endpoint request failed: {error}") from error
