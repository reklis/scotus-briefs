"""Deterministic page-aware chunking below the model context ceiling."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .extraction import ExtractedPage, PageStatus


@dataclass(frozen=True, slots=True)
class PageFragment:
    page_number: int
    text: str
    opinion_part: str | None
    attribution: str | None


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    index: int
    fragments: tuple[PageFragment, ...]
    estimated_tokens: int

    @property
    def start_page(self) -> int:
        return self.fragments[0].page_number

    @property
    def end_page(self) -> int:
        return self.fragments[-1].page_number

    def source_text(self) -> str:
        parts = []
        for fragment in self.fragments:
            heading = f"[SOURCE PAGE {fragment.page_number}]"
            if fragment.opinion_part:
                heading += f" [OPINION PART {fragment.opinion_part}]"
            if fragment.attribution:
                heading += f" [ATTRIBUTION {fragment.attribution}]"
            parts.append(f"{heading}\n{fragment.text}")
        return "\n\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Conservative deterministic approximation; callers retain a large reserve."""
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def chunk_pages(
    pages: Sequence[ExtractedPage],
    *,
    context_tokens: int = 32_768,
    reserved_tokens: int = 8_192,
    max_chunk_tokens: int | None = None,
    estimator: Callable[[str], int] = estimate_tokens,
) -> list[DocumentChunk]:
    if context_tokens < 2:
        raise ValueError("context_tokens must be at least 2")
    budget = max_chunk_tokens or context_tokens - reserved_tokens
    if budget < 1 or budget >= context_tokens:
        raise ValueError("chunk budget must be positive and below context_tokens")
    fragments: list[PageFragment] = []
    for page in pages:
        if page.status == PageStatus.UNAVAILABLE or not page.text:
            continue
        fragments.extend(_split_page(page, budget, estimator))
    chunks: list[DocumentChunk] = []
    pending: list[PageFragment] = []
    pending_tokens = 0
    for fragment in fragments:
        cost = estimator(_render(fragment))
        if pending and pending_tokens + cost > budget:
            chunks.append(DocumentChunk(len(chunks), tuple(pending), pending_tokens))
            pending, pending_tokens = [], 0
        pending.append(fragment)
        pending_tokens += cost
    if pending:
        chunks.append(DocumentChunk(len(chunks), tuple(pending), pending_tokens))
    return chunks


def _split_page(
    page: ExtractedPage,
    budget: int,
    estimator: Callable[[str], int],
) -> list[PageFragment]:
    base = PageFragment(
        page_number=page.page_number,
        text=page.text,
        opinion_part=page.opinion_part.value if page.opinion_part else None,
        attribution=page.attribution,
    )
    if estimator(_render(base)) <= budget:
        return [base]
    # Character slices are deterministic and conservative. Reduce until the
    # rendered fragment fits even for multibyte text or a custom estimator.
    target = max(1, budget * 3)
    output: list[PageFragment] = []
    start = 0
    while start < len(page.text):
        end = min(len(page.text), start + target)
        if end < len(page.text):
            boundary = page.text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        fragment = base.__class__(
            page_number=base.page_number,
            text=page.text[start:end].strip(),
            opinion_part=base.opinion_part,
            attribution=base.attribution,
        )
        while fragment.text and estimator(_render(fragment)) > budget:
            fragment = fragment.__class__(
                page_number=fragment.page_number,
                text=fragment.text[: max(1, len(fragment.text) // 2)].rstrip(),
                opinion_part=fragment.opinion_part,
                attribution=fragment.attribution,
            )
            end = start + len(fragment.text)
        if not fragment.text:
            raise ValueError(f"token budget too small for page {page.page_number} header")
        output.append(fragment)
        start = end
        while start < len(page.text) and page.text[start].isspace():
            start += 1
    return output


def _render(fragment: PageFragment) -> str:
    return f"[SOURCE PAGE {fragment.page_number}]\n{fragment.text}"
