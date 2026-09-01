from datetime import UTC, datetime
from pathlib import Path

from ragchew.proceedings.contracts import (
    DocumentType,
    ProceedingLifecycle,
    ProceedingType,
)
from ragchew.proceedings.discovery import ConditionalRequest
from ragchew.proceedings.sources.dc_council import (
    DcCouncilAdapter,
    parse_council_events,
    parse_council_release_feed,
)
from ragchew.proceedings.sources.http import SourceResponse

FIXTURES = Path("tests/fixtures/http")
NOW = datetime(2026, 8, 28, 15, tzinfo=UTC)
ENDPOINT = (
    "https://dccouncil.gov/wp-json/tribe/events/v1/events?"
    "per_page=50&start_date=2026-08-26&end_date=2026-09-27"
)


class FakeFetcher:
    def __init__(self, responses: dict[str, SourceResponse]):
        self.responses = responses
        self.requests: list[tuple[str, ConditionalRequest | None]] = []

    def get(
        self, url: str, conditional: ConditionalRequest | None = None
    ) -> SourceResponse:
        self.requests.append((url, conditional))
        return self.responses[url]


def response(url: str, fixture: str, content_type: str) -> SourceResponse:
    return SourceResponse(
        status_code=200,
        url=url,
        headers={"content-type": content_type, "etag": '"council-v1"'},
        content=(FIXTURES / fixture).read_bytes(),
    )


def test_council_api_keeps_platform_and_lims_links_out_of_descriptors() -> None:
    items = parse_council_events(
        (FIXTURES / "dc_council_events.json").read_bytes(), NOW
    )
    assert len(items) == 1
    item = items[0]
    assert item.proceeding_type is ProceedingType.HEARING
    assert item.lifecycle is ProceedingLifecycle.LIVE
    assert item.media == ()
    assert len(item.documents) == 1
    assert item.documents[0].document_type is DocumentType.AGENDA
    assert item.documents[0].official_url.startswith("https://dccouncil.gov/")
    assert item.metadata["excluded_link_hosts"] == (
        "dc.granicus.com",
        "lims.dccouncil.gov",
    )
    assert item.metadata["legislation_references"] == ["B26-0001"]


def test_council_adapter_uses_documented_bounded_api_window() -> None:
    fetcher = FakeFetcher(
        {ENDPOINT: response(ENDPOINT, "dc_council_events.json", "application/json")}
    )
    result = DcCouncilAdapter(fetcher, clock=lambda: NOW).poll(
        ConditionalRequest(etag='"council-v0"')
    )
    assert len(result.proceedings) == 1
    assert result.proceedings[0].media == ()
    assert fetcher.requests == [(ENDPOINT, ConditionalRequest(etag='"council-v0"'))]


def test_council_release_feed_accepts_only_same_host_releases() -> None:
    documents = parse_council_release_feed(
        (FIXTURES / "dc_council_feed.xml").read_bytes()
    )
    assert len(documents) == 1
    assert documents[0].document_type is DocumentType.RELEASE
    assert documents[0].official_url.startswith("https://dccouncil.gov/")
