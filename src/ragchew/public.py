"""Read-only public application backed only by sanitized projections."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ragchew.config import ServiceSettings
from ragchew.public_contracts import PublicProjection
from ragchew.publishing_store import PostgresPublishingStore


class ProjectionReader(Protocol):
    def active_projection(self) -> PublicProjection | None: ...


def create_public_app(reader: ProjectionReader) -> FastAPI:
    app = FastAPI(title="DCFD Hourly Incident News", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory="static"), name="static")
    templates = Jinja2Templates(directory="templates")

    def projection() -> PublicProjection:
        value = reader.active_projection()
        if value is None:
            raise HTTPException(503, "public projection is not yet available")
        return value

    @app.get("/api/projection", response_model=PublicProjection)
    def api_projection() -> PublicProjection:
        return projection()

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        value = projection()
        active = [story for story in value.stories if story.status == "active"]
        resolved = [story for story in value.stories if story.status != "active"]
        return templates.TemplateResponse(
            request,
            "index.html",
            {"projection": value, "active": active, "resolved": resolved},
        )

    @app.get("/stories/{story_id}", response_class=HTMLResponse)
    def story(request: Request, story_id: UUID) -> HTMLResponse:
        value = projection()
        selected = next((item for item in value.stories if item.story_id == story_id), None)
        if selected is None:
            raise HTTPException(404, "story not found")
        return templates.TemplateResponse(
            request, "story.html", {"projection": value, "story": selected}
        )

    @app.get("/today", response_class=HTMLResponse)
    def today(request: Request) -> HTMLResponse:
        value = projection()
        day = datetime.now(UTC).date()
        stories = [item for item in value.stories if item.first_reported_at.date() == day]
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "projection": value,
                "active": [item for item in stories if item.status == "active"],
                "resolved": [item for item in stories if item.status != "active"],
            },
        )

    @app.get("/digests/{watermark}", response_class=HTMLResponse)
    def digest(request: Request, watermark: str) -> HTMLResponse:
        value = projection()
        if watermark != value.watermark.isoformat():
            raise HTTPException(404, "digest not found in active projection")
        return templates.TemplateResponse(
            request, "digest.html", {"projection": value, "digest": value.digest}
        )

    return app


def main() -> None:
    settings = ServiceSettings()
    if settings.product_mode == "scotus_legal_briefs":
        from ragchew.config import ScotusConfig
        from ragchew.scotus.public import create_scotus_public_app
        from ragchew.scotus.publishing import PostgresScotusProjectionStore

        config = ScotusConfig.from_yaml(settings.scotus_config_path)
        if not config.publication.enabled:
            disabled = FastAPI(title="SCOTUS Legal Briefs (disabled)", docs_url=None)

            @disabled.get("/{path:path}", response_class=PlainTextResponse)
            def launch_disabled(path: str) -> PlainTextResponse:
                return PlainTextResponse("SCOTUS Legal Briefs is not launched", status_code=503)

            uvicorn.run(disabled, host="0.0.0.0", port=8081)
            return
        scotus_store = PostgresScotusProjectionStore(settings.database_dsn)
        uvicorn.run(create_scotus_public_app(scotus_store), host="0.0.0.0", port=8081)
        return
    store = PostgresPublishingStore(settings.database_dsn)
    uvicorn.run(create_public_app(store), host="0.0.0.0", port=8081)
