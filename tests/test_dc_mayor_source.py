from datetime import UTC, datetime
from pathlib import Path

from ragchew.proceedings.contracts import (
    DocumentType,
    ProceedingLifecycle,
    ProceedingType,
)
from ragchew.proceedings.discovery import ConditionalRequest
from ragchew.proceedings.sources.dc_mayor import DcMayorAdapter, parse_mayor_feed
from ragchew.proceedings.sources.http import SourceResponse

FIXTURE = Path("tests/fixtures/http/dc_mayor_feed.xml")
NOW = datetime(2026, 8, 28, 15, tzinfo=UTC)
ENDPOINT = "https://mayor.dc.gov/rss.xml"


class FakeFetcher:
    def __init__(self, response: SourceResponse):
        self.response = response
        self.request: tuple[str, ConditionalRequest | None] | None = None

    def get(
        self, url: str, conditional: ConditionalRequest | None = None
    ) -> SourceResponse:
        self.request = (url, conditional)
        return self.response


def test_mayor_feed_discovers_calendar_and_briefing_without_platform_media() -> None:
    items = parse_mayor_feed(FIXTURE.read_bytes(), NOW)
    assert len(items) == 2
    calendar, briefing = items
    assert calendar.lifecycle is ProceedingLifecycle.SCHEDULED
    assert calendar.proceeding_type is ProceedingType.OTHER
    assert calendar.media == ()
    assert {document.document_type for document in calendar.documents} == {
        DocumentType.RELEASE,
        DocumentType.OTHER_OFFICIAL_DOCUMENT,
    }
    assert calendar.metadata["excluded_link_hosts"] == ("youtube.com",)

    assert briefing.proceeding_type is ProceedingType.MAYORAL_BRIEFING
    assert briefing.lifecycle is ProceedingLifecycle.COMPLETED
    assert briefing.media == ()
    assert briefing.metadata["excluded_link_hosts"] == ("bit.ly",)
    assert briefing.metadata["publication_is_not_spoken_evidence"] is True


def test_mayor_adapter_uses_conditional_official_feed() -> None:
    response = SourceResponse(
        status_code=200,
        url=ENDPOINT,
        headers={"content-type": "application/rss+xml", "etag": '"mayor-v1"'},
        content=FIXTURE.read_bytes(),
    )
    fetcher = FakeFetcher(response)
    result = DcMayorAdapter(fetcher, clock=lambda: NOW).poll(
        ConditionalRequest(etag='"mayor-v0"')
    )
    assert len(result.proceedings) == 2
    assert fetcher.request == (ENDPOINT, ConditionalRequest(etag='"mayor-v0"'))
    assert result.etag == '"mayor-v1"'


def test_mayor_adapter_handles_not_modified() -> None:
    fetcher = FakeFetcher(
        SourceResponse(
            status_code=304,
            url=ENDPOINT,
            headers={"etag": '"same"'},
            content=b"",
        )
    )
    result = DcMayorAdapter(fetcher, clock=lambda: NOW).poll(
        ConditionalRequest(etag='"same"')
    )
    assert result.not_modified
    assert result.proceedings == ()
