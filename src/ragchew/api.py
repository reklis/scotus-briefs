"""Private receiver ingestion API and application factory."""

from __future__ import annotations

from datetime import UTC, datetime

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from ragchew.auth import AuthenticationError, ReceiverAuthenticator
from ragchew.config import MvpConfig, ServiceSettings
from ragchew.contracts import CaptureEnvelope, EdgeHeartbeat
from ragchew.ingestion import (
    IngestionConflict,
    IngestionNotFound,
    IngestionService,
    IntegrityFailure,
)
from ragchew.logging_config import configure_logging
from ragchew.metrics import (
    CONTROL_ACTIVITY,
    EDGE_CLOCK_OFFSET,
    EDGE_DROPPED_SAMPLES,
    EDGE_FREE_DISK,
    EDGE_SPOOL_DEPTH,
    HEARTBEAT_TIMESTAMP,
    INGESTION_LAG,
    INGESTION_TOTAL,
    OBJECT_FAILURES,
)
from ragchew.repository import PostgresRepository, Repository
from ragchew.storage import ObjectStore, S3ObjectStore


class UploadResponse(BaseModel):
    capture_id: str
    status: str
    object_key: str
    upload_url: str | None
    duplicate: bool


class CommitResponse(BaseModel):
    capture_id: str
    status: str
    acknowledged: bool = True


def create_app(
    *,
    settings: ServiceSettings | None = None,
    config: MvpConfig | None = None,
    repository: Repository | None = None,
    objects: ObjectStore | None = None,
) -> FastAPI:
    service_settings = settings or ServiceSettings()
    mvp_config = config or MvpConfig.from_yaml(service_settings.config_path)
    repo = repository or PostgresRepository(service_settings.database_dsn)
    object_store = objects or S3ObjectStore(service_settings)
    authenticator = ReceiverAuthenticator(service_settings.parsed_receiver_tokens())
    ingestion = IngestionService(repo, object_store, authenticator, mvp_config)

    app = FastAPI(title="Ragchew private ingestion", version="0.1.0", docs_url=None)

    def authorize(
        receiver_id: str = Path(min_length=1, max_length=64),
        authorization: str | None = Header(default=None),
    ) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "receiver authentication required")
        try:
            authenticator.authenticate(receiver_id, authorization.removeprefix("Bearer "))
        except AuthenticationError as error:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
        return receiver_id

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post(
        "/v1/receivers/{receiver_id}/captures",
        response_model=UploadResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def initiate_capture(
        envelope: CaptureEnvelope,
        receiver_id: str = Depends(authorize),
    ) -> UploadResponse:
        try:
            result = UploadResponse.model_validate(
                ingestion.initiate(receiver_id, envelope).__dict__
            )
            INGESTION_TOTAL.labels("initiated").inc()
            INGESTION_LAG.observe(
                max(0.0, (datetime.now(UTC) - envelope.started_at).total_seconds())
            )
            return result
        except IngestionConflict as error:
            INGESTION_TOTAL.labels("conflict").inc()
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error

    @app.post(
        "/v1/receivers/{receiver_id}/captures/{capture_id}/commit",
        response_model=CommitResponse,
    )
    def commit_capture(
        capture_id: str,
        receiver_id: str = Depends(authorize),
    ) -> CommitResponse:
        try:
            record = ingestion.commit(receiver_id, capture_id)
            INGESTION_TOTAL.labels("committed").inc()
            return CommitResponse(capture_id=record.capture_id, status=record.status)
        except IngestionNotFound as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except IngestionConflict as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        except IntegrityFailure as error:
            OBJECT_FAILURES.labels("commit_validation").inc()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    @app.post("/v1/receivers/{receiver_id}/heartbeats", status_code=status.HTTP_202_ACCEPTED)
    def heartbeat(
        payload: EdgeHeartbeat,
        receiver_id: str = Depends(authorize),
    ) -> dict[str, bool]:
        if payload.receiver_id != receiver_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "heartbeat receiver mismatch")
        repo.record_heartbeat(payload)
        HEARTBEAT_TIMESTAMP.labels(receiver_id).set(payload.observed_at.timestamp())
        CONTROL_ACTIVITY.labels(receiver_id).set(payload.control_messages_per_minute)
        EDGE_SPOOL_DEPTH.labels(receiver_id).set(payload.spool_depth)
        EDGE_FREE_DISK.labels(receiver_id).set(payload.free_disk_bytes)
        if payload.clock_offset_seconds is not None:
            EDGE_CLOCK_OFFSET.labels(receiver_id).set(payload.clock_offset_seconds)
        if payload.dropped_samples is not None:
            EDGE_DROPPED_SAMPLES.labels(receiver_id).set(payload.dropped_samples)
        return {"accepted": True}

    return app


def main() -> None:
    configure_logging()
    uvicorn.run(create_app(), host="0.0.0.0", port=8080)
