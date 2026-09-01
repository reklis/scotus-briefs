"""Source-adapter contract and idempotent official proceeding discovery."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Protocol, Self
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import Field, model_validator

from ragchew.contracts import StrictModel
from ragchew.proceedings.contracts import (
    DocumentType,
    GovernmentAuthority,
    Jurisdiction,
    MediaKind,
    OfficialSource,
    Proceeding,
    ProceedingLifecycle,
    ProceedingType,
    SourceAccessMethod,
    SourceHealth,
    UtcDatetime,
)
from ragchew.proceedings.registry import (
    SourceAuthorizationError,
    SourceAuthorizer,
    SourceRegistry,
)


class ConditionalRequest(StrictModel):
    etag: str | None = None
    last_modified: str | None = None


class MediaDescriptor(StrictModel):
    external_id: str = Field(min_length=1, max_length=500)
    kind: MediaKind
    source_url: str
    access_method: SourceAccessMethod
    content_type: str = Field(pattern=r"^(audio|video)/")
    source_updated_at: UtcDatetime | None = None


class DocumentDescriptor(StrictModel):
    external_id: str = Field(min_length=1, max_length=500)
    document_type: DocumentType
    official_url: str
    access_method: SourceAccessMethod
    content_type: str = Field(min_length=3, max_length=200)
    source_updated_at: UtcDatetime | None = None


class DiscoveredProceeding(StrictModel):
    external_id: str = Field(min_length=1, max_length=256)
    proceeding_type: ProceedingType
    title: str = Field(min_length=1, max_length=500)
    official_url: str
    lifecycle: ProceedingLifecycle
    scheduled_start_at: UtcDatetime
    scheduled_end_at: UtcDatetime | None = None
    actual_start_at: UtcDatetime | None = None
    actual_end_at: UtcDatetime | None = None
    source_updated_at: UtcDatetime | None = None
    media: tuple[MediaDescriptor, ...] = ()
    documents: tuple[DocumentDescriptor, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if self.scheduled_end_at and self.scheduled_end_at <= self.scheduled_start_at:
            raise ValueError("scheduled_end_at must follow scheduled_start_at")
        if self.actual_end_at and not self.actual_start_at:
            raise ValueError("actual_end_at requires actual_start_at")
        return self


class SourcePollResult(StrictModel):
    source_id: str
    endpoint_url: str
    access_method: SourceAccessMethod
    retrieved_at: UtcDatetime
    proceedings: tuple[DiscoveredProceeding, ...] = ()
    not_modified: bool = False
    quiet: bool = False
    etag: str | None = None
    last_modified: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.not_modified and self.proceedings:
            raise ValueError("not-modified response cannot contain proceedings")
        if self.quiet and self.proceedings:
            raise ValueError("quiet response cannot contain proceedings")
        return self


class OfficialSourceAdapter(Protocol):
    source_id: str

    def poll(self, conditional: ConditionalRequest) -> SourcePollResult: ...


class PollState(StrictModel):
    last_polled_at: UtcDatetime | None = None
    last_success_at: UtcDatetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    etag: str | None = None
    last_modified: str | None = None


class PollOutcome(StrictModel):
    source_id: str
    attempted: bool
    discovered: int = Field(default=0, ge=0)
    revisions: int = Field(default=0, ge=0)
    collection_jobs: int = Field(default=0, ge=0)
    health: SourceHealth
    retry_after_seconds: int = Field(ge=0)
    reason: str | None = None


class DiscoveryStore(Protocol):
    def get_proceeding(self, source_id: str, external_id: str) -> Proceeding | None: ...

    def save_proceeding_revision(
        self, proceeding: Proceeding, payload: dict[str, Any], payload_sha256: str
    ) -> bool: ...

    def enqueue_collection(
        self,
        proceeding_id: UUID,
        input_kind: str,
        external_id: str,
        input_version: str,
    ) -> bool: ...

    def get_poll_state(self, source_id: str) -> PollState: ...

    def record_poll(
        self,
        source_id: str,
        polled_at: datetime,
        *,
        success: bool,
        etag: str | None = None,
        last_modified: str | None = None,
        error: str | None = None,
    ) -> None: ...


class InMemoryDiscoveryStore:
    def __init__(self) -> None:
        self.proceedings: dict[tuple[str, str], Proceeding] = {}
        self.revisions: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
        self.jobs: set[tuple[UUID, str, str, str]] = set()
        self.poll_states: dict[str, PollState] = {}

    def get_proceeding(self, source_id: str, external_id: str) -> Proceeding | None:
        return self.proceedings.get((source_id, external_id))

    def save_proceeding_revision(
        self, proceeding: Proceeding, payload: dict[str, Any], payload_sha256: str
    ) -> bool:
        key = (proceeding.source_id, proceeding.external_id)
        self.proceedings[key] = proceeding
        revisions = self.revisions.setdefault(key, [])
        if any(digest == payload_sha256 for digest, _ in revisions):
            return False
        revisions.append((payload_sha256, payload))
        return True

    def enqueue_collection(
        self,
        proceeding_id: UUID,
        input_kind: str,
        external_id: str,
        input_version: str,
    ) -> bool:
        job = (proceeding_id, input_kind, external_id, input_version)
        before = len(self.jobs)
        self.jobs.add(job)
        return len(self.jobs) != before

    def get_poll_state(self, source_id: str) -> PollState:
        return self.poll_states.get(source_id, PollState())

    def record_poll(
        self,
        source_id: str,
        polled_at: datetime,
        *,
        success: bool,
        etag: str | None = None,
        last_modified: str | None = None,
        error: str | None = None,
    ) -> None:
        prior = self.get_poll_state(source_id)
        self.poll_states[source_id] = PollState(
            last_polled_at=polled_at,
            last_success_at=polled_at if success else prior.last_success_at,
            consecutive_failures=0 if success else prior.consecutive_failures + 1,
            etag=etag if etag is not None else prior.etag,
            last_modified=last_modified if last_modified is not None else prior.last_modified,
        )


class PostgresDiscoveryStore:
    def __init__(
        self,
        dsn: str,
        pool: ConnectionPool[Connection[dict[str, Any]]] | None = None,
    ) -> None:
        self.pool = pool or ConnectionPool(
            conninfo=dsn,
            kwargs={"row_factory": dict_row},
            min_size=1,
            max_size=5,
            open=True,
        )

    @staticmethod
    def _proceeding(row: dict[str, Any]) -> Proceeding:
        return Proceeding(
            schema_version=row["schema_version"],
            proceeding_id=row["proceeding_id"],
            source_id=row["source_id"],
            authority=GovernmentAuthority(row["authority"]),
            jurisdiction=Jurisdiction(row["jurisdiction"]),
            external_id=row["external_id"],
            proceeding_type=ProceedingType(row["proceeding_type"]),
            title=row["title_private"],
            official_url=row["official_url"],
            lifecycle=ProceedingLifecycle(row["lifecycle"]),
            scheduled_start_at=row["scheduled_start_at"],
            scheduled_end_at=row["scheduled_end_at"],
            actual_start_at=row["actual_start_at"],
            actual_end_at=row["actual_end_at"],
            discovered_at=row["discovered_at"],
            updated_at=row["updated_at"],
        )

    def get_proceeding(self, source_id: str, external_id: str) -> Proceeding | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                """SELECT proceeding_id,source_id,schema_version,authority,jurisdiction,
                          external_id,proceeding_type,title_private,official_url,lifecycle::text,
                          scheduled_start_at,scheduled_end_at,actual_start_at,actual_end_at,
                          discovered_at,updated_at
                   FROM proceedings WHERE source_id=%s AND external_id=%s""",
                (source_id, external_id),
            ).fetchone()
        return self._proceeding(row) if row else None

    def save_proceeding_revision(
        self, proceeding: Proceeding, payload: dict[str, Any], payload_sha256: str
    ) -> bool:
        with self.pool.connection() as connection, connection.transaction():
            connection.execute(
                """INSERT INTO proceedings
                   (proceeding_id,source_id,schema_version,authority,jurisdiction,external_id,
                    proceeding_type,title_private,official_url,lifecycle,scheduled_start_at,
                    scheduled_end_at,actual_start_at,actual_end_at,discovered_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(source_id,external_id) DO UPDATE SET
                     title_private=excluded.title_private,official_url=excluded.official_url,
                     lifecycle=excluded.lifecycle,scheduled_start_at=excluded.scheduled_start_at,
                     scheduled_end_at=excluded.scheduled_end_at,
                     actual_start_at=excluded.actual_start_at,actual_end_at=excluded.actual_end_at,
                     updated_at=excluded.updated_at""",
                (
                    proceeding.proceeding_id,
                    proceeding.source_id,
                    proceeding.schema_version,
                    proceeding.authority.value,
                    proceeding.jurisdiction.value,
                    proceeding.external_id,
                    proceeding.proceeding_type.value,
                    proceeding.title,
                    proceeding.official_url,
                    proceeding.lifecycle.value,
                    proceeding.scheduled_start_at,
                    proceeding.scheduled_end_at,
                    proceeding.actual_start_at,
                    proceeding.actual_end_at,
                    proceeding.discovered_at,
                    proceeding.updated_at,
                ),
            )
            row = connection.execute(
                """SELECT proceeding_id FROM proceedings
                   WHERE source_id=%s AND external_id=%s FOR UPDATE""",
                (proceeding.source_id, proceeding.external_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("proceeding upsert did not produce a row")
            result = connection.execute(
                """INSERT INTO proceeding_revisions
                   (revision_id,proceeding_id,revision_number,source_updated_at,observed_at,
                    payload_private,payload_sha256)
                   SELECT %s,%s,COALESCE(max(revision_number),0)+1,%s,%s,%s::jsonb,%s
                   FROM proceeding_revisions WHERE proceeding_id=%s
                   ON CONFLICT(proceeding_id,payload_sha256) DO NOTHING""",
                (
                    uuid4(),
                    row["proceeding_id"],
                    payload.get("source_updated_at"),
                    proceeding.updated_at,
                    json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
                    payload_sha256,
                    row["proceeding_id"],
                ),
            )
            return result.rowcount == 1

    def enqueue_collection(
        self,
        proceeding_id: UUID,
        input_kind: str,
        external_id: str,
        input_version: str,
    ) -> bool:
        input_id = f"{proceeding_id}:{external_id}"
        with self.pool.connection() as connection:
            result = connection.execute(
                """INSERT INTO jobs(stage,input_kind,input_id,input_version)
                   VALUES ('collect',%s,%s,%s)
                   ON CONFLICT(stage,input_kind,input_id,input_version) DO NOTHING""",
                (input_kind, input_id, input_version),
            )
            connection.commit()
            return result.rowcount == 1

    def get_poll_state(self, source_id: str) -> PollState:
        with self.pool.connection() as connection:
            row = connection.execute(
                """SELECT s.last_polled_at,s.last_success_at,s.consecutive_failures,
                          c.etag,c.last_modified
                   FROM official_sources s
                   LEFT JOIN source_checkpoints c ON c.source_id=s.source_id
                     AND c.checkpoint_kind='discovery' AND c.checkpoint_key='index'
                   WHERE s.source_id=%s""",
                (source_id,),
            ).fetchone()
        if row is None:
            raise KeyError(source_id)
        return PollState(**row)

    def record_poll(
        self,
        source_id: str,
        polled_at: datetime,
        *,
        success: bool,
        etag: str | None = None,
        last_modified: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.pool.connection() as connection, connection.transaction():
            result = connection.execute(
                """UPDATE official_sources SET last_polled_at=%s,
                     last_success_at=CASE WHEN %s THEN %s ELSE last_success_at END,
                     consecutive_failures=CASE WHEN %s THEN 0 ELSE consecutive_failures+1 END,
                     last_error=%s,updated_at=now()
                   WHERE source_id=%s""",
                (
                    polled_at,
                    success,
                    polled_at,
                    success,
                    error[:4_000] if error else None,
                    source_id,
                ),
            )
            if result.rowcount != 1:
                raise KeyError(source_id)
            if success and (etag is not None or last_modified is not None):
                connection.execute(
                    """INSERT INTO source_checkpoints
                       (source_id,checkpoint_kind,checkpoint_key,checkpoint_value,
                        etag,last_modified)
                       VALUES (%s,'discovery','index','{}'::jsonb,%s,%s)
                       ON CONFLICT(source_id,checkpoint_kind,checkpoint_key) DO UPDATE SET
                         etag=COALESCE(excluded.etag,source_checkpoints.etag),
                         last_modified=COALESCE(
                           excluded.last_modified,source_checkpoints.last_modified
                         ),
                         updated_at=now()""",
                    (source_id, etag, last_modified),
                )


_ALLOWED_TRANSITIONS: dict[ProceedingLifecycle, frozenset[ProceedingLifecycle]] = {
    ProceedingLifecycle.SCHEDULED: frozenset(
        {
            ProceedingLifecycle.SCHEDULED,
            ProceedingLifecycle.LIVE,
            ProceedingLifecycle.DELAYED,
            ProceedingLifecycle.COMPLETED,
            ProceedingLifecycle.POSTPONED,
            ProceedingLifecycle.CANCELLED,
            ProceedingLifecycle.ARCHIVE_PENDING,
            ProceedingLifecycle.UNAVAILABLE,
        }
    ),
    ProceedingLifecycle.DELAYED: frozenset(
        {
            ProceedingLifecycle.DELAYED,
            ProceedingLifecycle.LIVE,
            ProceedingLifecycle.POSTPONED,
            ProceedingLifecycle.CANCELLED,
            ProceedingLifecycle.UNAVAILABLE,
        }
    ),
    ProceedingLifecycle.LIVE: frozenset(
        {
            ProceedingLifecycle.LIVE,
            ProceedingLifecycle.COMPLETED,
            ProceedingLifecycle.ARCHIVE_PENDING,
            ProceedingLifecycle.UNAVAILABLE,
        }
    ),
    ProceedingLifecycle.POSTPONED: frozenset(
        {
            ProceedingLifecycle.POSTPONED,
            ProceedingLifecycle.SCHEDULED,
            ProceedingLifecycle.CANCELLED,
        }
    ),
    ProceedingLifecycle.CANCELLED: frozenset(
        {ProceedingLifecycle.CANCELLED, ProceedingLifecycle.SCHEDULED}
    ),
    ProceedingLifecycle.ARCHIVE_PENDING: frozenset(
        {
            ProceedingLifecycle.ARCHIVE_PENDING,
            ProceedingLifecycle.COMPLETED,
            ProceedingLifecycle.UNAVAILABLE,
        }
    ),
    ProceedingLifecycle.UNAVAILABLE: frozenset(
        {
            ProceedingLifecycle.UNAVAILABLE,
            ProceedingLifecycle.SCHEDULED,
            ProceedingLifecycle.LIVE,
            ProceedingLifecycle.ARCHIVE_PENDING,
        }
    ),
    ProceedingLifecycle.COMPLETED: frozenset(
        {ProceedingLifecycle.COMPLETED, ProceedingLifecycle.ARCHIVE_PENDING}
    ),
}


def validate_lifecycle_transition(
    prior: ProceedingLifecycle, new: ProceedingLifecycle
) -> None:
    if new not in _ALLOWED_TRANSITIONS[prior]:
        raise ValueError(f"invalid proceeding lifecycle transition: {prior.value} -> {new.value}")


def deterministic_proceeding_id(authority: GovernmentAuthority, external_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"ragchew:proceeding:{authority.value}:{external_id}")


def _canonical_payload(item: DiscoveredProceeding) -> tuple[dict[str, Any], str]:
    payload = item.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


def _descriptor_version(descriptor: MediaDescriptor | DocumentDescriptor) -> str:
    payload = descriptor.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class DiscoveryCoordinator:
    def __init__(
        self,
        registry: SourceRegistry,
        store: DiscoveryStore,
        adapters: dict[str, OfficialSourceAdapter],
        *,
        maximum_backoff_seconds: int = 21_600,
    ) -> None:
        self.registry = registry
        self.store = store
        self.adapters = adapters
        self.authorizer = SourceAuthorizer(registry)
        self.maximum_backoff_seconds = maximum_backoff_seconds

    def poll(self, source_id: str, now: datetime) -> PollOutcome:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("poll timestamp must be timezone-aware UTC")
        source = self.authorizer.require_source(source_id, now)
        adapter = self.adapters.get(source.adapter)
        if adapter is None or adapter.source_id != source_id:
            raise ValueError(f"no matching adapter configured for {source_id}")
        state = self.store.get_poll_state(source_id)
        retry_after = self._retry_after(source.poll_interval_seconds, state.consecutive_failures)
        if state.last_polled_at:
            elapsed = int((now - state.last_polled_at).total_seconds())
            if elapsed < retry_after:
                return PollOutcome(
                    source_id=source_id,
                    attempted=False,
                    health=source.health,
                    retry_after_seconds=retry_after - max(0, elapsed),
                    reason="poll interval has not elapsed",
                )
        try:
            self.authorizer.authorize_url(
                source_id,
                source.official_index_url,
                source.discovery_method,
                now,
                media=False,
            )
            result = adapter.poll(
                ConditionalRequest(etag=state.etag, last_modified=state.last_modified)
            )
            if result.source_id != source_id:
                raise ValueError("adapter returned a different source_id")
            self.authorizer.authorize_url(
                source_id,
                result.endpoint_url,
                result.access_method,
                now,
                media=False,
            )
            discovered, revisions, jobs = self._apply(source, result, now)
        except SourceAuthorizationError:
            self.store.record_poll(source_id, now, success=False, error="authorization denied")
            raise
        except Exception as error:
            self.store.record_poll(source_id, now, success=False, error=str(error))
            self.registry.mark_health(source_id, SourceHealth.DEGRADED, str(error))
            failures = state.consecutive_failures + 1
            return PollOutcome(
                source_id=source_id,
                attempted=True,
                health=SourceHealth.DEGRADED,
                retry_after_seconds=self._retry_after(source.poll_interval_seconds, failures),
                reason=str(error),
            )
        health = SourceHealth.QUIET if result.quiet else SourceHealth.HEALTHY
        self.store.record_poll(
            source_id,
            now,
            success=True,
            etag=result.etag,
            last_modified=result.last_modified,
        )
        self.registry.mark_health(source_id, health)
        return PollOutcome(
            source_id=source_id,
            attempted=True,
            discovered=discovered,
            revisions=revisions,
            collection_jobs=jobs,
            health=health,
            retry_after_seconds=source.poll_interval_seconds,
            reason="not modified" if result.not_modified else None,
        )

    def _apply(
        self,
        source: OfficialSource,
        result: SourcePollResult,
        now: datetime,
    ) -> tuple[int, int, int]:
        discovered = 0
        revisions = 0
        jobs = 0
        for item in result.proceedings:
            self.authorizer.authorize_url(
                source.source_id,
                item.official_url,
                source.discovery_method,
                now,
                media=False,
            )
            prior = self.store.get_proceeding(source.source_id, item.external_id)
            if prior:
                validate_lifecycle_transition(prior.lifecycle, item.lifecycle)
            proceeding = Proceeding(
                proceeding_id=deterministic_proceeding_id(source.authority, item.external_id),
                source_id=source.source_id,
                authority=source.authority,
                jurisdiction=source.jurisdiction,
                external_id=item.external_id,
                proceeding_type=item.proceeding_type,
                title=item.title,
                official_url=item.official_url,
                lifecycle=item.lifecycle,
                scheduled_start_at=item.scheduled_start_at,
                scheduled_end_at=item.scheduled_end_at,
                actual_start_at=item.actual_start_at,
                actual_end_at=item.actual_end_at,
                discovered_at=prior.discovered_at if prior else result.retrieved_at,
                updated_at=result.retrieved_at,
            )
            payload, digest = _canonical_payload(item)
            if self.store.save_proceeding_revision(proceeding, payload, digest):
                revisions += 1
            if prior is None:
                discovered += 1
            if item.lifecycle is ProceedingLifecycle.CANCELLED:
                continue
            for media in item.media:
                self.authorizer.authorize_url(
                    source.source_id,
                    media.source_url,
                    media.access_method,
                    now,
                    media=True,
                )
                if self.store.enqueue_collection(
                    proceeding.proceeding_id,
                    "proceeding_media",
                    media.external_id,
                    _descriptor_version(media),
                ):
                    jobs += 1
            for document in item.documents:
                self.authorizer.authorize_url(
                    source.source_id,
                    document.official_url,
                    document.access_method,
                    now,
                    media=False,
                )
                if self.store.enqueue_collection(
                    proceeding.proceeding_id,
                    "official_document",
                    document.external_id,
                    _descriptor_version(document),
                ):
                    jobs += 1
        return discovered, revisions, jobs

    def is_stale(self, source_id: str, now: datetime, stale_after_seconds: int) -> bool:
        state = self.store.get_poll_state(source_id)
        if state.last_success_at is None:
            return state.last_polled_at is not None
        return (now - state.last_success_at).total_seconds() > stale_after_seconds

    def _retry_after(self, poll_interval_seconds: int, failures: int) -> int:
        multiplier = 1 << max(0, failures)
        return min(self.maximum_backoff_seconds, poll_interval_seconds * multiplier)
