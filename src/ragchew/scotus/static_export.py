"""Deterministic, request-independent GitHub Pages export for SCOTUS briefs."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from pathlib import Path, PurePosixPath
from typing import Any
from xml.sax.saxutils import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ragchew.scotus.public_contracts import PublicCaseBrief, ScotusPublicProjection, public_case_key
from ragchew.scotus.static_contracts import (
    ReleaseFile,
    ReleaseManifest,
    StaticSearchEntry,
    StaticSearchIndex,
    canonical_json_bytes,
    sha256_hex,
    validate_projection_payload,
)
from ragchew.scotus.static_urls import (
    StaticUrlPolicy,
    archive_slug,
    latest_court_document_date,
    sort_cases,
)

TOOL_VERSION = "ragchew-static-v1"
DEFAULT_PAGE_SIZE = 20
MANIFEST_PATH = PurePosixPath("release/v1/release.json")
CNAME_PATH = PurePosixPath("CNAME")


class StaticExportError(RuntimeError):
    """A complete deployable static candidate could not be created."""


@dataclass(frozen=True)
class StaticExportResult:
    output: Path
    manifest: ReleaseManifest


def build_search_index(
    projection: ScotusPublicProjection, urls: StaticUrlPolicy
) -> StaticSearchIndex:
    return StaticSearchIndex(
        cases=tuple(
            StaticSearchEntry(
                path=urls.case(case),
                title=case.title,
                caption=case.caption,
                docket=case.primary_docket,
                term=case.term,
                latest_court_document_date=(
                    latest_court_document_date(case).date().isoformat()
                ),
                argument_date=(
                    case.argument_date.date().isoformat()
                    if case.argument_date is not None
                    else None
                ),
                status=case.case_status.value,
                topics=tuple(sorted(set(case.topics), key=lambda value: (value.casefold(), value))),
            )
            for case in sort_cases(projection.cases)
        )
    )


def content_release_id(
    *,
    files: tuple[ReleaseFile, ...],
    source_commit: str,
    previous_release_id: str | None,
    projection_sha256: str,
    config_sha256: str,
    tool_version: str,
    case_count: int,
    page_count: int,
) -> str:
    """Derive an ID solely from release content and reproducibility inputs."""
    payload = {
        "case_count": case_count,
        "config_sha256": config_sha256,
        "files": [item.model_dump(mode="json") for item in files],
        "page_count": page_count,
        "previous_release_id": previous_release_id,
        "projection_sha256": projection_sha256,
        "source_commit": source_commit,
        "tool_version": tool_version,
    }
    return sha256_hex(canonical_json_bytes(payload, privacy_check=False))


class StaticSiteExporter:
    """Render a complete candidate in a temporary directory and publish it atomically."""

    def __init__(
        self,
        urls: StaticUrlPolicy,
        *,
        template_directory: str | Path = "templates",
        static_directory: str | Path = "static",
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        if page_size < 1 or page_size > 100:
            raise ValueError("static page size must be between 1 and 100")
        self.urls = urls
        self.template_directory = Path(template_directory)
        self.static_directory = Path(static_directory)
        self.page_size = page_size
        self.environment = Environment(
            loader=FileSystemLoader(self.template_directory),
            autoescape=select_autoescape(("html", "xml")),
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.environment.globals.update(
            latest_court_document_date=latest_court_document_date,
            archive_slug=archive_slug,
            public_case_key=public_case_key,
        )

    def export(
        self,
        projection: ScotusPublicProjection,
        destination: str | Path,
        *,
        source_commit: str,
        build_epoch: datetime,
        config_sha256: str,
        previous_release_id: str | None = None,
        legacy_slugs: Mapping[str, tuple[str, ...]] | None = None,
        validate: bool = True,
    ) -> StaticExportResult:
        """Write a deterministic complete tree; never leave a partial destination."""
        # Round-trip through canonical JSON even for already constructed models. This
        # normalizes aware timestamps to UTC and set-like fields before HTML, search,
        # and public JSON are derived from the exact same projection values.
        projection_payload = json.loads(canonical_json_bytes(projection))
        if not isinstance(projection_payload, dict):  # pragma: no cover - model invariant
            raise StaticExportError("public projection must serialize as an object")
        for case_payload in projection_payload.get("cases", []):
            if isinstance(case_payload, dict) and isinstance(case_payload.get("topics"), list):
                case_payload["topics"] = sorted(
                    set(case_payload["topics"]),
                    key=lambda value: (value.casefold(), value),
                )
        projection = validate_projection_payload(projection_payload)
        if build_epoch.tzinfo is None or build_epoch.utcoffset() is None:
            raise StaticExportError("build epoch must be timezone-aware")
        build_epoch = build_epoch.astimezone(UTC)
        if len(source_commit) != 40 or any(
            character not in "0123456789abcdef" for character in source_commit
        ):
            raise StaticExportError("source commit must be a lowercase 40-character Git SHA")
        if len(config_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in config_sha256
        ):
            raise StaticExportError("config digest must be a lowercase SHA-256")
        topic_slugs: dict[str, str] = {}
        for topic in (topic for case in projection.cases for topic in case.topics):
            slug = archive_slug(topic)
            existing = topic_slugs.get(slug)
            if existing is not None and existing != topic:
                raise StaticExportError(
                    f"topic labels produce the same archive path: {existing!r} and {topic!r}"
                )
            topic_slugs[slug] = topic
        output = Path(destination)
        if output.exists():
            raise StaticExportError("static candidate destination already exists")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
        try:
            manifest = self._render_tree(
                projection,
                temporary,
                source_commit=source_commit,
                build_epoch=build_epoch,
                config_sha256=config_sha256,
                previous_release_id=previous_release_id,
                legacy_slugs=legacy_slugs or {},
            )
            if validate:
                from ragchew.scotus.static_validation import validate_static_candidate

                validate_static_candidate(temporary, self.urls)
            os.replace(temporary, output)
            return StaticExportResult(output=output, manifest=manifest)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def _render_tree(
        self,
        projection: ScotusPublicProjection,
        root: Path,
        *,
        source_commit: str,
        build_epoch: datetime,
        config_sha256: str,
        previous_release_id: str | None,
        legacy_slugs: Mapping[str, tuple[str, ...]],
    ) -> ReleaseManifest:
        css_asset = self._copy_asset(root, "scotus.css")
        search_asset = self._copy_asset(root, "scotus-search.js")
        common: dict[str, Any] = {
            "projection": projection,
            "urls": self.urls,
            "css_asset": css_asset,
            "search_asset": search_asset,
            "current_term": max((case.term for case in projection.cases), default=None),
        }
        canonical_pages: list[str] = []

        def render(template: str, internal_url: str, **context: Any) -> None:
            values = {**common, **context, "canonical_url": self.urls.canonical(internal_url)}
            rendered = self.environment.get_template(template).render(values)
            self._write(root / self.urls.output_relative(internal_url), rendered.encode("utf-8"))
            canonical_pages.append(internal_url)

        root_url = self.urls.internal()
        statuses = sorted({case.case_status.value for case in projection.cases})
        topics = sorted(
            {topic for case in projection.cases for topic in case.topics},
            key=lambda value: (value.casefold(), value),
        )
        render(
            "scotus_search.html",
            root_url,
            page_title="SCOTUS Legal Briefs",
            statuses=statuses,
            topics=topics,
        )
        archive_links = self._archive_links(projection)
        self._render_listing(
            render,
            projection,
            route="",
            cases=projection.cases,
            heading="Latest case briefs",
            introduction="Browse every published brief without JavaScript.",
            archive_links=archive_links,
        )
        for term in sorted({case.term for case in projection.cases}, reverse=True):
            self._render_listing(
                render,
                projection,
                route=f"terms/{term}",
                cases=tuple(case for case in projection.cases if case.term == term),
                heading=f"October Term {term}",
            )
        dates = sorted(
            {
                argument.argument_date.date()
                for case in projection.cases
                for argument in case.arguments
            },
            reverse=True,
        )
        for argument_date in dates:
            self._render_listing(
                render,
                projection,
                route=f"arguments/{argument_date.isoformat()}",
                cases=tuple(
                    case
                    for case in projection.cases
                    if any(item.argument_date.date() == argument_date for item in case.arguments)
                ),
                heading=f"Arguments on {argument_date.isoformat()}",
            )
        for status in sorted({case.case_status.value for case in projection.cases}):
            self._render_listing(
                render,
                projection,
                route=f"statuses/{archive_slug(status)}",
                cases=tuple(case for case in projection.cases if case.case_status.value == status),
                heading=f"Status: {status.replace('_', ' ').title()}",
            )
        for topic in sorted(
            {topic for case in projection.cases for topic in case.topics},
            key=lambda value: (value.casefold(), value),
        ):
            self._render_listing(
                render,
                projection,
                route=f"topics/{archive_slug(topic)}",
                cases=tuple(case for case in projection.cases if topic in case.topics),
                heading=f"Topic: {topic}",
            )
        for case in sort_cases(projection.cases):
            case_url = self.urls.case(case)
            render("scotus_case.html", case_url, case=case)
            key = public_case_key(case.term, case.primary_docket)
            for old_slug in sorted(set(legacy_slugs.get(key, ()))):
                if old_slug == case.slug:
                    continue
                redirect_url = self.urls.case(case, slug=old_slug)
                rendered = self.environment.get_template("scotus_redirect.html").render(
                    **common,
                    target_url=case_url,
                    canonical_url=self.urls.canonical(case_url),
                )
                self._write(
                    root / self.urls.output_relative(redirect_url), rendered.encode("utf-8")
                )

        corrected = tuple(case for case in projection.cases if len(case.revisions) > 1)
        self._render_corrections(render, corrected)
        render(
            "scotus_search.html",
            self.urls.section("search"),
            statuses=statuses,
            topics=topics,
        )
        rendered_404 = self.environment.get_template("scotus_404.html").render(
            **common,
            canonical_url=self.urls.canonical(self.urls.internal("404.html")).rstrip("/"),
        )
        self._write(root / "404.html", rendered_404.encode("utf-8"))

        projection_bytes = canonical_json_bytes(projection)
        self._write(root / "data/v1/projection.json", projection_bytes)
        for case in sort_cases(projection.cases):
            key = public_case_key(case.term, case.primary_docket)
            self._write(root / f"data/v1/cases/{key}.json", canonical_json_bytes(case))
        self._write(
            root / "data/v1/search.json",
            canonical_json_bytes(build_search_index(projection, self.urls)),
        )
        self._write(root / ".nojekyll", b"")
        if self.urls.custom_domain is not None:
            self._write(root / CNAME_PATH, f"{self.urls.custom_domain}\n".encode("ascii"))
        self._write(
            root / "robots.txt",
            (
                "User-agent: *\nAllow: "
                + self.urls.project_base_path
                + "\nSitemap: "
                + self.urls.canonical(self.urls.internal("sitemap.xml")).rstrip("/")
                + "\n"
            ).encode("utf-8"),
        )
        sitemap = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "".join(
                f"  <url><loc>{escape(self.urls.canonical(url))}</loc></url>\n"
                for url in sorted(set(canonical_pages))
            )
            + "</urlset>\n"
        )
        self._write(root / "sitemap.xml", sitemap.encode("utf-8"))

        files = self._file_records(root)
        page_count = sum(1 for path in root.rglob("*.html"))
        projection_digest = sha256_hex(projection_bytes)
        release_id = content_release_id(
            files=files,
            source_commit=source_commit,
            previous_release_id=previous_release_id,
            projection_sha256=projection_digest,
            config_sha256=config_sha256,
            tool_version=TOOL_VERSION,
            case_count=len(projection.cases),
            page_count=page_count,
        )
        manifest = ReleaseManifest(
            release_id=release_id,
            previous_release_id=previous_release_id,
            source_commit=source_commit,
            projection_sha256=projection_digest,
            config_sha256=config_sha256,
            tool_version=TOOL_VERSION,
            generated_at=build_epoch,
            files=files,
            case_count=len(projection.cases),
            page_count=page_count,
        )
        self._write(root / MANIFEST_PATH, canonical_json_bytes(manifest))
        return manifest

    def _render_listing(
        self,
        render: Any,
        projection: ScotusPublicProjection,
        *,
        route: str,
        cases: tuple[PublicCaseBrief, ...],
        heading: str,
        introduction: str = "",
        archive_links: tuple[tuple[str, str], ...] = (),
    ) -> None:
        ordered = sort_cases(cases)
        count = max(1, ceil(len(ordered) / self.page_size))
        for page in range(1, count + 1):
            start = (page - 1) * self.page_size
            internal_url = self.urls.page(route, page)
            render(
                "scotus_index.html",
                internal_url,
                cases=ordered[start : start + self.page_size],
                heading=heading if page == 1 else f"{heading} — page {page}",
                introduction=introduction,
                archive_links=archive_links if page == 1 else (),
                page=page,
                page_count=count,
                page_start=start + 1,
                total_cases=len(ordered),
                previous_url=self.urls.page(route, page - 1) if page > 1 else None,
                next_url=self.urls.page(route, page + 1) if page < count else None,
            )

    def _render_corrections(
        self,
        render: Any,
        cases: tuple[PublicCaseBrief, ...],
    ) -> None:
        ordered = sort_cases(cases)
        count = max(1, ceil(len(ordered) / self.page_size))
        for page in range(1, count + 1):
            start = (page - 1) * self.page_size
            render(
                "scotus_corrections.html",
                self.urls.page("corrections", page),
                corrected_cases=ordered[start : start + self.page_size],
                page=page,
                page_count=count,
                page_start=start + 1,
                total_cases=len(ordered),
                previous_url=(
                    self.urls.page("corrections", page - 1) if page > 1 else None
                ),
                next_url=(
                    self.urls.page("corrections", page + 1) if page < count else None
                ),
            )

    def _archive_links(self, projection: ScotusPublicProjection) -> tuple[tuple[str, str], ...]:
        links: list[tuple[str, str]] = []
        links.extend(
            (f"October Term {term}", self.urls.section(f"terms/{term}"))
            for term in sorted({case.term for case in projection.cases}, reverse=True)
        )
        links.extend(
            (f"Arguments on {value}", self.urls.section(f"arguments/{value}"))
            for value in sorted(
                {
                    item.argument_date.date().isoformat()
                    for case in projection.cases
                    for item in case.arguments
                },
                reverse=True,
            )
        )
        links.extend(
            (
                f"Status: {status.replace('_', ' ').title()}",
                self.urls.section(f"statuses/{archive_slug(status)}"),
            )
            for status in sorted({case.case_status.value for case in projection.cases})
        )
        links.extend(
            (f"Topic: {topic}", self.urls.section(f"topics/{archive_slug(topic)}"))
            for topic in sorted(
                {topic for case in projection.cases for topic in case.topics},
                key=lambda value: (value.casefold(), value),
            )
        )
        return tuple(links)

    def _copy_asset(self, root: Path, name: str) -> str:
        value = (self.static_directory / name).read_bytes()
        source = Path(name)
        fingerprinted = f"{source.stem}.{sha256_hex(value)[:12]}{source.suffix}"
        self._write(root / "assets" / fingerprinted, value)
        return fingerprinted

    @staticmethod
    def _file_records(root: Path) -> tuple[ReleaseFile, ...]:
        return tuple(
            ReleaseFile(
                path=path.relative_to(root).as_posix(),
                sha256=sha256_hex(path.read_bytes()),
                byte_count=path.stat().st_size,
            )
            for path in sorted(item for item in root.rglob("*") if item.is_file())
            if path.relative_to(root) != Path(MANIFEST_PATH)
        )

    @staticmethod
    def _write(path: Path, value: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
