"""End-to-end and unit tests for the simplified SCOTUS citizen's guide."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from scotus.docket import (
    CaseDocket,
    FilingLink,
    Proceeding,
    classify_filing,
    discover_dockets_from_html,
    infer_term,
    is_official_url,
    monitor_dockets,
    normalize_docket,
    parse_docket_html,
)
from scotus.explain import LLMConfig, explain_case, extract_summary
from scotus.extract import build_case_briefing
from scotus.site import build_site, render_markdown

SAMPLE_INDEX_HTML = """
<html><body>
  <table>
    <tr><th>Date</th><th>Docket</th><th>Case</th></tr>
    <tr>
      <td>06/27/25</td>
      <td><a href="/docket/docketfiles/html/public/24-297.html">24-297</a></td>
      <td><a href="/opinions/24pdf/24-297_4f14.pdf">Mahmoud v. Taylor</a></td>
    </tr>
    <tr>
      <td>06/20/25</td>
      <td>24-154</td>
      <td>Catholic Charities Bureau v. Wisconsin</td>
    </tr>
  </table>
</body></html>
"""

SAMPLE_DOCKET_HTML = """
<html><body>
  <span class="title">Mahmoud v. Taylor</span>
  <table id="proceedings">
    <tr><th>Date</th><th>Proceedings and Orders</th></tr>
    <tr>
      <td>Sep 12 2024</td>
      <td>Petition for a writ of certiorari filed.
        <a href="/DocketPDF/24/24-297/petition.pdf">Petition</a>
        <a href="https://evil.example.com/bad.pdf">Ignored</a>
      </td>
    </tr>
    <tr>
      <td>Jan 17 2025</td>
      <td>Petition GRANTED.</td>
    </tr>
    <tr>
      <td>Apr 22 2025</td>
      <td>Argued.
        <a href="/oral_arguments/argument_transcripts/2024/24-297.pdf">Transcript</a>
      </td>
    </tr>
    <tr>
      <td>Jun 27 2025</td>
      <td>Judgment reversed and remanded.
        <a href="/opinions/24pdf/24-297_4f14.pdf">Slip Opinion</a>
      </td>
    </tr>
  </table>
</body></html>
"""

SAMPLE_LLM_MARKDOWN = """\
## In Plain English
Parents challenged a school board policy that stopped allowing opt-outs from certain storybooks. The Supreme Court ruled that the policy burdened the parents' religious exercise.

## The Backstory
A Maryland school board introduced new books and ended its opt-out notice policy.

## What Each Side Argues
- **Parents:** Forcing young children to participate without opt-outs violates the First Amendment.
- **School Board:** Managing public school curriculum does not coerce anyone's religion.

## Where the Case Stands
The Court reversed the lower court on June 27, 2025.

## Legal Terms Explained
- **Writ of certiorari:** An order from the Supreme Court agreeing to review a lower court's decision.
"""


def test_docket_normalization_and_helpers() -> None:
    assert normalize_docket("No. 24-297") == "24-297"
    assert normalize_docket("24A100") == "24A100"
    assert infer_term("24-297") == 2024
    assert infer_term("99-12") == 1999
    assert is_official_url("https://www.supremecourt.gov/opinions/24pdf/24-297.pdf")
    assert not is_official_url("http://www.supremecourt.gov/opinions/24pdf/24-297.pdf")
    assert not is_official_url("https://example.com/24-297.pdf")
    assert classify_filing("Slip Opinion", "/opinions/24pdf/24-297.pdf") == "opinion"
    assert classify_filing("Lower Court Orders/Opinions", "/DocketPDF/25/app.pdf") == "filing"
    assert classify_filing("Brief of Petitioner", "/DocketPDF/24/brief.pdf") == "brief"

    with pytest.raises(ValueError):
        normalize_docket("not-a-docket")


def test_discover_and_parse_docket_html() -> None:
    assert discover_dockets_from_html(SAMPLE_INDEX_HTML) == ["24-297", "24-154"]

    case = parse_docket_html(
        "24-297",
        SAMPLE_DOCKET_HTML,
        "https://www.supremecourt.gov/docket/docketfiles/html/public/24-297.html",
    )
    assert case.title == "Mahmoud v. Taylor"
    assert case.term == 2024
    assert case.status == "decided"
    assert len(case.proceedings) == 4
    # Non-official URL must be filtered out
    first_links = case.proceedings[0].links
    assert len(first_links) == 1
    assert first_links[0].kind == "petition"
    assert case.proceedings[2].links[0].kind == "transcript"
    assert case.proceedings[3].links[0].kind == "opinion"


def test_monitor_explain_and_build_pipeline(tmp_path: Path) -> None:
    def scotus_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/24-297.html"):
            return httpx.Response(200, text=SAMPLE_DOCKET_HTML)
        if url.endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF-1.4 fake bytes for " + url.encode())
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(scotus_handler))
    cases = monitor_dockets(
        dockets=["24-297"],
        root=tmp_path,
        client=http,
        delay_seconds=0,
    )
    assert len(cases) == 1
    case = cases[0]
    assert len(case.pdf_hashes()) == 3
    assert (tmp_path / "data" / "docket" / "24-297.json").exists()

    # Briefing includes the full docket timeline even if fake PDF bytes aren't valid PDFs
    briefing = build_case_briefing(case, root=tmp_path)
    assert "Mahmoud v. Taylor" in briefing
    assert "Petition GRANTED." in briefing

    # Explain with mocked OpenAI-compatible LLM
    llm_calls = 0

    def llm_handler(request: httpx.Request) -> httpx.Response:
        nonlocal llm_calls
        llm_calls += 1
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": SAMPLE_LLM_MARKDOWN}}]},
        )

    llm_http = httpx.Client(transport=httpx.MockTransport(llm_handler))
    cfg = LLMConfig(base_url="http://mock-llm/v1", model="test-model", api_key="secret")

    exp1 = explain_case(case, cfg, root=tmp_path, client=llm_http)
    assert exp1 is not None
    assert llm_calls == 1
    assert "school board policy" in exp1.summary

    # Second call with unchanged filings should skip LLM
    exp2 = explain_case(case, cfg, root=tmp_path, client=llm_http)
    assert exp2 is not None
    assert llm_calls == 1

    # Forced call re-runs LLM
    exp3 = explain_case(case, cfg, root=tmp_path, force=True, client=llm_http)
    assert exp3 is not None
    assert llm_calls == 2

    # Build static site
    count = build_site(root=tmp_path)
    assert count == 1

    dist = tmp_path / "dist"
    index_html = (dist / "index.html").read_text(encoding="utf-8")
    case_html = (dist / "cases" / "24-297.html").read_text(encoding="utf-8")
    term_html = (dist / "terms" / "2024.html").read_text(encoding="utf-8")
    feed_xml = (dist / "feed.xml").read_text(encoding="utf-8")

    assert "Mahmoud v. Taylor" in index_html
    assert 'id="case-search"' in index_html
    assert "search.js" in index_html
    assert "<script" not in case_html.lower()
    search_index = json.loads((dist / "search-index.json").read_text(encoding="utf-8"))
    assert len(search_index) == 1
    assert search_index[0]["docket"] == "24-297"
    assert "Writ of certiorari" in search_index[0]["text"]
    assert "Official Docket Proceedings (4 entries)" in case_html
    assert "<strong>Parents:</strong>" in case_html
    assert "October Term 2024" in term_html
    assert "scotusbriefs.us" in (dist / "CNAME").read_text(encoding="utf-8")
    assert "<feed xmlns=" in feed_xml


def test_markdown_and_summary_helpers() -> None:
    assert "Parents challenged" in extract_summary(SAMPLE_LLM_MARKDOWN)
    rendered = render_markdown("## Hello\n\nA **bold** <script>alert(1)</script> test.\n\n- One\n- Two")
    assert "<h2>Hello</h2>" in rendered
    assert "<strong>bold</strong>" in rendered
    assert "&lt;script&gt;" in rendered
    assert "<ul>" in rendered and "<li>One</li>" in rendered


def test_case_docket_roundtrip() -> None:
    d = CaseDocket(
        docket="24-100",
        title="Alpha v. Beta",
        term=2024,
        docket_url="https://www.supremecourt.gov/docket/docketfiles/html/public/24-100.html",
        proceedings=[
            Proceeding(
                date="Oct 1 2024",
                text="Petition filed.",
                links=[FilingLink(label="Petition", url="https://www.supremecourt.gov/a.pdf", kind="petition")],
            )
        ],
    )
    assert CaseDocket.from_dict(d.to_dict()) == d
