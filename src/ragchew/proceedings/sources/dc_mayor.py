"""Official DC mayoral RSS, calendar, release, and attachment adapter."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from ragchew.proceedings.contracts import (
    DocumentType,
    ProceedingLifecycle,
    ProceedingType,
    SourceAccessMethod,
)
from ragchew.proceedings.discovery import (
    ConditionalRequest,
    DiscoveredProceeding,
    DocumentDescriptor,
    SourcePollResult,
)
from ragchew.proceedings.sources.http import SourceFetcher

_EASTERN = ZoneInfo("America/New_York")
_CALENDAR_DATE = re.compile(
    r"Public Calendar for (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
    r"(?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})",
    re.IGNORECASE,
)
_RELEVANT = re.compile(
    r"public calendar|briefing|press conference|media availability|"
    r"to (?:announce|provide an update)|announces",
    re.IGNORECASE,
)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("href")
        if tag.lower() == "a" and href:
            self.urls.append(href)


def _documents(
    item_id: str, release_url: str, description: str
) -> tuple[tuple[DocumentDescriptor, ...], tuple[str, ...]]:
    release = DocumentDescriptor(
        external_id=f"dc-mayor:release:{item_id}",
        document_type=DocumentType.RELEASE,
        official_url=release_url,
        access_method=SourceAccessMethod.OFFICIAL_FEED,
        content_type="text/html",
    )
    documents = [release]
    excluded_hosts: set[str] = set()
    parser = _Links()
    parser.feed(description)
    for url in parser.urls:
        parsed = urlparse(url)
        host = parsed.hostname.lower().rstrip(".") if parsed.hostname else "missing"
        if parsed.scheme != "https" or host != "mayor.dc.gov":
            excluded_hosts.add(host)
            continue
        if not url.lower().endswith((".pdf", ".doc", ".docx")):
            continue
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        documents.append(
            DocumentDescriptor(
                external_id=f"dc-mayor:{item_id}:attachment:{digest}",
                document_type=DocumentType.OTHER_OFFICIAL_DOCUMENT,
                official_url=url,
                access_method=SourceAccessMethod.OFFICIAL_FEED,
                content_type=(
                    "application/pdf"
                    if url.lower().endswith(".pdf")
                    else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
            )
        )
    return tuple(documents), tuple(sorted(excluded_hosts))


def parse_mayor_feed(payload: bytes, now: datetime) -> tuple[DiscoveredProceeding, ...]:
    root = ElementTree.fromstring(payload)
    proceedings: list[DiscoveredProceeding] = []
    for item in root.findall("./channel/item"):
        title = " ".join((item.findtext("title") or "").split())
        release_url = (item.findtext("link") or "").strip()
        published_text = (item.findtext("pubDate") or "").strip()
        description = item.findtext("description") or ""
        if (
            not title
            or not _RELEVANT.search(title)
            or urlparse(release_url).hostname != "mayor.dc.gov"
            or not published_text
        ):
            continue
        published = parsedate_to_datetime(published_text).astimezone(UTC)
        item_id = hashlib.sha256(release_url.encode()).hexdigest()[:20]
        calendar_match = _CALENDAR_DATE.search(title)
        if calendar_match:
            calendar_date = datetime.strptime(
                calendar_match.group("date"), "%B %d, %Y"
            ).replace(tzinfo=_EASTERN)
            scheduled_at = calendar_date.astimezone(UTC)
            source_kind = "public_calendar"
        else:
            scheduled_at = published
            source_kind = "briefing_or_announcement_release"
        documents, excluded_hosts = _documents(item_id, release_url, description)
        proceeding_type = (
            ProceedingType.MAYORAL_BRIEFING
            if re.search(r"briefing|press conference|media availability", title, re.IGNORECASE)
            else ProceedingType.OTHER
        )
        proceedings.append(
            DiscoveredProceeding(
                external_id=f"dc-mayor:{item_id}",
                proceeding_type=proceeding_type,
                title=title,
                official_url=release_url,
                lifecycle=(
                    ProceedingLifecycle.SCHEDULED
                    if scheduled_at > now
                    else ProceedingLifecycle.COMPLETED
                ),
                scheduled_start_at=scheduled_at,
                source_updated_at=published,
                documents=documents,
                metadata={
                    "source_kind": source_kind,
                    "time_precision": "date" if calendar_match else "publication",
                    "excluded_link_hosts": excluded_hosts,
                    "media_collection": "not_approved",
                    "publication_is_not_spoken_evidence": True,
                },
            )
        )
    return tuple(proceedings)


class DcMayorAdapter:
    source_id = "dc_mayor"
    endpoint = "https://mayor.dc.gov/rss.xml"

    def __init__(self, fetcher: SourceFetcher, *, clock: Callable[[], datetime]) -> None:
        self.fetcher = fetcher
        self.clock = clock

    def poll(self, conditional: ConditionalRequest) -> SourcePollResult:
        now = self.clock()
        response = self.fetcher.get(self.endpoint, conditional)
        if response.status_code == 304:
            return SourcePollResult(
                source_id=self.source_id,
                endpoint_url=self.endpoint,
                access_method=SourceAccessMethod.OFFICIAL_FEED,
                retrieved_at=now,
                not_modified=True,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
        proceedings = parse_mayor_feed(response.content, now)
        return SourcePollResult(
            source_id=self.source_id,
            endpoint_url=self.endpoint,
            access_method=SourceAccessMethod.OFFICIAL_FEED,
            retrieved_at=now,
            proceedings=proceedings,
            quiet=not proceedings,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )
