"""Strictly sanitized contracts permitted at the public boundary."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ragchew.contracts import IncidentState, StoryRevision
from ragchew.correlation.engine import IncidentSnapshot

DISCLAIMER = (
    "Automatically generated from public DC Fire and EMS radio communications. "
    "Reports are delayed, incomplete, may be corrected, and are not an emergency service."
)
COVERAGE = (
    "MVP coverage uses one receiver and one RF window; some DCFD transmissions are not captured."
)


class PublicTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    occurred_at: datetime
    text: str


class PublicRevisionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    revision_number: int = Field(gt=0)
    created_at: datetime
    status: IncidentState


class PublicStory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    story_id: UUID
    title: str
    summary: str
    status: IncidentState
    incident_type: str
    location: str
    first_reported_at: datetime
    updated_at: datetime
    timeline: tuple[PublicTimelineItem, ...] = ()
    revisions: tuple[PublicRevisionSummary, ...] = ()


class PublicDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    watermark: datetime
    changed_story_ids: tuple[UUID, ...] = ()
    message: str


class PublicProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    watermark: datetime
    generated_at: datetime
    stories: tuple[PublicStory, ...]
    digest: PublicDigest
    disclaimer: str = DISCLAIMER
    coverage: str = COVERAGE


def sanitize_story(
    snapshot: IncidentSnapshot,
    revision: StoryRevision,
    revision_number: int,
    prior_revisions: tuple[PublicRevisionSummary, ...] = (),
) -> PublicStory:
    incident = snapshot.incident
    if not incident.incident_type or not incident.public_location:
        raise ValueError("public story requires sanitized type and location")
    return PublicStory(
        story_id=revision.story_id,
        title=revision.title,
        summary=revision.summary,
        status=revision.status,
        incident_type=incident.incident_type,
        location=incident.public_location,
        first_reported_at=incident.first_observed_at,
        updated_at=revision.created_at,
        timeline=tuple(
            PublicTimelineItem(occurred_at=item.occurred_at, text=item.text)
            for item in revision.timeline
        ),
        revisions=(
            *prior_revisions,
            PublicRevisionSummary(
                revision_number=revision_number,
                created_at=revision.created_at,
                status=revision.status,
            ),
        ),
    )
