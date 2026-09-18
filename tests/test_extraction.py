from __future__ import annotations

import hashlib
from pathlib import Path

from scotus_guide.chunking import chunk_pages
from scotus_guide.extraction import (
    OpinionPart,
    PageStatus,
    PdfTextExtractor,
)
from scotus_guide.models import DocumentType


class Ocr:
    def extract_page(self, _path: Path, page_number: int) -> str | None:
        if page_number == 2:
            return "OCR recovered enough alphabetic text from the second source page for use."
        return None


def pdf(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-1.7 fake fixture")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_page_extraction_uses_ocr_and_marks_unavailable(tmp_path: Path) -> None:
    path, digest = pdf(tmp_path)
    extractor = PdfTextExtractor(
        ocr=Ocr(),
        minimum_characters=20,
        page_reader=lambda _path: [
            "Embedded alphabetic text with enough content for the first page.",
            "x",
            None,
        ],
    )
    result = extractor.extract(path, digest, DocumentType.TRANSCRIPT)
    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert [page.status for page in result.pages] == [
        PageStatus.EMBEDDED,
        PageStatus.OCR,
        PageStatus.UNAVAILABLE,
    ]
    assert result.pages[2].text == ""
    assert result.pages[2].reason == "OCR did not produce usable text"


def test_opinion_parts_and_authors_carry_across_pages(tmp_path: Path) -> None:
    path, digest = pdf(tmp_path)
    pages = [
        "Justice Alpha delivered the opinion of the Court. " + "majority " * 10,
        "continued majority reasoning " * 10,
        "Justice Beta, dissenting. " + "dissent reasoning " * 10,
        "continued dissent reasoning " * 10,
    ]
    result = PdfTextExtractor(page_reader=lambda _path: pages).extract(
        path, digest, DocumentType.OPINION
    )
    assert [page.opinion_part for page in result.pages] == [
        OpinionPart.MAJORITY,
        OpinionPart.MAJORITY,
        OpinionPart.DISSENT,
        OpinionPart.DISSENT,
    ]
    assert result.pages[0].attribution == "Justice Alpha"
    assert result.pages[3].attribution == "Justice Beta"


def test_chunking_is_page_aware_bounded_and_deterministic(tmp_path: Path) -> None:
    path, digest = pdf(tmp_path)
    extracted = PdfTextExtractor(
        minimum_characters=1,
        minimum_alpha_ratio=0,
        page_reader=lambda _path: ["a" * 120, "b" * 120, "c" * 120],
    ).extract(path, digest)
    first = chunk_pages(extracted.pages, context_tokens=100, reserved_tokens=40)
    second = chunk_pages(extracted.pages, context_tokens=100, reserved_tokens=40)
    assert first == second
    assert all(chunk.estimated_tokens <= 60 for chunk in first)
    assert [fragment.page_number for chunk in first for fragment in chunk.fragments] == [1, 2, 3]
    assert "[SOURCE PAGE 1]" in first[0].source_text()
