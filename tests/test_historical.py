from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from scotus_guide.historical import (
    HistoricalDocumentCandidate,
    HistoricalParseError,
    HistoricalRecoveryPlan,
    HistoricalRecoveryReport,
    RecoveryComponent,
    RecoveryConflict,
    classify_historical_document_type,
    parse_historical_metadata,
    read_pdf_opening,
)
from scotus_guide.models import DocumentType, Lifecycle

HASH = "a" * 64


def test_versioned_recovery_contracts_reject_unknown_fields() -> None:
    conflict = RecoveryConflict(code="caption-conflict", message="captions disagree")
    component = RecoveryComponent(component_id="component-1", conflicts=[conflict])
    plan = HistoricalRecoveryPlan(
        parser_version="5",
        max_pages=3,
        max_characters=60_000,
        max_pdf_bytes=100 * 1024 * 1024,
        candidate_payload_digest=hashlib.sha256(b"[]").hexdigest(),
        source_manifest_hash=HASH,
        components=[component],
    )
    report = HistoricalRecoveryReport(documents_examined=1, conflicts=[conflict])

    assert HistoricalDocumentCandidate.model_fields["schema_version"].default == "1.0.0"
    assert plan.schema_version == report.schema_version == "1.0.0"
    with pytest.raises(ValueError):
        RecoveryConflict(code="x", message="x", unsupported=True)  # type: ignore[call-arg]


def test_document_type_comes_only_from_immediate_import_path_parent() -> None:
    assert (
        classify_historical_document_type("court/group/transcript/source.pdf")
        is DocumentType.TRANSCRIPT
    )
    assert (
        classify_historical_document_type("court/group/opinion/source.pdf")
        is DocumentType.OPINION
    )
    assert (
        classify_historical_document_type("court/group/order/source.pdf") is DocumentType.ORDER
    )
    with pytest.raises(ValueError, match="unsupported"):
        classify_historical_document_type("court/transcript/group/source.pdf")
    with pytest.raises(ValueError, match="invalid"):
        classify_historical_document_type("../order/source.pdf")


def test_historical_transcript_parses_consolidated_unicode_dockets_and_parties() -> None:
    text = """
    SUPREME COURT OF THE UNITED STATES
    ALPHA ASSOCIATION, )
       Petitioner, )
    v. ) Nos. 20\N{SOFT HYPHEN}123 and 20\N{EN DASH}124
    BETA BOARD, )
       Respondent. )
    Date: October 7, 2020
    Wednesday, October 7, 2020
    The above-entitled matter came on for oral argument.
    """
    candidate = parse_historical_metadata(
        document_hash=HASH,
        import_path="archive/group/transcript/source.pdf",
        opening_text=text,
    )

    assert candidate.docket_numbers == ["20-123", "20-124"]
    assert candidate.title == "ALPHA ASSOCIATION v. BETA BOARD"
    assert [(party.name, party.role) for party in candidate.parties] == [
        ("ALPHA ASSOCIATION", "petitioner"),
        ("BETA BOARD", "respondent"),
    ]
    assert candidate.dates.argument == date(2020, 10, 7)
    assert candidate.term == 2020
    assert candidate.lifecycle is Lifecycle.ARGUED
    assert {item.field for item in candidate.provenance} >= {
        "docket_numbers",
        "title",
        "dates",
        "term",
    }


def test_legacy_numbered_transcript_caption_layout_is_supported() -> None:
    candidate = parse_historical_metadata(
        document_hash=HASH,
        import_path="archive/group/transcript/source.pdf",
        opening_text="""
        1 IN THE SUPREME COURT OF THE UNITED STATES
        2 -------------------------------- x
        3 UNITED STATES, :
        4 Petitioner :
          v. : No. 10-382
        6 JICARILLA APACHE NATION :
        7 -------------------------------- x
        8 Washington, D.C.
        9 Wednesday, April 20, 2011
        The above-entitled matter came on for oral argument.
        """,
        embedded_title="10-382.exe",
    )

    assert candidate.title == "UNITED STATES v. JICARILLA APACHE NATION"
    assert candidate.dates.argument == date(2011, 4, 20)
    assert candidate.term == 2010


def test_transcript_role_caption_wins_over_speech_and_reporter_citations() -> None:
    candidate = parse_historical_metadata(
        document_hash=HASH,
        import_path="archive/group/transcript/source.pdf",
        opening_text="""
        SUPREME COURT OF THE UNITED STATES
        ACME WORKERS UNION, )
          Petitioner, )
        v. ) No. 22-123
        BETA COUNTY BOARD, )
          Respondent. )
        MR. SMITH: In Smith v. Jones, 410 U.S. 1, the Court said otherwise.
        The above-entitled matter came on for argument in Alpha v. Boilerplate.
        Date: October 4, 2022
        """,
    )

    assert candidate.title == "ACME WORKERS UNION v. BETA COUNTY BOARD"


def test_generic_caption_requires_header_context_and_two_supported_party_sides() -> None:
    citation_only = parse_historical_metadata(
        document_hash=HASH,
        import_path="archive/group/opinion/source.pdf",
        opening_text="""
        No. 22-123. Decided May 1, 2023
        The rule from Smith v. Jones, 410 U.S. 1, controls this dispute.
        """,
    )
    conflicting = parse_historical_metadata(
        document_hash="b" * 64,
        import_path="archive/group/opinion/source.pdf",
        opening_text="""
        SUPREME COURT OF THE UNITED STATES
        ALPHA WORKERS v. BETA BOARD
        No. 22-123. Decided May 1, 2023
        """,
        embedded_title="22-123 Omega Consumers v. Gamma Agency",
    )

    assert citation_only.title is None
    assert conflicting.ambiguous is True
    assert any("conflicting captions" in warning for warning in conflicting.warnings)


def test_historical_opinion_parses_caption_explicit_term_and_dates() -> None:
    text = """
    1 OCTOBER TERM, 2021
    Syllabus
    ALPHA WORKERS et al. v. BETA, SECRETARY OF LABOR
    certiorari to the court of appeals
    No. 21\N{NON-BREAKING HYPHEN}99. Argued December 1, 2021—Decided June 2, 2022
    """
    candidate = parse_historical_metadata(
        document_hash=HASH,
        import_path="archive/group/opinion/source.pdf",
        opening_text=text,
    )

    assert candidate.docket_numbers == ["21-99"]
    assert candidate.title == "ALPHA WORKERS et al. v. BETA, SECRETARY OF LABOR"
    assert candidate.term == 2021
    assert candidate.dates.argument == date(2021, 12, 1)
    assert candidate.dates.decision == date(2022, 6, 2)
    assert candidate.lifecycle is Lifecycle.DECIDED


def test_later_cited_dockets_do_not_become_case_aliases() -> None:
    candidate = parse_historical_metadata(
        document_hash=HASH,
        import_path="archive/group/opinion/source.pdf",
        opening_text="""
        OCTOBER TERM, 2023
        SNYDER v. UNITED STATES
        No. 23-108. Argued November 8, 2023—Decided June 26, 2024
        The Court previously considered No. 99-797 and no. 10-12.
        """,
    )

    assert candidate.docket_numbers == ["23-108"]
    assert candidate.docket_labels == ["No. 23-108"]
    assert candidate.title == "SNYDER v. UNITED STATES"


@pytest.mark.parametrize(
    "caption",
    [
        "TOWN OF ALPHA v. BETA, GAMMA, AND",
        "Services, et al. v. Louisiana et al., also on application for stay.",
        "The rule follows ZIVOTOFSKY v. CLINTON, 566 U. S. 1",
        "counsel was ineffective under Strickland v. Washington, 466",
    ],
)
def test_incomplete_or_prose_caption_is_not_promoted_to_case_identity(
    caption: str,
) -> None:
    candidate = parse_historical_metadata(
        document_hash=HASH,
        import_path="archive/group/opinion/source.pdf",
        opening_text=f"""
        OCTOBER TERM, 2004
        {caption}
        No. 04-278. Decided June 27, 2005
        """,
    )

    assert candidate.title is None


def test_docket_bearing_pdf_title_is_preferred_and_conflicts_fail_closed() -> None:
    candidate = parse_historical_metadata(
        document_hash=HASH,
        import_path="archive/group/opinion/source.pdf",
        opening_text="""
        OCTOBER TERM, 2022
        UNRELATED PARTY v. OTHER PARTY
        No. 22-8. Decided May 1, 2023
        """,
        embedded_title="21-7 Alpha v. Beta (05/01/2023)",
    )

    assert candidate.title == "Alpha v. Beta"
    assert candidate.ambiguous is True
    assert candidate.lifecycle is Lifecycle.UNRESOLVED
    assert "different dockets" in candidate.warnings[0]


def test_supported_order_disposition_is_explicit_and_unknown_order_is_not_dismissed() -> None:
    dismissed = parse_historical_metadata(
        document_hash=HASH,
        import_path="archive/group/order/source.pdf",
        opening_text="""
        ALPHA v. BETA
        No. 24-100. Decided January 10, 2025
        The motion for leave is denied, and the petition for a writ of certiorari is dismissed.
        """,
    )
    unsupported = parse_historical_metadata(
        document_hash="b" * 64,
        import_path="archive/group/order/source.pdf",
        opening_text="""
        ALPHA v. BETA
        No. 24-101. Decided January 10, 2025
        The Clerk is directed to transmit the record.
        """,
    )

    assert dismissed.disposition == "dismissed"
    assert dismissed.lifecycle is Lifecycle.DISMISSED
    assert unsupported.disposition is None
    assert unsupported.lifecycle is Lifecycle.DECIDED


def test_opening_page_reader_enforces_byte_page_and_character_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")

    class Page:
        def __init__(self, text: str) -> None:
            self.text = text

        def extract_text(self) -> str:
            return self.text

    class Reader:
        is_encrypted = False
        metadata = SimpleNamespace(title="  24-7 Alpha v. Beta  ")
        pages: ClassVar[list[Page]] = [Page("abcd"), Page("efgh"), Page("ignored")]

        def __init__(self, _source: object, *, strict: bool) -> None:
            assert strict is True

    monkeypatch.setattr("scotus_guide.historical.PdfReader", Reader)
    opening = read_pdf_opening(source, max_pages=2, max_characters=7)

    assert opening.embedded_title == "24-7 Alpha v. Beta"
    assert opening.pages_read == 2
    assert "ignored" not in opening.text
    assert opening.truncated is True

    with pytest.raises(HistoricalParseError, match="limit"):
        read_pdf_opening(source, max_pdf_bytes=2)
    with pytest.raises(ValueError, match="positive"):
        read_pdf_opening(source, max_pages=0)
