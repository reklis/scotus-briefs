"""Bounded generation modes and durable accepted-case checkpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .archive import ManifestStore
from .evidence import EvidenceGenerationError, EvidenceGenerator
from .generation import GuideGenerationError, GuideGenerator
from .models import NormalizedCase, ValidationState
from .validation import validate_repository


class OperationMode(StrEnum):
    INCREMENTAL = "incremental"
    BACKFILL = "bounded-backfill"
    CASE = "case-regeneration"
    VALIDATE = "validation-only"

    @classmethod
    def parse(cls, value: str) -> OperationMode:
        aliases = {
            "backfill": cls.BACKFILL,
            "case": cls.CASE,
            "validate": cls.VALIDATE,
        }
        try:
            return aliases[value] if value in aliases else cls(value)
        except ValueError as error:
            choices = "incremental, backfill, case, validate"
            raise ValueError(f"unsupported mode {value!r}; choose {choices}") from error


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: OperationMode
    completed_case_ids: list[str] = Field(default_factory=list)
    failed_cases: dict[str, str] = Field(default_factory=dict)
    updated_at: datetime


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: OperationMode
    selected_case_ids: list[str]
    completed_case_ids: list[str]
    skipped_case_ids: list[str]
    failures: dict[str, str]


class GenerationOrchestrator:
    def __init__(self, root: Path) -> None:
        self.root = root

    def select_cases(
        self,
        mode: OperationMode,
        *,
        batch_size: int,
        case_id: str | None = None,
    ) -> list[NormalizedCase]:
        if not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        cases = {
            case_id: case
            for case_id, case in self._cases().items()
            if case.unresolved_group is None and case.primary_docket is not None
        }
        if mode == OperationMode.VALIDATE:
            return []
        if mode == OperationMode.CASE:
            if not case_id:
                raise ValueError("case_id is required for case-regeneration")
            try:
                return [cases[case_id]]
            except KeyError as error:
                raise ValueError(f"unknown case_id: {case_id}") from error
        accepted = {item for item in cases if self._accepted(item)}
        checkpoint = self.load_checkpoint(mode)
        completed = set(checkpoint.completed_case_ids) | accepted
        if mode == OperationMode.BACKFILL:
            pending_ids = [item for item in sorted(cases) if item not in completed]
            # Let later cases make progress before retrying failures from the prior batch.
            pending_ids.sort(key=lambda item: item in checkpoint.failed_cases)
            candidates = [cases[item] for item in pending_ids]
        else:
            changed = self._changed_case_ids()
            candidates = [cases[item] for item in sorted(changed) if item in cases]
        return candidates[:batch_size]

    def unattempted_backfill_case_ids(self) -> list[str]:
        """Return docketed cases that have never completed or failed a backfill attempt."""
        cases = {
            case_id: case
            for case_id, case in self._cases().items()
            if case.unresolved_group is None and case.primary_docket is not None
        }
        checkpoint = self.load_checkpoint(OperationMode.BACKFILL)
        attempted = set(checkpoint.completed_case_ids) | set(checkpoint.failed_cases)
        accepted = {case_id for case_id in cases if self._accepted(case_id)}
        return sorted(set(cases) - attempted - accepted)

    def extract(
        self,
        mode: OperationMode,
        generator: EvidenceGenerator,
        *,
        batch_size: int,
        case_id: str | None = None,
    ) -> RunSummary:
        cases = self.select_cases(mode, batch_size=batch_size, case_id=case_id)
        manifest = ManifestStore(self.root / "manifests" / "documents.json").load()
        entries = {item.sha256: item for item in manifest.documents}
        failures: dict[str, str] = {}
        completed: list[str] = []
        skipped: list[str] = []
        for case in cases:
            processed = False
            for reference in case.documents:
                if not reference.current:
                    continue
                entry = entries.get(reference.sha256)
                if entry is None:
                    failures[case.case_id] = f"manifest lacks document {reference.sha256}"
                    continue
                try:
                    generator.generate_document(
                        case,
                        entry,
                        force=mode == OperationMode.CASE,
                    )
                    processed = True
                except EvidenceGenerationError as error:
                    failures[case.case_id] = str(error)
            if processed and case.case_id not in failures:
                completed.append(case.case_id)
            elif not case.documents:
                skipped.append(case.case_id)
        summary = RunSummary(
            mode=mode,
            selected_case_ids=[item.case_id for item in cases],
            completed_case_ids=completed,
            skipped_case_ids=skipped,
            failures=failures,
        )
        self._report("extraction", summary)
        return summary

    def generate(
        self,
        mode: OperationMode,
        generator: GuideGenerator,
        *,
        batch_size: int,
        case_id: str | None = None,
    ) -> RunSummary:
        cases = self.select_cases(mode, batch_size=batch_size, case_id=case_id)
        manifest = ManifestStore(self.root / "manifests" / "documents.json").load()
        failures: dict[str, str] = {}
        completed: list[str] = []
        skipped: list[str] = []
        for case in cases:
            try:
                accepted = generator.generate_case(case, manifest)
                if accepted and accepted.validation.state == ValidationState.ACCEPTED:
                    completed.append(case.case_id)
                else:
                    failures[case.case_id] = "candidate rejected; prior accepted guide retained"
            except GuideGenerationError as error:
                failures[case.case_id] = str(error)
        if cases:
            checkpoint = self.load_checkpoint(mode)
            merged = sorted(set(checkpoint.completed_case_ids) | set(completed))
            retained_failures = {
                **checkpoint.failed_cases,
                **failures,
            }
            for completed_case_id in completed:
                retained_failures.pop(completed_case_id, None)
            self.save_checkpoint(
                Checkpoint(
                    mode=mode,
                    completed_case_ids=merged,
                    failed_cases=retained_failures,
                    updated_at=datetime.now(UTC),
                )
            )
        summary = RunSummary(
            mode=mode,
            selected_case_ids=[item.case_id for item in cases],
            completed_case_ids=completed,
            skipped_case_ids=skipped,
            failures=failures,
        )
        self._report("generation", summary)
        return summary

    def validate(self) -> list[str]:
        errors = validate_repository(self.root)
        self._report_payload("validation", {"valid": not errors, "errors": errors})
        return errors

    def load_checkpoint(self, mode: OperationMode) -> Checkpoint:
        path = self._checkpoint_path(mode)
        if not path.exists():
            return Checkpoint(mode=mode, updated_at=datetime.now(UTC))
        return Checkpoint.model_validate_json(path.read_text())

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        from .evidence import _atomic_json

        _atomic_json(self._checkpoint_path(checkpoint.mode), checkpoint.model_dump(mode="json"))

    def _checkpoint_path(self, mode: OperationMode) -> Path:
        return self.root / "data" / "checkpoints" / f"{mode.value}.json"

    def _cases(self) -> dict[str, NormalizedCase]:
        result: dict[str, NormalizedCase] = {}
        for path in sorted((self.root / "data" / "cases").glob("*.json")):
            case = NormalizedCase.model_validate_json(path.read_text())
            result[case.case_id] = case
        return result

    def _accepted(self, case_id: str) -> bool:
        path = self.root / "data" / "guides" / f"{case_id}.json"
        if not path.exists():
            return False
        try:
            from .models import CitizenGuide

            return (
                CitizenGuide.model_validate_json(path.read_text()).validation.state
                == ValidationState.ACCEPTED
            )
        except ValueError:
            return False

    def _changed_case_ids(self) -> set[str]:
        result: set[str] = set()
        ingestion = self.root / "reports" / "ingestion.json"
        names = ("ingestion.json",) if ingestion.exists() else ("discovery.json", "reconcile.json")
        for name in names:
            path = self.root / "reports" / name
            try:
                payload = json.loads(path.read_text())
                changed = payload.get("changed_case_ids", [])
                if isinstance(changed, list):
                    result.update(item for item in changed if isinstance(item, str))
            except (OSError, ValueError, AttributeError):
                continue
        return result

    def _report(self, stage: str, summary: RunSummary) -> None:
        self._report_payload(stage, summary.model_dump(mode="json"))

    def _report_payload(self, stage: str, payload: object) -> None:
        from .evidence import _atomic_json

        _atomic_json(self.root / "reports" / f"{stage}.json", payload)
