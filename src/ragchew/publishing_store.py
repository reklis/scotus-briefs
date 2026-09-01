"""PostgreSQL append-only story and atomic projection persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.contracts import IncidentState, StoryRevision
from ragchew.correlation.engine import IncidentSnapshot
from ragchew.correlation.store import PostgresIncidentStore
from ragchew.policy import PolicyDecision
from ragchew.policy_store import PostgresPolicyStore
from ragchew.public_contracts import (
    PublicDigest,
    PublicProjection,
    PublicRevisionSummary,
    PublicStory,
    sanitize_story,
)

PROJECTION_NAMESPACE = UUID("d7945d29-c19f-45ad-87cc-4011c4108bb8")


class PostgresPublishingStore:
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
        self.incidents = PostgresIncidentStore("", pool=self.pool)
        self.policies = PostgresPolicyStore("", pool=self.pool)

    def load_changed(self, since: datetime, through: datetime) -> list[IncidentSnapshot]:
        # Incident state is created only after all linked transcript/extraction work completes.
        snapshots = self.incidents.load_active(since)
        return [item for item in snapshots if item.incident.updated_at <= through]

    def save_decision(self, decision: PolicyDecision) -> PolicyDecision:
        return self.policies.save(decision)

    def _prior_revisions(self, story_id: UUID) -> tuple[PublicRevisionSummary, ...]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """SELECT revision_number,created_at,
                          public_payload->>'status' AS status
                   FROM story_revisions WHERE story_id=%s ORDER BY revision_number""",
                (story_id,),
            ).fetchall()
        return tuple(
            PublicRevisionSummary(
                revision_number=row["revision_number"],
                created_at=row["created_at"],
                status=IncidentState(row["status"]),
            )
            for row in rows
        )

    def save_story(
        self,
        snapshot: IncidentSnapshot,
        decision: PolicyDecision,
        revision: StoryRevision,
    ) -> PublicStory:
        prior = self._prior_revisions(revision.story_id)
        number = len(prior) + 1
        public_story = sanitize_story(snapshot, revision, number, prior)
        with self.pool.connection() as connection, connection.transaction():
            connection.execute(
                """INSERT INTO story_revisions
                   (revision_id,story_id,incident_id,revision_number,public_payload,
                    generator_model,policy_decision_id,created_at)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                   ON CONFLICT(story_id,revision_number) DO NOTHING""",
                (
                    revision.revision_id,
                    revision.story_id,
                    revision.incident_id,
                    number,
                    public_story.model_dump_json(),
                    revision.generator_model,
                    decision.decision_id,
                    revision.created_at,
                ),
            )
        return public_story

    def retract_story(
        self, snapshot: IncidentSnapshot, decision: PolicyDecision, at: datetime
    ) -> PublicStory | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                """SELECT story_id,public_payload FROM story_revisions
                   WHERE incident_id=%s ORDER BY revision_number DESC LIMIT 1""",
                (snapshot.incident.incident_id,),
            ).fetchone()
        if row is None:
            return None
        prior_story = PublicStory.model_validate(row["public_payload"])
        reason = decision.reasons[0] if decision.reasons else "report withdrawn"
        revision = StoryRevision(
            story_id=row["story_id"],
            incident_id=snapshot.incident.incident_id,
            title=f"Retracted: {prior_story.title}",
            summary=f"This automatically generated report was retracted: {reason}.",
            status=IncidentState.RETRACTED,
            claim_ids=(uuid5(PROJECTION_NAMESPACE, f"retract:{decision.decision_id}"),),
            created_at=at,
            generator_model="deterministic-retraction",
        )
        return self.save_story(snapshot, decision, revision)

    def activate_projection(
        self, watermark: datetime, changed_story_ids: tuple[str, ...]
    ) -> PublicProjection:
        generated = datetime.now(UTC)
        with self.pool.connection() as connection, connection.transaction():
            rows = connection.execute(
                """SELECT DISTINCT ON (story_id) public_payload
                   FROM story_revisions WHERE created_at <= %s
                   ORDER BY story_id,revision_number DESC""",
                (generated,),
            ).fetchall()
            stories = tuple(
                sorted(
                    (PublicStory.model_validate(row["public_payload"]) for row in rows),
                    key=lambda story: story.updated_at,
                    reverse=True,
                )
            )
            changed = tuple(UUID(value) for value in changed_story_ids)
            message = (
                f"{len(changed)} qualifying incident update(s) were published."
                if changed
                else "No qualifying incidents were published this hour."
            )
            projection = PublicProjection(
                watermark=watermark,
                generated_at=generated,
                stories=stories,
                digest=PublicDigest(
                    watermark=watermark,
                    changed_story_ids=changed,
                    message=message,
                ),
            )
            projection_id = uuid5(PROJECTION_NAMESPACE, watermark.isoformat())
            connection.execute(
                """INSERT INTO public_projections(projection_id,watermark,payload,status)
                   VALUES (%s,%s,%s::jsonb,'building')
                   ON CONFLICT(watermark) DO UPDATE SET
                     payload=excluded.payload,status='building'""",
                (projection_id, watermark, projection.model_dump_json()),
            )
            connection.execute(
                "UPDATE public_projections SET status='superseded' WHERE status='active'"
            )
            connection.execute(
                """UPDATE public_projections SET status='active',activated_at=now()
                   WHERE projection_id=%s""",
                (projection_id,),
            )
        return projection

    def active_projection(self) -> PublicProjection | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM active_public_projection"
            ).fetchone()
        if row is None:
            return None
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return PublicProjection.model_validate(payload)
