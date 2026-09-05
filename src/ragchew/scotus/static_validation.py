"""Privacy, integrity, contract, and link validation for static release candidates."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from html.parser import HTMLParser
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from pydantic import ValidationError

from ragchew.scotus.public_contracts import (
    STATIC_PUBLIC_SCHEMA_VERSION,
    PublicCaseBrief,
    ScotusPublicProjection,
    public_case_key,
)
from ragchew.scotus.static_contracts import (
    ReleaseFile,
    ReleaseManifest,
    assert_public_payload,
    canonical_json_bytes,
    sha256_hex,
    validate_projection_payload,
    validate_search_payload,
)
from ragchew.scotus.static_export import CNAME_PATH, MANIFEST_PATH, content_release_id
from ragchew.scotus.static_state import StaticStateStore
from ragchew.scotus.static_urls import (
    StaticUrlPolicy,
    archive_slug,
    latest_court_document_date,
    sort_cases,
)

_UUID = re.compile(
    rb"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])"
)
_SECRET = re.compile(
    rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})",
    re.I,
)
_FORBIDDEN_TEXT = (
    b"%PDF-",
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"s3://",
    b"/api/scotus",
    b"/api/",
    b"/static/",
    b"transcript_text",
    b"transcript_payload",
    b"source_html",
    b"source_body",
    b"extracted_text",
    b"private_text",
    b"object_key",
    b"signed_url",
    b"prompt_payload",
    b"model_output",
    b"model_response",
    b"raw_response",
    b"stack_trace",
)
_FORBIDDEN_SUFFIXES = {".pdf", ".doc", ".docx", ".wav", ".mp3", ".mp4", ".sqlite", ".db"}


class StaticValidationError(RuntimeError):
    """A candidate is not safe and complete enough to deploy."""


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.canonical: list[str] = []
        self.titles: list[str] = []
        self._in_title = False
        self._title_parts: list[str] = []
        self.landmarks: set[str] = set()
        self.h1_count = 0
        self.disclosure_text: list[str] = []
        self._capture_disclosure = 0
        self.redirect_target: str | None = None
        self.case_cards: list[tuple[str, str, str]] = []
        self.case_pages: list[tuple[str, str, str]] = []
        self.pagination_links: list[tuple[str, str]] = []
        self.argument_history_count = 0
        self.argument_date_count = 0
        self.argument_card_count = 0
        self.latest_activity_times: list[tuple[str, str]] = []
        self.latest_activity_fields: list[tuple[str, str, str, str]] = []
        self._latest_activity_datetime: str | None = None
        self._latest_activity_text: list[str] = []
        self._activity_field_path: str | None = None
        self._activity_field_label = ""
        self._activity_field_time: tuple[str, str] | None = None
        self.anchors: list[tuple[str, str]] = []
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"a", "link"} and values.get("href"):
            self.links.append((tag, values["href"] or ""))
        if tag == "a" and values.get("rel") in {"prev", "next"} and values.get("href"):
            self.pagination_links.append((values["rel"] or "", values["href"] or ""))
        if tag in {"script", "img"} and values.get("src"):
            self.links.append((tag, values["src"] or ""))
        if tag == "link" and values.get("rel") == "canonical" and values.get("href"):
            self.canonical.append(values["href"] or "")
        if tag == "title":
            self._in_title = True
            self._title_parts = []
        if tag in {"header", "main", "footer", "nav"}:
            self.landmarks.add(tag)
        if tag == "h1":
            self.h1_count += 1
        classes = set((values.get("class") or "").split())
        if tag == "a" and values.get("href"):
            self._anchor_href = values["href"] or ""
            self._anchor_text = []
        if tag == "div" and "latest-court-activity-field" in classes:
            self._activity_field_path = values.get("data-case-path") or ""
            self._activity_field_label = ""
            self._activity_field_time = None
        activity = (
            values.get("data-case-path") or values.get("data-case-page"),
            values.get("data-latest-court-document-date"),
            values.get("data-argument-session-count"),
        )
        activity_record = (
            activity[0] or "",
            activity[1] or "",
            activity[2] or "",
        )
        if tag == "article" and "case-card" in classes:
            self.case_cards.append(activity_record)
        if tag == "article" and "case-brief" in classes:
            self.case_pages.append(activity_record)
        if tag == "section" and "argument-history" in classes:
            self.argument_history_count += 1
        if "argument-date" in classes:
            self.argument_date_count += 1
        if tag == "article" and "argument-card" in classes:
            self.argument_card_count += 1
        if tag == "time" and "latest-court-activity" in classes:
            if self._latest_activity_datetime is not None:
                self._latest_activity_datetime = ""
            else:
                self._latest_activity_datetime = values.get("datetime") or ""
            self._latest_activity_text = []
        if (
            values.get("class") == "disclosure"
            or values.get("aria-label") == "Important disclosure"
        ):
            self._capture_disclosure += 1
        if tag == "meta" and (values.get("http-equiv") or "").casefold() == "refresh":
            match = re.search(r"(?:^|;)\s*url=(.+)$", values.get("content") or "", re.I)
            if match:
                self.redirect_target = match.group(1).strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "time" and self._latest_activity_datetime is not None:
            activity_time = (
                self._latest_activity_datetime,
                " ".join(" ".join(self._latest_activity_text).split()),
            )
            self.latest_activity_times.append(activity_time)
            if self._activity_field_path is not None:
                self._activity_field_time = activity_time
            self._latest_activity_datetime = None
            self._latest_activity_text = []
        if tag == "div" and self._activity_field_path is not None:
            field_time = self._activity_field_time or ("", "")
            self.latest_activity_fields.append(
                (
                    self._activity_field_path,
                    self._activity_field_label,
                    field_time[0],
                    field_time[1],
                )
            )
            self._activity_field_path = None
            self._activity_field_label = ""
            self._activity_field_time = None
        if tag == "a" and self._anchor_href is not None:
            self.anchors.append(
                (
                    self._anchor_href,
                    " ".join(" ".join(self._anchor_text).split()),
                )
            )
            self._anchor_href = None
            self._anchor_text = []
        if tag == "title" and self._in_title:
            self.titles.append("".join(self._title_parts).strip())
            self._in_title = False
        if self._capture_disclosure and tag in {"aside", "footer"}:
            self._capture_disclosure -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._capture_disclosure:
            self.disclosure_text.append(data)
        if self._latest_activity_datetime is not None:
            self._latest_activity_text.append(data)
        if self._anchor_href is not None:
            self._anchor_text.append(data)
        if (
            self._activity_field_path is not None
            and " ".join(data.split()) == "Latest official Court activity"
        ):
            self._activity_field_label = "Latest official Court activity"


def _fail(message: str) -> NoReturn:
    raise StaticValidationError(message)


def _manifest_records(root: Path) -> tuple[ReleaseFile, ...]:
    return tuple(
        ReleaseFile(
            path=path.relative_to(root).as_posix(),
            sha256=sha256_hex(path.read_bytes()),
            byte_count=path.stat().st_size,
        )
        for path in sorted(item for item in root.rglob("*") if item.is_file())
        if path.relative_to(root) != Path(MANIFEST_PATH)
    )


def scan_public_files(paths: Iterable[Path], *, labels: Iterable[str] = ()) -> None:
    """Scan candidate/state/log/upload bytes and names for prohibited public material."""
    for label in labels:
        encoded = label.encode("utf-8", errors="replace")
        lowered = encoded.lower()
        if (
            _SECRET.search(encoded)
            or _UUID.search(encoded)
            or any(value.lower() in lowered for value in _FORBIDDEN_TEXT)
        ):
            _fail("upload or log label contains forbidden private material")
    for path in paths:
        if not path.is_file():
            continue
        name = path.as_posix().encode("utf-8", errors="replace").lower()
        if (
            _SECRET.search(name)
            or _UUID.search(name)
            or any(marker.lower() in name for marker in _FORBIDDEN_TEXT)
        ):
            _fail(f"forbidden private marker in public path: {path}")
        if path.suffix.casefold() in _FORBIDDEN_SUFFIXES:
            _fail(f"prohibited file type in public bundle: {path}")
        value = path.read_bytes()
        lowered = value.lower()
        if _SECRET.search(value) or _UUID.search(value):
            _fail(f"credential or internal UUID detected: {path}")
        if any(marker.lower() in lowered for marker in _FORBIDDEN_TEXT):
            _fail(f"forbidden private payload or legacy route detected: {path}")


def validate_static_candidate(
    root: str | Path,
    urls: StaticUrlPolicy,
    *,
    state_root: str | Path | None = None,
    log_paths: Iterable[str | Path] = (),
    upload_paths: Iterable[str] = (),
) -> ReleaseManifest:
    """Reparse and cross-check an entire candidate before it becomes deployable."""
    candidate = Path(root)
    if not candidate.is_dir():
        _fail("static candidate directory does not exist")
    required = {
        Path("index.html"),
        Path("404.html"),
        Path(".nojekyll"),
        Path("robots.txt"),
        Path("sitemap.xml"),
        Path("data/v1/projection.json"),
        Path("data/v1/search.json"),
        Path(MANIFEST_PATH),
        Path(urls.output_relative(urls.section())),
        Path(urls.output_relative(urls.section("search"))),
        Path(urls.output_relative(urls.section("corrections"))),
    }
    if urls.custom_domain is not None:
        required.add(Path(CNAME_PATH))
    missing = sorted(str(path) for path in required if not (candidate / path).is_file())
    if missing:
        _fail(f"candidate is missing required files: {missing}")
    if any(path.is_symlink() for path in candidate.rglob("*")):
        _fail("candidate cannot contain symbolic links")

    cname = candidate / CNAME_PATH
    if urls.custom_domain is None:
        if cname.exists():
            _fail("project Pages candidate must not contain a CNAME")
    elif cname.read_bytes() != f"{urls.custom_domain}\n".encode("ascii"):
        _fail("CNAME is malformed or does not match the canonical origin")

    all_files = tuple(sorted(path for path in candidate.rglob("*") if path.is_file()))
    scan_public_files(
        (*all_files, *(Path(path) for path in log_paths)),
        labels=upload_paths,
    )
    try:
        projection_bytes = (candidate / "data/v1/projection.json").read_bytes()
        projection_payload = json.loads(projection_bytes)
        if not isinstance(projection_payload, dict):
            _fail("public projection JSON must be an object")
        projection = validate_projection_payload(projection_payload)
        if projection.schema_version != STATIC_PUBLIC_SCHEMA_VERSION:
            _fail("public projection requires explicit activity-contract migration")
        if projection_bytes != canonical_json_bytes(projection):
            _fail("public projection JSON is not canonical")
        projection_cases = {
            public_case_key(case.term, case.primary_docket): case for case in projection.cases
        }
        case_paths = (
            tuple(sorted((candidate / "data/v1/cases").glob("*.json")))
            if (candidate / "data/v1/cases").exists()
            else ()
        )
        if {path.stem for path in case_paths} != set(projection_cases):
            _fail("per-case JSON files do not exactly match projection identities")
        for path in case_paths:
            public_case = PublicCaseBrief.model_validate_json(path.read_bytes())
            assert_public_payload(public_case.model_dump(mode="python"))
            if public_case != projection_cases[
                path.stem
            ] or path.read_bytes() != canonical_json_bytes(public_case):
                _fail(f"per-case JSON differs from projection: {path.name}")
        search_bytes = (candidate / "data/v1/search.json").read_bytes()
        search_payload = json.loads(search_bytes)
        if not isinstance(search_payload, dict):
            _fail("search index JSON must be an object")
        search = validate_search_payload(search_payload)
        if search.schema_version != "1.1":
            _fail("search index requires explicit activity-contract migration")
        if search_bytes != canonical_json_bytes(search):
            _fail("search index JSON is not canonical")
        # Rebuild rather than comparing identities as a set: every field and the
        # exact shared newest-first order are part of the deployed search contract.
        from ragchew.scotus.static_export import build_search_index

        expected_search = build_search_index(projection, urls)
        if search != expected_search:
            _fail("search fields or exact newest-first order differ from projection cases")
        manifest = ReleaseManifest.model_validate_json((candidate / MANIFEST_PATH).read_bytes())
        if (candidate / MANIFEST_PATH).read_bytes() != canonical_json_bytes(manifest):
            _fail("release manifest is not canonical")
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as error:
        raise StaticValidationError(f"public contract validation failed: {error}") from error

    allowed_json = {
        "data/v1/projection.json",
        "data/v1/search.json",
        "release/v1/release.json",
        *(f"data/v1/cases/{key}.json" for key in projection_cases),
    }
    actual_json = {
        path.relative_to(candidate).as_posix() for path in all_files if path.suffix == ".json"
    }
    if actual_json != allowed_json:
        _fail("candidate contains JSON outside the allowlisted public contracts")

    _validate_file_allowlist(candidate, all_files, projection, urls)
    records = _manifest_records(candidate)
    if records != manifest.files:
        _fail("release manifest digest, size, or file set mismatch")
    if manifest.projection_sha256 != sha256_hex(projection_bytes):
        _fail("release projection digest mismatch")
    html_count = sum(1 for path in all_files if path.suffix == ".html")
    if manifest.case_count != len(projection.cases) or manifest.page_count != html_count:
        _fail("release aggregate counts are incorrect")
    expected_release = content_release_id(
        files=records,
        source_commit=manifest.source_commit,
        previous_release_id=manifest.previous_release_id,
        projection_sha256=manifest.projection_sha256,
        config_sha256=manifest.config_sha256,
        tool_version=manifest.tool_version,
        case_count=manifest.case_count,
        page_count=manifest.page_count,
    )
    if manifest.release_id != expected_release:
        _fail("content-derived release ID is incorrect")

    _validate_html(candidate, urls, projection.disclosure)
    _validate_route_coverage(candidate, urls, projection)
    _validate_sitemap(candidate, urls)
    robots = (candidate / "robots.txt").read_text(encoding="utf-8")
    expected_sitemap = urls.canonical(urls.internal("sitemap.xml")).rstrip("/")
    if (
        f"Allow: {urls.project_base_path}" not in robots
        or f"Sitemap: {expected_sitemap}" not in robots
    ):
        _fail("robots policy does not match configured project path and sitemap")

    if state_root is not None:
        from ragchew.scotus.activity_migration import require_current_activity_contracts

        state = StaticStateStore(state_root).load()
        require_current_activity_contracts(state)
        if state.projection != projection:
            _fail("generated state projection differs from the static candidate")
        if state.release is None or state.release.release_id != manifest.release_id:
            _fail("generated state release pointer differs from the static candidate")
        if state.release != manifest:
            _fail("generated state does not contain the exact exporter manifest")
        if state.release.previous_release_id != manifest.previous_release_id:
            _fail("release-parent consistency check failed")
        state_files = tuple(path for path in Path(state_root).rglob("*") if path.is_file())
        allowed_state = {
            StaticStateStore.PROJECTION_PATH.as_posix(),
            StaticStateStore.PUBLICATION_PATH.as_posix(),
            StaticStateStore.COST_LEDGER_PATH.as_posix(),
            StaticStateStore.RELEASE_PATH.as_posix(),
            *(
                StaticStateStore._revision_path(key, number).as_posix()
                for key, number in state.revisions
            ),
        }
        if {path.relative_to(state_root).as_posix() for path in state_files} != allowed_state:
            _fail("generated state contains files outside its allowlisted contracts")
        scan_public_files(state_files)
    return manifest


def _validate_file_allowlist(
    root: Path,
    files: tuple[Path, ...],
    projection: ScotusPublicProjection,
    urls: StaticUrlPolicy,
) -> None:
    fixed = {
        "index.html",
        "404.html",
        ".nojekyll",
        "robots.txt",
        "sitemap.xml",
        "data/v1/projection.json",
        "data/v1/search.json",
        "release/v1/release.json",
        urls.output_relative(urls.section()).as_posix(),
        urls.output_relative(urls.section("search")).as_posix(),
        urls.output_relative(urls.section("corrections")).as_posix(),
    }
    if urls.custom_domain is not None:
        fixed.add(CNAME_PATH.as_posix())
    case_directories: set[str] = set()
    archive_routes = {"", "corrections"}
    for case in projection.cases:
        key = public_case_key(case.term, case.primary_docket)
        fixed.add(f"data/v1/cases/{key}.json")
        case_path = urls.output_relative(urls.case(case)).as_posix()
        fixed.add(case_path)
        case_directories.add(str(Path(case_path).parent.parent))
        archive_routes.add(f"terms/{case.term}")
        archive_routes.add(f"statuses/{archive_slug(case.case_status.value)}")
        archive_routes.update(f"topics/{archive_slug(topic)}" for topic in case.topics)
        archive_routes.update(
            f"arguments/{argument.argument_date.date().isoformat()}" for argument in case.arguments
        )
    archive_prefixes = {
        urls.output_relative(urls.section(route)).parent.as_posix() for route in archive_routes
    }
    fixed.update(f"{prefix}/index.html" for prefix in archive_prefixes)
    for path in files:
        relative = path.relative_to(root).as_posix()
        if relative in fixed:
            continue
        if relative.startswith("assets/"):
            match = re.fullmatch(
                r"assets/(?:scotus|scotus-search)\.([0-9a-f]{12})\.(?:css|js)", relative
            )
            if match is None or sha256_hex(path.read_bytes())[:12] != match.group(1):
                _fail(f"asset is not allowlisted or fingerprinted correctly: {relative}")
            continue
        file_path = Path(relative)
        if file_path.name == "index.html" and str(file_path.parent.parent) in case_directories:
            continue
        pagination = re.fullmatch(r"(.+)/page/([1-9][0-9]*)/index\.html", relative)
        if pagination is not None and pagination.group(1) in archive_prefixes:
            continue
        _fail(f"candidate contains a file outside generated route allowlists: {relative}")


def _listing_paths(root: Path, urls: StaticUrlPolicy, route: str) -> tuple[Path, ...]:
    first = root / urls.output_relative(urls.page(route, 1))
    pages = [first]
    page_root = first.parent / "page"
    if page_root.exists():
        numbered = sorted(
            (int(path.parent.name), path)
            for path in page_root.glob("*/index.html")
            if path.parent.name.isdigit()
        )
        if [number for number, _ in numbered] != list(range(2, len(numbered) + 2)):
            _fail(f"pagination is not contiguous for archive {route!r}")
        pages.extend(path for _, path in numbered)
    return tuple(pages)


def _validate_route_coverage(
    root: Path, urls: StaticUrlPolicy, projection: ScotusPublicProjection
) -> None:
    grouped: dict[str, list[PublicCaseBrief]] = {
        "": list(projection.cases),
        "corrections": [case for case in projection.cases if len(case.revisions) > 1],
    }
    for case in projection.cases:
        grouped.setdefault(f"terms/{case.term}", []).append(case)
        grouped.setdefault(f"statuses/{archive_slug(case.case_status.value)}", []).append(case)
        for topic in set(case.topics):
            grouped.setdefault(f"topics/{archive_slug(topic)}", []).append(case)
        for argument_date in {
            argument.argument_date.date().isoformat() for argument in case.arguments
        }:
            grouped.setdefault(f"arguments/{argument_date}", []).append(case)

    listings = {
        route: sort_cases(tuple(cases)) for route, cases in grouped.items()
    }
    case_by_url = {urls.case(case): case for case in projection.cases}
    all_case_urls = set(case_by_url)
    for route, expected_cases in listings.items():
        expected_links = tuple(urls.case(case) for case in expected_cases)
        expected_cards = tuple(
            (
                urls.case(case),
                latest_court_document_date(case).date().isoformat(),
                str(len(case.arguments)),
            )
            for case in expected_cases
        )
        expected_activity_fields = tuple(
            (
                urls.case(case),
                "Latest official Court activity",
                latest_court_document_date(case).isoformat(),
                latest_court_document_date(case).date().isoformat(),
            )
            for case in expected_cases
        )
        actual_links: list[str] = []
        actual_cards: list[tuple[str, str, str]] = []
        actual_activity_fields: list[tuple[str, str, str, str]] = []
        page_card_counts: list[int] = []
        pages = _listing_paths(root, urls, route)
        if not all(path.is_file() for path in pages):
            _fail(f"archive is missing its first page: {route!r}")
        for page_number, page in enumerate(pages, 1):
            parser = _PageParser()
            parser.feed(page.read_text(encoding="utf-8"))
            page_links = tuple(
                link for tag, link in parser.links if tag == "a" and link in all_case_urls
            )
            actual_links.extend(page_links)
            actual_cards.extend(parser.case_cards)
            actual_activity_fields.extend(parser.latest_activity_fields)
            page_card_counts.append(len(parser.case_cards))
            expected_pagination: list[tuple[str, str]] = []
            if page_number > 1:
                expected_pagination.append(("prev", urls.page(route, page_number - 1)))
            if page_number < len(pages):
                expected_pagination.append(("next", urls.page(route, page_number + 1)))
            if parser.pagination_links != expected_pagination:
                _fail(f"pagination links are inconsistent for archive {route!r}")
        if tuple(actual_links) != expected_links:
            _fail(f"archive case links differ from exact newest-first order: {route!r}")
        if (
            tuple(actual_cards) != expected_cards
            or tuple(actual_activity_fields) != expected_activity_fields
        ):
            _fail(f"archive activity markup differs from exact case order: {route!r}")
        if len(pages) > 1:
            page_size = page_card_counts[0]
            if (
                page_size == 0
                or any(count != page_size for count in page_card_counts[:-1])
                or not 0 < page_card_counts[-1] <= page_size
            ):
                _fail(f"archive pagination has inconsistent page boundaries: {route!r}")

    for case_url, case in case_by_url.items():
        page = root / urls.output_relative(case_url)
        parser = _PageParser()
        parser.feed(page.read_text(encoding="utf-8"))
        expected_activity = (
            case_url,
            latest_court_document_date(case).date().isoformat(),
            str(len(case.arguments)),
        )
        expected_activity_field = (
            case_url,
            "Latest official Court activity",
            latest_court_document_date(case).isoformat(),
            latest_court_document_date(case).date().isoformat(),
        )
        if (
            parser.case_pages != [expected_activity]
            or parser.latest_activity_fields != [expected_activity_field]
        ):
            _fail(f"case page activity markup differs from public contract: {case_url}")
        expected_argument_markup = int(bool(case.arguments))
        argument_links = tuple(
            (href, text)
            for href, text in parser.anchors
            if any(
                marker in f"{href} {text}".casefold()
                for marker in (
                    "/oral_arguments/",
                    "oral-argument",
                    "oral argument",
                    "transcript",
                )
            )
        )
        if (
            parser.argument_history_count != expected_argument_markup
            or parser.argument_date_count != expected_argument_markup
            or parser.argument_card_count != len(case.arguments)
            or (not case.arguments and argument_links)
        ):
            _fail(f"case page argument markup differs from real sessions: {case_url}")


def _url_to_file(root: Path, urls: StaticUrlPolicy, value: str) -> Path | None:
    parsed = urlsplit(value)
    if (
        parsed.scheme or parsed.netloc
    ) and f"{parsed.scheme}://{parsed.netloc}" != urls.canonical_origin:
        return None
    path = unquote(parsed.path)
    if not path.startswith(urls.project_base_path):
        _fail(f"internal URL escapes configured project base path: {value}")
    relative = path[len(urls.project_base_path) :].lstrip("/")
    parts = tuple(part for part in relative.split("/") if part)
    if any(part in {".", ".."} for part in parts):
        _fail(f"internal URL contains a traversal segment: {value}")
    if not relative:
        return root / "index.html"
    candidate = root.joinpath(*parts)
    if path.endswith("/"):
        candidate /= "index.html"
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        _fail(f"internal URL resolves outside the candidate: {value}")
    return candidate


def _validate_html(root: Path, urls: StaticUrlPolicy, disclosure: str) -> None:
    titles: dict[str, Path] = {}
    for path in sorted(root.rglob("*.html")):
        parser = _PageParser()
        try:
            parser.feed(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as error:
            raise StaticValidationError(f"invalid HTML encoding: {path}") from error
        if len(parser.titles) != 1 or not parser.titles[0]:
            _fail(f"page must have one non-empty title: {path}")
        if parser.titles[0] in titles and "Brief moved" not in parser.titles[0]:
            _fail(f"page titles must be unique: {path} and {titles[parser.titles[0]]}")
        titles[parser.titles[0]] = path
        if len(parser.canonical) != 1:
            _fail(f"page must have exactly one canonical URL: {path}")
        canonical = urlsplit(parser.canonical[0])
        if (
            f"{canonical.scheme}://{canonical.netloc}" != urls.canonical_origin
            or canonical.query
            or canonical.fragment
            or not canonical.path.startswith(urls.project_base_path)
        ):
            _fail(f"canonical URL escapes configured site: {path}")
        relative = path.relative_to(root)
        if relative.name == "index.html":
            route = relative.parent.as_posix()
            expected_canonical = urls.canonical(urls.internal("" if route == "." else route))
        else:
            expected_canonical = urls.canonical(urls.internal(relative.as_posix())).rstrip("/")
        if parser.redirect_target is None and parser.canonical[0] != expected_canonical:
            _fail(f"canonical URL does not match the generated page path: {path}")
        if not {"header", "main", "footer", "nav"}.issubset(
            parser.landmarks
        ) or parser.h1_count != 1:
            _fail(f"page lacks required semantic landmarks or one h1: {path}")
        if disclosure not in " ".join(" ".join(parser.disclosure_text).split()):
            _fail(f"page lacks the complete public disclosure: {path}")
        for tag, link in parser.links:
            if link.startswith(("mailto:", "tel:", "#")):
                continue
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc:
                if parsed.hostname == "www.supremecourt.gov":
                    if (
                        parsed.scheme != "https"
                        or tag != "a"
                        or parsed.username
                        or parsed.password
                        or parsed.port is not None
                    ):
                        _fail(f"invalid official source link: {link}")
                    continue
                if f"{parsed.scheme}://{parsed.netloc}" != urls.canonical_origin:
                    _fail(f"unapproved external link in static candidate: {link}")
            target = _url_to_file(root, urls, link)
            if target is not None and not target.is_file():
                _fail(f"broken internal link in {path}: {link}")
        if parser.redirect_target:
            target = _url_to_file(root, urls, parser.redirect_target)
            if (
                target is None
                or not target.is_file()
                or parser.canonical[0] != urls.canonical(parser.redirect_target)
            ):
                _fail(f"invalid redirect page: {path}")


def _validate_sitemap(root: Path, urls: StaticUrlPolicy) -> None:
    try:
        tree = ElementTree.parse(root / "sitemap.xml")
    except (ElementTree.ParseError, OSError) as error:
        raise StaticValidationError("sitemap is not valid XML") from error
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locations = [element.text or "" for element in tree.findall("s:url/s:loc", namespace)]
    if locations != sorted(set(locations)):
        _fail("sitemap URLs must be unique and deterministically sorted")
    for location in locations:
        target = _url_to_file(root, urls, location)
        if target is None or not target.is_file() or target.name == "404.html":
            _fail(f"sitemap contains an invalid page: {location}")
    expected: set[str] = set()
    for path in root.rglob("*.html"):
        if path.name == "404.html":
            continue
        parser = _PageParser()
        parser.feed(path.read_text(encoding="utf-8"))
        if parser.redirect_target is None:
            expected.add(parser.canonical[0])
    if set(locations) != expected:
        _fail("sitemap does not cover exactly the canonical non-redirect pages")
