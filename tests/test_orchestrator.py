from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from scotus_guide.cli import main
from scotus_guide.models import Lifecycle, NormalizedCase
from scotus_guide.orchestrator import (
    Checkpoint,
    GenerationOrchestrator,
    OperationMode,
)


def write_case(root: Path, case_id: str, docket: str) -> None:
    case = NormalizedCase(
        case_id=case_id,
        slug=case_id,
        title=f"Case {docket}",
        term=2024,
        docket_numbers=[docket],
        primary_docket=docket,
        lifecycle=Lifecycle.PENDING,
    )
    path = root / "data" / "cases" / f"{case_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(case.model_dump_json())


def test_backfill_checkpoint_resumes_and_batch_is_bounded(tmp_path: Path) -> None:
    write_case(tmp_path, "scotus-24-7", "24-7")
    write_case(tmp_path, "scotus-24-8", "24-8")
    orchestrator = GenerationOrchestrator(tmp_path)
    orchestrator.save_checkpoint(
        Checkpoint(
            mode=OperationMode.BACKFILL,
            completed_case_ids=["scotus-24-7"],
            updated_at=datetime.now(UTC),
        )
    )
    selected = orchestrator.select_cases(OperationMode.BACKFILL, batch_size=1)
    assert [case.case_id for case in selected] == ["scotus-24-8"]
    loaded = orchestrator.load_checkpoint(OperationMode.BACKFILL)
    assert loaded.completed_case_ids == ["scotus-24-7"]


def test_modes_aliases_case_requirements_and_cli_validation(tmp_path: Path) -> None:
    assert OperationMode.parse("backfill") == OperationMode.BACKFILL
    assert OperationMode.parse("case") == OperationMode.CASE
    assert OperationMode.parse("validate") == OperationMode.VALIDATE
    with pytest.raises(ValueError, match="case_id"):
        GenerationOrchestrator(tmp_path).select_cases(OperationMode.CASE, batch_size=1)
    assert main(["run", "--mode", "validate", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "reports" / "repository-validation.json").exists()


def test_no_change_incremental_selects_no_work(tmp_path: Path) -> None:
    write_case(tmp_path, "scotus-24-7", "24-7")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "ingestion.json").write_text('{"changed_case_ids":[]}')

    selected = GenerationOrchestrator(tmp_path).select_cases(
        OperationMode.INCREMENTAL, batch_size=25
    )

    assert selected == []


def test_incremental_only_selects_changed_cases(tmp_path: Path) -> None:
    write_case(tmp_path, "scotus-24-7", "24-7")
    write_case(tmp_path, "scotus-24-8", "24-8")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "ingestion.json").write_text('{"changed_case_ids":["scotus-24-8"]}')
    selected = GenerationOrchestrator(tmp_path).select_cases(
        OperationMode.INCREMENTAL, batch_size=10
    )
    assert [case.case_id for case in selected] == ["scotus-24-8"]
