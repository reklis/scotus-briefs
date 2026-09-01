import json
from pathlib import Path

import httpx
import pytest

from ragchew.proceedings.discovery import ConditionalRequest
from ragchew.proceedings.sources.http import HttpxSourceFetcher, SourceFetchError

FIXTURES = Path("tests/fixtures/http")


def test_recorded_contract_manifest_covers_every_initial_source() -> None:
    manifest = json.loads((FIXTURES / "official_source_contracts.json").read_text())
    contracts = manifest["contracts"]
    assert {item["source_id"] for item in contracts} == {
        "supreme_court",
        "house_floor",
        "dc_council",
        "dc_mayor",
    }
    for contract in contracts:
        fixture = contract["fixture"]
        if fixture:
            assert (FIXTURES / fixture).is_file()
    allowed_media = [item for item in contracts if item["media_allowed"]]
    assert [(item["source_id"], item["method"]) for item in allowed_media] == [
        ("supreme_court", "downloadable_file")
    ]
    assert not any(item.get("collection_enabled", False) for item in contracts)


def test_http_fetcher_sends_conditionals_and_descriptive_user_agent() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"prior"'
        assert request.headers["if-modified-since"] == "yesterday"
        assert "contact" in request.headers["user-agent"]
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain", "ETag": '"new"'},
            content=b"official",
        )

    fetcher = HttpxSourceFetcher(
        user_agent="ragchew-test contact=test@example.test",
        minimum_interval_seconds=0,
        client=httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False),
    )
    response = fetcher.get(
        "https://official.example/data",
        ConditionalRequest(etag='"prior"', last_modified="yesterday"),
    )
    assert response.content == b"official"
    assert response.headers["etag"] == '"new"'


def test_http_fetcher_rejects_redirects_and_oversized_responses() -> None:
    redirecting = HttpxSourceFetcher(
        user_agent="ragchew-test contact=test@example.test",
        minimum_interval_seconds=0,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    302, headers={"Location": "https://platform.example/media"}
                )
            ),
            follow_redirects=False,
        ),
    )
    with pytest.raises(SourceFetchError, match="unexpected redirect"):
        redirecting.get("https://official.example/data")

    oversized = HttpxSourceFetcher(
        user_agent="ragchew-test contact=test@example.test",
        maximum_bytes=4,
        minimum_interval_seconds=0,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"too-large")
            )
        ),
    )
    with pytest.raises(SourceFetchError, match="byte limit"):
        oversized.get("https://official.example/data")
