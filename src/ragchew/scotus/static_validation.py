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
    PublicCaseBrief,
    ScotusPublicProjection,
    public_case_key,
)
from ragchew.scotus.static_contracts import (
    ReleaseFile,
    ReleaseManifest,
    StaticSearchIndex,
    assert_public_payload,
    canonical_json_bytes,
    sha256_hex,
    validate_projection_payload,
)
from ragchew.scotus.static_export import MANIFEST_PATH, content_release_id
from ragchew.scotus.static_state import StaticStateStore
from ragchew.scotus.static_urls import StaticUrlPolicy, archive_slug

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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"a", "link"} and values.get("href"):
            self.links.append((tag, values["href"] or ""))
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
    missing = sorted(str(path) for path in required if not (candidate / path).is_file())
    if missing:
        _fail(f"candidate is missing required files: {missing}")

    if any(path.is_symlink() for path in candidate.rglob("*")):
        _fail("candidate cannot contain symbolic links")
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
        search = StaticSearchIndex.model_validate_json(search_bytes)
        assert_public_payload(search.model_dump(mode="python"))
        if search_bytes != canonical_json_bytes(search):
            _fail("search index JSON is not canonical")
        if len(search.cases) != len(projection.cases):
            _fail("search index case count differs from projection")
        expected_search_paths = {urls.case(case) for case in projection.cases}
        if {item.path for item in search.cases} != expected_search_paths:
            _fail("search index paths differ from projection cases")
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
        state = StaticStateStore(state_root).load()
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
    case_directories: set[str] = set()
    archive_routes = {""}
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
    listings: dict[str, set[str]] = {"": {urls.case(case) for case in projection.cases}}
    for case in projection.cases:
        case_url = urls.case(case)
        listings.setdefault(f"terms/{case.term}", set()).add(case_url)
        listings.setdefault(f"statuses/{archive_slug(case.case_status.value)}", set()).add(case_url)
        for topic in case.topics:
            listings.setdefault(f"topics/{archive_slug(topic)}", set()).add(case_url)
        for argument in case.arguments:
            date = argument.argument_date.date().isoformat()
            listings.setdefault(f"arguments/{date}", set()).add(case_url)
    all_case_urls = {urls.case(case) for case in projection.cases}
    for route, expected in listings.items():
        actual: list[str] = []
        pages = _listing_paths(root, urls, route)
        if not all(path.is_file() for path in pages):
            _fail(f"archive is missing its first page: {route!r}")
        for page in pages:
            parser = _PageParser()
            parser.feed(page.read_text(encoding="utf-8"))
            actual.extend(
                link for tag, link in parser.links if tag == "a" and link in all_case_urls
            )
        if set(actual) != expected or len(actual) != len(set(actual)):
            _fail(f"archive does not link to every expected case exactly once: {route!r}")
    corrected = {urls.case(case) for case in projection.cases if len(case.revisions) > 1}
    parser = _PageParser()
    parser.feed(
        (root / urls.output_relative(urls.section("corrections"))).read_text(encoding="utf-8")
    )
    actual_corrected = {link for tag, link in parser.links if tag == "a" and link in all_case_urls}
    if actual_corrected != corrected:
        _fail("corrections index does not match corrected public cases")


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
