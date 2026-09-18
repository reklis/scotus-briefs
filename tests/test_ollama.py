from __future__ import annotations

import json

import httpx
import pytest
from scotus_guide.ollama import DEFAULT_MODEL, OllamaClient, OllamaError, OllamaResponseError


def test_health_captures_digest_and_context() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": DEFAULT_MODEL, "digest": "fallback"}]}
            )
        assert request.url.path == "/api/show"
        return httpx.Response(
            200,
            json={"digest": "sha256:model", "model_info": {"gpt.context_length": 32768}},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ollama = OllamaClient("http://ollama", client=client, sleeper=lambda _: None)
    assert ollama.health().digest == "sha256:model"


def test_generate_retries_server_failure_and_sends_deterministic_options() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"response": '{"ok": true}'})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ollama = OllamaClient("http://ollama", client=client, attempts=2, sleeper=lambda _: None)
    assert ollama.generate_json("prompt") == {"ok": True}
    assert requests[-1]["messages"] == [{"role": "user", "content": "prompt"}]
    assert requests[-1]["stream"] is False
    assert requests[-1]["options"] == {"temperature": 0, "seed": 0, "num_ctx": 32768}


def test_generate_reads_chat_message_content() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": '{"ok": true}',
                        "thinking": "private reasoning",
                    }
                },
                request=request,
            )
        )
    )
    assert OllamaClient("http://ollama", client=client).generate_json("prompt") == {"ok": True}


def test_malformed_model_output_and_missing_model_fail_closed() -> None:
    malformed = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"response": "not json"})
        )
    )
    with pytest.raises(OllamaResponseError, match="malformed JSON"):
        OllamaClient("http://ollama", client=malformed).generate_json("prompt")

    missing = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"models": []}))
    )
    with pytest.raises(OllamaError, match="unavailable"):
        OllamaClient("http://ollama", client=missing).health()
