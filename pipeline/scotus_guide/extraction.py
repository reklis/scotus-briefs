"""Page-aware PDF extraction, quality assessment, and source classification."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .archive import hash_file
from .models import DocumentType

EXTRACTOR_VERSION = "1.0.2"


class PageStatus(StrEnum):
    EMBEDDED = "embedded"
    OCR = "ocr"
    UNAVAILABLE = "unavailable"


class OpinionPart(StrEnum):
    MAJORITY = "majority"
    PLURALITY = "plurality"
    CONCURRENCE = "concurrence"
    DISSENT = "dissent"
    PER_CURIAM = "per_curiam"


class ExtractedPage(BaseModel):
    """Normalized text and immutable identity for exactly one PDF source page."""

    model_config = ConfigDict(extra="forbid")
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(ge=1)
    text: str
    status: PageStatus
    quality: float = Field(ge=0, le=1)
    reason: str | None = None
    opinion_part: OpinionPart | None = None
    attribution: str | None = None


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extractor_version: str = EXTRACTOR_VERSION
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_type: DocumentType
    classification_confident: bool
    pages: list[ExtractedPage]


class OcrEngine(Protocol):
    def extract_page(self, pdf_path: Path, page_number: int) -> str | None: ...


class CommandOcrEngine:
    """Run an explicitly configured OCR argv template without invoking a shell.

    Each argument may contain ``{input}`` and ``{page}``; OCR text is read from
    stdout. A non-zero exit or timeout makes the page unavailable.
    """

    def __init__(self, command: Sequence[str], *, timeout: float = 120.0) -> None:
        if not command:
            raise ValueError("OCR command cannot be empty")
        self.command = tuple(command)
        self.timeout = timeout

    def extract_page(self, pdf_path: Path, page_number: int) -> str | None:
        argv = [part.format(input=str(pdf_path), page=str(page_number)) for part in self.command]
        try:
            result = subprocess.run(
                argv,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout


class PdfTextExtractor:
    """Extract one record per physical PDF page and never fabricate missing text."""

    def __init__(
        self,
        *,
        ocr: OcrEngine | None = None,
        minimum_characters: int = 40,
        minimum_alpha_ratio: float = 0.45,
        page_reader: Callable[[Path], Sequence[str | None]] | None = None,
    ) -> None:
        self.ocr = ocr
        self.minimum_characters = minimum_characters
        self.minimum_alpha_ratio = minimum_alpha_ratio
        self.page_reader = page_reader or _pypdf_pages

    def extract(
        self,
        path: Path,
        document_hash: str,
        declared_type: DocumentType = DocumentType.UNKNOWN,
    ) -> ExtractedDocument:
        actual_hash, _ = hash_file(path)
        if actual_hash != document_hash:
            raise ValueError(f"PDF hash mismatch: expected {document_hash}, got {actual_hash}")
        raw_pages = self.page_reader(path)
        preliminary: list[ExtractedPage] = []
        for page_number, raw_text in enumerate(raw_pages, 1):
            text = normalize_text(raw_text or "")
            quality = text_quality(text)
            status = PageStatus.EMBEDDED
            reason: str | None = None
            if not self._usable(text, quality):
                ocr_text = self.ocr.extract_page(path, page_number) if self.ocr else None
                normalized_ocr = normalize_text(ocr_text or "")
                ocr_quality = text_quality(normalized_ocr)
                if self._usable(normalized_ocr, ocr_quality):
                    text, quality, status = normalized_ocr, ocr_quality, PageStatus.OCR
                else:
                    text, quality, status = "", 0.0, PageStatus.UNAVAILABLE
                    reason = "OCR did not produce usable text" if self.ocr else "OCR unavailable"
            preliminary.append(
                ExtractedPage(
                    document_hash=document_hash,
                    page_number=page_number,
                    text=text,
                    status=status,
                    quality=quality,
                    reason=reason,
                )
            )
        document_type, confident = classify_document(declared_type, preliminary)
        pages = (
            classify_opinion_parts(preliminary)
            if document_type == DocumentType.OPINION
            else preliminary
        )
        return ExtractedDocument(
            document_hash=document_hash,
            document_type=document_type,
            classification_confident=confident,
            pages=pages,
        )

    def _usable(self, text: str, quality: float) -> bool:
        return len(text) >= self.minimum_characters and quality >= self.minimum_alpha_ratio


def normalize_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def text_quality(text: str) -> float:
    visible = [character for character in text if not character.isspace()]
    if not visible:
        return 0.0
    alpha = sum(character.isalpha() for character in visible)
    replacement = text.count("�")
    return max(0.0, min(1.0, (alpha - replacement * 4) / len(visible)))


def classify_document(
    declared_type: DocumentType, pages: Sequence[ExtractedPage]
) -> tuple[DocumentType, bool]:
    """Prefer archive metadata; use conservative source-text markers otherwise."""
    if declared_type not in {DocumentType.UNKNOWN, DocumentType.OTHER}:
        return declared_type, True
    sample = "\n".join(page.text for page in pages[:5]).lower()
    markers: tuple[tuple[DocumentType, tuple[str, ...]], ...] = (
        (DocumentType.TRANSCRIPT, ("official transcript", "oral argument", "chief justice:")),
        (
            DocumentType.OPINION,
            (
                "supreme court of the united states",
                "justice delivered the opinion",
                "page proof pending publication",
            ),
        ),
        (DocumentType.ORDER, ("order list", "it is ordered")),
        (DocumentType.AMICUS_BRIEF, ("brief of amicus", "amici curiae")),
        (DocumentType.REPLY_BRIEF, ("reply brief", "reply for petitioner")),
        (DocumentType.MERITS_BRIEF, ("brief for petitioner", "brief for respondent")),
        (DocumentType.PETITION, ("petition for a writ",)),
        (DocumentType.RESPONSE, ("brief in opposition", "response to petition")),
    )
    matches = [kind for kind, terms in markers if any(term in sample for term in terms)]
    if len(set(matches)) == 1:
        return matches[0], True
    return DocumentType.UNKNOWN, False


def classify_opinion_parts(pages: Sequence[ExtractedPage]) -> list[ExtractedPage]:
    """Carry an opinion heading forward until a new authored part begins."""
    current_part: OpinionPart | None = None
    current_author: str | None = None
    result: list[ExtractedPage] = []
    for page in pages:
        heading = page.text[:1200]
        detected = _opinion_heading(heading)
        if detected is not None:
            current_part, current_author = detected
        result.append(
            page.model_copy(update={"opinion_part": current_part, "attribution": current_author})
        )
    return result


def _opinion_heading(text: str) -> tuple[OpinionPart, str | None] | None:
    normalized = " ".join(text.split())
    upper = normalized.upper()
    short_heading = re.search(
        r"\b([A-Z][A-Z'-]+),\s*J\.,\s*(?:CONCURRING|DISSENTING)", upper
    )
    justice_heading = re.search(
        r"\bJUSTICE\s+([A-Z][A-Z'-]+)(?=,|\s+WITH\b|\s+DELIVERED\b)", upper
    )
    author_match = short_heading or justice_heading
    author = f"Justice {author_match.group(1).title()}" if author_match else None
    if "PER CURIAM" in upper:
        return OpinionPart.PER_CURIAM, "Court"
    if "DISSENTING" in upper or (author and re.search(r"\bDISSENT\b", upper)):
        return OpinionPart.DISSENT, author
    if re.search(r"\bCONCURRING\b", upper) or (author and re.search(r"\bCONCURRENCE\b", upper)):
        return OpinionPart.CONCURRENCE, author
    if "PLURALITY" in upper:
        return OpinionPart.PLURALITY, author
    if "DELIVERED THE OPINION OF THE COURT" in upper or "OPINION OF THE COURT" in upper:
        return OpinionPart.MAJORITY, author or "Court"
    return None


def _pypdf_pages(path: Path) -> Sequence[str | None]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as error:  # pypdf exposes backend-specific exceptions
            raise ValueError("encrypted PDF cannot be extracted") from error
    return [page.extract_text() for page in reader.pages]
