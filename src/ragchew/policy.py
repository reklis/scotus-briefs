"""Deterministic default-deny publication policy and sanitization."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict

from ragchew.config import PublicationDefaults
from ragchew.contracts import (
    ApprovedPublicClaim,
    Certainty,
    IncidentState,
    Observation,
    ObservationType,
    SensitivityLabel,
)
from ragchew.correlation.engine import IncidentSnapshot

POLICY_NAMESPACE = UUID("ed2dca79-109f-4125-97fc-fc9992b410a8")
PUBLIC_OBSERVATION_TYPES = {
    ObservationType.LOCATION,
    ObservationType.INCIDENT_TYPE,
    ObservationType.REPORTED_EVENT,
    ObservationType.DISPATCH,
    ObservationType.RESPONSE,
    ObservationType.ARRIVAL,
    ObservationType.ON_SCENE,
    ObservationType.ESCALATION,
    ObservationType.CANCELLATION,
    ObservationType.CONTAINMENT,
    ObservationType.RESOLUTION,
    ObservationType.CORRECTION,
}
MANDATORY_LABELS = {
    SensitivityLabel.MEDICAL,
    SensitivityLabel.BEHAVIORAL_HEALTH,
    SensitivityLabel.SUICIDE,
    SensitivityLabel.OVERDOSE,
    SensitivityLabel.JUVENILE,
}
NAME_PATTERN = re.compile(r"\b(?:patient|caller|victim)\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b")
UNIT_PATTERN = re.compile(r"\b(?:apartment|apt|unit)\s*[A-Z0-9-]+\b", re.I)
STREET_NUMBER = re.compile(r"\b(\d{3,5})\s+(.+)")


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: UUID
    incident_id: UUID
    eligible: bool
    policy_version: str
    reasons: tuple[str, ...]
    approved_claims: tuple[ApprovedPublicClaim, ...] = ()
    created_at: datetime


class PublicationPolicy:
    def __init__(self, config: PublicationDefaults, version: str = "publication-v1") -> None:
        self.config = config
        self.version = version

    @staticmethod
    def _certainty(observation: Observation) -> Certainty:
        mapping = {
            ObservationType.DISPATCH: Certainty.DISPATCHED,
            ObservationType.RESPONSE: Certainty.DISPATCHED,
            ObservationType.ARRIVAL: Certainty.ON_SCENE_REPORTED,
            ObservationType.ON_SCENE: Certainty.ON_SCENE_REPORTED,
            ObservationType.ESCALATION: Certainty.ON_SCENE_REPORTED,
            ObservationType.CONTAINMENT: Certainty.ON_SCENE_REPORTED,
            ObservationType.RESOLUTION: Certainty.ON_SCENE_REPORTED,
        }
        return mapping.get(observation.type, Certainty.REPORTED)

    @staticmethod
    def _generalize_location(value: str) -> str:
        value = UNIT_PATTERN.sub("", value)
        if match := STREET_NUMBER.match(value.strip()):
            number = int(match.group(1))
            block = number // 100 * 100
            return f"{block} block of {match.group(2).strip()}"
        return value.strip()

    def _sanitize(self, observation: Observation) -> str | None:
        if observation.type not in PUBLIC_OBSERVATION_TYPES:
            return None
        if observation.sensitivity:
            return None
        value = observation.normalized_value or observation.raw_value
        value = NAME_PATTERN.sub("", value)
        value = UNIT_PATTERN.sub("", value)
        value = " ".join(value.split()).strip(" ,.-")
        if observation.type == ObservationType.LOCATION:
            value = self._generalize_location(value)
        return value or None

    @staticmethod
    def _has_strong_evidence(snapshot: IncidentSnapshot) -> bool:
        return any(
            context.observation.type
            in {
                ObservationType.ON_SCENE,
                ObservationType.ESCALATION,
                ObservationType.CONTAINMENT,
                ObservationType.RESOLUTION,
            }
            and context.observation.confidence >= 0.7
            for context in snapshot.contexts
        )

    def evaluate(self, snapshot: IncidentSnapshot, now: datetime | None = None) -> PolicyDecision:
        current = now or datetime.now(UTC)
        incident = snapshot.incident
        reasons: list[str] = []
        sensitivity = set(incident.sensitivity)
        if sensitivity & MANDATORY_LABELS:
            reasons.append("mandatory sensitive category")
        if incident.state in {IncidentState.CANDIDATE, IncidentState.CORROBORATING}:
            reasons.append("incident has insufficient evidence")
        if incident.state in {IncidentState.SUPPRESSED, IncidentState.RETRACTED}:
            reasons.append(f"incident state is {incident.state.value}")
        if not incident.incident_type or incident.incident_type not in self.config.allowlist:
            reasons.append("incident category is not allowlisted")
        if not incident.public_location:
            reasons.append("incident lacks a publishable location")
        if not self._has_strong_evidence(snapshot):
            reasons.append("incident lacks on-scene or escalation evidence")
        if incident.state == IncidentState.RESOLVED and any(
            context.observation.type == ObservationType.CANCELLATION
            for context in snapshot.contexts
        ) and not self._has_strong_evidence(snapshot):
            reasons.append("unconfirmed report was cancelled or unfounded")

        claims: list[ApprovedPublicClaim] = []
        if not reasons:
            seen: set[tuple[ObservationType, str, Certainty]] = set()
            for context in snapshot.contexts:
                observation = context.observation
                value = self._sanitize(observation)
                if value is None:
                    continue
                certainty = self._certainty(observation)
                key = (observation.type, value, certainty)
                if key in seen:
                    continue
                seen.add(key)
                claim_id = uuid5(
                    POLICY_NAMESPACE,
                    f"{self.version}:{incident.incident_id}:{observation.observation_id}:{value}",
                )
                claims.append(
                    ApprovedPublicClaim(
                        claim_id=claim_id,
                        incident_id=incident.incident_id,
                        claim_type=observation.type,
                        public_value=value,
                        certainty=certainty,
                        source_observation_ids=(observation.observation_id,),
                        approved_at=current,
                        policy_version=self.version,
                    )
                )
            if not claims:
                reasons.append("no claims remained after sanitization")
        eligible = not reasons
        decision_id = uuid5(
            POLICY_NAMESPACE,
            f"decision:{self.version}:{incident.incident_id}:{incident.updated_at.isoformat()}",
        )
        return PolicyDecision(
            decision_id=decision_id,
            incident_id=incident.incident_id,
            eligible=eligible,
            policy_version=self.version,
            reasons=tuple(reasons),
            approved_claims=tuple(claims) if eligible else (),
            created_at=current,
        )
