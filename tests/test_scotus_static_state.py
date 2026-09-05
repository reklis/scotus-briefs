from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from ragchew.scotus.contracts import BriefMaturity, ScotusCaseStatus
from ragchew.scotus.public_contracts import (
    PublicArgumentAnalysis,
    PublicBriefRevisionSummary,
    PublicBriefSection,
    PublicCaseBrief,
    PublicCaseHistoryEvent,
    PublicSourceLink,
    ScotusPublicProjection,
    public_case_key,
    public_case_slug,
)
from ragchew.scotus.static_contracts import (
    CaseRevisionPointer,
    ConditionalValidators,
    CostLedger,
    ModelAttemptOutcome,
    ModelAttemptReceipt,
    PendingReason,
    PendingWork,
    PublicationState,
    ReleaseManifest,
    assert_public_payload,
    canonical_json_bytes,
    contract_digest,
    model_input_fingerprint,
    sha256_hex,
)
from ragchew.scotus.static_state import (
    CompareAndSwapConflict,
    GeneratedContent,
    ReconciliationChoice,
    StaticStateError,
    StaticStateStore,
    generated_public_content_digest,
    reconcile_release_ids,
)

NOW = datetime(2026, 8, 28, 3, 17, tzinfo=UTC)
ZERO = "0" * 64
ONE = "1" * 64


def source() -> PublicSourceLink:
    return PublicSourceLink(
        evidence_type="Official transcript",
        label="Official Supreme Court transcript — file page 5",
        official_url="https://www.supremecourt.gov/oral_arguments/argument_transcripts/2025/25-466.pdf",
        page_label="file page 5",
    )


def case(*, revision: int = 1, caption: str = "Synthetic Example v. Agency") -> PublicCaseBrief:
    slug = public_case_slug("2025", "25-466", caption)
    revisions = tuple(
        PublicBriefRevisionSummary(
            revision_number=number,
            maturity=(BriefMaturity.CORRECTED if number > 1 else BriefMaturity.OFFICIAL_TRANSCRIPT),
            created_at=NOW + timedelta(hours=number - 1),
            correction_note=("Corrected from revised synthetic source." if number > 1 else None),
        )
        for number in range(1, revision + 1)
    )
    return PublicCaseBrief(
        slug=slug,
        term="2025",
        primary_docket="25-466",
        caption=caption,
        argument_date=NOW,
        latest_court_document_date=NOW,
        case_status=(ScotusCaseStatus.CORRECTED if revision > 1 else ScotusCaseStatus.ARGUED),
        maturity=(BriefMaturity.CORRECTED if revision > 1 else BriefMaturity.OFFICIAL_TRANSCRIPT),
        title="A synthetic question",
        dek="This synthetic fixture contains no Court text.",
        title_sources=(source(),),
        dek_sources=(source(),),
        sections=(
            PublicBriefSection(
                heading="Synthetic overview",
                paragraphs=("This is invented test content.",),
                sources=(source(),),
            ),
        ),
        arguments=(
            PublicArgumentAnalysis(
                sequence=1,
                argument_date=NOW,
                heading="Synthetic argument",
                paragraphs=("One invented point.", "A second invented point."),
                official_detail_url="https://www.supremecourt.gov/oral_arguments/audio/2025/25-466",
                official_transcript_url="https://www.supremecourt.gov/oral_arguments/argument_transcripts/2025/25-466.pdf",
                sources=(source(),),
            ),
        ),
        case_history=(
            PublicCaseHistoryEvent(
                status=ScotusCaseStatus.ARGUED,
                changed_at=NOW,
                explanation="Synthetic argument event.",
            ),
        ),
        official_detail_url="https://www.supremecourt.gov/oral_arguments/audio/2025/25-466",
        official_docket_url="https://www.supremecourt.gov/docket/docketfiles/html/public/25-466.html",
        revisions=revisions,
        updated_at=NOW + timedelta(hours=revision - 1),
        topics=("Synthetic law",),
    )


def with_release(
    content: GeneratedContent, release_id: str, previous: str | None
) -> GeneratedContent:
    assert content.projection is not None
    publication = content.publication.model_copy(update={"active_release_id": release_id})
    release = ReleaseManifest(
        release_id=release_id,
        previous_release_id=previous,
        source_commit="a" * 40,
        projection_sha256=sha256_hex(canonical_json_bytes(content.projection)),
        config_sha256=ZERO,
        tool_version="test-v1",
        generated_at=NOW,
        files=(),
        case_count=len(content.projection.cases),
        page_count=1,
    )
    return replace(content, publication=publication, release=release)


def test_public_contract_is_versioned_and_has_no_private_uuids() -> None:
    payload = json.loads(
        canonical_json_bytes(
            ScotusPublicProjection(watermark=NOW, generated_at=NOW, cases=(case(),))
        )
    )
    assert payload["schema_version"] == "1.1"
    assert payload["cases"][0]["schema_version"] == "1.1"
    serialized = json.dumps(payload).casefold()
    assert "claim_id" not in serialized
    assert "document_id" not in serialized
    assert "observation_id" not in serialized
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PublicSourceLink.model_validate({**source().model_dump(), "claim_ids": ["not-public"]})


def test_contracts_reject_unknown_schema_urls_digests_and_validators() -> None:
    with pytest.raises(ValidationError):
        PublicationState.model_validate({"schema_version": "2.0", "updated_at": NOW})
    with pytest.raises(ValidationError):
        CostLedger.model_validate({"updated_at": NOW, "revision": "1"})
    with pytest.raises(ValidationError):
        ConditionalValidators(etag="unquoted")
    with pytest.raises(ValidationError):
        ConditionalValidators(last_modified="yesterday")
    with pytest.raises(ValidationError):
        CaseRevisionPointer(
            case_key="wrong-key",
            term="2025",
            primary_docket="25-466",
            active_revision=1,
            active_slug=case().slug,
            active_case_sha256="bad",
        )
    with pytest.raises(ValidationError):
        PublicSourceLink.model_validate(
            {**source().model_dump(), "official_url": "https://example.test/source"}
        )
    assert public_case_key("2025", "25-466") == "2025-25-466"


def test_canonical_serialization_normalizes_utc_and_checks_privacy() -> None:
    state = PublicationState(updated_at=datetime(2026, 8, 27, 23, 17, tzinfo=UTC))
    first = canonical_json_bytes(state)
    second = canonical_json_bytes(PublicationState.model_validate_json(first))
    assert first == second
    assert first.endswith(b"\n") and b'"schema_version":"1.1"' in first
    with pytest.raises(ValueError, match="forbidden public field"):
        assert_public_payload({"nested": {"transcript_text": "private"}})
    with pytest.raises(ValueError, match="UUID"):
        assert_public_payload({"value": "00000000-0000-0000-0000-000000000000"})
    for forbidden_key in ("source_text", "private_text", "model_output", "response_body"):
        with pytest.raises(ValueError, match="forbidden public field"):
            assert_public_payload({forbidden_key: "not public"})


def test_model_fingerprint_and_cost_contract_are_opaque_and_bounded() -> None:
    fingerprint = model_input_fingerprint((ZERO, ONE), {"parser": "1", "prompt": "v2"})
    assert len(fingerprint) == 64
    blocked = ModelAttemptReceipt(
        input_fingerprint=fingerprint,
        stage="brief",
        outcome=ModelAttemptOutcome.BLOCKED,
        attempted_at=NOW,
        call_count=0,
        estimated_cost_usd=Decimal("0"),
    )
    assert CostLedger(updated_at=NOW, receipts=(blocked,)).receipts == (blocked,)
    with pytest.raises(ValidationError, match="exactly one"):
        ModelAttemptReceipt.model_validate(
            {**blocked.model_dump(), "outcome": ModelAttemptOutcome.ATTEMPTED}
        )


def test_state_store_round_trip_carries_revision_bytes_and_appends(tmp_path: Path) -> None:
    builder = StaticStateStore(tmp_path / "active")
    merged = builder.merge_accepted_case(
        GeneratedContent.empty(), case(), watermark=NOW, generated_at=NOW
    )
    first = with_release(merged, ZERO, None)
    first_path = builder.write_candidate(tmp_path / "first", first)
    loaded = StaticStateStore(first_path).load()
    first_bytes = loaded.revisions[("2025-25-466", 1)].serialized

    corrected = StaticStateStore(first_path).merge_accepted_case(
        loaded,
        case(revision=2, caption="Synthetic Example v. Renamed Agency"),
        watermark=NOW + timedelta(hours=1),
        generated_at=NOW + timedelta(hours=1),
    )
    second = with_release(corrected, ONE, ZERO)
    second_path = StaticStateStore(first_path).write_candidate(tmp_path / "second", second)
    reloaded = StaticStateStore(second_path).load()
    assert reloaded.revisions[("2025-25-466", 1)].serialized == first_bytes
    assert tuple(number for key, number in reloaded.revisions if key == "2025-25-466") == (1, 2)
    assert reloaded.publication.cases[0].legacy_slugs == (case().slug,)

    rewritten = case(revision=2).model_copy(
        update={
            "revisions": (
                case().revisions[0].model_copy(update={"correction_note": "rewritten"}),
                case(revision=2).revisions[1],
            )
        }
    )
    with pytest.raises(StaticStateError, match="immutable revision history"):
        StaticStateStore(first_path).merge_accepted_case(
            loaded,
            rewritten,
            watermark=NOW + timedelta(hours=1),
            generated_at=NOW + timedelta(hours=1),
        )


def test_projection_rejects_normalized_docket_collisions() -> None:
    first = case()
    collision = PublicCaseBrief.model_validate(
        {
            **first.model_dump(),
            "primary_docket": "25 466",
            "caption": "Different synthetic caption",
            "slug": public_case_slug("2025", "25 466", "Different synthetic caption"),
        }
    )
    with pytest.raises(ValidationError, match="duplicate case pages"):
        ScotusPublicProjection(watermark=NOW, generated_at=NOW, cases=(first, collision))


def test_store_refuses_incompatible_partial_and_interrupted_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    partial = tmp_path / "partial"
    path = partial / StaticStateStore.PUBLICATION_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical_json_bytes(PublicationState(updated_at=NOW)))
    with pytest.raises(StaticStateError, match="incomplete"):
        StaticStateStore(partial).load()

    builder = StaticStateStore(tmp_path / "active")
    content = with_release(
        builder.merge_accepted_case(
            GeneratedContent.empty(), case(), watermark=NOW, generated_at=NOW
        ),
        ZERO,
        None,
    )
    real_replace = os.replace

    def fail_replace(source_path: Path, destination_path: Path) -> None:
        if Path(destination_path) == tmp_path / "interrupted":
            raise OSError("synthetic interruption")
        real_replace(source_path, destination_path)

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="interruption"):
        builder.write_candidate(tmp_path / "interrupted", content)
    assert not (tmp_path / "interrupted").exists()
    assert not tuple(tmp_path.glob(".interrupted.*"))


def test_finalize_attaches_manifest_and_active_pointer_in_one_candidate_write(
    tmp_path: Path,
) -> None:
    store = StaticStateStore(tmp_path / "active")
    content = store.merge_accepted_case(
        GeneratedContent.empty(), case(), watermark=NOW, generated_at=NOW
    )
    manifest = with_release(content, ZERO, None).release
    assert manifest is not None
    destination = tmp_path / "candidate"
    finalized = store.finalize_candidate(destination, content, manifest)
    loaded = StaticStateStore(destination).load()
    assert loaded == finalized
    assert loaded.release == manifest
    assert loaded.publication.active_release_id == manifest.release_id


def test_store_rejects_complete_tree_extras_and_sanitizes_contract_errors(
    tmp_path: Path,
) -> None:
    builder = StaticStateStore(tmp_path / "unused")
    content = with_release(
        builder.merge_accepted_case(
            GeneratedContent.empty(), case(), watermark=NOW, generated_at=NOW
        ),
        ZERO,
        None,
    )
    root = builder.write_candidate(tmp_path / "state", content)
    (root / "unexpected.txt").write_text("apparently harmless", encoding="utf-8")
    with pytest.raises(StaticStateError, match="non-contract"):
        StaticStateStore(root).load()

    (root / "unexpected.txt").unlink()
    publication_path = root / StaticStateStore.PUBLICATION_PATH
    payload = json.loads(publication_path.read_text(encoding="utf-8"))
    forbidden_token = "sk-" + "this-secret-must-not-appear-123456789"
    payload["unknown"] = forbidden_token
    publication_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(StaticStateError) as raised:
        StaticStateStore(root).load()
    assert forbidden_token not in str(raised.value)
    assert str(raised.value) == "invalid generated-content state"


def test_cost_and_release_compare_and_swap_conflicts(tmp_path: Path) -> None:
    builder = StaticStateStore(tmp_path / "empty")
    content = with_release(
        builder.merge_accepted_case(
            GeneratedContent.empty(), case(), watermark=NOW, generated_at=NOW
        ),
        ZERO,
        None,
    )
    root = builder.write_candidate(tmp_path / "active", content)
    store = StaticStateStore(root)
    expected_parent = generated_public_content_digest(store.load())
    candidate = with_release(content, ONE, ZERO)
    store.require_release_parent(
        candidate,
        expected_parent_release_id=ZERO,
        expected_parent_digest=expected_parent,
    )
    fingerprint = model_input_fingerprint((ZERO,), {"model": "fixture"})
    receipt = ModelAttemptReceipt(
        input_fingerprint=fingerprint,
        stage="extraction",
        outcome=ModelAttemptOutcome.SUCCEEDED,
        attempted_at=NOW,
        call_count=1,
        input_tokens=10,
        output_tokens=2,
        estimated_cost_usd=Decimal("0.01"),
    )
    expected = contract_digest(store.load().cost_ledger)
    updated = store.append_cost_receipt(receipt, expected_digest=expected)
    assert updated.revision == 1
    assert store.load().publication.active_release_id == ZERO
    with pytest.raises(CompareAndSwapConflict):
        store.append_cost_receipt(receipt, expected_digest=expected)

    # A receipts-only append is intentionally mergeable and does not invalidate the
    # build's public-parent token.
    store.require_release_parent(
        candidate,
        expected_parent_release_id=ZERO,
        expected_parent_digest=expected_parent,
    )
    publication_path = root / StaticStateStore.PUBLICATION_PATH
    changed_publication = store.load().publication.model_copy(
        update={"updated_at": NOW + timedelta(minutes=1)}
    )
    publication_path.write_bytes(canonical_json_bytes(changed_publication))
    with pytest.raises(CompareAndSwapConflict, match="public state"):
        store.require_release_parent(
            candidate,
            expected_parent_release_id=ZERO,
            expected_parent_digest=expected_parent,
        )

    wrong_parent = replace(
        candidate, release=candidate.release.model_copy(update={"previous_release_id": ONE})
    )
    with pytest.raises(CompareAndSwapConflict, match="parent"):
        store.require_release_parent(
            wrong_parent,
            expected_parent_release_id=ZERO,
            expected_parent_digest=generated_public_content_digest(store.load()),
        )


def test_static_fixtures_are_explicitly_synthetic_and_contract_valid() -> None:
    fixture_directory = Path("tests/fixtures/static")
    fixture_paths = sorted(fixture_directory.glob("*.json"))
    assert {path.stem for path in fixture_paths} == {
        "correction",
        "empty-bootstrap",
        "multiple-terms",
        "one-case",
        "pending-work",
        "prior-release",
        "reargument",
        "search",
    }
    for path in fixture_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["_fixture"]["synthetic"] is True
        serialized = path.read_text(encoding="utf-8").casefold()
        assert "claim_id" not in serialized
        assert "document_id" not in serialized
        if "projection" in payload:
            ScotusPublicProjection.model_validate(payload["projection"])
        if "publication_state" in payload:
            PublicationState.model_validate_json(json.dumps(payload["publication_state"]))
        if "release" in payload:
            ReleaseManifest.model_validate_json(json.dumps(payload["release"]))


def test_pending_work_is_sanitized_and_reconciliation_is_explicit() -> None:
    pending = PendingWork(
        case_key="2025-25-466",
        reason=PendingReason.SOURCE_UNAVAILABLE,
        attempts=1,
        first_seen_at=NOW,
    )
    assert "source_unavailable" in canonical_json_bytes(pending).decode()
    updated = StaticStateStore("unused").update_publication_state(
        GeneratedContent.empty(),
        updated_at=NOW,
        sources=(),
        documents=(),
        pending_work=(pending,),
        cursors=(),
        processor=None,
    )
    assert updated.publication.pending_work == (pending,)
    assert updated.publication.active_release_id is None
    assert (
        reconcile_release_ids(live_release_id=ZERO, branch_release_id=ZERO)
        is ReconciliationChoice.IN_SYNC
    )
    assert (
        reconcile_release_ids(
            live_release_id=ONE,
            branch_release_id=ZERO,
            validated_release_ids={ONE},
        )
        is ReconciliationChoice.PROMOTE_VALIDATED_LIVE
    )
    assert (
        reconcile_release_ids(live_release_id=ONE, branch_release_id=ZERO)
        is ReconciliationChoice.REDEPLOY_BRANCH_ACTIVE
    )
