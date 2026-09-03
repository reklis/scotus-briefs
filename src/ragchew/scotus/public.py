"""Read-only SCOTUS Legal Briefs public application."""

from __future__ import annotations

from datetime import date
from math import ceil
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ragchew.scotus.public_contracts import PublicCaseBrief, ScotusPublicProjection
from ragchew.scotus.publishing import ScotusProjectionReader
from ragchew.scotus.static_urls import (
    StaticUrlPolicy,
    archive_slug,
    latest_court_document_date,
    sort_cases,
)

_PAGE_SIZE = 20


def public_case_path(case: PublicCaseBrief) -> str:
    return f"/scotus/cases/{case.term}/{quote(case.primary_docket, safe='-')}/{case.slug}"


def create_scotus_public_app(reader: ScotusProjectionReader) -> FastAPI:
    app = FastAPI(title="SCOTUS Legal Briefs", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory="static"), name="static")
    app.mount("/assets", StaticFiles(directory="static"), name="assets")
    templates = Jinja2Templates(directory="templates")
    dynamic_urls = StaticUrlPolicy("https://example.invalid", "/", "/scotus/")
    templates.env.globals.update(
        public_case_path=public_case_path,
        latest_court_document_date=latest_court_document_date,
        archive_slug=archive_slug,
        public_case_key=lambda term, docket: f"{term}-{docket}",
    )

    def projection() -> ScotusPublicProjection:
        value = reader.active_projection()
        if value is None:
            raise HTTPException(503, "SCOTUS public projection is not yet available")
        return value

    def render_cases(
        request: Request,
        value: ScotusPublicProjection,
        cases: tuple[PublicCaseBrief, ...],
        heading: str,
        query: str = "",
        page: int = 1,
    ) -> HTMLResponse:
        ordered = sort_cases(cases)
        total_cases = len(ordered)
        page_count = ceil(total_cases / _PAGE_SIZE) if total_cases else 1
        if page > page_count and page > 1:
            raise HTTPException(404, "case-list page not found")
        start = (page - 1) * _PAGE_SIZE
        paged_cases = ordered[start : start + _PAGE_SIZE]

        def page_url(value: int) -> str:
            parameters = [
                (key, item) for key, item in request.query_params.multi_items() if key != "page"
            ]
            parameters.append(("page", str(value)))
            return f"{request.url.path}?{urlencode(parameters)}"

        return templates.TemplateResponse(
            request,
            "scotus_index.html",
            {
                "projection": value,
                "cases": paged_cases,
                "heading": heading,
                "query": query,
                "page": page,
                "page_count": page_count,
                "page_start": start + 1,
                "total_cases": total_cases,
                "previous_url": page_url(page - 1) if page > 1 else None,
                "next_url": page_url(page + 1) if page < page_count else None,
                "urls": dynamic_urls,
                "css_asset": "scotus.css",
                "search_asset": "scotus-search.js",
                "canonical_url": dynamic_urls.canonical(request.url.path + "/"),
                "current_term": max((case.term for case in value.cases), default=None),
                "introduction": "",
                "archive_links": (),
            },
        )

    @app.get("/api/scotus/projection", response_model=ScotusPublicProjection)
    def api_projection() -> ScotusPublicProjection:
        value = projection()
        return value.model_copy(update={"cases": sort_cases(value.cases)})

    @app.get("/", include_in_schema=False)
    def root_redirect() -> HTMLResponse:
        return HTMLResponse(
            '<!doctype html><meta http-equiv="refresh" content="0;url=/scotus">'
            '<a href="/scotus">SCOTUS Legal Briefs</a>'
        )

    @app.get("/scotus", response_class=HTMLResponse)
    def home(
        request: Request,
        status: str | None = None,
        topic: str | None = None,
        page: int = Query(default=1, ge=1),
    ) -> HTMLResponse:
        value = projection()
        cases = value.cases
        if status:
            cases = tuple(case for case in cases if case.case_status.value == status)
        if topic:
            lowered = topic.lower()
            cases = tuple(
                case for case in cases if any(lowered in value.lower() for value in case.topics)
            )
        return render_cases(request, value, cases, "Latest case briefs", page=page)

    @app.get("/scotus/terms/{term}", response_class=HTMLResponse)
    def term_archive(
        request: Request, term: str, page: int = Query(default=1, ge=1)
    ) -> HTMLResponse:
        if len(term) != 4 or not term.isdigit():
            raise HTTPException(404, "term not found")
        value = projection()
        cases = tuple(case for case in value.cases if case.term == term)
        return render_cases(request, value, cases, f"October Term {term}", page=page)

    @app.get("/scotus/arguments/{argument_date}", response_class=HTMLResponse)
    def argument_archive(
        request: Request,
        argument_date: date,
        page: int = Query(default=1, ge=1),
    ) -> HTMLResponse:
        value = projection()
        cases = tuple(
            case
            for case in value.cases
            if any(session.argument_date.date() == argument_date for session in case.arguments)
        )
        return render_cases(
            request,
            value,
            cases,
            f"Arguments on {argument_date.isoformat()}",
            page=page,
        )

    @app.get("/scotus/search", response_class=HTMLResponse)
    def search(
        request: Request,
        q: str = Query(default="", max_length=200),
        page: int = Query(default=1, ge=1),
    ) -> HTMLResponse:
        value = projection()
        query = " ".join(q.split()).lower()
        if not query:
            return render_cases(request, value, (), "Search", q, page)
        cases = tuple(
            case
            for case in value.cases
            if query
            in " ".join(
                (
                    case.caption,
                    case.primary_docket,
                    case.title,
                    case.dek,
                    *case.topics,
                )
            ).lower()
        )
        return render_cases(request, value, cases, "Search results", q, page)

    @app.get(
        "/scotus/cases/{term}/{primary_docket}/{slug}",
        response_class=HTMLResponse,
    )
    def case_page(request: Request, term: str, primary_docket: str, slug: str) -> HTMLResponse:
        value = projection()
        selected = next(
            (
                case
                for case in value.cases
                if case.term == term and case.primary_docket == primary_docket and case.slug == slug
            ),
            None,
        )
        if selected is None:
            raise HTTPException(404, "case brief not found")
        return templates.TemplateResponse(
            request,
            "scotus_case.html",
            {
                "projection": value,
                "case": selected,
                "urls": dynamic_urls,
                "css_asset": "scotus.css",
                "canonical_url": dynamic_urls.canonical(public_case_path(selected) + "/"),
                "current_term": max((case.term for case in value.cases), default=None),
            },
        )

    return app
