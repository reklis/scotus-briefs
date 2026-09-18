"""Small, retrying Ollama JSON client with model identity capture."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_MODEL = "ragchew-gpt-oss:120b-32k"


class OllamaError(RuntimeError):
    pass


class OllamaResponseError(OllamaError):
    pass


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    name: str
    digest: str


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        *,
        model: str = DEFAULT_MODEL,
        context_length: int = 32_768,
        timeout: float = 300.0,
        attempts: int = 3,
        parameters: Mapping[str, object] | None = None,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be positive")
        if context_length < 1:
            raise ValueError("context_length must be positive")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.context_length = context_length
        self.attempts = attempts
        self.parameters = {
            "temperature": 0,
            "seed": 0,
            "num_ctx": context_length,
            **dict(parameters or {}),
        }
        self._owned_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
        )
        self.sleeper = sleeper

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owned_client:
            self.client.close()

    def health(self) -> ModelIdentity:
        tags = self._request("GET", "/api/tags")
        models = tags.get("models")
        if not isinstance(models, list):
            raise OllamaResponseError("Ollama tags response lacks models")
        match = next(
            (item for item in models if isinstance(item, dict) and item.get("name") == self.model),
            None,
        )
        if match is None:
            raise OllamaError(f"required Ollama model is unavailable: {self.model}")
        shown = self._request("POST", "/api/show", {"name": self.model})
        digest = _digest(shown) or _string(match.get("digest"))
        if not digest:
            raise OllamaResponseError("Ollama did not report a model digest")
        context = _context_length(shown)
        if context is not None and context < self.context_length:
            raise OllamaError(f"model context {context} is below configured {self.context_length}")
        return ModelIdentity(self.model, digest)

    def generate_json(self, prompt: str, *, schema: dict[str, Any] | None = None) -> Any:
        # GPT-OSS emits its final answer through Ollama's chat template. The
        # generate endpoint can return an empty response after producing only
        # reasoning tokens, so use chat and read message.content.
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": schema or "json",
            "options": self.parameters,
        }
        last_error: OllamaResponseError | None = None
        for attempt in range(self.attempts):
            response = self._request("POST", "/api/chat", payload)
            message = response.get("message")
            raw = message.get("content") if isinstance(message, dict) else response.get("response")
            if not isinstance(raw, str):
                last_error = OllamaResponseError("Ollama generation response lacks text")
            else:
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as error:
                    last_error = OllamaResponseError(f"model returned malformed JSON: {error.msg}")
            if attempt + 1 < self.attempts:
                self.sleeper(min(2**attempt, 8))
        assert last_error is not None
        raise last_error

    def _request(
        self, method: str, endpoint: str, payload: Mapping[str, object] | None = None
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                response = self.client.request(
                    method,
                    f"{self.base_url}{endpoint}",
                    json=dict(payload) if payload is not None else None,
                )
                response.raise_for_status()
                parsed = response.json()
                if not isinstance(parsed, dict):
                    raise OllamaResponseError("Ollama response must be a JSON object")
                return parsed
            except (httpx.HTTPError, ValueError, OllamaResponseError) as error:
                last_error = error
                retryable = not isinstance(error, httpx.HTTPStatusError) or (
                    error.response.status_code == 429 or error.response.status_code >= 500
                )
                if not retryable or attempt + 1 == self.attempts:
                    break
                self.sleeper(min(2**attempt, 8))
        raise OllamaError(f"Ollama request failed: {last_error}") from last_error


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _digest(payload: Mapping[str, Any]) -> str | None:
    direct = _string(payload.get("digest"))
    if direct:
        return direct
    details = payload.get("details")
    return _string(details.get("digest")) if isinstance(details, dict) else None


def _context_length(payload: Mapping[str, Any]) -> int | None:
    model_info = payload.get("model_info")
    if not isinstance(model_info, dict):
        return None
    for key, value in model_info.items():
        if key.endswith(".context_length") and isinstance(value, int):
            return value
    return None
