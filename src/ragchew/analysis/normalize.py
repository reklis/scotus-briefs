"""Conservative normalization that retains raw values and uncertainty."""

from __future__ import annotations

import re
from datetime import UTC, datetime

QUADRANTS = {
    "NORTHEAST": "NE",
    "NORTHWEST": "NW",
    "SOUTHEAST": "SE",
    "SOUTHWEST": "SW",
    "NE": "NE",
    "NW": "NW",
    "SE": "SE",
    "SW": "SW",
}
STREET_TYPES = {
    "STREET": "St",
    "AVENUE": "Ave",
    "ROAD": "Rd",
    "BOULEVARD": "Blvd",
    "PLACE": "Pl",
    "DRIVE": "Dr",
    "LANE": "Ln",
    "COURT": "Ct",
}
INCIDENT_TYPES = {
    "structure fire": "structure_fire",
    "building fire": "structure_fire",
    "working fire": "structure_fire",
    "hazmat": "hazmat",
    "hazardous materials": "hazmat",
    "rescue": "rescue",
    "entrapment": "entrapment",
    "collision": "major_collision",
    "motor vehicle accident": "major_collision",
    "mva": "major_collision",
}


def normalize_location(raw: str) -> tuple[str, bool]:
    """Return normalized DC location and whether a quadrant was supported."""
    value = " ".join(raw.strip().replace(",", " ").split())
    upper = value.upper()
    quadrant: str | None = None
    for source, normalized in QUADRANTS.items():
        if re.search(rf"\b{source}\b", upper):
            quadrant = normalized
            upper = re.sub(rf"\b{source}\b", "", upper).strip()
            break
    for source, normalized in STREET_TYPES.items():
        upper = re.sub(rf"\b{source}\b", normalized, upper, flags=re.IGNORECASE)
    upper = re.sub(r"\s+", " ", upper).strip().title()
    normalized_value = f"{upper} {quadrant}" if quadrant else upper
    return normalized_value, quadrant is not None


def normalize_unit(raw: str) -> str:
    value = re.sub(r"\s+", " ", raw.strip())
    replacements = {
        r"\bE\s*(\d+)\b": r"Engine \1",
        r"\bT\s*(\d+)\b": r"Truck \1",
        r"\bM\s*(\d+)\b": r"Medic \1",
        r"\bA\s*(\d+)\b": r"Ambulance \1",
        r"\bBC\s*(\d+)\b": r"Battalion Chief \1",
    }
    for pattern, replacement in replacements.items():
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    return value.title().replace("Ems", "EMS")


def normalize_incident_type(raw: str) -> str | None:
    lowered = raw.lower()
    return next((value for key, value in INCIDENT_TYPES.items() if key in lowered), None)


def normalize_talkgroup(talkgroup_id: int, configured_name: str) -> str:
    return f"{talkgroup_id}:{configured_name.strip().upper()}"


def normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)
