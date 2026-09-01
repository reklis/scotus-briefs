"""Versioned radio-domain hint sets."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class HintSet(BaseModel):
    version: str
    units: list[str]
    talkgroups: list[str]
    streets: list[str]
    quadrants: list[str]
    landmarks: list[str]

    @classmethod
    def load(cls, path: str | Path) -> HintSet:
        with Path(path).open(encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle))

    def prompt(self, talkgroup_name: str) -> str:
        vocabulary = ", ".join(
            self.units + self.streets + self.quadrants + self.landmarks
        )
        return (
            f"DC Fire and EMS radio traffic on {talkgroup_name}. "
            f"Possible domain vocabulary: {vocabulary}. "
            "Use hints only when supported by audio; preserve negation and uncertainty."
        )
