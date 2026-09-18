from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from scotus_guide.discovery import (
    DiscoveryResult,
    ScotusDiscoveryAdapter,
    SourcePage,
    parse_source_page,
    reconcile_discovery_state,
)
from scotus_guide.models import DocumentType, Lifecycle

SOURCE_URL = "https://www.supremecourt.gov/oral_arguments/argument_transcript/2024"


def test_official_fixture_normalizes_consolidated_and_application_dockets() -> None:
    html = Path("tests/fixtures/discovery/official-index.html").read_text()
    source = SourcePage(url=SOURCE_URL, term=2024, date_kind="argument")
    cases, documents = parse_source_page(html, source)
    assert cases[0].docket_numbers == ["24-7", "24-38"]
    assert cases[0].primary_docket == "24-7"
    assert cases[0].title == "Alpha Corp. v. Citizen"
    assert cases[0].lifecycle is Lifecycle.ARGUED
    assert cases[0].dates["argument"].isoformat() == "2024-10-07"
    assert next(case for case in cases if case.primary_docket == "24A884")
    transcript = next(item for item in documents if item.document_type is DocumentType.TRANSCRIPT)
    assert transcript.cases[0].docket_numbers == ["24-7", "24-38"]
    assert str(transcript.url).startswith("https://www.supremecourt.gov/")
    assert {item.document_type for item in documents} >= {
        DocumentType.OPINION,
        DocumentType.MERITS_BRIEF,
        DocumentType.ORDER,
    }


def test_docket_page_briefs_and_aggregate_orders_are_supported() -> None:
    docket_html = """
    <h2>No. 24-200</h2><h3>Person v. Agency</h3>
    <table><tr><td>01/02/25</td><td>Merits filing</td>
    <td><a href="/DocketPDF/24/24-200/respondent-brief.pdf">Respondent brief</a></td></tr></table>
    """
    cases, documents = parse_source_page(docket_html, SourcePage(url=SOURCE_URL, term=2024))
    assert cases[0].title == "Person v. Agency"
    assert cases[0].primary_docket == "24-200"
    assert documents[0].document_type is DocumentType.MERITS_BRIEF

    order_html = """
    <table><tr><td>January 2, 2025</td>
    <td><a href="/orders/courtorders/010225zor.pdf">Order List</a></td></tr></table>
    """
    cases, documents = parse_source_page(
        order_html,
        SourcePage(url=SOURCE_URL, term=2024, document_type=DocumentType.ORDER),
    )
    assert cases == []
    assert documents[0].cases == []


def test_discovery_state_reports_metadata_changes_without_deleting_absent_cases(
    tmp_path: Path,
) -> None:
    html = Path("tests/fixtures/discovery/official-index.html").read_text()
    source = SourcePage(url=SOURCE_URL, term=2024)
    cases, documents = parse_source_page(html, source)
    state = tmp_path / "discovered.json"
    first = DiscoveryResult(cases=cases, documents=documents)
    assert reconcile_discovery_state(state, first) == {case.case_id for case in cases}
    assert (
        reconcile_discovery_state(state, DiscoveryResult(cases=cases, documents=documents)) == set()
    )
    changed_case = cases[0].model_copy(update={"title": "Corrected title"})
    assert reconcile_discovery_state(state, DiscoveryResult(cases=[changed_case])) == {
        changed_case.case_id
    }
    payload = state.read_text()
    assert all(case.case_id in payload for case in cases)


def test_source_page_rejects_nonofficial_indexes() -> None:
    with pytest.raises(ValueError, match="official HTTPS"):
        SourcePage(url="https://example.com/fake", term=2024)


def test_adapter_retries_transient_failure_with_bounded_attempts() -> None:
    html = Path("tests/fixtures/discovery/official-index.html").read_text()
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, text=html, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = ScotusDiscoveryAdapter(
            client,
            attempts=2,
            backoff_seconds=0.25,
            request_delay_seconds=0,
            sleeper=sleeps.append,
        ).discover([SourcePage(url=SOURCE_URL, term=2024)])
    assert not result.failures
    assert len(result.documents) == 4
    assert calls == 2
    assert sleeps == [0.25]


def test_empty_or_nonofficial_source_is_reported_not_destructive() -> None:
    html = "<html><table><tr><td>No documents today</td></tr></table></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = ScotusDiscoveryAdapter(client, request_delay_seconds=0).discover(
            [SourcePage(url=SOURCE_URL, term=2024)]
        )
    assert result.documents == []
    assert "unexpectedly contained no PDF" in result.failures[0].error
    assert result.as_dict()["failures"]
