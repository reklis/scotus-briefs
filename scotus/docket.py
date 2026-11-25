"""Monitor supremecourt.gov dockets and archive filing PDFs."""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from pypdf import PdfReader

USER_AGENT = "scotusbriefs.us/0.2 (+https://scotusbriefs.us/about.html)"
BASE_URL = "https://www.supremecourt.gov"

DOCKET_RE = re.compile(r"(?<![A-Za-z0-9])(\d{2}-\d+|\d{2}[A-Z]\d+|\d+O)(?![A-Za-z0-9])")
DOCKET_URL_TEMPLATE = f"{BASE_URL}/docket/docketfiles/html/public/{{docket}}.html"


@dataclass
class FilingLink:
    label: str
    url: str
    kind: str = "filing"
    sha256: str | None = None
    pdf_path: str | None = None


@dataclass
class Proceeding:
    date: str
    text: str
    links: list[FilingLink] = field(default_factory=list)


@dataclass
class CaseDocket:
    docket: str
    title: str
    term: int
    docket_url: str
    status: str = "pending"
    updated_at: str = ""
    proceedings: list[Proceeding] = field(default_factory=list)

    def pdf_hashes(self) -> list[str]:
        hashes: list[str] = []
        for proc in self.proceedings:
            for link in proc.links:
                if link.sha256 and link.sha256 not in hashes:
                    hashes.append(link.sha256)
        return sorted(hashes)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CaseDocket:
        proceedings = [
            Proceeding(
                date=p.get("date", ""),
                text=p.get("text", ""),
                links=[FilingLink(**lk) for lk in p.get("links", [])],
            )
            for p in raw.get("proceedings", [])
        ]
        return cls(
            docket=raw["docket"],
            title=raw.get("title", f"Docket {raw['docket']}"),
            term=int(raw.get("term", infer_term(raw["docket"]))),
            docket_url=raw.get("docket_url", DOCKET_URL_TEMPLATE.format(docket=raw["docket"])),
            status=raw.get("status", "pending"),
            updated_at=raw.get("updated_at", ""),
            proceedings=proceedings,
        )


def is_official_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (
        host == "supremecourt.gov" or host.endswith(".supremecourt.gov")
    )


def normalize_docket(raw: str) -> str:
    cleaned = re.sub(r"^(?:docket|nos?\.?)\s+", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*-\s*", "-", cleaned).upper().rstrip(".,;")
    if not DOCKET_RE.fullmatch(cleaned):
        raise ValueError(f"Invalid Supreme Court docket number: {raw!r}")
    return cleaned


def infer_term(docket: str) -> int:
    m = re.match(r"^(\d{2})[-A-Z]", docket)
    if not m:
        return datetime.now(UTC).year
    yy = int(m.group(1))
    return (1900 + yy) if yy >= 70 else (2000 + yy)


def classify_filing(label: str, url: str = "", context: str = "") -> str:
    lbl = label.lower().strip()
    url_low = url.lower()
    ctx = context.lower()

    if "lower court" in lbl:
        return "filing"
    if "/opinions/" in url_low or lbl in {"opinion", "slip opinion"} or "slip opinion" in lbl:
        return "opinion"
    if "transcript" in lbl or "/argument_transcripts/" in url_low:
        return "transcript"
    if "/orders/" in url_low or lbl in {"order", "orders"}:
        return "order"
    if "brief" in lbl or "amici" in lbl or "amicus" in lbl or "brief" in ctx:
        return "brief"
    if re.search(r"\bpetition\b", lbl) or "certiorari" in lbl or re.search(r"\bpetition\b", ctx):
        return "petition"
    return "filing"


def infer_status(proceedings: list[Proceeding]) -> str:
    for p in proceedings:
        txt = p.text.lower()
        if any(lk.kind == "opinion" for lk in p.links):
            return "decided"
        if any(
            k in txt
            for k in (
                "judgment issued",
                "judgment affirmed",
                "judgment reversed",
                "judgment vacated",
                "slip opinion",
                "affirmed.",
                "reversed.",
                "vacated.",
                "reversed and remanded",
                "vacated and remanded",
            )
        ):
            return "decided"
    for p in proceedings:
        txt = p.text.lower()
        if any(lk.kind == "transcript" for lk in p.links):
            return "argued"
        if txt.startswith("argued") or "oral argument" in txt:
            return "argued"
    return "pending"


class _HTMLTableParser(HTMLParser):
    """Minimal stdlib HTML parser for supremecourt.gov docket and index tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_candidates: list[str] = []
        self.rows: list[list[tuple[str, list[tuple[str, str]]]]] = []
        self.all_links: list[tuple[str, str]] = []

        self._in_title_span = False
        self._in_heading = False
        self._title_buf: list[str] = []

        self._row: list[tuple[str, list[tuple[str, str]]]] | None = None
        self._cell_text: list[str] | None = None
        self._cell_links: list[tuple[str, str]] | None = None

        self._href: str | None = None
        self._anchor_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        cls_attr = (attr_map.get("class") or "").lower()
        if tag == "span" and "title" in cls_attr:
            self._in_title_span = True
            self._title_buf = []
        elif tag in {"h1", "h2", "h3"}:
            self._in_heading = True
            self._title_buf = []
        elif tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_text = []
            self._cell_links = []
        elif tag == "a":
            self._href = attr_map.get("href")
            self._anchor_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_title_span or self._in_heading:
            self._title_buf.append(data)
        if self._cell_text is not None:
            self._cell_text.append(data)
        if self._href is not None:
            self._anchor_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self._in_title_span:
            text = _clean(" ".join(self._title_buf))
            if text:
                self.title_candidates.insert(0, text)
            self._in_title_span = False
            self._title_buf = []
        elif tag in {"h1", "h2", "h3"} and self._in_heading:
            text = _clean(" ".join(self._title_buf))
            if text:
                self.title_candidates.append(text)
            self._in_heading = False
            self._title_buf = []
        elif tag == "a" and self._href is not None:
            label = _clean(" ".join(self._anchor_buf))
            self.all_links.append((self._href, label))
            if self._cell_links is not None:
                self._cell_links.append((self._href, label))
            self._href = None
            self._anchor_buf = []
        elif tag in {"td", "th"} and self._row is not None and self._cell_text is not None:
            self._row.append((_clean(" ".join(self._cell_text)), self._cell_links or []))
            self._cell_text = None
            self._cell_links = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_docket_html(docket: str, html: str, page_url: str) -> CaseDocket:
    """Parse a supremecourt.gov public docket HTML page into a CaseDocket."""
    parser = _HTMLTableParser()
    parser.feed(html)

    title = next(
        (t for t in parser.title_candidates if " v. " in t or "in re " in t.lower()),
        parser.title_candidates[0] if parser.title_candidates else f"Docket {docket}",
    )

    proceedings: list[Proceeding] = []
    date_re = re.compile(r"^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{4}$|^\d{1,2}/\d{1,2}/\d{2,4}$")

    for row in parser.rows:
        if len(row) < 2:
            continue
        first_text = row[0][0]
        if not date_re.match(first_text):
            continue
        proc_text = row[1][0]
        links: list[FilingLink] = []
        for _cell_text, cell_links in row:
            for href, label in cell_links:
                full_url = urljoin(page_url, href)
                if not is_official_url(full_url):
                    continue
                if not full_url.lower().endswith(".pdf"):
                    continue
                link_label = label or Path(urlsplit(full_url).path).name
                links.append(
                    FilingLink(
                        label=link_label,
                        url=full_url,
                        kind=classify_filing(link_label, full_url, proc_text),
                    )
                )
        proceedings.append(Proceeding(date=first_text, text=proc_text, links=links))

    return CaseDocket(
        docket=docket,
        title=title,
        term=infer_term(docket),
        docket_url=page_url,
        status=infer_status(proceedings),
        updated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        proceedings=proceedings,
    )


def discover_dockets_from_html(html: str) -> list[str]:
    """Extract unique Supreme Court docket numbers from an official index page."""
    parser = _HTMLTableParser()
    parser.feed(html)

    found: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        try:
            norm = normalize_docket(candidate)
        except ValueError:
            return
        if norm not in seen:
            seen.add(norm)
            found.append(norm)

    for href, label in parser.all_links:
        if "/docket/docketfiles/html/public/" in href:
            stem = Path(urlsplit(href).path).stem
            _add(stem)
        for m in DOCKET_RE.finditer(label):
            _add(m.group(1))

    for row in parser.rows:
        for cell_text, _ in row[:3]:
            for m in DOCKET_RE.finditer(cell_text):
                _add(m.group(1))

    return found


def index_urls_for_term(term: int) -> list[str]:
    """Return official supremecourt.gov index URLs for a given October Term."""
    yy = f"{term % 100:02d}"
    return [
        f"{BASE_URL}/opinions/slipopinion/{yy}",
        f"{BASE_URL}/oral_arguments/argument_transcript/{term}",
        f"{BASE_URL}/orders/{yy}grantednotedlist.pdf",
    ]


def discover_dockets_from_pdf(data: bytes) -> list[str]:
    """Extract unique valid Supreme Court docket numbers from a granted/noted list PDF."""
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    found: list[str] = []
    seen: set[str] = set()
    for m in DOCKET_RE.finditer(text):
        try:
            norm = normalize_docket(m.group(1))
        except ValueError:
            continue
        # Skip bogus zero-number matches like "20-000" from PDF headers/dates
        if norm.endswith("-0") or norm.endswith("-00") or norm.endswith("-000"):
            continue
        if norm not in seen:
            seen.add(norm)
            found.append(norm)
    return found


def _is_amicus_proceeding(text: str) -> bool:
    low = text.lower()
    return "amicus" in low or "amici curiae" in low


def _get_with_retry(client: httpx.Client, url: str, attempts: int = 3) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(url, follow_redirects=True)
            if resp.status_code in {429, 500, 502, 503, 504} and attempt < attempts:
                time.sleep(float(attempt))
                continue
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(float(attempt))
    assert last_exc is not None
    raise last_exc


def download_pdf(
    client: httpx.Client,
    url: str,
    documents_dir: Path,
) -> tuple[str, str]:
    """Download a PDF from supremecourt.gov into documents/<prefix>/<sha256>.pdf."""
    if not is_official_url(url):
        raise ValueError(f"Refusing non-official URL: {url}")
    resp = _get_with_retry(client, url)
    data = resp.content
    digest = hashlib.sha256(data).hexdigest()
    rel_path = Path("documents") / digest[:2] / f"{digest}.pdf"
    dest = documents_dir.parent / rel_path if documents_dir.name == "documents" else (
        documents_dir / digest[:2] / f"{digest}.pdf"
    )
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return digest, str(rel_path)


def make_client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )


def monitor_dockets(
    *,
    dockets: list[str] | None = None,
    term: int | None = None,
    limit: int | None = None,
    download_pdfs: bool = True,
    download_amicus: bool = False,
    root: Path = Path("."),
    client: httpx.Client | None = None,
    delay_seconds: float = 0.25,
) -> list[CaseDocket]:
    """Fetch docket pages, download new filing PDFs, and save data/docket/<docket>.json."""
    docket_dir = root / "data" / "docket"
    docs_dir = root / "documents"
    docket_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    owns_client = client is None
    http = client or make_client()
    try:
        targets: list[str] = []
        active_term: int | None = term
        if dockets:
            targets = [normalize_docket(d) for d in dockets]
        else:
            active_term = term or _current_term()
            seen: set[str] = set()
            for url in index_urls_for_term(active_term):
                try:
                    resp = _get_with_retry(http, url)
                except httpx.HTTPError:
                    continue
                if url.lower().endswith(".pdf") and resp.content.startswith(b"%PDF"):
                    found = discover_dockets_from_pdf(resp.content)
                else:
                    found = discover_dockets_from_html(resp.text)
                for d in found:
                    if d not in seen:
                        seen.add(d)
                        targets.append(d)
                if delay_seconds:
                    time.sleep(delay_seconds)

        if limit is not None:
            targets = targets[:limit]

        results: list[CaseDocket] = []
        for docket_num in targets:
            page_url = DOCKET_URL_TEMPLATE.format(docket=docket_num)
            try:
                resp = _get_with_retry(http, page_url)
            except httpx.HTTPError as exc:
                print(f"[warn] failed to fetch docket {docket_num}: {exc}")
                continue

            case = parse_docket_html(docket_num, resp.text, page_url)
            if active_term is not None:
                case.term = active_term

            # Reuse previously downloaded hashes for known URLs
            existing_by_url: dict[str, tuple[str, str]] = {}
            out_path = docket_dir / f"{docket_num}.json"
            if out_path.exists():
                prev = CaseDocket.from_dict(json.loads(out_path.read_text(encoding="utf-8")))
                for proc in prev.proceedings:
                    for lk in proc.links:
                        if lk.sha256 and lk.pdf_path:
                            existing_by_url[lk.url] = (lk.sha256, lk.pdf_path)

            if download_pdfs:
                for proc in case.proceedings:
                    if not download_amicus and _is_amicus_proceeding(proc.text):
                        continue
                    for lk in proc.links:
                        if lk.url in existing_by_url:
                            lk.sha256, lk.pdf_path = existing_by_url[lk.url]
                            continue
                        try:
                            lk.sha256, lk.pdf_path = download_pdf(http, lk.url, docs_dir)
                            if delay_seconds:
                                time.sleep(delay_seconds)
                        except httpx.HTTPError as exc:
                            print(f"[warn] failed to download {lk.url}: {exc}")

            out_path.write_text(
                json.dumps(case.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            results.append(case)
            if delay_seconds:
                time.sleep(delay_seconds)

        return results
    finally:
        if owns_client:
            http.close()


def load_all_dockets(root: Path = Path(".")) -> list[CaseDocket]:
    docket_dir = root / "data" / "docket"
    if not docket_dir.exists():
        return []
    cases = [
        CaseDocket.from_dict(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(docket_dir.glob("*.json"))
    ]
    cases.sort(key=lambda c: (c.term, c.docket), reverse=True)
    return cases


def _current_term() -> int:
    now = datetime.now(UTC)
    return now.year if now.month >= 10 else now.year - 1
