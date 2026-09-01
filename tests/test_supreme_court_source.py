from datetime import UTC, datetime
from pathlib import Path

from ragchew.proceedings.contracts import (
    DocumentType,
    ProceedingLifecycle,
)
from ragchew.proceedings.discovery import ConditionalRequest
from ragchew.proceedings.sources.http import SourceResponse
from ragchew.proceedings.sources.supreme_court import (
    SupremeCourtAdapter,
    parse_argument_detail,
    parse_argument_index,
    parse_related_opinion_documents,
    parse_transcript_archive_index,
)

FIXTURES = Path("tests/fixtures/http")
NOW = datetime(2026, 4, 21, 18, tzinfo=UTC)


class FakeFetcher:
    def __init__(self, responses: dict[str, SourceResponse]):
        self.responses = responses
        self.requests: list[tuple[str, ConditionalRequest | None]] = []

    def get(
        self, url: str, conditional: ConditionalRequest | None = None
    ) -> SourceResponse:
        self.requests.append((url, conditional))
        return self.responses[url]


def response(url: str, fixture: str, **headers: str) -> SourceResponse:
    return SourceResponse(
        status_code=200,
        url=url,
        headers={"content-type": "text/html; charset=utf-8", **headers},
        content=(FIXTURES / fixture).read_bytes(),
    )


def test_argument_index_and_detail_are_parsed_from_official_links() -> None:
    index_url = "https://www.supremecourt.gov/oral_arguments/argument_audio/2025"
    detail_url = "https://www.supremecourt.gov/oral_arguments/audio/2025/25-466"
    rows = parse_argument_index(
        (FIXTURES / "supreme_court_argument_index.html").read_text(), index_url
    )
    assert rows[0] == (
        "25-466",
        "Sripetch v. SEC",
        datetime(2026, 4, 20, tzinfo=UTC),
        detail_url,
    )
    media, documents = parse_argument_detail(
        (FIXTURES / "supreme_court_argument_detail.html").read_text(),
        detail_url,
        "25-466",
        "2025",
    )
    assert media is not None
    assert media.source_url.endswith("/25-466.mp3")
    assert {item.document_type for item in documents} == {
        DocumentType.DOCKET,
        DocumentType.OFFICIAL_TRANSCRIPT,
    }


def test_transcript_archive_parser_supports_y2k_pdfs_and_original_dockets() -> None:
    index_url = "https://www.supremecourt.gov/oral_arguments/argument_transcript/2000"
    rows = parse_transcript_archive_index(
        (FIXTURES / "supreme_court_transcript_archive_2000.html").read_text(),
        index_url,
        "2000",
    )
    assert rows == [
        (
            "00-6374",
            "Becker v. Montgomery",
            datetime(2001, 4, 16, tzinfo=UTC),
            "https://www.supremecourt.gov/pdfs/transcripts/2000/00-6374.pdf",
        ),
        (
            "130 ORIG.",
            "New Hampshire v. Maine",
            datetime(2001, 4, 16, tzinfo=UTC),
            "https://www.supremecourt.gov/pdfs/transcripts/2000/130orig.pdf",
        ),
    ]


def test_archive_adapter_emits_direct_transcript_documents_without_audio() -> None:
    index_url = "https://www.supremecourt.gov/oral_arguments/argument_transcript/2000"
    fetcher = FakeFetcher(
        {index_url: response(index_url, "supreme_court_transcript_archive_2000.html")}
    )
    result = SupremeCourtAdapter(
        fetcher,
        term="2000",
        clock=lambda: NOW,
        transcript_archive=True,
    ).poll(ConditionalRequest())
    assert len(result.proceedings) == 2
    assert all(item.lifecycle is ProceedingLifecycle.COMPLETED for item in result.proceedings)
    assert all(item.media == () for item in result.proceedings)
    assert all(
        item.documents[0].document_type is DocumentType.OFFICIAL_TRANSCRIPT
        for item in result.proceedings
    )
    assert all(len(item.documents) == 1 for item in result.proceedings)
    assert [url for url, _ in fetcher.requests] == [index_url]


def test_recent_transcript_archive_adds_available_docket_and_disposition_documents() -> None:
    index_url = "https://www.supremecourt.gov/oral_arguments/argument_transcript/2025"
    opinion_url = "https://www.supremecourt.gov/opinions/slipopinion/25"
    order_url = "https://www.supremecourt.gov/opinions/relatingtoorders/25"
    archive = SourceResponse(
        status_code=200,
        url=index_url,
        headers={"content-type": "text/html; charset=utf-8"},
        content=(
            b'<table><tr><td>4/20/2026</td><td>Sripetch v. SEC</td>'
            b'<td><a href="/oral_arguments/argument_transcripts/2025/25-466.pdf">'
            b'25-466</a></td></tr></table>'
        ),
    )
    fetcher = FakeFetcher(
        {
            index_url: archive,
            opinion_url: response(opinion_url, "supreme_court_opinions.html"),
            order_url: response(order_url, "supreme_court_orders.html"),
        }
    )
    result = SupremeCourtAdapter(
        fetcher,
        term="2025",
        clock=lambda: NOW,
        transcript_archive=True,
    ).poll(ConditionalRequest())
    document_types = {item.document_type for item in result.proceedings[0].documents}
    assert DocumentType.OFFICIAL_TRANSCRIPT in document_types
    assert DocumentType.DOCKET in document_types
    assert DocumentType.OPINION in document_types


def test_opinion_parser_requires_same_row_docket_evidence() -> None:
    documents = parse_related_opinion_documents(
        (FIXTURES / "supreme_court_opinions.html").read_text(),
        "https://www.supremecourt.gov/opinions/slipopinion/25",
        "25-466",
        document_type=DocumentType.OPINION,
    )
    assert len(documents) == 1
    assert documents[0].official_url.endswith("25-466_example.pdf")


def test_opinion_parser_prefers_exact_docket_files_and_excludes_diff_reports() -> None:
    html = """<table><tr><td>25-5 Example</td>
    <td><a href='/opinions/25pdf/25-5_main.pdf'>main</a></td>
    <td><a href='/opinions/25pdf/25-5146_new.pdf'>other case</a></td>
    <td><a href='/opinions/25pdf/25-5_diff_test.pdf'>comparison</a></td>
    <td><a href='/opinions/25pdf/607us1r02.pdf'>unrelated volume file</a></td>
    </tr></table>"""
    documents = parse_related_opinion_documents(
        html,
        "https://www.supremecourt.gov/opinions/slipopinion/25",
        "25-5",
        document_type=DocumentType.OPINION,
    )
    assert [item.official_url.rsplit("/", 1)[-1] for item in documents] == [
        "25-5_main.pdf"
    ]


def test_opinion_parser_does_not_match_a_longer_docket_number() -> None:
    documents = parse_related_opinion_documents(
        (FIXTURES / "supreme_court_opinions.html").read_text(),
        "https://www.supremecourt.gov/opinions/slipopinion/25",
        "25-46",
        document_type=DocumentType.OPINION,
    )
    assert documents == ()


def test_adapter_emits_recent_archive_transcript_and_docket_descriptors() -> None:
    index_url = "https://www.supremecourt.gov/oral_arguments/argument_audio/2025"
    detail_url = "https://www.supremecourt.gov/oral_arguments/audio/2025/25-466"
    opinion_url = "https://www.supremecourt.gov/opinions/slipopinion/25"
    order_url = "https://www.supremecourt.gov/opinions/relatingtoorders/25"
    fetcher = FakeFetcher(
        {
            index_url: response(
                index_url,
                "supreme_court_argument_index.html",
                etag='"argument-index-v1"',
            ),
            detail_url: response(detail_url, "supreme_court_argument_detail.html"),
            opinion_url: response(opinion_url, "supreme_court_opinions.html"),
            order_url: response(order_url, "supreme_court_orders.html"),
        }
    )
    adapter = SupremeCourtAdapter(fetcher, term="2025", clock=lambda: NOW)
    result = adapter.poll(ConditionalRequest(etag='"argument-index-v0"'))
    assert result.etag == '"argument-index-v1"'
    assert len(result.proceedings) == 2
    recent = result.proceedings[0]
    assert recent.lifecycle is ProceedingLifecycle.COMPLETED
    assert recent.media == ()
    assert recent.metadata["audio_available"] is True
    assert recent.metadata["audio_collection"] == "disabled"
    assert {document.document_type for document in recent.documents} == {
        DocumentType.DOCKET,
        DocumentType.OFFICIAL_TRANSCRIPT,
        DocumentType.OPINION,
        DocumentType.ORDER,
    }
    older = result.proceedings[1]
    assert older.lifecycle is ProceedingLifecycle.ARCHIVE_PENDING
    assert older.media == ()
    assert fetcher.requests[0][1] == ConditionalRequest(etag='"argument-index-v0"')


def test_adapter_discovers_later_opinion_and_order_documents() -> None:
    index_url = "https://www.supremecourt.gov/oral_arguments/argument_audio/2025"
    opinion_url = "https://www.supremecourt.gov/opinions/slipopinion/25"
    order_url = "https://www.supremecourt.gov/opinions/relatingtoorders/25"
    fetcher = FakeFetcher(
        {
            index_url: response(index_url, "supreme_court_argument_index.html"),
            opinion_url: response(opinion_url, "supreme_court_opinions.html"),
            order_url: response(order_url, "supreme_court_orders.html"),
        }
    )
    adapter = SupremeCourtAdapter(fetcher, term="2025", clock=lambda: NOW)
    documents = adapter.discover_related_documents("25-466")
    assert {document.document_type for document in documents} == {
        DocumentType.OPINION,
        DocumentType.ORDER,
    }


def test_adapter_preserves_not_modified_without_parsing() -> None:
    index_url = "https://www.supremecourt.gov/oral_arguments/argument_audio/2025"
    fetcher = FakeFetcher(
        {
            index_url: SourceResponse(
                status_code=304,
                url=index_url,
                headers={"etag": '"same"'},
                content=b"",
            )
        }
    )
    result = SupremeCourtAdapter(fetcher, term="2025", clock=lambda: NOW).poll(
        ConditionalRequest(etag='"same"')
    )
    assert result.not_modified
    assert result.proceedings == ()
