from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from scotus_guide.archive import ManifestStore
from scotus_guide.historical import (
    HistoricalDocumentCandidate,
    apply_historical_recovery,
    build_historical_recovery_plan,
    plan_historical_recovery,
    validate_historical_recovery_plan,
)
from scotus_guide.models import (
    CaseAssociation,
    CaseDates,
    CaseDocumentReference,
    DocumentManifest,
    DocumentManifestEntry,
    DocumentType,
    Lifecycle,
    MetadataProvenance,
    NormalizedCase,
    SourceIdentity,
)


def _blob(number: int) -> bytes:
    return f"historical-pdf-{number}".encode()


def _hash(number: int) -> str:
    return hashlib.sha256(_blob(number)).hexdigest()


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
        byte_size=len(_blob(number)),
        document_type=DocumentType.UNKNOWN,
        sources=[SourceIdentity(import_path=f"corpus/{group}/opinion/source-{number}.pdf")],
        retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
        cases=[CaseAssociation(historical_group=group)],
    )


def _write_archive(root: Path, *entries: DocumentManifestEntry) -> None:
    for entry in entries:
        destination = root / entry.archive_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        number = int(entry.sources[0].import_path.rsplit("-", 1)[1].removesuffix(".pdf"))
        destination.write_bytes(_blob(number))


def _extractor(
    *candidates: HistoricalDocumentCandidate,
) -> Callable[..., HistoricalDocumentCandidate]:
    by_hash = {candidate.document_hash: candidate for candidate in candidates}

    def extract(
        entry: DocumentManifestEntry, _root: Path, **_limits: int
    ) -> HistoricalDocumentCandidate:
        return by_hash[entry.sha256]

    return extract


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

    assert len(plan.components) == 2
    assert all(component.proposed_case is None for component in plan.components)
    assert {conflict.code for conflict in plan.conflicts} == {"duplicate-target-case"}
    assert report.assigned_document_hashes == []
    assert report.unresolved_document_hashes == [_hash(1), _hash(2)]


def test_application_splits_unresolved_record_and_repeat_is_idempotent(tmp_path: Path) -> None:
    manifest = DocumentManifest(documents=[_entry(1, "legacy"), _entry(2, "legacy")])
    store = ManifestStore(tmp_path / "manifests" / "documents.json")
    store.save(manifest)
    _write_archive(tmp_path, *manifest.documents)
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
    first = apply_historical_recovery(
        tmp_path,
        store,
        plan,
        report_path=tmp_path / "reports" / "historical.json",
        candidate_extractor=_extractor(candidate),
    )

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
    second = apply_historical_recovery(
        tmp_path,
        store,
        second_plan,
        report_path=tmp_path / "reports" / "historical.json",
        candidate_extractor=_extractor(candidate),
    )

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
        apply_historical_recovery(
            tmp_path,
            store,
            plan,
            report_path=tmp_path / "report.json",
            candidate_extractor=_extractor(_candidate(1, "20-1", "legacy")),
        )
    assert not (tmp_path / "data" / "cases" / "2020-20-1.json").exists()


def test_missing_caption_joins_captioned_peer_with_same_group_and_docket() -> None:
    manifest = DocumentManifest(documents=[_entry(1, "one"), _entry(2, "one")])
    captioned = _candidate(1, "20-1", "one")
    missing = _candidate(2, "20-1", "one").model_copy(
        update={"title": None, "parties": []}
    )

    plan, _ = build_historical_recovery_plan(manifest, [captioned, missing])

    proposed = [item for item in plan.components if item.proposed_case is not None]
    assert len(proposed) == 1
    assert proposed[0].document_hashes == [_hash(1), _hash(2)]


def test_extracted_existing_title_can_be_corrected() -> None:
    manifest = DocumentManifest(documents=[_entry(1, "one")])
    recovered = NormalizedCase(
        case_id="2020-20-1",
        slug="2020-20-1",
        title="CAPACITY AS DIRECTOR v. BETA",
        term=2020,
        docket_numbers=["20-1"],
        primary_docket="20-1",
        lifecycle=Lifecycle.DECIDED,
        dates=CaseDates(decision=date(2021, 5, 1)),
        provenance=[
            MetadataProvenance(
                field="title",
                document_hash=_hash(1),
                method="extracted",
            )
        ],
        documents=[CaseDocumentReference(sha256=_hash(1), document_type=DocumentType.OPINION)],
    )

    plan, _ = build_historical_recovery_plan(
        manifest, [_candidate(1, "20-1", "one", title="Alpha v. Beta")], [recovered]
    )

    proposed = next(item.proposed_case for item in plan.components if item.proposed_case)
    assert proposed.title == "Alpha v. Beta"


def test_transitive_docket_bridge_does_not_merge_incompatible_captions() -> None:
    manifest = DocumentManifest(
        documents=[_entry(1, "one"), _entry(2, "bridge"), _entry(3, "two")]
    )
    bridge = _candidate(
        2,
        "20-1",
        "bridge",
        title="Alpha Group Coalition v. Beta Board Council",
    ).model_copy(update={"docket_numbers": ["20-1", "20-2"]})

    plan, _ = build_historical_recovery_plan(
        manifest,
        [
            _candidate(1, "20-1", "one", title="Alpha Group v. Beta Board"),
            bridge,
            _candidate(3, "20-2", "two", title="Coalition v. Council"),
        ],
    )

    assert len(plan.components) == 2
    assert sorted(len(component.document_hashes) for component in plan.components) == [1, 2]
    assert all(component.proposed_case is None for component in plan.components)
    assert "overlapping-target-docket" in {conflict.code for conflict in plan.conflicts}


def test_curated_docket_match_wins_over_unresolved_holder() -> None:
    curated = NormalizedCase(
        case_id="curated",
        slug="curated",
        title="Alpha v. Beta",
        term=2020,
        docket_numbers=["20-1"],
        primary_docket="20-1",
        lifecycle=Lifecycle.DECIDED,
        dates=CaseDates(decision=date(2021, 5, 1)),
    )
    unresolved = NormalizedCase(
        case_id="unresolved-source",
        slug="unresolved-source",
        title="Unresolved historical group source",
        docket_numbers=["20-1"],
        primary_docket="20-1",
        lifecycle=Lifecycle.UNRESOLVED,
        unresolved_group="source",
        documents=[CaseDocumentReference(sha256=_hash(1), document_type=DocumentType.UNKNOWN)],
    )
    manifest = DocumentManifest(documents=[_entry(1, "source")])

    plan, _ = build_historical_recovery_plan(
        manifest,
        [_candidate(1, "20-1", "source")],
        [curated, unresolved],
    )

    assert plan.components[0].target_case_id == "curated"
    assert plan.components[0].proposed_case is not None


def test_local_caption_conflict_does_not_abort_unrelated_component(tmp_path: Path) -> None:
    manifest = DocumentManifest(
        documents=[_entry(1, "bad-a"), _entry(2, "bad-b"), _entry(3, "good")]
    )
    candidates = [
        _candidate(1, "20-1", "bad-a", title="Alpha v. Beta"),
        _candidate(2, "20-1", "bad-b", title="Gamma v. Delta"),
        _candidate(3, "20-2", "good", title="Useful v. Valid"),
    ]
    plan, report = build_historical_recovery_plan(manifest, candidates)

    assert report.assigned_document_hashes == [_hash(3)]
    assert report.unresolved_document_hashes == [_hash(1), _hash(2)]

    store = ManifestStore(tmp_path / "manifests" / "documents.json")
    store.save(manifest)
    _write_archive(tmp_path, *manifest.documents)
    applied = apply_historical_recovery(
        tmp_path,
        store,
        plan,
        report_path=tmp_path / "report.json",
        candidate_extractor=_extractor(*candidates),
    )
    assert applied.cases_written == ["2020-20-2"]
    assert store.load().documents[0].document_type is DocumentType.UNKNOWN
    assert store.load().documents[2].document_type is DocumentType.OPINION


def test_curated_document_ownership_blocks_reassignment() -> None:
    curated = NormalizedCase(
        case_id="curated",
        slug="curated",
        title="Alpha v. Beta",
        term=2020,
        docket_numbers=["20-9"],
        primary_docket="20-9",
        lifecycle=Lifecycle.DECIDED,
        dates=CaseDates(decision=date(2021, 5, 1)),
        documents=[CaseDocumentReference(sha256=_hash(1), document_type=DocumentType.OPINION)],
    )
    manifest = DocumentManifest(documents=[_entry(1, "legacy")])

    plan, report = build_historical_recovery_plan(
        manifest,
        [_candidate(1, "20-1", "legacy")],
        [curated],
    )

    assert plan.components[0].proposed_case is None
    assert "curated-document-ownership" in {
        conflict.code for conflict in plan.components[0].conflicts
    }
    assert report.assigned_document_hashes == []


def test_apply_requires_blob_and_reextracted_candidate_match(tmp_path: Path) -> None:
    entry = _entry(1, "legacy")
    manifest = DocumentManifest(documents=[entry])
    store = ManifestStore(tmp_path / "manifests" / "documents.json")
    store.save(manifest)
    original = _candidate(1, "20-1", "legacy")
    plan, _ = build_historical_recovery_plan(manifest, [original])

    with pytest.raises(ValueError, match="archived document is missing"):
        apply_historical_recovery(
            tmp_path,
            store,
            plan,
            report_path=tmp_path / "report.json",
            candidate_extractor=_extractor(original),
        )

    archive_path = tmp_path / entry.archive_path
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="archived document precondition"):
        apply_historical_recovery(
            tmp_path,
            store,
            plan,
            report_path=tmp_path / "report.json",
            candidate_extractor=_extractor(original),
        )

    _write_archive(tmp_path, entry)
    tampered = original.model_copy(update={"title": "Tampered v. Caption"})
    tampered_plan, _ = build_historical_recovery_plan(manifest, [tampered])
    with pytest.raises(ValueError, match="re-extracted"):
        apply_historical_recovery(
            tmp_path,
            store,
            tampered_plan,
            report_path=tmp_path / "report.json",
            candidate_extractor=_extractor(original),
        )


def test_unassigned_candidate_does_not_change_manifest_type(tmp_path: Path) -> None:
    entry = _entry(1, "legacy")
    manifest = DocumentManifest(documents=[entry])
    store = ManifestStore(tmp_path / "manifests" / "documents.json")
    store.save(manifest)
    _write_archive(tmp_path, entry)
    blocked = _candidate(1, "20-1", "legacy").model_copy(
        update={"ambiguous": True, "lifecycle": Lifecycle.UNRESOLVED}
    )
    plan, _ = build_historical_recovery_plan(manifest, [blocked])

    report = apply_historical_recovery(
        tmp_path,
        store,
        plan,
        report_path=tmp_path / "report.json",
        candidate_extractor=_extractor(blocked),
    )

    assert report.no_op is True
    assert store.load().documents[0].document_type is DocumentType.UNKNOWN
    assert (tmp_path / "report.json").is_file()


def test_association_must_reference_document_in_case(tmp_path: Path) -> None:
    entry = _entry(1, "legacy").model_copy(
        update={"cases": [CaseAssociation(case_id="curated", docket_numbers=["20-1"])]}
    )
    manifest = DocumentManifest(documents=[entry])
    store = ManifestStore(tmp_path / "manifests" / "documents.json")
    store.save(manifest)
    curated = NormalizedCase(
        case_id="curated",
        slug="curated",
        title="Other v. Case",
        docket_numbers=["20-1"],
        primary_docket="20-1",
        lifecycle=Lifecycle.UNRESOLVED,
    )
    _write_case(tmp_path, curated)
    plan, _ = build_historical_recovery_plan(manifest, [], [curated])

    with pytest.raises(ValueError, match="absent from case"):
        validate_historical_recovery_plan(tmp_path, store, plan)


def test_scan_skips_fixed_ten_paths_without_conflict(tmp_path: Path) -> None:
    entry = _entry(1, "legacy").model_copy(
        update={
            "sources": [SourceIdentity(import_path="fixed-ten/2025-26A124/opinion.pdf")],
            "cases": [],
        }
    )
    store = ManifestStore(tmp_path / "manifests" / "documents.json")
    store.save(DocumentManifest(documents=[entry]))

    plan, report = plan_historical_recovery(tmp_path, store)

    assert plan.candidates == []
    assert report.documents_examined == 0
    assert report.conflicts == []


@pytest.mark.parametrize(
    "failure_point",
    ["prepared", "write-0", "write-1", "write-2", "committed"],
)
def test_transaction_failure_is_recoverable_at_every_boundary(
    tmp_path: Path, failure_point: str
) -> None:
    entry = _entry(1, "legacy")
    manifest = DocumentManifest(documents=[entry])
    store = ManifestStore(tmp_path / "manifests" / "documents.json")
    store.save(manifest)
    _write_archive(tmp_path, entry)
    candidate = _candidate(1, "20-1", "legacy")
    plan, _ = build_historical_recovery_plan(manifest, [candidate])
    original_manifest = store.path.read_bytes()

    def fail(point: str) -> None:
        if point == failure_point:
            raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected"):
        apply_historical_recovery(
            tmp_path,
            store,
            plan,
            report_path=tmp_path / "reports" / "historical.json",
            candidate_extractor=_extractor(candidate),
            failure_injector=fail,
        )

    recovered_case = tmp_path / "data" / "cases" / "2020-20-1.json"
    if failure_point == "committed":
        assert store.path.read_bytes() != original_manifest
        assert recovered_case.is_file()
    else:
        assert store.path.read_bytes() == original_manifest
        assert not recovered_case.exists()
    assert not (tmp_path / ".historical-recovery" / "journal.json").exists()


def test_startup_rolls_back_durable_prepared_journal(tmp_path: Path) -> None:
    entry = _entry(1, "legacy")
    manifest = DocumentManifest(documents=[entry])
    store = ManifestStore(tmp_path / "manifests" / "documents.json")
    store.save(manifest)
    plan, _ = build_historical_recovery_plan(manifest, [])
    original_manifest = store.path.read_bytes()

    transaction_dir = tmp_path / ".historical-recovery"
    stage_dir = transaction_dir / "staging"
    stage_dir.mkdir(parents=True)
    backup = stage_dir / "0.backup"
    backup.write_bytes(original_manifest)
    store.save(DocumentManifest())
    journal = {
        "phase": "prepared",
        "entries": [
            {
                "destination": str(store.path.resolve()),
                "backup": str(backup),
                "desired": None,
            }
        ],
    }
    (transaction_dir / "journal.json").write_text(json.dumps(journal), encoding="utf-8")

    apply_historical_recovery(
        tmp_path,
        store,
        plan,
        report_path=tmp_path / "report.json",
    )

    assert store.path.read_bytes() == original_manifest
    assert not (transaction_dir / "journal.json").exists()


def test_apply_rejects_protected_report_path(tmp_path: Path) -> None:
    entry = _entry(1, "legacy")
    manifest = DocumentManifest(documents=[entry])
    store = ManifestStore(tmp_path / "manifests" / "documents.json")
    store.save(manifest)
    plan, _ = build_historical_recovery_plan(manifest, [])

    with pytest.raises(ValueError, match="protected repository data"):
        apply_historical_recovery(
            tmp_path,
            store,
            plan,
            report_path=tmp_path / entry.archive_path,
        )


def test_apply_rejects_missing_report_path(tmp_path: Path) -> None:
    manifest = DocumentManifest()
    store = ManifestStore(tmp_path / "manifest.json")
    store.save(manifest)
    plan, _ = build_historical_recovery_plan(manifest, [])

    with pytest.raises(ValueError, match="report path"):
        apply_historical_recovery(tmp_path, store, plan, report_path=None)
