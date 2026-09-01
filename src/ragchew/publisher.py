"""Hourly editorial watermark and atomic public projection orchestration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from openai import OpenAI

from ragchew.config import MvpConfig, PublicationDefaults, ServiceSettings
from ragchew.contracts import IncidentState, StoryRevision
from ragchew.correlation.engine import IncidentSnapshot
from ragchew.generation import (
    OpenAIStoryGenerator,
    StoryGenerator,
    build_story_revision,
)
from ragchew.metrics import (
    LAST_PUBLICATION_TIMESTAMP,
    POLICY_DECISIONS,
    PUBLICATION_OUTCOMES,
)
from ragchew.policy import PolicyDecision, PublicationPolicy
from ragchew.public_contracts import PublicProjection, PublicStory
from ragchew.publishing_store import PostgresPublishingStore


class PublishingStore(Protocol):
    def load_changed(self, since: datetime, through: datetime) -> list[IncidentSnapshot]: ...

    def save_decision(self, decision: PolicyDecision) -> PolicyDecision: ...

    def save_story(
        self,
        snapshot: IncidentSnapshot,
        decision: PolicyDecision,
        revision: StoryRevision,
    ) -> PublicStory: ...

    def retract_story(
        self, snapshot: IncidentSnapshot, decision: PolicyDecision, at: datetime
    ) -> PublicStory | None: ...

    def activate_projection(
        self, watermark: datetime, changed_story_ids: tuple[str, ...]
    ) -> PublicProjection: ...


def publication_watermark(now: datetime, grace_minutes: int) -> datetime:
    if now.tzinfo is None:
        raise ValueError("publisher clock must be timezone-aware")
    current = now.astimezone(UTC)
    hour = current.replace(minute=0, second=0, microsecond=0)
    if current < hour + timedelta(minutes=grace_minutes):
        return hour - timedelta(hours=1)
    return hour


class HourlyPublisher:
    def __init__(
        self,
        store: PublishingStore,
        policy: PublicationPolicy,
        generator: StoryGenerator,
        config: PublicationDefaults,
    ) -> None:
        self.store = store
        self.policy = policy
        self.generator = generator
        self.config = config

    def run(self, now: datetime | None = None) -> PublicProjection:
        current = now or datetime.now(UTC)
        watermark = publication_watermark(current, self.config.grace_minutes)
        since = watermark - timedelta(hours=self.config.lookback_hours)
        changed: list[str] = []
        for snapshot in self.store.load_changed(since, watermark):
            decision = self.store.save_decision(self.policy.evaluate(snapshot, current))
            POLICY_DECISIONS.labels(
                "eligible" if decision.eligible else "suppressed"
            ).inc()
            if decision.eligible:
                draft = self.generator.generate(decision, snapshot.incident.state)
                revision = build_story_revision(
                    decision, draft, self.generator.model_name, created_at=current
                )
                story = self.store.save_story(snapshot, decision, revision)
                changed.append(str(story.story_id))
            elif snapshot.published or snapshot.incident.state in {
                IncidentState.RETRACTED,
                IncidentState.SUPPRESSED,
            }:
                retracted_story = self.store.retract_story(snapshot, decision, current)
                if retracted_story:
                    changed.append(str(retracted_story.story_id))
        try:
            projection = self.store.activate_projection(
                watermark, tuple(dict.fromkeys(changed))
            )
        except Exception:
            PUBLICATION_OUTCOMES.labels("failed").inc()
            raise
        PUBLICATION_OUTCOMES.labels("complete").inc()
        LAST_PUBLICATION_TIMESTAMP.set(current.timestamp())
        return projection


def main() -> None:
    settings = ServiceSettings()
    config = MvpConfig.from_yaml(settings.config_path)
    client = OpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key.get_secret_value(),
    )
    HourlyPublisher(
        PostgresPublishingStore(settings.database_dsn),
        PublicationPolicy(config.publication),
        OpenAIStoryGenerator(settings.llm_model, client),
        config.publication,
    ).run()
