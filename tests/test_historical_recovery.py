from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from scotus_guide.archive import ManifestStore
from scotus_guide.historical import (
    HistoricalDocumentCandidate,
    apply_historical_recovery,
    build_historical_recovery_plan,
)
from scotus_guide.models import (
    CaseAssociation,
    CaseDates,
    CaseDocumentReference,
    DocumentManifest,
    DocumentManifestEntry,
    DocumentType,
    Lifecycle,
    NormalizedCase,
    SourceIdentity,
)


def _hash(number: int) -> str:
    return f"{number:064x}"


def _candidate(
    number: int,
    docket: str,
    group: str,
    *,
    title: str = "Alpha v. Beta",
    term: int = 2020,
    document_type: DocumentType = DocumentType.OPINION,
) -> HistoricalDocumentCandidate:
    return HistoricalDocumentCandidate(
        document_hash=_hash(number),
        import_path=f"corpus/{group}/opinion/source-{number}.pdf",
        document_type=document_type,
        historical_groups=[group],
        docket_numbers=[docket],
        docket_labels=[f"No. {docket}"],
        title=title,
        term=term,
        dates=CaseDates(decision=date(term + 1, 5, 1)),
        lifecycle=Lifecycle.DECIDED,
        confidence=1,
    )


def _entry(number: int, group: str) -> DocumentManifestEntry:
    document_hash = _hash(number)
    return DocumentManifestEntry(
        sha256=document_hash,
        archive_path=f"documents/{document_hash[:2]}/{document_hash}.pdf",
        byte_size=10,
        document_type=DocumentType.UNKNOWN,
        sources=[SourceIdentity(import_path=f"corpus/{group}/opinion/source-{number}.pdf")],
        retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
        cases=[CaseAssociation(historical_group=group)],
    )


def _write_case(root: Path, case: NormalizedCase) -> None:
    destination = root / "data" / "cases" / f"{case.case_id}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(case.model_dump_json(indent=2) + "\n")


def test_polluted_group_is_split_by_exact_docket_overlap() -> None:
    manifest = DocumentManifest(documents=[_entry(1, "polluted"), _entry(2, "polluted")])
    plan, report = build_historical_recovery_plan(
        manifest,
        [_candidate(1, "20-1", "polluted"), _candidate(2, "20-2", "polluted")],
    )

    assert len(plan.components) == 2
    assert sorted(component.docket_numbers for component in plan.components) == [
        ["20-1"],
        ["20-2"],
    ]
    assert len(report.split_groups["polluted"]) == 2


def test_same_docket_across_groups_is_one_merged_component() -> None:
    manifest = DocumentManifest(documents=[_entry(1, "first"), _entry(2, "second")])
    plan, report = build_historical_recovery_plan(
        manifest,
        [_candidate(1, "20-1", "first"), _candidate(2, "20-1", "second")],
    )

    assert len(plan.components) == 1
    component = plan.components[0]
    assert component.document_hashes == [_hash(1), _hash(2)]
    assert component.historical_groups == ["first", "second"]
    assert report.merged_groups[component.component_id] == ["first", "second"]


def test_curated_case_matches_any_docket_and_stronger_fields_win() -> None:
    curated = NormalizedCase(
        case_id="curated-alpha",
        slug="curated-alpha",
        title="Curated Alpha v. Beta",
        term=2019,
        docket_numbers=["19-1", "20-1"],
        primary_docket="19-1",
        lifecycle=Lifecycle.ARGUED,
        dates=CaseDates(argument=date(2020, 1, 1)),
        documents=[
            CaseDocumentReference(
                sha256=_hash(1), document_type=DocumentType.UNKNOWN, current=False
            )
        ],
    )
    manifest = DocumentManifest(documents=[_entry(1, "legacy"), _entry(2, "other")])
    plan, _ = build_historical_recovery_plan(
        manifest,
        [
            _candidate(1, "20-1", "legacy", title="Weaker v. Caption", term=2020),
            _candidate(2, "19-1", "other", title="Weaker v. Caption", term=2020),
        ],
        [curated],
    )

    assert len(plan.components) == 1
    component = plan.components[0]
    assert component.target_case_id == "curated-alpha"
    assert component.proposed_case is not None
    assert component.proposed_case.title == curated.title
    assert component.proposed_case.term == curated.term
    assert component.proposed_case.primary_docket == curated.primary_docket
    assert [item.sha256 for item in component.proposed_case.documents] == [_hash(1), _hash(2)]
    assert component.proposed_case.documents[0].current is False
    assert {conflict.code for conflict in component.conflicts} == {
        "existing-metadata-precedence"
    }


def test_conflicting_primary_source_titles_remain_unresolved() -> None:
    manifest = DocumentManifest(documents=[_entry(1, "first"), _entry(2, "second")])
    plan, report = build_historical_recovery_plan(
        manifest,
        [
            _candidate(1, "20-1", "first", title="Alpha v. Beta"),
            _candidate(2, "20-1", "second", title="Gamma v. Delta"),
        ],
    )

    component = plan.components[0]
    assert component.proposed_case is None
    assert any(conflict.field == "title" for conflict in component.conflicts)
    assert report.assigned_document_hashes == []
    assert report.unresolved_document_hashes == [_hash(1), _hash(2)]


def test_application_splits_unresolved_record_and_repeat_is_idempotent(tmp_path: Path) -> None:
    manifest = DocumentManifest(documents=[_entry(1, "legacy"), _entry(2, "legacy")])
    store = ManifestStore(tmp_path / "manifests" / "documents.json")
    store.save(manifest)
    unresolved = NormalizedCase(
        case_id="unresolved-legacy",
        slug="unresolved-legacy",
        title="Unresolved historical group legacy",
        lifecycle=Lifecycle.UNRESOLVED,
        unresolved_group="legacy",
        documents=[
            CaseDocumentReference(sha256=_hash(1), document_type=DocumentType.UNKNOWN),
            CaseDocumentReference(sha256=_hash(2), document_type=DocumentType.UNKNOWN),
        ],
    )
    _write_case(tmp_path, unresolved)
    candidate = _candidate(1, "20-1", "legacy")

    plan, _ = build_historical_recovery_plan(manifest, [candidate], [unresolved])
    first = apply_historical_recovery(tmp_path, store, plan)

    assert first.no_op is False
    assert first.unresolved_cases_reduced == ["unresolved-legacy"]
    recovered_path = tmp_path / "data" / "cases" / "2020-20-1.json"
    recovered = NormalizedCase.model_validate_json(recovered_path.read_text())
    assert [item.sha256 for item in recovered.documents] == [_hash(1)]
    reduced = NormalizedCase.model_validate_json(
        (tmp_path / "data" / "cases" / "unresolved-legacy.json").read_text()
    )
    assert [item.sha256 for item in reduced.documents] == [_hash(2)]
    applied_entry = store.load().documents[0]
    assert applied_entry.document_type is DocumentType.OPINION
    assert applied_entry.cases[0].historical_group == "legacy"
    assert applied_entry.cases[0].case_id == recovered.case_id

    current_cases = [
        NormalizedCase.model_validate_json(path.read_text())
        for path in sorted((tmp_path / "data" / "cases").glob("*.json"))
    ]
    second_plan, _ = build_historical_recovery_plan(store.load(), [candidate], current_cases)
    second = apply_historical_recovery(tmp_path, store, second_plan)

    assert second.no_op is True
    assert second.cases_written == []
    assert second.manifest_changed is False


def test_stale_plan_fails_before_writes(tmp_path: Path) -> None:
    manifest = DocumentManifest(documents=[_entry(1, "legacy")])
    store = ManifestStore(tmp_path / "manifests" / "documents.json")
    store.save(manifest)
    plan, _ = build_historical_recovery_plan(manifest, [_candidate(1, "20-1", "legacy")])
    changed = manifest.model_copy(
        update={"documents": [manifest.documents[0].model_copy(update={"byte_size": 11})]}
    )
    store.save(changed)

    with pytest.raises(ValueError, match="manifest precondition"):
        apply_historical_recovery(tmp_path, store, plan)
    assert not (tmp_path / "data" / "cases" / "2020-20-1.json").exists()
