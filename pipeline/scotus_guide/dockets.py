"""Supreme Court docket normalization and deterministic ordering."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
from hashlib import sha256

_PREFIX_RE = re.compile(r"^(?:(?:docket|nos?\.?)\s+)", re.IGNORECASE)
_STANDARD_RE = re.compile(r"^(?P<term>\d{2,4})-(?P<number>\d+)$")
_LETTER_RE = re.compile(r"^(?P<term>\d{2,4})(?P<letter>[A-Z])(?P<number>\d+)$")
_ORIGINAL_RE = re.compile(r"^(?P<number>\d+)O$")
_ORIGINAL_LABEL_RE = re.compile(r"^(?P<number>\d+)(?:\s*,\s*|\s+)ORIG(?:INAL)?\.?$")
_DASH_SPACING_RE = re.compile(r"\s*-\s*")
_DOCKET_CHARACTER_TRANSLATION = str.maketrans(
    {
        "\N{NO-BREAK SPACE}": " ",
        "\N{SOFT HYPHEN}": "-",
        "\N{HYPHEN}": "-",
        "\N{NON-BREAKING HYPHEN}": "-",
        "\N{FIGURE DASH}": "-",
        "\N{EN DASH}": "-",
        "\N{EM DASH}": "-",
        "\N{HORIZONTAL BAR}": "-",
        "\N{MINUS SIGN}": "-",
        "\N{SMALL EM DASH}": "-",
        "\N{SMALL HYPHEN-MINUS}": "-",
        "\N{FULLWIDTH HYPHEN-MINUS}": "-",
    }
)


class DocketKind(IntEnum):
    STANDARD = 0
    APPLICATION = 1
    ORIGINAL = 2
    OTHER_LETTER = 3


@dataclass(frozen=True, slots=True)
class ParsedDocket:
    canonical: str
    term: int | None
    number: int
    kind: DocketKind
    letter: str = ""

    @property
    def sort_key(self) -> tuple[int, int, str]:
        """Order regular dockets first, then A applications, original, and other series."""
        return (int(self.kind), self.number, self.letter)


def normalize_docket(value: str) -> str:
    """Return the Court's compact docket form, rejecting ambiguous input."""
    cleaned = value.translate(_DOCKET_CHARACTER_TRANSLATION).strip().upper()
    cleaned = _PREFIX_RE.sub("", cleaned).strip().rstrip(".,;")
    cleaned = _DASH_SPACING_RE.sub("-", cleaned)
    match = _STANDARD_RE.fullmatch(cleaned)
    if match:
        return f"{int(match.group('term')):02d}-{int(match.group('number'))}"
    match = _LETTER_RE.fullmatch(cleaned)
    if match:
        return f"{int(match.group('term')):02d}{match.group('letter')}{int(match.group('number'))}"
    match = _ORIGINAL_RE.fullmatch(cleaned) or _ORIGINAL_LABEL_RE.fullmatch(cleaned)
    if match:
        return f"{int(match.group('number'))}O"
    raise ValueError(f"unsupported Supreme Court docket number: {value!r}")


def parse_docket(value: str) -> ParsedDocket:
    canonical = normalize_docket(value)
    match = _STANDARD_RE.fullmatch(canonical)
    if match:
        return ParsedDocket(
            canonical, int(match.group("term")), int(match.group("number")), DocketKind.STANDARD
        )
    match = _LETTER_RE.fullmatch(canonical)
    if match:
        letter = match.group("letter")
        kind = {
            "A": DocketKind.APPLICATION,
            "O": DocketKind.ORIGINAL,
        }.get(letter, DocketKind.OTHER_LETTER)
        return ParsedDocket(
            canonical, int(match.group("term")), int(match.group("number")), kind, letter
        )
    match = _ORIGINAL_RE.fullmatch(canonical)
    assert match is not None
    return ParsedDocket(canonical, None, int(match.group("number")), DocketKind.ORIGINAL, "O")


def docket_sort_key(value: str) -> tuple[int, int, str]:
    return parse_docket(value).sort_key


def normalize_dockets(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize and de-duplicate a consolidated docket set in display order."""
    return tuple(sorted({normalize_docket(value) for value in values}, key=docket_sort_key))


def stable_case_id(primary_docket: str | None, *, unresolved_group: str | None = None) -> str:
    """Build an identity that does not depend on mutable case titles."""
    if primary_docket:
        docket = normalize_docket(primary_docket).lower().replace("-", "-")
        return f"scotus-{docket}"
    if unresolved_group:
        digest = sha256(unresolved_group.encode()).hexdigest()[:16]
        return f"unresolved-{digest}"
    raise ValueError("a primary docket or unresolved group is required")


def stable_case_slug(primary_docket: str | None, *, unresolved_group: str | None = None) -> str:
    """Public route key; deliberately title-independent so corrections do not break links."""
    return stable_case_id(primary_docket, unresolved_group=unresolved_group)
