from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ragchew.scotus.legacy_export import (
    LegacyExportError,
    export_legacy_bootstrap,
    sanitize_legacy_projection,
)
from ragchew.scotus.public_contracts import ScotusPublicProjection
from ragchew.scotus.static_state import StaticStateStore

NOW = datetime(2026, 8, 28, 3, 17, tzinfo=UTC)


def fixture_projection(name: str = "one-case") -> dict[str, object]:
    payload = json.loads(Path(f"tests/fixtures/static/{name}.json").read_text())
    return payload["projection"]  # type: ignore[no-any-return]


def test_legacy_sanitizer_removes_only_source_link_claim_ids() -> None:
    payload = fixture_projection()
    case = payload["cases"][0]  # type: ignore[index]
    link = case["title_sources"][0]  # type: ignore[index]
    link["claim_ids"] = ["00000000-0000-0000-0000-000000000001"]
    projection = sanitize_legacy_projection(payload)
    serialized = projection.model_dump_json()
    assert "claim_ids" not in serialized
    assert "00000000-0000-0000-0000-000000000001" not in serialized


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transcript_text", "private source words"),
        ("prompt", "private prompt"),
        ("object_key", "private/object"),
        ("credential", "not-for-public"),
        ("document_id", "00000000-0000-0000-0000-000000000001"),
    ],
)
def test_legacy_sanitizer_fails_on_every_other_forbidden_field(
    field: str, value: str
) -> None:
    payload = fixture_projection()
    payload[field] = value
    with pytest.raises(ValueError, match="forbidden public field"):
        sanitize_legacy_projection(payload)


def test_export_writes_only_canonical_sanitized_versioned_state(tmp_path: Path) -> None:
    projection = sanitize_legacy_projection(fixture_projection())
    destination = tmp_path / "bootstrap"
    export_legacy_bootstrap(
        (projection,),
        destination,
        source_commit="a" * 40,
        config_sha256="b" * 64,
        build_epoch=NOW,
    )
    loaded = StaticStateStore(destination).load()
    assert loaded.projection == projection
    assert loaded.release is not None
    assert loaded.release.case_count == 1
    paths = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    assert paths == {
        "release/v1/release.json",
        "snapshot/v1/cases/2025-25-466/revisions/1.json",
        "snapshot/v1/projection.json",
        "state/v1/cost-ledger.json",
        "state/v1/publication.json",
    }
    all_bytes = b"\n".join(path.read_bytes() for path in destination.rglob("*.json"))
    for forbidden in (b"claim_id", b"document_id", b"transcript_text", b"%PDF-", b"prompt"):
        assert forbidden not in all_bytes


def test_empty_bootstrap_is_complete_and_versioned(tmp_path: Path) -> None:
    empty = ScotusPublicProjection(watermark=NOW, generated_at=NOW, cases=())
    destination = tmp_path / "empty"
    export_legacy_bootstrap(
        (empty,),
        destination,
        source_commit="a" * 40,
        config_sha256="b" * 64,
        build_epoch=NOW,
        tool_version="empty-bootstrap-v1",
    )
    loaded = StaticStateStore(destination).load()
    assert loaded.projection == empty
    assert loaded.release is not None and loaded.release.case_count == 0


def test_export_preserves_complete_public_revision_history(tmp_path: Path) -> None:
    corrected = ScotusPublicProjection.model_validate(fixture_projection("correction"))
    current_case = corrected.cases[0]
    first_summary = current_case.revisions[0]
    prior_case = current_case.model_copy(
        update={
            "maturity": first_summary.maturity,
            "revisions": (first_summary,),
            "updated_at": first_summary.created_at,
        }
    )
    prior = corrected.model_copy(update={"cases": (prior_case,)})
    destination = tmp_path / "with-history"
    export_legacy_bootstrap(
        (prior, corrected),
        destination,
        source_commit="a" * 40,
        config_sha256="b" * 64,
        build_epoch=NOW,
    )
    loaded = StaticStateStore(destination).load()
    assert loaded.projection == corrected
    case_key = loaded.publication.cases[0].case_key
    assert loaded.publication.cases[0].active_revision == 2
    assert loaded.revisions[(case_key, 1)].record.case.revisions == (first_summary,)
    assert loaded.revisions[(case_key, 2)].record.case == current_case


def test_export_refuses_to_invent_missing_historical_revision_content() -> None:
    corrected = ScotusPublicProjection.model_validate(fixture_projection("correction"))
    with pytest.raises(LegacyExportError, match="missing public case revision"):
        export_legacy_bootstrap(
            (corrected,),
            Path("must-not-be-created"),
            source_commit="a" * 40,
            config_sha256="b" * 64,
            build_epoch=NOW,
        )
