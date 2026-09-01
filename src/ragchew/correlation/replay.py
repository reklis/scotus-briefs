"""Deterministic incident replay and comparison tooling."""

from __future__ import annotations

import json
from pathlib import Path

from ragchew.correlation.engine import CorrelationEngine, ObservationContext


def replay_to_json(
    contexts: list[ObservationContext], engine: CorrelationEngine, output: Path
) -> None:
    incidents = engine.replay(contexts)
    payload = [
        {
            "incident": item.incident.model_dump(mode="json"),
            "observation_ids": [
                str(context.observation.observation_id) for context in item.contexts
            ],
            "talkgroup_ids": sorted(item.talkgroup_ids),
            "units": sorted(item.units),
            "history": [
                {
                    "prior_state": change.prior_state,
                    "new_state": change.new_state,
                    "reason": change.reason,
                    "evidence_ids": [str(value) for value in change.evidence_ids],
                    "changed_at": change.changed_at.isoformat(),
                    "correlation_version": change.correlation_version,
                }
                for change in item.history
            ],
        }
        for item in incidents
    ]
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    temporary.replace(output)
