"""Fail-closed registry and authorization for official proceeding sources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlparse

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.proceedings.contracts import (
    GovernmentAuthority,
    Jurisdiction,
    OfficialSource,
    SourceAccessMethod,
    SourceHealth,
)


class SourceAuthorizationError(PermissionError):
    """Raised when source collection is not affirmatively authorized."""


@dataclass(frozen=True)
class SourceApproval:
    source_id: str
    enabled: bool
    access_basis: str | None
    discovery_method: SourceAccessMethod
    media_method: SourceAccessMethod
    allowed_hosts: tuple[str, ...]
    reviewed_at: datetime | None
    reviewed_by: str | None
    review_expires_at: datetime | None
    reason: str


class SourceRegistry(Protocol):
    def get(self, source_id: str) -> OfficialSource | None: ...

    def register(self, source: OfficialSource, reason: str) -> None: ...

    def mark_health(
        self, source_id: str, health: SourceHealth, error: str | None = None
    ) -> None: ...


class InMemorySourceRegistry:
    def __init__(self) -> None:
        self.sources: dict[str, OfficialSource] = {}
        self.approvals: list[SourceApproval] = []
        self.errors: dict[str, str | None] = {}

    @staticmethod
    def _approval(source: OfficialSource, reason: str) -> SourceApproval:
        return SourceApproval(
            source_id=source.source_id,
            enabled=source.enabled,
            access_basis=source.access_basis,
            discovery_method=source.discovery_method,
            media_method=source.media_method,
            allowed_hosts=source.allowed_hosts,
            reviewed_at=source.access_reviewed_at,
            reviewed_by=source.access_reviewed_by,
            review_expires_at=source.access_review_expires_at,
            reason=reason,
        )

    def get(self, source_id: str) -> OfficialSource | None:
        return self.sources.get(source_id)

    def register(self, source: OfficialSource, reason: str) -> None:
        if not reason.strip():
            raise ValueError("source registry change requires a reason")
        prior = self.sources.get(source.source_id)
        self.sources[source.source_id] = source
        approval = self._approval(source, reason)
        if prior is None or self._approval(prior, reason) != approval:
            self.approvals.append(approval)

    def mark_health(
        self, source_id: str, health: SourceHealth, error: str | None = None
    ) -> None:
        source = self.sources.get(source_id)
        if source is None:
            raise KeyError(source_id)
        self.sources[source_id] = source.model_copy(update={"health": health})
        self.errors[source_id] = error


class PostgresSourceRegistry:
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
    def _source(row: dict[str, Any]) -> OfficialSource:
        return OfficialSource(
            schema_version=row["schema_version"],
            source_id=row["source_id"],
            authority=GovernmentAuthority(row["authority"]),
            jurisdiction=Jurisdiction(row["jurisdiction"]),
            display_name=row["display_name"],
            official_index_url=row["official_index_url"],
            adapter=row["adapter"],
            discovery_method=SourceAccessMethod(row["discovery_method"]),
            media_method=SourceAccessMethod(row["media_method"]),
            access_basis=row["access_basis"],
            access_reviewed_at=row["access_reviewed_at"],
            access_reviewed_by=row["access_reviewed_by"],
            access_review_expires_at=row["access_review_expires_at"],
            allowed_hosts=tuple(row["allowed_hosts"]),
            poll_interval_seconds=row["poll_interval_seconds"],
            expected_schedule=row["expected_schedule"],
            enabled=row["enabled"],
            health=SourceHealth(row["health"]),
        )

    @staticmethod
    def _approval_values(source: OfficialSource) -> tuple[object, ...]:
        return (
            source.enabled,
            source.access_basis,
            source.discovery_method.value,
            source.media_method.value,
            list(source.allowed_hosts),
            source.access_reviewed_at,
            source.access_reviewed_by,
            source.access_review_expires_at,
        )

    def get(self, source_id: str) -> OfficialSource | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                """SELECT schema_version,source_id,authority,jurisdiction,display_name,
                          official_index_url,adapter,discovery_method,media_method,access_basis,
                          access_reviewed_at,access_reviewed_by,access_review_expires_at,
                          allowed_hosts,poll_interval_seconds,expected_schedule,enabled,
                          health::text
                   FROM official_sources WHERE source_id=%s""",
                (source_id,),
            ).fetchone()
        return self._source(row) if row else None

    def register(self, source: OfficialSource, reason: str) -> None:
        if not reason.strip():
            raise ValueError("source registry change requires a reason")
        with self.pool.connection() as connection, connection.transaction():
            prior_row = connection.execute(
                """SELECT schema_version,source_id,authority,jurisdiction,display_name,
                          official_index_url,adapter,discovery_method,media_method,access_basis,
                          access_reviewed_at,access_reviewed_by,access_review_expires_at,
                          allowed_hosts,poll_interval_seconds,expected_schedule,enabled,
                          health::text
                   FROM official_sources WHERE source_id=%s FOR UPDATE""",
                (source.source_id,),
            ).fetchone()
            connection.execute(
                """INSERT INTO official_sources
                   (source_id,schema_version,authority,jurisdiction,display_name,
                    official_index_url,adapter,discovery_method,media_method,access_basis,
                    access_reviewed_at,access_reviewed_by,access_review_expires_at,
                    allowed_hosts,poll_interval_seconds,expected_schedule,enabled,health)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                   ON CONFLICT(source_id) DO UPDATE SET
                     schema_version=excluded.schema_version,authority=excluded.authority,
                     jurisdiction=excluded.jurisdiction,display_name=excluded.display_name,
                     official_index_url=excluded.official_index_url,adapter=excluded.adapter,
                     discovery_method=excluded.discovery_method,media_method=excluded.media_method,
                     access_basis=excluded.access_basis,
                     access_reviewed_at=excluded.access_reviewed_at,
                     access_reviewed_by=excluded.access_reviewed_by,
                     access_review_expires_at=excluded.access_review_expires_at,
                     allowed_hosts=excluded.allowed_hosts,
                     poll_interval_seconds=excluded.poll_interval_seconds,
                     expected_schedule=excluded.expected_schedule,enabled=excluded.enabled,
                     health=excluded.health,updated_at=now()""",
                (
                    source.source_id,
                    source.schema_version,
                    source.authority.value,
                    source.jurisdiction.value,
                    source.display_name,
                    source.official_index_url,
                    source.adapter,
                    source.discovery_method.value,
                    source.media_method.value,
                    source.access_basis,
                    source.access_reviewed_at,
                    source.access_reviewed_by,
                    source.access_review_expires_at,
                    json.dumps(list(source.allowed_hosts)),
                    source.poll_interval_seconds,
                    source.expected_schedule,
                    source.enabled,
                    source.health.value,
                ),
            )
            prior = self._source(prior_row) if prior_row else None
            approval_changed = (
                prior is None or self._approval_values(prior) != self._approval_values(source)
            )
            reviewed_at = source.access_reviewed_at
            reviewed_by = source.access_reviewed_by
            if approval_changed and reviewed_at is not None and reviewed_by is not None:
                connection.execute(
                    """INSERT INTO official_source_approval_history
                       (source_id,enabled,access_basis,discovery_method,media_method,allowed_hosts,
                        reviewed_at,reviewed_by,review_expires_at,reason)
                       VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)""",
                    (
                        source.source_id,
                        source.enabled,
                        source.access_basis,
                        source.discovery_method.value,
                        source.media_method.value,
                        json.dumps(list(source.allowed_hosts)),
                        reviewed_at,
                        reviewed_by,
                        source.access_review_expires_at,
                        reason.strip(),
                    ),
                )

    def mark_health(
        self, source_id: str, health: SourceHealth, error: str | None = None
    ) -> None:
        with self.pool.connection() as connection:
            result = connection.execute(
                """UPDATE official_sources SET health=%s,last_error=%s,updated_at=now()
                   WHERE source_id=%s""",
                (health.value, error[:4_000] if error else None, source_id),
            )
            connection.commit()
        if result.rowcount != 1:
            raise KeyError(source_id)


class SourceAuthorizer:
    def __init__(self, registry: SourceRegistry):
        self.registry = registry

    def require_source(self, source_id: str, now: datetime) -> OfficialSource:
        source = self.registry.get(source_id)
        if source is None:
            raise SourceAuthorizationError("source is not registered")
        if not source.enabled:
            raise SourceAuthorizationError("source is disabled")
        if (
            not source.access_basis
            or not source.access_reviewed_at
            or not source.access_reviewed_by
            or not source.allowed_hosts
        ):
            raise SourceAuthorizationError("source access review is incomplete")
        if source.access_review_expires_at and source.access_review_expires_at <= now:
            self.registry.mark_health(
                source_id, SourceHealth.REVIEW_REQUIRED, "access review expired"
            )
            raise SourceAuthorizationError("source access review has expired")
        return source

    def authorize_url(
        self,
        source_id: str,
        url: str,
        method: SourceAccessMethod,
        now: datetime,
        *,
        media: bool,
    ) -> OfficialSource:
        source = self.require_source(source_id, now)
        approved_method = source.media_method if media else source.discovery_method
        if approved_method is SourceAccessMethod.NONE or method is not approved_method:
            self.registry.mark_health(
                source_id,
                SourceHealth.REVIEW_REQUIRED,
                f"access method changed from {approved_method.value} to {method.value}",
            )
            raise SourceAuthorizationError("access method is not approved")
        parsed = urlparse(url)
        host = parsed.hostname.lower().rstrip(".") if parsed.hostname else None
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username
            or parsed.password
            or host not in source.allowed_hosts
        ):
            self.registry.mark_health(
                source_id,
                SourceHealth.REVIEW_REQUIRED,
                f"URL host or scheme is not approved: {host or 'missing'}",
            )
            raise SourceAuthorizationError("URL is outside the approved host allowlist")
        return source
