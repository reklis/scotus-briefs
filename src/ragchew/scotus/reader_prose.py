"""Validated, versioned policy for reader-facing Supreme Court prose."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REQUIRED_TERMS = frozenset(
    {
        "certiorari",
        "domicile",
        "due_process",
        "equal_protection",
        "finality",
        "habeas",
        "injunction",
        "jurisdiction",
        "mootness",
        "preemption",
        "pretext",
        "procedural_history",
        "rebuttal",
        "remand",
        "scrutiny",
        "sovereign_immunity",
        "standing",
        "tolling",
        "vacatur",
        "waiver",
    }
)
_REQUIRED_ACTIONS = frozenset(
    {
        "affirm",
        "deny",
        "dismiss",
        "grant",
        "hold",
        "remand",
        "reverse",
        "stay",
        "vacate",
    }
)


def _validate_regex(value: str) -> str:
    try:
        re.compile(value, re.IGNORECASE)
    except re.error as error:
        raise ValueError(f"invalid reader-prose regular expression: {error}") from error
    return value


def _validate_regexes(values: tuple[str, ...]) -> tuple[str, ...]:
    for value in values:
        _validate_regex(value)
    return values


class ReaderTerm(BaseModel):
    """One reviewed term and the ordinary wording that can explain it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    patterns: tuple[str, ...] = Field(min_length=1, max_length=8)
    ordinary_alternatives: tuple[str, ...] = Field(min_length=1, max_length=8)
    explanation_patterns: tuple[str, ...] = Field(min_length=1, max_length=12)

    _term_patterns_are_valid = field_validator("patterns")(_validate_regexes)
    _explanation_patterns_are_valid = field_validator("explanation_patterns")(_validate_regexes)

    @field_validator("ordinary_alternatives")
    @classmethod
    def validate_alternatives(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() or len(value) > 120 for value in values):
            raise ValueError("ordinary alternatives must be nonempty and at most 120 characters")
        return values


class ForbiddenReaderPhrase(BaseModel):
    """Lawyer-facing wording that must be replaced rather than glossed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    pattern: str
    ordinary_alternative: str = Field(min_length=1, max_length=120)

    _pattern_is_valid = field_validator("pattern")(_validate_regex)


class ActionEquivalent(BaseModel):
    """A reviewed legal or ordinary expression for one canonical action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical: str = Field(pattern=r"^[a-z][a-z_]{1,31}$")
    pattern: str
    ordinary: bool

    _pattern_is_valid = field_validator("pattern")(_validate_regex)


class ReaderProsePolicy(BaseModel):
    """Strict schema for the reviewed reader-prose YAML resource."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=r"^scotus-reader-prose-v[1-9][0-9]*$")
    terms: tuple[ReaderTerm, ...] = Field(min_length=1, max_length=128)
    forbidden_phrases: tuple[ForbiddenReaderPhrase, ...] = Field(min_length=1, max_length=64)
    process_patterns: tuple[str, ...] = Field(min_length=1, max_length=32)
    action_equivalents: tuple[ActionEquivalent, ...] = Field(min_length=1, max_length=128)

    _process_patterns_are_valid = field_validator("process_patterns")(_validate_regexes)

    @model_validator(mode="after")
    def validate_reviewed_coverage(self) -> Self:
        labels = tuple(term.label for term in self.terms)
        if len(labels) != len(set(labels)):
            raise ValueError("reader-prose term labels must be unique")
        missing_terms = sorted(_REQUIRED_TERMS.difference(labels))
        if missing_terms:
            raise ValueError(f"reader-prose resource is missing required terms: {missing_terms}")
        phrase_labels = tuple(phrase.label for phrase in self.forbidden_phrases)
        if len(phrase_labels) != len(set(phrase_labels)):
            raise ValueError("forbidden reader-phrase labels must be unique")
        action_pairs = tuple(
            (equivalent.canonical, equivalent.pattern) for equivalent in self.action_equivalents
        )
        if len(action_pairs) != len(set(action_pairs)):
            raise ValueError("action equivalents must be unique")
        canonical_actions = {equivalent.canonical for equivalent in self.action_equivalents}
        missing_actions = sorted(_REQUIRED_ACTIONS.difference(canonical_actions))
        if missing_actions:
            raise ValueError(
                f"reader-prose resource is missing canonical actions: {missing_actions}"
            )
        ordinary_actions = {
            equivalent.canonical for equivalent in self.action_equivalents if equivalent.ordinary
        }
        missing_ordinary = sorted(
            {"affirm", "deny", "grant", "remand", "stay", "vacate"}.difference(ordinary_actions)
        )
        if missing_ordinary:
            raise ValueError(
                f"reader-prose resource lacks ordinary action equivalents: {missing_ordinary}"
            )
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> ReaderProsePolicy:
        with Path(path).open(encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle))


@lru_cache(maxsize=1)
def load_reader_prose_policy() -> ReaderProsePolicy:
    """Load and validate the policy shipped with this package exactly once."""
    resource = files("ragchew.scotus").joinpath("resources/reader-prose-v2.yaml")
    return ReaderProsePolicy.model_validate(yaml.safe_load(resource.read_text(encoding="utf-8")))
