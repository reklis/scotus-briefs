"""Supreme Court archived oral-argument source adapter."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin

from ragchew.proceedings.contracts import (
    DocumentType,
    MediaKind,
    ProceedingLifecycle,
    ProceedingType,
    SourceAccessMethod,
)
from ragchew.proceedings.discovery import (
    ConditionalRequest,
    DiscoveredProceeding,
    DocumentDescriptor,
    MediaDescriptor,
    SourcePollResult,
)
from ragchew.proceedings.sources.http import SourceFetcher

_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_AUDIO_PATH = re.compile(r"(?:^|/)audio/(?P<term>\d{4})/(?P<docket>[^/?#]+)$")
_TRANSCRIPT_PDF_PATH = re.compile(
    r"/(?:pdfs/transcripts|oral_arguments/argument_transcripts)/"
    r"(?P<term>\d{4})/[^/?#]+\.pdf$",
    re.IGNORECASE,
)
_ARCHIVE_ORIGINAL_DOCKET = re.compile(
    r"^(?P<number>\d{1,3})[-\s]*orig\.?$", re.I
)
_ARCHIVE_DOCKET_SUFFIX = re.compile(
    r"-(?:question-\d+|monday|tuesday|wednesday|thursday|friday)$", re.I
)
_OPINION_FILENAME_DOCKET = re.compile(
    r"^(?P<docket>\d{1,3}(?:A)?-\d+)(?:_|\.pdf$)", re.IGNORECASE
)


class _RowsAndLinksParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.row_text: list[str] = []
        self.row_links: list[tuple[str, str]] = []
        self.rows: list[tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = []
        self.current_href: str | None = None
        self.current_link_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "tr":
            self.in_row = True
            self.row_text = []
            self.row_links = []
        elif tag.lower() == "a" and values.get("href"):
            self.current_href = values["href"]
            self.current_link_text = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self.in_row:
            self.row_text.append(text)
        if self.current_href is not None:
            self.current_link_text.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.current_href is not None:
            link = (self.current_href, " ".join(self.current_link_text).strip())
            self.links.append(link)
            if self.in_row:
                self.row_links.append(link)
            self.current_href = None
            self.current_link_text = []
        elif tag.lower() == "tr" and self.in_row:
            self.rows.append((tuple(self.row_text), tuple(self.row_links)))
            self.in_row = False


def parse_argument_index(html: str, base_url: str) -> list[tuple[str, str, datetime, str]]:
    """Return docket, title, date-at-UTC-date-precision, and detail URL."""
    parser = _RowsAndLinksParser()
    parser.feed(html)
    arguments: list[tuple[str, str, datetime, str]] = []
    for text_parts, links in parser.rows:
        detail: tuple[str, str] | None = None
        for href, anchor_text in links:
            match = _AUDIO_PATH.search(urljoin(base_url, href))
            if match:
                detail = (urljoin(base_url, href), anchor_text.strip())
                break
        joined = " ".join(text_parts)
        date_match = _DATE.search(joined)
        if detail is None or date_match is None or not detail[1]:
            continue
        date = datetime.strptime(date_match.group(), "%m/%d/%y").replace(tzinfo=UTC)
        excluded = {detail[1], date_match.group()}
        title = " ".join(part for part in text_parts if part not in excluded).strip()
        if not title:
            continue
        arguments.append((detail[1], title, date, detail[0]))
    return arguments


def _archive_docket(value: str) -> str:
    normalized = " ".join(value.replace("\N{EN DASH}", "-").split()).rstrip(".")
    normalized = _ARCHIVE_DOCKET_SUFFIX.sub("", normalized)
    original = _ARCHIVE_ORIGINAL_DOCKET.fullmatch(normalized)
    if original:
        return f"{original.group('number')} ORIG."
    return normalized.upper()


def parse_transcript_archive_index(
    html: str, base_url: str, term: str
) -> list[tuple[str, str, datetime, str]]:
    """Return docket, caption, argument date, and official transcript PDF URL."""
    parser = _RowsAndLinksParser()
    parser.feed(html)
    arguments: list[tuple[str, str, datetime, str]] = []
    seen: set[str] = set()
    for text_parts, links in parser.rows:
        transcript: tuple[str, str] | None = None
        for href, anchor_text in links:
            url = urljoin(base_url, href)
            match = _TRANSCRIPT_PDF_PATH.search(url)
            if match and match.group("term") == term:
                transcript = (url, anchor_text.strip())
                break
        if transcript is None or transcript[0] in seen:
            continue
        joined = " ".join(text_parts)
        date_match = _DATE.search(joined)
        if date_match is None or not transcript[1]:
            continue
        date_format = "%m/%d/%Y" if len(date_match.group().rsplit("/", 1)[-1]) == 4 else "%m/%d/%y"
        argued_at = datetime.strptime(date_match.group(), date_format).replace(tzinfo=UTC)
        title = " ".join(
            part
            for part in text_parts
            if part != transcript[1] and _DATE.search(part) is None
        ).strip()
        if not title:
            continue
        seen.add(transcript[0])
        arguments.append(
            (_archive_docket(transcript[1]), title, argued_at, transcript[0])
        )
    return arguments


def parse_argument_detail(
    html: str, base_url: str, docket: str, term: str
) -> tuple[MediaDescriptor | None, tuple[DocumentDescriptor, ...]]:
    parser = _RowsAndLinksParser()
    parser.feed(html)
    media: MediaDescriptor | None = None
    documents: list[DocumentDescriptor] = []
    seen: set[str] = set()
    for href, _link_text in parser.links:
        url = urljoin(base_url, href)
        lowered = url.lower()
        if url in seen:
            continue
        seen.add(url)
        if lowered.endswith(".mp3") and "/media/audio/mp3files/" in lowered:
            media = MediaDescriptor(
                external_id=f"{term}:{docket}:argument-audio",
                kind=MediaKind.ARCHIVE,
                source_url=url,
                access_method=SourceAccessMethod.DOWNLOADABLE_FILE,
                content_type="audio/mpeg",
            )
        elif lowered.endswith(".pdf") and "/argument_transcripts/" in lowered:
            documents.append(
                DocumentDescriptor(
                    external_id=f"{term}:{docket}:transcript:{url.rsplit('/', 1)[-1]}",
                    document_type=DocumentType.OFFICIAL_TRANSCRIPT,
                    official_url=url,
                    access_method=SourceAccessMethod.OFFICIAL_PAGE,
                    content_type="application/pdf",
                )
            )
    docket_url = (
        "https://www.supremecourt.gov/docket/docketfiles/html/public/"
        f"{docket}.html"
    )
    documents.append(
        DocumentDescriptor(
            external_id=f"{docket}:docket",
            document_type=DocumentType.DOCKET,
            official_url=docket_url,
            access_method=SourceAccessMethod.OFFICIAL_PAGE,
            content_type="text/html",
        )
    )
    return media, tuple(documents)


def parse_related_opinion_documents(
    html: str,
    base_url: str,
    docket: str,
    *,
    document_type: DocumentType,
) -> tuple[DocumentDescriptor, ...]:
    """Extract same-row opinion/order PDFs only when the official row names the docket."""
    parser = _RowsAndLinksParser()
    parser.feed(html)
    documents: list[DocumentDescriptor] = []
    docket_pattern = re.compile(
        rf"(?<![0-9A-Z]){re.escape(docket)}(?![0-9A-Z])", re.IGNORECASE
    )
    for text_parts, links in parser.rows:
        if docket_pattern.search(" ".join(text_parts)) is None:
            continue
        pdf_urls = tuple(
            url
            for href, _ in links
            if (url := urljoin(base_url, href)).lower().endswith(".pdf")
            and "/opinions/" in url.lower()
            and "_diff_" not in url.rsplit("/", 1)[-1].lower()
        )
        exact_docket_urls = tuple(
            url
            for url in pdf_urls
            if (match := _OPINION_FILENAME_DOCKET.match(url.rsplit("/", 1)[-1]))
            and match.group("docket").upper() == docket.upper()
        )
        for url in exact_docket_urls or pdf_urls:
            documents.append(
                DocumentDescriptor(
                    external_id=f"{docket}:{document_type.value}:{url.rsplit('/', 1)[-1]}",
                    document_type=document_type,
                    official_url=url,
                    access_method=SourceAccessMethod.OFFICIAL_PAGE,
                    content_type="application/pdf",
                )
            )
    return tuple(documents)


class SupremeCourtAdapter:
    source_id = "supreme_court"

    def __init__(
        self,
        fetcher: SourceFetcher,
        *,
        term: str,
        clock: Callable[[], datetime],
        detail_lookback_days: int = 14,
        maximum_detail_requests: int = 20,
        transcript_archive: bool = False,
    ) -> None:
        if not re.fullmatch(r"\d{4}", term):
            raise ValueError("Supreme Court term must be a four-digit start year")
        self.fetcher = fetcher
        self.term = term
        self.clock = clock
        self.detail_lookback_days = detail_lookback_days
        self.maximum_detail_requests = maximum_detail_requests
        self.transcript_archive = transcript_archive
        self.index_url = (
            "https://www.supremecourt.gov/oral_arguments/argument_transcript/" f"{term}"
            if transcript_archive
            else "https://www.supremecourt.gov/oral_arguments/argument_audio/" f"{term}"
        )
        term_code = term[-2:]
        self.opinion_index_url = (
            "https://www.supremecourt.gov/opinions/slipopinion/" f"{term_code}"
        )
        self.order_index_url = (
            "https://www.supremecourt.gov/opinions/relatingtoorders/" f"{term_code}"
        )

    def discover_related_documents(self, docket: str) -> tuple[DocumentDescriptor, ...]:
        """Find later opinion/order PDFs only when an official index row names the docket."""
        opinion_response = self.fetcher.get(self.opinion_index_url)
        order_response = self.fetcher.get(self.order_index_url)
        return (
            *parse_related_opinion_documents(
                opinion_response.text(),
                self.opinion_index_url,
                docket,
                document_type=DocumentType.OPINION,
            ),
            *parse_related_opinion_documents(
                order_response.text(),
                self.order_index_url,
                docket,
                document_type=DocumentType.ORDER,
            ),
        )

    def poll(self, conditional: ConditionalRequest) -> SourcePollResult:
        now = self.clock()
        response = self.fetcher.get(self.index_url, conditional)
        if response.status_code == 304:
            return SourcePollResult(
                source_id=self.source_id,
                endpoint_url=self.index_url,
                access_method=SourceAccessMethod.OFFICIAL_PAGE,
                retrieved_at=now,
                not_modified=True,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
        if self.transcript_archive:
            archived = parse_transcript_archive_index(
                response.text(), self.index_url, self.term
            )
            opinion_html = ""
            order_html = ""
            if int(self.term) >= 2017:
                opinion_html = self.fetcher.get(self.opinion_index_url).text()
                order_html = self.fetcher.get(self.order_index_url).text()
            sessions_by_url: dict[str, tuple[int, bool]] = {}
            by_docket: dict[str, list[tuple[datetime, str]]] = {}
            for docket, _title, argued_at, transcript_url in archived:
                by_docket.setdefault(docket, []).append((argued_at, transcript_url))
            for values in by_docket.values():
                for sequence, (_argued_at, transcript_url) in enumerate(
                    sorted(values, key=lambda item: (item[0], item[1])), 1
                ):
                    sessions_by_url[transcript_url] = (sequence, sequence > 1)
            proceedings: list[DiscoveredProceeding] = []
            for docket, title, argued_at, transcript_url in archived:
                sequence, reargument = sessions_by_url[transcript_url]
                docket_documents: tuple[DocumentDescriptor, ...] = ()
                if int(self.term) >= 2015 and "ORIG" not in docket:
                    docket_documents = (
                        DocumentDescriptor(
                            external_id=f"{docket}:docket",
                            document_type=DocumentType.DOCKET,
                            official_url=(
                                "https://www.supremecourt.gov/docket/docketfiles/"
                                f"html/public/{docket}.html"
                            ),
                            access_method=SourceAccessMethod.OFFICIAL_PAGE,
                            content_type="text/html",
                        ),
                    )
                proceedings.append(
                    DiscoveredProceeding(
                        external_id=docket,
                        proceeding_type=ProceedingType.ORAL_ARGUMENT,
                        title=title,
                        official_url=self.index_url,
                        lifecycle=ProceedingLifecycle.COMPLETED,
                        scheduled_start_at=argued_at,
                        source_updated_at=now,
                        media=(),
                        documents=(
                            DocumentDescriptor(
                                external_id=(
                                    f"{self.term}:{docket}:transcript:"
                                    f"{transcript_url.rsplit('/', 1)[-1]}"
                                ),
                                document_type=DocumentType.OFFICIAL_TRANSCRIPT,
                                official_url=transcript_url,
                                access_method=SourceAccessMethod.OFFICIAL_PAGE,
                                content_type="application/pdf",
                            ),
                            *docket_documents,
                            *parse_related_opinion_documents(
                                opinion_html,
                                self.opinion_index_url,
                                docket,
                                document_type=DocumentType.OPINION,
                            ),
                            *parse_related_opinion_documents(
                                order_html,
                                self.order_index_url,
                                docket,
                                document_type=DocumentType.ORDER,
                            ),
                        ),
                        metadata={
                            "time_precision": "date",
                            "term": self.term,
                            "audio_collection": "disabled",
                            "transcript_available": True,
                            "archive_index": True,
                            "argument_sequence": sequence,
                            "reargument": reargument,
                        },
                    )
                )
            return SourcePollResult(
                source_id=self.source_id,
                endpoint_url=self.index_url,
                access_method=SourceAccessMethod.OFFICIAL_PAGE,
                retrieved_at=now,
                proceedings=tuple(proceedings),
                quiet=not proceedings,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
        parsed = parse_argument_index(response.text(), self.index_url)
        cutoff = now - timedelta(days=self.detail_lookback_days)
        has_recent_arguments = any(argued_at >= cutoff for _, _, argued_at, _ in parsed)
        opinion_html = ""
        order_html = ""
        if has_recent_arguments:
            opinion_html = self.fetcher.get(self.opinion_index_url).text()
            order_html = self.fetcher.get(self.order_index_url).text()
        items: list[DiscoveredProceeding] = []
        detail_requests = 0
        for docket, title, argued_at, detail_url in parsed:
            documents: tuple[DocumentDescriptor, ...] = ()
            audio_available = False
            if argued_at >= cutoff and detail_requests < self.maximum_detail_requests:
                detail_response = self.fetcher.get(detail_url)
                detail_requests += 1
                discovered_media, documents = parse_argument_detail(
                    detail_response.text(), detail_url, docket, self.term
                )
                documents = (
                    *documents,
                    *parse_related_opinion_documents(
                        opinion_html,
                        self.opinion_index_url,
                        docket,
                        document_type=DocumentType.OPINION,
                    ),
                    *parse_related_opinion_documents(
                        order_html,
                        self.order_index_url,
                        docket,
                        document_type=DocumentType.ORDER,
                    ),
                )
                audio_available = discovered_media is not None
            transcript_available = any(
                document.document_type is DocumentType.OFFICIAL_TRANSCRIPT
                for document in documents
            )
            items.append(
                DiscoveredProceeding(
                    external_id=docket,
                    proceeding_type=ProceedingType.ORAL_ARGUMENT,
                    title=title,
                    official_url=detail_url,
                    lifecycle=(
                        ProceedingLifecycle.COMPLETED
                        if transcript_available
                        else ProceedingLifecycle.ARCHIVE_PENDING
                    ),
                    scheduled_start_at=argued_at,
                    source_updated_at=now,
                    media=(),
                    documents=documents,
                    metadata={
                        "time_precision": "date",
                        "term": self.term,
                        "audio_available": audio_available,
                        "audio_collection": "disabled",
                        "transcript_available": transcript_available,
                    },
                )
            )
        return SourcePollResult(
            source_id=self.source_id,
            endpoint_url=self.index_url,
            access_method=SourceAccessMethod.OFFICIAL_PAGE,
            retrieved_at=now,
            proceedings=tuple(items),
            quiet=not items,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )
