"""Extract clean text from archived Supreme Court PDFs."""

from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

from .docket import CaseDocket

# Priority order when picking which filings to include in the LLM prompt
_KIND_PRIORITY = {
    "opinion": 0,
    "order": 1,
    "transcript": 2,
    "petition": 3,
    "brief": 4,
    "filing": 5,
}


def extract_pdf_text(pdf_path: Path, *, max_chars: int = 24_000) -> str:
    """Extract readable text from a PDF, truncated cleanly to max_chars."""
    reader = PdfReader(str(pdf_path))
    chunks: list[str] = []
    total = 0

    for idx, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        cleaned = re.sub(r"[ \t]+", " ", raw)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if not cleaned:
            continue
        block = f"[Page {idx}]\n{cleaned}\n\n"
        if total + len(block) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                chunks.append(block[:remaining].rstrip() + "\n[...truncated...]")
            break
        chunks.append(block)
        total += len(block)

    return "".join(chunks).strip()


def _is_amicus_entry(text: str) -> bool:
    low = text.lower()
    return "amicus" in low or "amici curiae" in low


def build_case_briefing(
    case: CaseDocket,
    *,
    root: Path = Path("."),
    max_total_chars: int = 32_000,
    max_per_doc_chars: int = 12_000,
) -> str:
    """Assemble the docket timeline + key PDF excerpts into one text bundle for the LLM."""
    lines: list[str] = [
        f"Case: {case.title}",
        f"Docket: {case.docket}",
        f"Term: OT {case.term}",
        f"Status: {case.status}",
        f"Official Docket URL: {case.docket_url}",
        "",
        "## Docket Proceedings Timeline",
    ]

    amicus_count = 0
    for proc in case.proceedings:
        if _is_amicus_entry(proc.text):
            amicus_count += 1
            continue
        link_notes = ""
        if proc.links:
            link_notes = " [" + ", ".join(f"{lk.label} ({lk.kind})" for lk in proc.links) + "]"
        lines.append(f"- {proc.date}: {proc.text}{link_notes}")

    if amicus_count:
        lines.append(f"- (Plus {amicus_count} friend-of-the-court / amicus curiae brief filings.)")

    # Collect unique downloaded PDFs ordered by importance, newest first
    candidates: list[tuple[int, int, str, str, Path]] = []
    seen_hashes: set[str] = set()
    for proc_idx, proc in enumerate(reversed(case.proceedings)):
        if _is_amicus_entry(proc.text):
            continue
        for lk in proc.links:
            if not lk.pdf_path or not lk.sha256 or lk.sha256 in seen_hashes:
                continue
            path = root / lk.pdf_path
            if not path.exists():
                continue
            seen_hashes.add(lk.sha256)
            prio = _KIND_PRIORITY.get(lk.kind, 5)
            candidates.append((prio, proc_idx, f"{proc.date} — {lk.label} ({lk.kind})", lk.url, path))

    candidates.sort(key=lambda item: (item[0], item[1]))

    header_text = "\n".join(lines) + "\n"
    budget = max(0, max_total_chars - len(header_text))
    doc_sections: list[str] = []

    for _prio, _idx, heading, url, path in candidates:
        if budget < 500:
            break
        try:
            text = extract_pdf_text(path, max_chars=min(max_per_doc_chars, budget))
        except Exception:
            continue
        if not text:
            continue
        section = f"\n## Filing Excerpt: {heading}\nSource: {url}\n\n{text}\n"
        doc_sections.append(section)
        budget -= len(section)

    return header_text + "".join(doc_sections)
