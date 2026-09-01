"""Grounded story generation and fail-closed validation."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid5

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from ragchew.contracts import (
    ApprovedPublicClaim,
    Certainty,
    IncidentState,
    StoryRevision,
    StoryTimelineItem,
)
from ragchew.policy import PolicyDecision

STORY_NAMESPACE = UUID("221713de-3de5-4f93-86dc-f089696344ac")
SENSITIVE_OUTPUT = re.compile(
    r"\b(patient|apartment|apt\.?|overdose|suicide|behavioral health|juvenile)\b", re.I
)
UNSUPPORTED_OUTCOME = re.compile(
    r"\b(?:\d+|one|two|three|four|five)\s+(?:people\s+)?(?:injured|dead|fatalities|casualties)\b",
    re.I,
)
CAUSE_LANGUAGE = re.compile(r"\b(?:caused by|cause was|originated from)\b", re.I)
QUALIFIED_LANGUAGE = re.compile(
    r"\b(reported|dispatched|responded|radio traffic indicated)\b", re.I
)
CONFIRMED_LANGUAGE = re.compile(r"\b(confirmed|definitely|is on fire|was on fire)\b", re.I)


class GroundedText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2_000)
    claim_ids: tuple[UUID, ...] = Field(min_length=1)


class GroundedTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    occurred_at: datetime
    text: str = Field(min_length=1, max_length=500)
    claim_ids: tuple[UUID, ...] = Field(min_length=1)


class StoryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: GroundedText
    summary: GroundedText
    status: IncidentState
    timeline: tuple[GroundedTimelineItem, ...] = ()


class StoryGenerator(Protocol):
    model_name: str

    def generate(self, decision: PolicyDecision, status: IncidentState) -> StoryDraft: ...


class OpenAIStoryGenerator:
    def __init__(self, model_name: str, client: OpenAI) -> None:
        self.model_name = model_name
        self.client = client

    def generate(self, decision: PolicyDecision, status: IncidentState) -> StoryDraft:
        claims = [claim.model_dump(mode="json") for claim in decision.approved_claims]
        completion = self.client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Write a concise local incident update using only supplied "
                        "approved claims. "
                        "Every title, summary, and timeline item must cite supporting claim IDs. "
                        "Preserve certainty: reported and dispatched claims are not confirmation. "
                        "Never add causes, casualties, identities, exact units, or outcomes."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Status: {status.value}\nApproved claims: {claims}",
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "grounded_story",
                    "strict": True,
                    "schema": StoryDraft.model_json_schema(),
                },
            },
        )
        content = completion.choices[0].message.content
        if not content:
            raise ValueError("story model returned no structured content")
        return StoryDraft.model_validate_json(content)


class StoryValidationError(ValueError):
    pass


def _validate_grounded_item(
    item: GroundedText | GroundedTimelineItem,
    known: dict[UUID, ApprovedPublicClaim],
) -> None:
    if not set(item.claim_ids).issubset(known):
        raise StoryValidationError("generated text references an unknown claim")
    if SENSITIVE_OUTPUT.search(item.text):
        raise StoryValidationError("generated text contains sensitive language")
    if UNSUPPORTED_OUTCOME.search(item.text) or CAUSE_LANGUAGE.search(item.text):
        raise StoryValidationError("generated text adds an unsupported outcome or cause")
    referenced = [known[claim_id] for claim_id in item.claim_ids]
    if all(
        claim.certainty in {Certainty.REPORTED, Certainty.DISPATCHED}
        for claim in referenced
    ) and (CONFIRMED_LANGUAGE.search(item.text) or not QUALIFIED_LANGUAGE.search(item.text)):
        raise StoryValidationError(
            "generated text overstates reported or dispatch evidence"
        )


def validate_story(draft: StoryDraft, claims: tuple[ApprovedPublicClaim, ...]) -> None:
    known = {claim.claim_id: claim for claim in claims}
    _validate_grounded_item(draft.title, known)
    _validate_grounded_item(draft.summary, known)
    for item in draft.timeline:
        _validate_grounded_item(item, known)


def build_story_revision(
    decision: PolicyDecision,
    draft: StoryDraft,
    model_name: str,
    created_at: datetime | None = None,
) -> StoryRevision:
    if not decision.eligible or not decision.approved_claims:
        raise StoryValidationError("ineligible policy decision cannot generate a story")
    validate_story(draft, decision.approved_claims)
    timestamp = created_at or datetime.now(UTC)
    story_id = uuid5(STORY_NAMESPACE, str(decision.incident_id))
    all_claim_ids = tuple(
        dict.fromkeys(
            [
                *draft.title.claim_ids,
                *draft.summary.claim_ids,
                *(claim_id for item in draft.timeline for claim_id in item.claim_ids),
            ]
        )
    )
    return StoryRevision(
        story_id=story_id,
        incident_id=decision.incident_id,
        title=draft.title.text,
        summary=draft.summary.text,
        status=draft.status,
        claim_ids=all_claim_ids,
        timeline=tuple(
            StoryTimelineItem(
                occurred_at=item.occurred_at,
                text=item.text,
                claim_ids=item.claim_ids,
            )
            for item in draft.timeline
        ),
        created_at=timestamp,
        generator_model=model_name,
    )
