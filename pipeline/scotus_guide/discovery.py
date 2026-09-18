"""Fixture-testable discovery adapter for authoritative supremecourt.gov pages."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from .archive import DocumentCandidate
from .dockets import docket_sort_key, normalize_dockets, stable_case_id
from .models import CaseAssociation, DocumentType, Lifecycle

USER_AGENT = "scotusbriefs.us-archive/0.1 (+https://scotusbriefs.us/about)"
_DOCKET_PATTERN = re.compile(r"(?<![A-Za-z0-9])(\d{2,4}(?:-\d+|[A-Za-z]\d+))(?![A-Za-z0-9])")
_DATE_FORMATS = ("%m/%d/%y", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y")


class SourcePage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: HttpUrl
    term: int = Field(ge=1789, le=2200)
    document_type: DocumentType | None = None
    date_kind: Literal["filed", "granted", "argument", "decision", "published"] | None = None

    @field_validator("url")
    @classmethod
    def official_source(cls, value: HttpUrl) -> HttpUrl:
        hostname = (value.host or "").lower()
        if value.scheme != "https" or not (
            hostname == "supremecourt.gov" or hostname.endswith(".supremecourt.gov")
        ):
            raise ValueError("discovery source must be an official HTTPS supremecourt.gov page")
        return value


class DiscoveredCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    title: str
    term: int
    docket_numbers: list[str]
    primary_docket: str
    lifecycle: Lifecycle
    dates: dict[str, date] = Field(default_factory=dict)
    source_url: HttpUrl


@dataclass(slots=True)
class DiscoveryFailure:
    source_url: str
    error: str


@dataclass(slots=True)
class DiscoveryResult:
    cases: list[DiscoveredCase] = field(default_factory=list)
    documents: list[DocumentCandidate] = field(default_factory=list)
    failures: list[DiscoveryFailure] = field(default_factory=list)
    changed_case_ids: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, object]:
        return {
            "cases": [case.model_dump(mode="json") for case in self.cases],
            "documents": [document.model_dump(mode="json") for document in self.documents],
            "failures": [asdict(item) for item in self.failures],
            "changed_case_ids": sorted(self.changed_case_ids),
        }


@dataclass(slots=True)
class _Cell:
    text: str
    links: list[tuple[str, str]]


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[_Cell]] = []
        self.headings: list[str] = []
        self._row: list[_Cell] | None = None
        self._heading_text: list[str] | None = None
        self._text: list[str] | None = None
        self._links: list[tuple[str, str]] | None = None
        self._href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self._row = []
        elif tag in {"h1", "h2", "h3", "h4"}:
            self._heading_text = []
        elif tag in {"td", "th"} and self._row is not None:
            self._text, self._links = [], []
        elif tag == "a" and self._text is not None:
            self._href = attributes.get("href")
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)
        if self._heading_text is not None:
            self._heading_text.append(data)
        if self._href is not None:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None and self._links is not None:
            self._links.append((self._href, _clean_text(" ".join(self._anchor_text))))
            self._href = None
            self._anchor_text = []
        elif tag in {"h1", "h2", "h3", "h4"} and self._heading_text is not None:
            heading = _clean_text(" ".join(self._heading_text))
            if heading:
                self.headings.append(heading)
            self._heading_text = None
        elif tag in {"td", "th"} and self._row is not None and self._text is not None:
            self._row.append(_Cell(_clean_text(" ".join(self._text)), self._links or []))
            self._text, self._links = None, None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


class ScotusDiscoveryAdapter:
    """Discover PDF links and normalized cases from official Court table pages."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        attempts: int = 3,
        backoff_seconds: float = 1.0,
        request_delay_seconds: float = 0.5,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be positive")
        self.client = client
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds
        self.request_delay_seconds = request_delay_seconds
        self.sleeper = sleeper

    def discover(self, sources: list[SourcePage]) -> DiscoveryResult:
        result = DiscoveryResult()
        for index, source in enumerate(sources):
            if index and self.request_delay_seconds:
                self.sleeper(self.request_delay_seconds)
            try:
                response = self._get(str(source.url))
                cases, documents = parse_source_page(response.text, source)
                if not documents:
                    raise ValueError(
                        "official source page unexpectedly contained no PDF candidates"
                    )
                result.cases.extend(cases)
                result.documents.extend(documents)
            except (httpx.HTTPError, ValueError) as error:
                result.failures.append(DiscoveryFailure(str(source.url), str(error)))
        result.cases = _merge_cases(result.cases)
        result.documents = _merge_document_candidates(result.documents)
        return result

    def _get(self, url: str) -> httpx.Response:
        last_error: httpx.HTTPError | None = None
        for attempt in range(self.attempts):
            try:
                response = self.client.get(url, headers={"User-Agent": USER_AGENT})
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                return response
            except httpx.HTTPError as error:
                last_error = error
                retryable = not isinstance(error, httpx.HTTPStatusError) or (
                    error.response.status_code == 429 or error.response.status_code >= 500
                )
                if not retryable or attempt + 1 == self.attempts:
                    raise
                delay = self.backoff_seconds * (2**attempt)
                if isinstance(error, httpx.HTTPStatusError):
                    delay = _retry_delay(error.response, delay)
                self.sleeper(delay)
        assert last_error is not None
        raise last_error


def reconcile_discovery_state(path: Path, result: DiscoveryResult) -> set[str]:
    """Persist metadata snapshots append-and-update, never removing absent prior cases."""
    existing: dict[str, DiscoveredCase] = {}
    if path.exists():
        payload = json.loads(path.read_text())
        existing = {
            case.case_id: case
            for case in (DiscoveredCase.model_validate(item) for item in payload.get("cases", []))
        }
    changed: set[str] = set()
    for case in result.cases:
        prior = existing.get(case.case_id)
        if prior is None or prior.model_dump(mode="json") != case.model_dump(mode="json"):
            existing[case.case_id] = case
            changed.add(case.case_id)
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "cases": [
                        case.model_dump(mode="json")
                        for case in sorted(
                            existing.values(), key=lambda item: docket_sort_key(item.primary_docket)
                        )
                    ],
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n"
        )
        temporary.replace(path)
    result.changed_case_ids.update(changed)
    return changed


def parse_source_page(
    html: str, source: SourcePage
) -> tuple[list[DiscoveredCase], list[DocumentCandidate]]:
    parser = _TableParser()
    parser.feed(html)
    cases: list[DiscoveredCase] = []
    documents: list[DocumentCandidate] = []
    heading_text = " ".join(parser.headings)
    heading_dockets = _DOCKET_PATTERN.findall(heading_text)
    fallback_dockets = normalize_dockets(heading_dockets) if heading_dockets else ()
    fallback_title = next(
        (
            heading
            for heading in parser.headings
            if " v. " in heading.lower() or heading.lower().startswith("in re ")
        ),
        None,
    )
    for row in parser.rows:
        row_text = " ".join(cell.text for cell in row)
        links = [link for cell in row for link in cell.links if _is_pdf_link(link[0])]
        if not links:
            continue
        raw_dockets = _DOCKET_PATTERN.findall(row_text)
        try:
            dockets = normalize_dockets(raw_dockets) if raw_dockets else fallback_dockets
        except ValueError:
            continue
        if not dockets:
            if source.document_type == DocumentType.ORDER:
                documents.extend(_unassociated_documents(links, source))
            continue
        title = _extract_title(row, dockets, fallback_title=fallback_title)
        primary = dockets[0]
        case_id = stable_case_id(primary)
        document_types = [
            source.document_type or _infer_document_type(href, text) for href, text in links
        ]
        lifecycle = _lifecycle(document_types)
        row_date = _extract_date(row_text)
        dates = {source.date_kind: row_date} if source.date_kind and row_date else {}
        cases.append(
            DiscoveredCase(
                case_id=case_id,
                title=title,
                term=source.term,
                docket_numbers=list(dockets),
                primary_docket=primary,
                lifecycle=lifecycle,
                dates=dates,
                source_url=source.url,
            )
        )
        association = CaseAssociation(case_id=case_id, docket_numbers=list(dockets))
        for (href, _anchor_text), document_type in zip(links, document_types, strict=True):
            url = _official_pdf_url(str(source.url), href)
            documents.append(
                DocumentCandidate(
                    url=url,
                    document_type=document_type,
                    cases=[association],
                    source_id=f"{case_id}:{urlsplit(url).path}",
                    page_url=source.url,
                    official_filename=urlsplit(url).path.rsplit("/", 1)[-1],
                )
            )
    return _merge_cases(cases), _merge_document_candidates(documents)


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _is_pdf_link(href: str) -> bool:
    return urlsplit(href).path.lower().endswith(".pdf")


def _official_pdf_url(page_url: str, href: str) -> str:
    url = urljoin(page_url, href)
    parts = urlsplit(url)
    hostname = (parts.hostname or "").lower()
    if parts.scheme != "https" or not (
        hostname == "supremecourt.gov" or hostname.endswith(".supremecourt.gov")
    ):
        raise ValueError(f"refusing non-official PDF URL: {url}")
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ""))


def _unassociated_documents(
    links: list[tuple[str, str]], source: SourcePage
) -> list[DocumentCandidate]:
    documents: list[DocumentCandidate] = []
    for href, _anchor_text in links:
        url = _official_pdf_url(str(source.url), href)
        documents.append(
            DocumentCandidate(
                url=url,
                document_type=DocumentType.ORDER,
                source_id=f"unassociated:{urlsplit(url).path}",
                page_url=source.url,
                official_filename=urlsplit(url).path.rsplit("/", 1)[-1],
            )
        )
    return documents


def _extract_title(
    row: list[_Cell], dockets: tuple[str, ...], *, fallback_title: str | None = None
) -> str:
    docket_indexes = [index for index, cell in enumerate(row) if _DOCKET_PATTERN.search(cell.text)]
    if docket_indexes:
        candidates = [
            cell.text
            for cell in row[docket_indexes[-1] + 1 :]
            if cell.text and not _extract_date(cell.text)
        ]
        if candidates:
            return candidates[0]
    if fallback_title:
        return fallback_title
    return f"Supreme Court case {dockets[0]}"


def _extract_date(text: str) -> date | None:
    tokens = re.findall(
        r"\b(?:\d{1,2}/\d{1,2}/\d{2,4}|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\.? \d{1,2}, \d{4})\b",
        text,
        re.IGNORECASE,
    )
    for token in tokens:
        for format_string in _DATE_FORMATS:
            try:
                return datetime.strptime(token.replace(".", ""), format_string).date()
            except ValueError:
                pass
    return None


def _infer_document_type(href: str, anchor_text: str) -> DocumentType:
    value = f"{href} {anchor_text}".lower()
    if "transcript" in value:
        return DocumentType.TRANSCRIPT
    if "amicus" in value:
        return DocumentType.AMICUS_BRIEF
    if "reply" in value:
        return DocumentType.REPLY_BRIEF
    if "brief" in value:
        return DocumentType.MERITS_BRIEF
    if "order" in value:
        return DocumentType.ORDER
    if "opinion" in value or "slipopinion" in value:
        return DocumentType.OPINION
    return DocumentType.UNKNOWN


def _lifecycle(types: list[DocumentType]) -> Lifecycle:
    if DocumentType.OPINION in types:
        return Lifecycle.DECIDED
    if DocumentType.TRANSCRIPT in types:
        return Lifecycle.ARGUED
    return Lifecycle.PENDING


def _merge_cases(cases: list[DiscoveredCase]) -> list[DiscoveredCase]:
    merged: dict[str, DiscoveredCase] = {}
    precedence = {Lifecycle.PENDING: 0, Lifecycle.ARGUED: 1, Lifecycle.DECIDED: 2}
    for case in cases:
        existing = merged.get(case.case_id)
        if existing is None:
            merged[case.case_id] = case
            continue
        dockets = normalize_dockets([*existing.docket_numbers, *case.docket_numbers])
        lifecycle = max(
            (existing.lifecycle, case.lifecycle), key=lambda item: precedence.get(item, 0)
        )
        merged[case.case_id] = existing.model_copy(
            update={
                "docket_numbers": list(dockets),
                "lifecycle": lifecycle,
                "dates": {**existing.dates, **case.dates},
            }
        )
    return sorted(merged.values(), key=lambda case: docket_sort_key(case.primary_docket))


def _merge_document_candidates(candidates: list[DocumentCandidate]) -> list[DocumentCandidate]:
    merged: dict[str, DocumentCandidate] = {}
    for candidate in candidates:
        key = str(candidate.url)
        existing = merged.get(key)
        if existing is None:
            merged[key] = candidate
            continue
        associations = {
            association.model_dump_json(): association for association in existing.cases
        }
        associations.update(
            {association.model_dump_json(): association for association in candidate.cases}
        )
        merged[key] = existing.model_copy(update={"cases": list(associations.values())})
    return [merged[key] for key in sorted(merged)]


def _retry_delay(response: httpx.Response, default: float) -> float:
    value = response.headers.get("Retry-After")
    if not value:
        return default
    try:
        return min(float(value), 60.0)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            now = datetime.now(retry_at.tzinfo)
            return min(max((retry_at - now).total_seconds(), 0), 60.0)
        except (TypeError, ValueError):
            return default
