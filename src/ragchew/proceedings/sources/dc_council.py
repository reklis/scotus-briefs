"""DC Council documented calendar API and official release adapter."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode, urlparse
from xml.etree import ElementTree

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

_LEGISLATION = re.compile(r"\b(?:B|PR|CER|R)\d{2}-\d{3,5}\b", re.IGNORECASE)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.href: str | None = None
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "a" and values.get("href"):
            self.href = values["href"]
            self.text = []

    def handle_data(self, data: str) -> None:
        if self.href is not None:
            text = " ".join(data.split())
            if text:
                self.text.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.href is not None:
            self.links.append((self.href, " ".join(self.text)))
            self.href = None
            self.text = []


def _utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _type(event: dict[str, object]) -> ProceedingType:
    title = str(event.get("title", "")).lower()
    categories_value = event.get("categories", [])
    categories = categories_value if isinstance(categories_value, list) else []
    slugs = {
        str(item.get("slug", "")).lower()
        for item in categories
        if isinstance(item, dict)
    }
    if "hearing" in title or "hearing" in slugs:
        return ProceedingType.HEARING
    if "legislative" in title or "legislative-meeting" in slugs or "meeting" in slugs:
        return ProceedingType.LEGISLATIVE_MEETING
    return ProceedingType.OTHER


def _lifecycle(title: str, start: datetime, end: datetime, now: datetime) -> ProceedingLifecycle:
    lowered = title.lower()
    if "cancelled" in lowered or "canceled" in lowered:
        return ProceedingLifecycle.CANCELLED
    if "postponed" in lowered:
        return ProceedingLifecycle.POSTPONED
    if now < start:
        return ProceedingLifecycle.SCHEDULED
    if now <= end:
        return ProceedingLifecycle.LIVE
    return ProceedingLifecycle.COMPLETED


def _event_documents(
    event_id: str, description: str
) -> tuple[tuple[DocumentDescriptor, ...], tuple[str, ...]]:
    parser = _LinkParser()
    parser.feed(description)
    documents: list[DocumentDescriptor] = []
    excluded_hosts: set[str] = set()
    for url, text in parser.links:
        parsed = urlparse(url)
        host = parsed.hostname.lower().rstrip(".") if parsed.hostname else "missing"
        if parsed.scheme != "https" or host != "dccouncil.gov":
            excluded_hosts.add(host)
            continue
        lowered = f"{url} {text}".lower()
        if url.lower().endswith(".pdf"):
            content_type = "application/pdf"
        elif url.lower().endswith((".doc", ".docx")):
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            continue
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        documents.append(
            DocumentDescriptor(
                external_id=f"dc-council:{event_id}:attachment:{digest}",
                document_type=(
                    DocumentType.AGENDA
                    if "agenda" in lowered
                    else DocumentType.OTHER_OFFICIAL_DOCUMENT
                ),
                official_url=url,
                # The documented event API is the authorization basis for same-host links.
                access_method=SourceAccessMethod.DOCUMENTED_API,
                content_type=content_type,
            )
        )
    return tuple(documents), tuple(sorted(excluded_hosts))


def parse_council_events(payload: bytes, now: datetime) -> tuple[DiscoveredProceeding, ...]:
    data = json.loads(payload)
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise ValueError("DC Council API response has no events list")
    proceedings: list[DiscoveredProceeding] = []
    for raw in data["events"]:
        if not isinstance(raw, dict):
            raise ValueError("DC Council event is not an object")
        event_id = str(raw.get("global_id") or raw.get("id") or "")
        title = str(raw.get("title") or "").strip()
        official_url = str(raw.get("url") or "")
        if not event_id or not title or not official_url:
            raise ValueError("DC Council event lacks identity, title, or URL")
        start = _utc(str(raw["utc_start_date"]))
        end = _utc(str(raw["utc_end_date"]))
        description = str(raw.get("description") or "")
        documents, excluded_hosts = _event_documents(event_id, description)
        organizer_names = tuple(
            str(item.get("organizer"))
            for item in raw.get("organizer", [])
            if isinstance(item, dict) and item.get("organizer")
        )
        proceedings.append(
            DiscoveredProceeding(
                external_id=event_id,
                proceeding_type=_type(raw),
                title=title,
                official_url=official_url,
                lifecycle=_lifecycle(title, start, end, now),
                scheduled_start_at=start,
                scheduled_end_at=end,
                source_updated_at=(
                    _utc(str(raw["modified_utc"])) if raw.get("modified_utc") else None
                ),
                documents=documents,
                metadata={
                    "organizers": organizer_names,
                    "legislation_references": sorted(set(_LEGISLATION.findall(description))),
                    "excluded_link_hosts": excluded_hosts,
                    "media_collection": "not_approved",
                    "lims_collection": "not_approved",
                },
            )
        )
    return tuple(proceedings)


def parse_council_release_feed(payload: bytes) -> tuple[DocumentDescriptor, ...]:
    root = ElementTree.fromstring(payload)
    documents: list[DocumentDescriptor] = []
    for item in root.findall("./channel/item"):
        title = " ".join((item.findtext("title") or "").split())
        link = (item.findtext("link") or "").strip()
        if not title or urlparse(link).hostname != "dccouncil.gov":
            continue
        digest = hashlib.sha256(link.encode()).hexdigest()[:16]
        documents.append(
            DocumentDescriptor(
                external_id=f"dc-council:release:{digest}",
                document_type=DocumentType.RELEASE,
                official_url=link,
                access_method=SourceAccessMethod.DOCUMENTED_API,
                content_type="text/html",
            )
        )
    return tuple(documents)


class DcCouncilAdapter:
    source_id = "dc_council"

    def __init__(
        self,
        fetcher: SourceFetcher,
        *,
        clock: Callable[[], datetime],
        lookback_days: int = 2,
        lookahead_days: int = 30,
        per_page: int = 50,
    ) -> None:
        self.fetcher = fetcher
        self.clock = clock
        self.lookback_days = lookback_days
        self.lookahead_days = lookahead_days
        self.per_page = per_page

    def _endpoint(self, now: datetime) -> str:
        query = urlencode(
            {
                "per_page": self.per_page,
                "start_date": (now - timedelta(days=self.lookback_days)).date().isoformat(),
                "end_date": (now + timedelta(days=self.lookahead_days)).date().isoformat(),
            }
        )
        return f"https://dccouncil.gov/wp-json/tribe/events/v1/events?{query}"

    def poll(self, conditional: ConditionalRequest) -> SourcePollResult:
        now = self.clock()
        endpoint = self._endpoint(now)
        response = self.fetcher.get(endpoint, conditional)
        if response.status_code == 304:
            return SourcePollResult(
                source_id=self.source_id,
                endpoint_url=endpoint,
                access_method=SourceAccessMethod.DOCUMENTED_API,
                retrieved_at=now,
                not_modified=True,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
        proceedings = parse_council_events(response.content, now)
        return SourcePollResult(
            source_id=self.source_id,
            endpoint_url=endpoint,
            access_method=SourceAccessMethod.DOCUMENTED_API,
            retrieved_at=now,
            proceedings=proceedings,
            quiet=not proceedings,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )

    def discover_official_releases(self) -> tuple[DocumentDescriptor, ...]:
        response = self.fetcher.get("https://dccouncil.gov/feed/")
        return parse_council_release_feed(response.content)
