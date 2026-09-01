"""Pure deterministic incident matching and lifecycle derivation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from ragchew.contracts import (
    EpistemicStatus,
    Incident,
    IncidentState,
    Observation,
    ObservationType,
    SensitivityLabel,
)

INCIDENT_NAMESPACE = UUID("650b5673-8f38-4f50-8f14-c8be82818607")
OPEN_STATES = {
    IncidentState.CANDIDATE,
    IncidentState.CORROBORATING,
    IncidentState.PUBLISHABLE,
    IncidentState.ACTIVE,
    IncidentState.CORRECTED,
    IncidentState.SUPPRESSED,
}


@dataclass(frozen=True)
class ObservationContext:
    observation: Observation
    talkgroup_id: int
    talkgroup_name: str


@dataclass(frozen=True)
class StateChange:
    prior_state: IncidentState | None
    new_state: IncidentState
    reason: str
    evidence_ids: tuple[UUID, ...]
    changed_at: datetime
    correlation_version: str


@dataclass(frozen=True)
class IncidentSnapshot:
    incident: Incident
    contexts: tuple[ObservationContext, ...]
    talkgroup_ids: frozenset[int]
    units: frozenset[str]
    published: bool = False
    history: tuple[StateChange, ...] = ()

    @property
    def locations(self) -> frozenset[str]:
        return frozenset(
            context.observation.normalized_value
            for context in self.contexts
            if context.observation.type == ObservationType.LOCATION
            and context.observation.normalized_value
        )

    @property
    def incident_types(self) -> frozenset[str]:
        return frozenset(
            context.observation.normalized_value
            for context in self.contexts
            if context.observation.type == ObservationType.INCIDENT_TYPE
            and context.observation.normalized_value
        )


@dataclass(frozen=True)
class CorrelationRules:
    version: str = "correlation-v1"
    lookback_hours: int = 6
    match_threshold: float = 0.5
    publishable_threshold: float = 0.75
    corroborating_threshold: float = 0.4
    mandatory_suppression: frozenset[SensitivityLabel] = frozenset(
        {
            SensitivityLabel.MEDICAL,
            SensitivityLabel.BEHAVIORAL_HEALTH,
            SensitivityLabel.SUICIDE,
            SensitivityLabel.OVERDOSE,
            SensitivityLabel.JUVENILE,
        }
    )


class CorrelationEngine:
    def __init__(self, rules: CorrelationRules | None = None) -> None:
        self.rules = rules or CorrelationRules()

    @staticmethod
    def _values(
        contexts: tuple[ObservationContext, ...], observation_type: ObservationType
    ) -> frozenset[str]:
        return frozenset(
            context.observation.normalized_value
            for context in contexts
            if context.observation.type == observation_type
            and context.observation.normalized_value
        )

    def match_score(
        self,
        incident: IncidentSnapshot,
        incoming: tuple[ObservationContext, ...],
        now: datetime,
    ) -> float:
        if incident.incident.state not in OPEN_STATES:
            return -1
        if now - incident.incident.updated_at > timedelta(hours=self.rules.lookback_hours):
            return -1
        locations = self._values(incoming, ObservationType.LOCATION)
        if locations and incident.locations and locations.isdisjoint(incident.locations):
            return -1

        score = 0.0
        if locations & incident.locations:
            score += 0.55
        incoming_talkgroups = {context.talkgroup_id for context in incoming}
        shared_talkgroups = incoming_talkgroups & incident.talkgroup_ids
        if shared_talkgroups:
            score += 0.4 if any(value not in {101, 102} for value in shared_talkgroups) else 0.15
        units = self._values(incoming, ObservationType.UNIT_ASSIGNMENT)
        if units & incident.units:
            score += 0.15
        incident_types = self._values(incoming, ObservationType.INCIDENT_TYPE)
        if incident_types & incident.incident_types:
            score += 0.15
        minutes = abs((now - incident.incident.updated_at).total_seconds()) / 60
        if minutes <= 5:
            score += 0.2
        elif minutes <= 15:
            score += 0.1
        return min(1.0, score)

    @staticmethod
    def _evidence_weight(observation: Observation) -> float:
        if observation.routine or observation.type == ObservationType.ROUTINE:
            return 0.0
        if observation.type in {ObservationType.ON_SCENE, ObservationType.ESCALATION}:
            return 1.0
        if observation.epistemic_status in {
            EpistemicStatus.CONFIRMED,
            EpistemicStatus.ON_SCENE_REPORTED,
        }:
            return 0.9
        if observation.epistemic_status == EpistemicStatus.DISPATCHED:
            return 0.35
        if observation.epistemic_status == EpistemicStatus.RESPONDING:
            return 0.25
        return 0.2

    def _confidence(self, contexts: tuple[ObservationContext, ...]) -> float:
        remaining = 1.0
        for context in contexts:
            weighted = self._evidence_weight(context.observation) * context.observation.confidence
            remaining *= 1 - min(1.0, weighted)
        return round(1 - remaining, 6)

    def _derive_state(
        self,
        prior: IncidentSnapshot | None,
        contexts: tuple[ObservationContext, ...],
        confidence: float,
        sensitivity: frozenset[SensitivityLabel],
    ) -> tuple[IncidentState, str]:
        observations = [context.observation for context in contexts]
        if sensitivity & self.rules.mandatory_suppression:
            return IncidentState.SUPPRESSED, "mandatory sensitivity classification"
        if any(item.type == ObservationType.CANCELLATION for item in observations):
            return IncidentState.RESOLVED, "supported cancellation or unfounded disposition"
        if any(
            item.type in {ObservationType.RESOLUTION, ObservationType.CONTAINMENT}
            for item in observations
        ):
            return IncidentState.RESOLVED, "supported incident resolution"
        if prior and prior.published and any(
            item.type == ObservationType.CORRECTION for item in observations
        ):
            return IncidentState.CORRECTED, "published incident received a correction"
        if confidence >= self.rules.publishable_threshold:
            return (
                (IncidentState.ACTIVE, "published incident has active supported evidence")
                if prior and prior.published
                else (IncidentState.PUBLISHABLE, "evidence threshold reached")
            )
        if confidence >= self.rules.corroborating_threshold:
            return IncidentState.CORROBORATING, "multiple or stronger observations accumulated"
        return IncidentState.CANDIDATE, "evidence remains insufficient"

    def _new_snapshot(
        self, incoming: tuple[ObservationContext, ...], now: datetime
    ) -> IncidentSnapshot:
        seed = min(
            (context.observation for context in incoming),
            key=lambda item: (item.occurred_at, str(item.observation_id)),
        )
        incident_id = uuid5(INCIDENT_NAMESPACE, str(seed.observation_id))
        placeholder = Incident(
            incident_id=incident_id,
            state=IncidentState.CANDIDATE,
            confidence=0,
            first_observed_at=seed.occurred_at,
            updated_at=now,
            correlation_version=self.rules.version,
        )
        return IncidentSnapshot(placeholder, (), frozenset(), frozenset())

    def correlate(
        self,
        incoming: list[ObservationContext],
        active: list[IncidentSnapshot],
    ) -> IncidentSnapshot | None:
        meaningful = tuple(
            sorted(
                (
                    context
                    for context in incoming
                    if not context.observation.routine
                    and context.observation.type != ObservationType.ROUTINE
                ),
                key=lambda context: (
                    context.observation.occurred_at,
                    str(context.observation.observation_id),
                ),
            )
        )
        if not meaningful:
            return None
        now = max(context.observation.occurred_at for context in meaningful).astimezone(UTC)
        candidates = [
            (self.match_score(snapshot, meaningful, now), snapshot) for snapshot in active
        ]
        matched = max(candidates, key=lambda item: item[0])[1] if candidates else None
        if candidates and max(score for score, _ in candidates) < self.rules.match_threshold:
            matched = None
        prior = matched or self._new_snapshot(meaningful, now)

        existing_ids = {context.observation.observation_id for context in prior.contexts}
        combined = prior.contexts + tuple(
            context
            for context in meaningful
            if context.observation.observation_id not in existing_ids
        )
        sensitivity = frozenset(
            label for context in combined for label in context.observation.sensitivity
        ) | frozenset(prior.incident.sensitivity)
        confidence = self._confidence(combined)
        state, reason = self._derive_state(matched, combined, confidence, sensitivity)
        locations = self._values(combined, ObservationType.LOCATION)
        incident_types = self._values(combined, ObservationType.INCIDENT_TYPE)
        units = self._values(combined, ObservationType.UNIT_ASSIGNMENT)
        talkgroups = prior.talkgroup_ids | {context.talkgroup_id for context in combined}
        incident = prior.incident.model_copy(
            update={
                "state": state,
                "incident_type": (
                    sorted(incident_types)[0]
                    if incident_types
                    else prior.incident.incident_type
                ),
                "public_location": (
                    sorted(locations)[0] if locations else prior.incident.public_location
                ),
                "confidence": confidence,
                "sensitivity": tuple(sorted(sensitivity, key=str)),
                "observation_ids": tuple(
                    context.observation.observation_id for context in combined
                ),
                "updated_at": now,
                "correlation_version": self.rules.version,
            }
        )
        history = prior.history
        if state != prior.incident.state or not prior.contexts:
            history += (
                StateChange(
                    prior_state=prior.incident.state if prior.contexts else None,
                    new_state=state,
                    reason=reason,
                    evidence_ids=tuple(
                        context.observation.observation_id for context in meaningful
                    ),
                    changed_at=now,
                    correlation_version=self.rules.version,
                ),
            )
        return replace(
            prior,
            incident=incident,
            contexts=combined,
            talkgroup_ids=frozenset(talkgroups),
            units=frozenset(units),
            history=history,
        )

    def replay(self, contexts: list[ObservationContext]) -> list[IncidentSnapshot]:
        groups: dict[str, list[ObservationContext]] = {}
        for context in contexts:
            groups.setdefault(context.observation.capture_id, []).append(context)
        ordered_groups = sorted(
            groups.values(),
            key=lambda group: min(
                (item.observation.occurred_at, str(item.observation.observation_id))
                for item in group
            ),
        )
        incidents: list[IncidentSnapshot] = []
        for group in ordered_groups:
            updated = self.correlate(group, incidents)
            if updated is None:
                continue
            incidents = [
                item
                for item in incidents
                if item.incident.incident_id != updated.incident.incident_id
            ]
            incidents.append(updated)
        return sorted(incidents, key=lambda item: str(item.incident.incident_id))
