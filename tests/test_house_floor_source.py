from datetime import UTC, datetime
from pathlib import Path

from ragchew.proceedings.contracts import DocumentType, ProceedingLifecycle
from ragchew.proceedings.discovery import ConditionalRequest
from ragchew.proceedings.sources.house_floor import HouseFloorAdapter, parse_house_floor_xml
from ragchew.proceedings.sources.http import SourceResponse

FIXTURE = Path("tests/fixtures/http/house_floor_day.xml")
NOW = datetime(2026, 7, 23, 16, tzinfo=UTC)
ENDPOINT = "https://clerk.house.gov/floor/20260723.xml"


class FakeFetcher:
    def __init__(self, response: SourceResponse):
        self.response = response
        self.conditional: ConditionalRequest | None = None

    def get(
        self, url: str, conditional: ConditionalRequest | None = None
    ) -> SourceResponse:
        assert url == ENDPOINT
        self.conditional = conditional
        return self.response


def test_floor_xml_emits_activity_legislation_amendment_and_vote_records() -> None:
    item = parse_house_floor_xml(FIXTURE.read_bytes(), ENDPOINT)
    assert item.external_id == "house-floor-20260723"
    assert item.lifecycle is ProceedingLifecycle.COMPLETED
    assert item.actual_start_at == datetime(2026, 7, 23, 13, tzinfo=UTC)
    assert item.actual_end_at == datetime(2026, 7, 23, 14, 50, tzinfo=UTC)
    assert item.media == ()
    by_type = {document.document_type: document for document in item.documents}
    assert by_type[DocumentType.LEGISLATION].official_url.endswith("/house-bill/8884")
    assert by_type[DocumentType.AMENDMENT].official_url.endswith("/house-amendment/123")
    assert by_type[DocumentType.VOTE_RECORD].official_url.endswith("/roll283.xml")
    assert by_type[DocumentType.OTHER_OFFICIAL_DOCUMENT].official_url == ENDPOINT
    assert item.metadata["media_collection"] == "not_approved"


def test_house_adapter_uses_conditional_daily_clerk_xml() -> None:
    response = SourceResponse(
        status_code=200,
        url=ENDPOINT,
        headers={"content-type": "text/xml", "etag": '"floor-v1"'},
        content=FIXTURE.read_bytes(),
    )
    fetcher = FakeFetcher(response)
    result = HouseFloorAdapter(fetcher, clock=lambda: NOW).poll(
        ConditionalRequest(etag='"floor-v0"')
    )
    assert len(result.proceedings) == 1
    assert result.proceedings[0].media == ()
    assert result.etag == '"floor-v1"'
    assert fetcher.conditional == ConditionalRequest(etag='"floor-v0"')


def test_house_adapter_handles_not_modified() -> None:
    response = SourceResponse(
        status_code=304,
        url=ENDPOINT,
        headers={"etag": '"same"'},
        content=b"",
    )
    result = HouseFloorAdapter(FakeFetcher(response), clock=lambda: NOW).poll(
        ConditionalRequest(etag='"same"')
    )
    assert result.not_modified
    assert result.proceedings == ()
