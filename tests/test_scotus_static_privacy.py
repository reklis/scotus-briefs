from pathlib import Path

import pytest

from ragchew.scotus.static_validation import (
    StaticValidationError,
    scan_public_files,
)


@pytest.mark.parametrize(
    "private_marker",
    (
        "reader_guide_plan",
        "reader_claim",
        "section_packet",
        "argument_packet",
        "action_slot",
        "operative_object",
        "plain_language_guidance",
        "field_path",
        "diagnostic",
        "rejected_prose",
        "rejected_text",
        "repair_instruction",
        "detailed_diagnostic",
        "required_transformation",
        "offending_term",
    ),
)
def test_static_file_scanner_rejects_private_reader_and_repair_fields(
    tmp_path: Path, private_marker: str
) -> None:
    artifact = tmp_path / "candidate.log"
    artifact.write_text(f'{{"{private_marker}":"must remain private"}}', encoding="utf-8")

    with pytest.raises(StaticValidationError, match="forbidden private"):
        scan_public_files((artifact,))


def test_static_file_scanner_accepts_fixed_repair_codes(tmp_path: Path) -> None:
    artifact = tmp_path / "sanitized.log"
    artifact.write_text("reader_language_failed\nrepair_exhausted\n", encoding="utf-8")

    scan_public_files((artifact,))


def test_static_file_scanner_allows_ordinary_public_diagnostic_prose(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "case.json"
    artifact.write_text(
        '{"summary":"The Court discussed a diagnostic test and the word '
        '\\"diagnostic\\"."}',
        encoding="utf-8",
    )

    scan_public_files((artifact,))


@pytest.mark.parametrize(
    "private_payload",
    (
        "diagnostic=must remain private",
        "diagnostic: must remain private",
        "{'diagnostic': 'must remain private'}",
    ),
)
def test_static_file_scanner_rejects_private_diagnostic_log_forms(
    tmp_path: Path,
    private_payload: str,
) -> None:
    artifact = tmp_path / "candidate.log"
    artifact.write_text(private_payload, encoding="utf-8")

    with pytest.raises(StaticValidationError, match="forbidden private"):
        scan_public_files((artifact,))
