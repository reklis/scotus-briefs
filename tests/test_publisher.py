from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

import pytest
from fastapi.testclient import TestClient

from ragchew.config import MvpConfig
from ragchew.contracts import IncidentState, StoryRevision
from ragchew.correlation.engine import IncidentSnapshot
from ragchew.generation import GroundedText, StoryDraft
from ragchew.policy import PolicyDecision, PublicationPolicy
from ragchew.public import create_public_app
from ragchew.public_contracts import (
    PublicDigest,
    PublicProjection,
    PublicRevisionSummary,
    PublicStory,
    sanitize_story,
)
from ragchew.publisher import HourlyPublisher, publication_watermark
from tests.test_policy_generation import confirmed_snapshot


class FakeGenerator:
    model_name = "story-test"

    def generate(self, decision: PolicyDecision, status: IncidentState) -> StoryDraft:
        claim_ids = tuple(claim.claim_id for claim in decision.approved_claims)
        return StoryDraft(
            title=GroundedText(
                text="Crews reported a significant DCFD incident", claim_ids=claim_ids
            ),
            summary=GroundedText(
                text="Radio traffic indicated crews responded and reported activity on scene.",
                claim_ids=claim_ids,
            ),
            status=status,
        )


class MemoryPublishingStore:
    def __init__(self) -> None:
        self.changed: list[IncidentSnapshot] = []
        self.decisions: dict[UUID, PolicyDecision] = {}
        self.histories: dict[UUID, list[PublicStory]] = {}
        self.active: PublicProjection | None = None
        self.fail_activation = False

    def load_changed(self, since: datetime, through: datetime) -> list[IncidentSnapshot]:
        return [
            item
            for item in self.changed
            if since <= item.incident.updated_at <= through
        ]

    def save_decision(self, decision: PolicyDecision) -> PolicyDecision:
        return self.decisions.setdefault(decision.decision_id, decision)

    def save_story(
        self,
        snapshot: IncidentSnapshot,
        decision: PolicyDecision,
        revision: StoryRevision,
    ) -> PublicStory:
        history = self.histories.setdefault(revision.story_id, [])
        prior = tuple(
            PublicRevisionSummary(
                revision_number=index,
                created_at=item.updated_at,
                status=item.status,
            )
            for index, item in enumerate(history, 1)
        )
        story = sanitize_story(snapshot, revision, len(history) + 1, prior)
        history.append(story)
        return story

    def retract_story(
        self, snapshot: IncidentSnapshot, decision: PolicyDecision, at: datetime
    ) -> PublicStory | None:
        expected_story_id = uuid5(
            UUID("221713de-3de5-4f93-86dc-f089696344ac"),
            str(snapshot.incident.incident_id),
        )
        history = next(
            (
                stories
                for story_id, stories in self.histories.items()
                if stories and story_id == expected_story_id
            ),
            None,
        )
        if not history:
            return None
        prior = history[-1]
        retracted = prior.model_copy(
            update={
                "title": f"Retracted: {prior.title}",
                "summary": "This automatically generated report was retracted.",
                "status": IncidentState.RETRACTED,
                "updated_at": at,
                "revisions": (
                    *prior.revisions,
                    PublicRevisionSummary(
                        revision_number=len(prior.revisions) + 1,
                        created_at=at,
                        status=IncidentState.RETRACTED,
                    ),
                ),
            }
        )
        history.append(retracted)
        return retracted

    def activate_projection(
        self, watermark: datetime, changed_story_ids: tuple[str, ...]
    ) -> PublicProjection:
        if self.fail_activation:
            raise RuntimeError("projection activation failed")
        stories = tuple(
            sorted(
                (history[-1] for history in self.histories.values() if history),
                key=lambda item: item.updated_at,
                reverse=True,
            )
        )
        ids = tuple(UUID(value) for value in changed_story_ids)
        self.active = PublicProjection(
            watermark=watermark,
            generated_at=watermark + timedelta(minutes=10),
            stories=stories,
            digest=PublicDigest(
                watermark=watermark,
                changed_story_ids=ids,
                message=(
                    f"{len(ids)} qualifying incident update(s) were published."
                    if ids
                    else "No qualifying incidents were published this hour."
                ),
            ),
        )
        return self.active

    def active_projection(self) -> PublicProjection | None:
        return self.active


def make_publisher(store: MemoryPublishingStore) -> HourlyPublisher:
    settings = MvpConfig.from_yaml("config/mvp.yaml").publication
    return HourlyPublisher(store, PublicationPolicy(settings), FakeGenerator(), settings)


def test_watermark_waits_for_late_arrival_grace() -> None:
    assert publication_watermark(
        datetime(2026, 8, 27, 19, 5, tzinfo=UTC), 10
    ) == datetime(2026, 8, 27, 18, tzinfo=UTC)
    assert publication_watermark(
        datetime(2026, 8, 27, 19, 10, tzinfo=UTC), 10
    ) == datetime(2026, 8, 27, 19, tzinfo=UTC)


def test_hourly_story_updates_keep_identity_and_revision_history() -> None:
    store = MemoryPublishingStore()
    snapshot = confirmed_snapshot(location="1400 H St NE")
    store.changed = [snapshot]
    publisher = make_publisher(store)
    first_projection = publisher.run(datetime(2026, 8, 27, 19, 10, tzinfo=UTC))
    first = first_projection.stories[0]
    assert len(first.revisions) == 1

    corrected = replace(
        snapshot,
        published=True,
        incident=snapshot.incident.model_copy(
            update={
                "state": IncidentState.CORRECTED,
                "updated_at": datetime(2026, 8, 27, 19, 30, tzinfo=UTC),
            }
        ),
    )
    store.changed = [corrected]
    corrected_projection = publisher.run(datetime(2026, 8, 27, 20, 10, tzinfo=UTC))
    assert corrected_projection.stories[0].status == IncidentState.CORRECTED

    resolved = replace(
        corrected,
        incident=corrected.incident.model_copy(
            update={
                "state": IncidentState.RESOLVED,
                "updated_at": datetime(2026, 8, 27, 20, 30, tzinfo=UTC),
            }
        ),
    )
    store.changed = [resolved]
    final_projection = publisher.run(datetime(2026, 8, 27, 21, 10, tzinfo=UTC))
    final = final_projection.stories[0]
    assert final.story_id == first.story_id
    assert final.status == IncidentState.RESOLVED
    assert len(final.revisions) == 3


def test_retraction_and_failed_projection_preserve_last_safe_projection() -> None:
    store = MemoryPublishingStore()
    snapshot = confirmed_snapshot(location="1400 H St NE")
    store.changed = [snapshot]
    publisher = make_publisher(store)
    safe = publisher.run(datetime(2026, 8, 27, 19, 10, tzinfo=UTC))

    retracted = replace(
        snapshot,
        published=True,
        incident=snapshot.incident.model_copy(
            update={
                "state": IncidentState.RETRACTED,
                "updated_at": datetime(2026, 8, 27, 19, 30, tzinfo=UTC),
            }
        ),
    )
    store.changed = [retracted]
    projection = publisher.run(datetime(2026, 8, 27, 20, 10, tzinfo=UTC))
    assert projection.stories[0].status == IncidentState.RETRACTED

    previous = store.active
    store.fail_activation = True
    store.changed = []
    with pytest.raises(RuntimeError, match="activation"):
        publisher.run(datetime(2026, 8, 27, 21, 10, tzinfo=UTC))
    assert store.active is previous
    assert store.active is not safe


def test_empty_digest_does_not_claim_there_were_no_emergencies() -> None:
    store = MemoryPublishingStore()
    projection = make_publisher(store).run(datetime(2026, 8, 27, 19, 10, tzinfo=UTC))
    assert projection.digest.message == "No qualifying incidents were published this hour."
    assert "no emergencies" not in projection.digest.message.lower()


def test_public_site_exposes_only_sanitized_projection_and_disclaimers() -> None:
    store = MemoryPublishingStore()
    snapshot = confirmed_snapshot(location="1400 H St NE")
    store.changed = [snapshot]
    projection = make_publisher(store).run(datetime(2026, 8, 27, 19, 10, tzinfo=UTC))
    client = TestClient(create_public_app(store))

    response = client.get("/api/projection")
    assert response.status_code == 200
    serialized = response.text.lower()
    for forbidden in (
        "audio_uri",
        "object_key",
        "transcript",
        "source_radio",
        "observation_id",
        "claim_id",
        "api_key",
        "patient",
    ):
        assert forbidden not in serialized

    home = client.get("/")
    assert home.status_code == 200
    assert "automatically generated" in home.text.lower()
    assert "not an emergency service" in home.text.lower()
    story = client.get(f"/stories/{projection.stories[0].story_id}")
    assert story.status_code == 200
    assert "one receiver" in story.text.lower()
