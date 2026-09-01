"""U.S. House floor activity and official vote/document adapter."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlparse
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
_BILL = re.compile(
    r"^(?P<kind>H\.\s*R\.|H\.\s*Res\.|H\.\s*Con\.\s*Res\.|"
    r"H\.\s*J\.\s*Res\.|S\.)\s*(?P<number>\d+)$",
    re.IGNORECASE,
)
_AMENDMENT = re.compile(r"^H\.\s*Amdt\.\s*(?P<number>\d+)$", re.IGNORECASE)


def _text(element: ElementTree.Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _local_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%dT%H:%M:%S").replace(tzinfo=_EASTERN).astimezone(UTC)


def _legislation_descriptor(
    item: str, congress: str
) -> DocumentDescriptor | None:
    amendment = _AMENDMENT.fullmatch(" ".join(item.split()))
    if amendment:
        number = amendment.group("number")
        return DocumentDescriptor(
            external_id=f"{congress}:house-amendment:{number}",
            document_type=DocumentType.AMENDMENT,
            official_url=(
                f"https://www.congress.gov/amendment/{congress}th-congress/"
                f"house-amendment/{number}"
            ),
            access_method=SourceAccessMethod.OFFICIAL_PAGE,
            content_type="text/html",
        )
    match = _BILL.fullmatch(" ".join(item.split()))
    if not match:
        return None
    kind = re.sub(r"\s+", "", match.group("kind")).lower()
    paths = {
        "h.r.": "house-bill",
        "h.res.": "house-resolution",
        "h.con.res.": "house-concurrent-resolution",
        "h.j.res.": "house-joint-resolution",
        "s.": "senate-bill",
    }
    path = paths[kind]
    number = match.group("number")
    return DocumentDescriptor(
        external_id=f"{congress}:{path}:{number}",
        document_type=DocumentType.LEGISLATION,
        official_url=f"https://www.congress.gov/bill/{congress}th-congress/{path}/{number}",
        access_method=SourceAccessMethod.OFFICIAL_PAGE,
        content_type="text/html",
    )


def parse_house_floor_xml(xml: bytes, source_url: str) -> DiscoveredProceeding:
    root = ElementTree.fromstring(xml)
    day = root.find(".//legislative_day")
    if day is None or not day.attrib.get("date"):
        raise ValueError("House floor XML has no legislative day")
    date_key = day.attrib["date"]
    congress_element = root.find(".//legislative_congress")
    congress = congress_element.attrib.get("congress") if congress_element is not None else None
    session = congress_element.attrib.get("session") if congress_element is not None else None
    if not congress or not session:
        raise ValueError("House floor XML has no Congress/session identity")

    action_times: list[datetime] = []
    documents: dict[str, DocumentDescriptor] = {}
    for action in root.findall(".//floor_action"):
        action_time = action.find("action_time")
        timestamp = action_time.attrib.get("for-search") if action_time is not None else None
        if timestamp:
            action_times.append(_local_timestamp(timestamp))
        item = _text(action.find("action_item"))
        legislation = _legislation_descriptor(item, congress) if item else None
        if legislation:
            documents[legislation.external_id] = legislation
        for vote_link in action.findall(".//a[@rel='vote']"):
            query = parse_qs(urlparse(vote_link.attrib.get("href", "")).query)
            year = query.get("year", [date_key[:4]])[0]
            roll_value = query.get("rollnumber", [""])[0]
            if not roll_value.isdigit():
                continue
            roll = int(roll_value)
            descriptor = DocumentDescriptor(
                external_id=f"house-roll-call:{year}:{roll}",
                document_type=DocumentType.VOTE_RECORD,
                official_url=f"https://clerk.house.gov/evs/{year}/roll{roll:03d}.xml",
                access_method=SourceAccessMethod.OFFICIAL_PAGE,
                content_type="text/xml",
            )
            documents[descriptor.external_id] = descriptor

    floor_document = DocumentDescriptor(
        external_id=f"house-floor-activity:{date_key}",
        document_type=DocumentType.OTHER_OFFICIAL_DOCUMENT,
        official_url=source_url,
        access_method=SourceAccessMethod.OFFICIAL_PAGE,
        content_type="text/xml",
    )
    documents[floor_document.external_id] = floor_document
    local_date = datetime.strptime(date_key, "%Y%m%d").replace(tzinfo=_EASTERN)
    start = min(action_times) if action_times else local_date.astimezone(UTC)
    finished = _text(root.find(".//legislative_day_finished")).lower() == "yes"
    lifecycle = (
        ProceedingLifecycle.COMPLETED
        if finished
        else ProceedingLifecycle.LIVE
        if action_times
        else ProceedingLifecycle.SCHEDULED
    )
    published = _text(root.find("pubDate"))
    source_updated_at = parsedate_to_datetime(published).astimezone(UTC) if published else None
    return DiscoveredProceeding(
        external_id=f"house-floor-{date_key}",
        proceeding_type=ProceedingType.HOUSE_FLOOR,
        title=(
            f"U.S. House floor proceedings — {local_date:%B} "
            f"{local_date.day}, {local_date.year}"
        ),
        official_url=source_url,
        lifecycle=lifecycle,
        scheduled_start_at=start,
        actual_start_at=min(action_times) if action_times else None,
        actual_end_at=max(action_times) if finished and action_times else None,
        source_updated_at=source_updated_at,
        documents=tuple(documents.values()),
        metadata={
            "congress": congress,
            "session": session,
            "time_precision": "action" if action_times else "date",
            "media_collection": "not_approved",
        },
    )


class HouseFloorAdapter:
    source_id = "house_floor"

    def __init__(self, fetcher: SourceFetcher, *, clock: Callable[[], datetime]) -> None:
        self.fetcher = fetcher
        self.clock = clock

    def poll(self, conditional: ConditionalRequest) -> SourcePollResult:
        now = self.clock()
        date_key = now.astimezone(_EASTERN).strftime("%Y%m%d")
        endpoint = f"https://clerk.house.gov/floor/{date_key}.xml"
        response = self.fetcher.get(endpoint, conditional)
        if response.status_code == 304:
            return SourcePollResult(
                source_id=self.source_id,
                endpoint_url=endpoint,
                access_method=SourceAccessMethod.OFFICIAL_PAGE,
                retrieved_at=now,
                not_modified=True,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
        proceeding = parse_house_floor_xml(response.content, endpoint)
        return SourcePollResult(
            source_id=self.source_id,
            endpoint_url=endpoint,
            access_method=SourceAccessMethod.OFFICIAL_PAGE,
            retrieved_at=now,
            proceedings=(proceeding,),
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )
