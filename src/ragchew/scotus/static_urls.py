"""Shared ordering, slugs, and URL policy for SCOTUS public surfaces."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote, urljoin, urlsplit

from ragchew.scotus.public_contracts import PublicCaseBrief


def latest_court_document_date(case: PublicCaseBrief) -> datetime:
    """Return the latest date established by an argument record."""
    return max(case.argument_date, *(item.argument_date for item in case.arguments))


def sort_cases(cases: tuple[PublicCaseBrief, ...]) -> tuple[PublicCaseBrief, ...]:
    """Use one stable newest-first order for archives and search."""
    return tuple(
        sorted(
            cases,
            key=lambda case: (
                latest_court_document_date(case),
                max(item.argument_date for item in case.arguments),
                case.term,
                case.primary_docket.casefold(),
                case.slug,
            ),
            reverse=True,
        )
    )


def archive_slug(value: str) -> str:
    """Create a deterministic path segment for a public filter value."""
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not result:
        raise ValueError("archive value must contain an alphanumeric character")
    return result


def _normalize_path(value: str) -> str:
    if not value.startswith("/") or "\\" in value:
        raise ValueError("URL paths must be root-relative")
    parts = tuple(part for part in value.split("/") if part)
    if any(part in {".", ".."} for part in parts):
        raise ValueError("URL paths cannot contain dot segments")
    return "/" + "/".join(parts) + ("/" if parts else "")


@dataclass(frozen=True)
class StaticUrlPolicy:
    """Generate every internal, canonical, asset, data, and official URL."""

    canonical_origin: str
    project_base_path: str = "/"
    section_path: str = "/scotus/"

    def __post_init__(self) -> None:
        origin = urlsplit(self.canonical_origin)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.port is not None
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
        ):
            raise ValueError("canonical origin must be a bare HTTPS origin")
        object.__setattr__(self, "canonical_origin", self.canonical_origin.rstrip("/"))
        object.__setattr__(self, "project_base_path", _normalize_path(self.project_base_path))
        object.__setattr__(self, "section_path", _normalize_path(self.section_path))

    @property
    def custom_domain(self) -> str | None:
        """Return the Pages custom domain used by a root-hosted site, if configured."""
        hostname = urlsplit(self.canonical_origin).hostname
        if (
            self.project_base_path != "/"
            or hostname is None
            or hostname == "github.io"
            or hostname.endswith(".github.io")
        ):
            return None
        return hostname

    @property
    def section_root(self) -> str:
        return self.internal(self.section_path.strip("/"))

    def internal(self, relative: str = "") -> str:
        """Return a trailing-slash URL confined to the configured project path."""
        if relative.startswith("/") or "\\" in relative:
            raise ValueError("internal URL values must be relative")
        path = PurePosixPath(relative)
        if any(part in {".", ".."} for part in path.parts):
            raise ValueError("internal URL values cannot contain dot segments")
        encoded = "/".join(quote(part, safe="%:@-._~") for part in path.parts)
        base = self.project_base_path
        return base + encoded + ("/" if encoded else "")

    def section(self, relative: str = "") -> str:
        prefix = self.section_path.strip("/")
        return self.internal("/".join(part for part in (prefix, relative.strip("/")) if part))

    def case(self, case: PublicCaseBrief, *, slug: str | None = None) -> str:
        docket = archive_slug(case.primary_docket)
        return self.section(f"cases/{case.term}/{docket}/{quote(slug or case.slug, safe='-')}")

    def asset(self, filename: str) -> str:
        return self.internal(f"assets/{filename}").rstrip("/")

    def data(self, relative: str) -> str:
        return self.internal(f"data/v1/{relative}").rstrip("/")

    def page(self, route: str, page_number: int) -> str:
        if page_number < 1:
            raise ValueError("page number must be positive")
        return (
            self.section(route) if page_number == 1 else self.section(f"{route}/page/{page_number}")
        )

    def canonical(self, internal_url: str) -> str:
        if not internal_url.startswith(self.project_base_path):
            raise ValueError("canonical URL must be confined to the project base path")
        return urljoin(f"{self.canonical_origin}/", internal_url.lstrip("/"))

    def official(self, url: str) -> str:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.supremecourt.gov"
            or parsed.username
            or parsed.password
            or parsed.port is not None
        ):
            raise ValueError("external source must use the official Supreme Court host")
        return url

    def output_relative(self, internal_url: str) -> PurePosixPath:
        """Map one internal directory URL to its Pages artifact index path."""
        if not internal_url.startswith(self.project_base_path):
            raise ValueError("URL escapes configured project base path")
        relative = internal_url[len(self.project_base_path) :].strip("/")
        return PurePosixPath(relative, "index.html") if relative else PurePosixPath("index.html")
