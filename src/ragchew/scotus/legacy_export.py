"""Operator-only sanitizer for one-time legacy PostgreSQL bootstrap exports."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.scotus.public_contracts import ScotusPublicProjection, public_case_key
from ragchew.scotus.static_contracts import (
    ReleaseManifest,
    assert_public_payload,
    canonical_json_bytes,
    sha256_hex,
)
from ragchew.scotus.static_state import GeneratedContent, StaticStateError, StaticStateStore

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SOURCE_LINK_KEYS = {"evidence_type", "label", "official_url", "page_label"}


class LegacyExportError(RuntimeError):
    """Legacy data cannot be represented as complete sanitized public state."""


def sanitize_legacy_projection(payload: Mapping[str, Any]) -> ScotusPublicProjection:
    """Remove only the old source-link claim IDs, then enforce current strict contracts.

    The legacy public schema attached ``claim_ids`` to provenance links. No other
    field is silently dropped: unknown/private fields fail the recursive scanner or
    Pydantic's extra-field validation.
    """

    def clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            keys = {str(key) for key in value}
            if "claim_ids" in keys and keys - {"claim_ids"} == _SOURCE_LINK_KEYS:
                value = {key: item for key, item in value.items() if key != "claim_ids"}
            return {str(key): clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    cleaned = clean(payload)
    assert_public_payload(cleaned)
    try:
        projection = ScotusPublicProjection.model_validate(cleaned)
    except ValueError as error:
        raise LegacyExportError("legacy projection failed the sanitized public contract") from error
    # Scan the normalized output too, so defaults and coercion cannot bypass the boundary.
    assert_public_payload(projection.model_dump(mode="python"))
    return projection


class PostgresLegacyProjectionReader:
    """Read-only access to retained public projections; never reads private tables."""

    def __init__(
        self,
        dsn: str,
        pool: ConnectionPool[Connection[dict[str, Any]]] | None = None,
    ) -> None:
        self.pool = pool or ConnectionPool(
            conninfo=dsn,
            kwargs={"row_factory": dict_row},
            min_size=1,
            max_size=1,
            open=True,
        )
        self._owns_pool = pool is None

    def projections(self) -> tuple[ScotusPublicProjection, ...]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """SELECT payload,status::text AS status,activated_at,created_at
                   FROM scotus_public_projections
                   WHERE status IN ('active','superseded')
                   ORDER BY COALESCE(activated_at,created_at),created_at"""
            ).fetchall()
        active = [row for row in rows if row["status"] == "active"]
        if len(active) != 1:
            raise LegacyExportError("legacy database must contain exactly one active projection")
        ordered = [row for row in rows if row["status"] != "active"] + active
        projections = []
        for row in ordered:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, Mapping):
                raise LegacyExportError("legacy projection payload must be a JSON object")
            projections.append(sanitize_legacy_projection(payload))
        return tuple(projections)

    def close(self) -> None:
        if self._owns_pool:
            self.pool.close()


def build_legacy_bootstrap(
    projections: Iterable[ScotusPublicProjection],
    *,
    source_commit: str,
    config_sha256: str,
    build_epoch: datetime,
    tool_version: str,
) -> GeneratedContent:
    """Convert retained public projection history into an auditable initial snapshot."""
    if not _GIT_COMMIT.fullmatch(source_commit):
        raise LegacyExportError("source commit must be a full lowercase Git SHA")
    if not _SHA256.fullmatch(config_sha256):
        raise LegacyExportError("config digest must be a lowercase SHA-256")
    if build_epoch.tzinfo is None or build_epoch.utcoffset() is None:
        raise LegacyExportError("build epoch must be timezone-aware")
    history = tuple(projections)
    if not history:
        raise LegacyExportError("legacy database has no active public projection")
    active = history[-1]

    versions: dict[tuple[str, int], Any] = {}
    for projection in history:
        for case in projection.cases:
            key = public_case_key(case.term, case.primary_docket)
            number = case.revisions[-1].revision_number
            identity = (key, number)
            prior = versions.get(identity)
            if prior is not None and prior != case:
                raise LegacyExportError("legacy history contains conflicting public case revisions")
            versions[identity] = case

    content = GeneratedContent.empty()
    store = StaticStateStore(".legacy-read-only-placeholder")
    for active_case in sorted(
        active.cases, key=lambda case: public_case_key(case.term, case.primary_docket)
    ):
        key = public_case_key(active_case.term, active_case.primary_docket)
        latest = active_case.revisions[-1].revision_number
        for number in range(1, latest + 1):
            version = versions.get((key, number))
            if version is None:
                raise LegacyExportError(
                    f"legacy projection history is missing public case revision {key}:{number}"
                )
            if len(version.revisions) != number:
                raise LegacyExportError("legacy public revision history is not contiguous")
            content = store.merge_accepted_case(
                content,
                version,
                watermark=active.watermark,
                generated_at=version.updated_at,
            )

    if not active.cases:
        content = replace(content, projection=active)
    # Case ordering in the dynamic projection was presentation-driven; canonical static
    # serialization orders by stable case key. Compare identity/content maps before using
    # the exact sanitized active projection.
    rebuilt_cases = {
        public_case_key(case.term, case.primary_docket): case
        for case in (content.projection.cases if content.projection is not None else ())
    }
    active_cases = {
        public_case_key(case.term, case.primary_docket): case for case in active.cases
    }
    if rebuilt_cases != active_cases:
        raise LegacyExportError("legacy history does not reconstruct the active projection")
    content = replace(
        content,
        projection=active,
        publication=content.publication.model_copy(update={"updated_at": build_epoch}),
    )
    projection_sha256 = sha256_hex(canonical_json_bytes(active))
    release_id = sha256_hex(
        canonical_json_bytes(
            {
                "config_sha256": config_sha256,
                "projection_sha256": projection_sha256,
                "source_commit": source_commit,
                "tool_version": tool_version,
            },
            privacy_check=False,
        )
    )
    publication = content.publication.model_copy(update={"active_release_id": release_id})
    release = ReleaseManifest(
        release_id=release_id,
        source_commit=source_commit,
        projection_sha256=projection_sha256,
        config_sha256=config_sha256,
        tool_version=tool_version,
        generated_at=build_epoch,
        files=(),
        case_count=len(active.cases),
        page_count=max(1, len(active.cases)),
    )
    candidate = replace(content, publication=publication, release=release)
    try:
        # Use the store's complete cross-contract check before the caller writes anything.
        store._validate_consistency(candidate)
    except StaticStateError as error:
        raise LegacyExportError("legacy bootstrap is internally inconsistent") from error
    return candidate


def export_legacy_bootstrap(
    projections: Iterable[ScotusPublicProjection],
    destination: Path,
    *,
    source_commit: str,
    config_sha256: str,
    build_epoch: datetime,
    tool_version: str = "legacy-bootstrap-v1",
) -> Path:
    content = build_legacy_bootstrap(
        projections,
        source_commit=source_commit,
        config_sha256=config_sha256,
        build_epoch=build_epoch,
        tool_version=tool_version,
    )
    return StaticStateStore(destination.parent / ".active-not-mutated").write_candidate(
        destination, content
    )
