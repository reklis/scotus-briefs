"""PostgreSQL persistence for incident snapshots and append-only history."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.contracts import (
    EpistemicStatus,
    EvidenceRange,
    Incident,
    IncidentState,
    Observation,
    ObservationType,
    SensitivityLabel,
)
from ragchew.correlation.engine import (
    CorrelationEngine,
    IncidentSnapshot,
    ObservationContext,
)


class PostgresIncidentStore:
    def __init__(
        self,
        dsn: str,
        pool: ConnectionPool[Connection[dict[str, Any]]] | None = None,
    ) -> None:
        self.pool = pool or ConnectionPool(
            dsn,
            kwargs={"row_factory": dict_row},
            min_size=1,
            max_size=5,
            open=True,
        )

    @staticmethod
    def _context(row: dict[str, Any]) -> ObservationContext:
        evidence = row["evidence_private"]
        sensitivity = row["sensitivity"]
        if isinstance(evidence, str):
            evidence = json.loads(evidence)
        if isinstance(sensitivity, str):
            sensitivity = json.loads(sensitivity)
        observation = Observation(
            observation_id=row["observation_id"],
            transcript_revision_id=row["transcript_revision_id"],
            capture_id=row["capture_id"],
            type=ObservationType(row["observation_type"]),
            raw_value=row["raw_value_private"],
            normalized_value=row["normalized_value_private"],
            confidence=row["confidence"],
            epistemic_status=EpistemicStatus(row["epistemic_status"]),
            evidence=EvidenceRange.model_validate(evidence),
            occurred_at=row["occurred_at"].astimezone(UTC),
            sensitivity=tuple(SensitivityLabel(value) for value in sensitivity),
            routine=row["routine"],
            supersedes_observation_id=row["supersedes_observation_id"],
        )
        return ObservationContext(
            observation=observation,
            talkgroup_id=int(row["talkgroup_id"]),
            talkgroup_name=row["talkgroup_name"],
        )

    def load_extraction(self, extraction_revision_id: UUID) -> list[ObservationContext]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """SELECT o.*,(c.manifest->>'talkgroup_id')::integer AS talkgroup_id,
                          c.manifest->>'talkgroup_name' AS talkgroup_name
                   FROM observations o JOIN transcript_revisions t
                     ON t.revision_id=o.transcript_revision_id
                   JOIN captures c ON c.receiver_id=t.receiver_id AND c.capture_id=t.capture_id
                   WHERE o.extraction_revision_id=%s ORDER BY o.occurred_at,o.observation_id""",
                (extraction_revision_id,),
            ).fetchall()
        return [self._context(row) for row in rows]

    def load_active(self, since: datetime) -> list[IncidentSnapshot]:
        with self.pool.connection() as connection:
            incidents = connection.execute(
                """SELECT i.*,EXISTS(SELECT 1 FROM story_revisions s
                                      WHERE s.incident_id=i.incident_id) AS published
                   FROM incidents i WHERE i.updated_at >= %s
                     AND i.state IN
                       ('candidate','corroborating','publishable','active','resolved',
                        'corrected','retracted','suppressed')""",
                (since,),
            ).fetchall()
            snapshots: list[IncidentSnapshot] = []
            for row in incidents:
                contexts = connection.execute(
                    """SELECT o.*,(c.manifest->>'talkgroup_id')::integer AS talkgroup_id,
                              c.manifest->>'talkgroup_name' AS talkgroup_name
                       FROM incident_observations io JOIN observations o
                         ON o.observation_id=io.observation_id
                       JOIN transcript_revisions t ON t.revision_id=o.transcript_revision_id
                       JOIN captures c ON c.receiver_id=t.receiver_id AND c.capture_id=t.capture_id
                       WHERE io.incident_id=%s ORDER BY o.occurred_at,o.observation_id""",
                    (row["incident_id"],),
                ).fetchall()
                parsed = tuple(self._context(item) for item in contexts)
                sensitivity = row["sensitivity"]
                if isinstance(sensitivity, str):
                    sensitivity = json.loads(sensitivity)
                incident = Incident(
                    incident_id=row["incident_id"],
                    state=IncidentState(row["state"]),
                    incident_type=row["incident_type"],
                    public_location=row["public_location"],
                    confidence=row["confidence"],
                    sensitivity=tuple(SensitivityLabel(value) for value in sensitivity),
                    observation_ids=tuple(item.observation.observation_id for item in parsed),
                    first_observed_at=row["first_observed_at"],
                    updated_at=row["updated_at"],
                    correlation_version=row["correlation_version"],
                )
                snapshots.append(
                    IncidentSnapshot(
                        incident=incident,
                        contexts=parsed,
                        talkgroup_ids=frozenset(item.talkgroup_id for item in parsed),
                        units=frozenset(
                            item.observation.normalized_value
                            for item in parsed
                            if item.observation.type == ObservationType.UNIT_ASSIGNMENT
                            and item.observation.normalized_value
                        ),
                        published=row["published"],
                    )
                )
        return snapshots

    def save(self, snapshot: IncidentSnapshot) -> IncidentSnapshot:
        incident = snapshot.incident
        with self.pool.connection() as connection, connection.transaction():
            previous = connection.execute(
                "SELECT state::text FROM incidents WHERE incident_id=%s FOR UPDATE",
                (incident.incident_id,),
            ).fetchone()
            connection.execute(
                """INSERT INTO incidents
                   (incident_id,state,incident_type,public_location,confidence,sensitivity,
                    first_observed_at,updated_at,correlation_version)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                   ON CONFLICT(incident_id) DO UPDATE SET
                     state=excluded.state,incident_type=excluded.incident_type,
                     public_location=excluded.public_location,confidence=excluded.confidence,
                     sensitivity=excluded.sensitivity,updated_at=excluded.updated_at,
                     correlation_version=excluded.correlation_version""",
                (
                    incident.incident_id,
                    incident.state.value,
                    incident.incident_type,
                    incident.public_location,
                    incident.confidence,
                    json.dumps([label.value for label in incident.sensitivity]),
                    incident.first_observed_at,
                    incident.updated_at,
                    incident.correlation_version,
                ),
            )
            for context in snapshot.contexts:
                connection.execute(
                    """INSERT INTO incident_observations(incident_id,observation_id)
                       VALUES (%s,%s) ON CONFLICT DO NOTHING""",
                    (incident.incident_id, context.observation.observation_id),
                )
            prior_state = IncidentState(previous["state"]) if previous else None
            if prior_state != incident.state:
                change = snapshot.history[-1]
                connection.execute(
                    """INSERT INTO incident_state_history
                       (incident_id,prior_state,new_state,reason,evidence_ids,
                        correlation_version,changed_at)
                       VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)""",
                    (
                        incident.incident_id,
                        prior_state.value if prior_state else None,
                        incident.state.value,
                        change.reason,
                        json.dumps([str(value) for value in change.evidence_ids]),
                        change.correlation_version,
                        change.changed_at,
                    ),
                )
        return snapshot

    def correlate_extraction(
        self, extraction_revision_id: UUID, engine: CorrelationEngine
    ) -> IncidentSnapshot | None:
        incoming = self.load_extraction(extraction_revision_id)
        if not incoming:
            return None
        now = max(item.observation.occurred_at for item in incoming)
        active = self.load_active(now - timedelta(hours=engine.rules.lookback_hours))
        snapshot = engine.correlate(incoming, active)
        return self.save(snapshot) if snapshot else None

    def load_all_contexts(self) -> list[ObservationContext]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """SELECT o.*,(c.manifest->>'talkgroup_id')::integer AS talkgroup_id,
                          c.manifest->>'talkgroup_name' AS talkgroup_name
                   FROM observations o JOIN transcript_revisions t
                     ON t.revision_id=o.transcript_revision_id
                   JOIN captures c ON c.receiver_id=t.receiver_id AND c.capture_id=t.capture_id
                   ORDER BY o.occurred_at,o.observation_id"""
            ).fetchall()
        return [self._context(row) for row in rows]
