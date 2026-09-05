"""Atomic filesystem store for sanitized generated-content branch data."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ragchew.scotus.public_contracts import PublicCaseBrief, ScotusPublicProjection, public_case_key
from ragchew.scotus.static_contracts import (
    CostLedger,
    CursorState,
    DispositionDiscoveryState,
    LogicalDocumentState,
    LogicalSourceState,
    ModelAttemptReceipt,
    PendingWork,
    ProcessorFingerprint,
    PublicationState,
    PublicCaseRevisionRecord,
    ReleaseManifest,
    canonical_json_bytes,
    contract_digest,
    sha256_hex,
    validate_projection_payload,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class StaticStateError(RuntimeError):
    """Generated state is missing, invalid, private, or inconsistent."""


class CompareAndSwapConflict(StaticStateError):
    """The generated-content parent changed since the candidate was built."""


@dataclass(frozen=True)
class StoredCaseRevision:
    record: PublicCaseRevisionRecord
    serialized: bytes


@dataclass(frozen=True)
class GeneratedContent:
    projection: ScotusPublicProjection | None
    publication: PublicationState
    cost_ledger: CostLedger
    release: ReleaseManifest | None
    revisions: Mapping[tuple[str, int], StoredCaseRevision]

    @classmethod
    def empty(cls) -> GeneratedContent:
        return cls(
            projection=None,
            publication=PublicationState(updated_at=_EPOCH),
            cost_ledger=CostLedger(updated_at=_EPOCH),
            release=None,
            revisions={},
        )


class ReconciliationChoice(StrEnum):
    IN_SYNC = "in_sync"
    PROMOTE_VALIDATED_LIVE = "promote_validated_live"
    REDEPLOY_BRANCH_ACTIVE = "redeploy_branch_active"
    STOP_UNKNOWN_LIVE = "stop_unknown_live"


def _generated_content_digest(
    content: GeneratedContent, *, include_cost_ledger: bool
) -> str:
    payload = {
        "projection": (
            sha256_hex(canonical_json_bytes(content.projection))
            if content.projection is not None
            else None
        ),
        "publication": contract_digest(content.publication),
        "release": contract_digest(content.release) if content.release is not None else None,
        "revisions": {
            f"{case_key}:{number}": sha256_hex(stored.serialized)
            for (case_key, number), stored in sorted(content.revisions.items())
        },
    }
    if include_cost_ledger:
        payload["cost_ledger"] = contract_digest(content.cost_ledger)
    return sha256_hex(canonical_json_bytes(payload))


def generated_content_digest(content: GeneratedContent) -> str:
    """Return a CAS token covering every active generated-content byte."""
    return _generated_content_digest(content, include_cost_ledger=True)


def generated_public_content_digest(content: GeneratedContent) -> str:
    """Return the build-parent CAS token while permitting receipts-only appends."""
    return _generated_content_digest(content, include_cost_ledger=False)


def reconcile_release_ids(
    *,
    live_release_id: str | None,
    branch_release_id: str | None,
    validated_release_ids: set[str] | frozenset[str] = frozenset(),
) -> ReconciliationChoice:
    """Choose a fail-closed recovery action after interrupted deploy/promotion."""
    if live_release_id == branch_release_id:
        return ReconciliationChoice.IN_SYNC
    if live_release_id is not None and live_release_id in validated_release_ids:
        return ReconciliationChoice.PROMOTE_VALIDATED_LIVE
    if branch_release_id is not None:
        return ReconciliationChoice.REDEPLOY_BRANCH_ACTIVE
    return ReconciliationChoice.STOP_UNKNOWN_LIVE


class StaticStateStore:
    """Read immutable active state and create complete candidates beside it."""

    PROJECTION_PATH = Path("snapshot/v1/projection.json")
    PUBLICATION_PATH = Path("state/v1/publication.json")
    COST_LEDGER_PATH = Path("state/v1/cost-ledger.json")
    RELEASE_PATH = Path("release/v1/release.json")

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load(self) -> GeneratedContent:
        if not self.root.exists():
            return GeneratedContent.empty()
        if not self.root.is_dir():
            raise StaticStateError("generated-content root is not a directory")
        core_paths = (
            self.PROJECTION_PATH,
            self.PUBLICATION_PATH,
            self.COST_LEDGER_PATH,
            self.RELEASE_PATH,
        )
        present = [path for path in core_paths if (self.root / path).is_file()]
        revision_paths = sorted(self.root.glob("snapshot/v1/cases/*/revisions/*.json"))
        public_files = {
            path.relative_to(self.root)
            for path in self.root.rglob("*")
            if path.is_file() and path.relative_to(self.root).parts[0] != ".git"
        }
        if not present and not revision_paths:
            if public_files or self._unexpected_public_directories(set()):
                raise StaticStateError("generated-content tree contains non-contract entries")
            return GeneratedContent.empty()
        if len(present) != len(core_paths):
            missing = sorted(str(path) for path in core_paths if path not in present)
            raise StaticStateError(f"generated-content snapshot is incomplete: {missing}")

        try:
            projection_payload, _ = self._load_json(self.PROJECTION_PATH)
            _, publication_bytes = self._load_json(self.PUBLICATION_PATH)
            _, cost_bytes = self._load_json(self.COST_LEDGER_PATH)
            _, release_bytes = self._load_json(self.RELEASE_PATH)
            projection = validate_projection_payload(projection_payload)
            publication = PublicationState.model_validate_json(publication_bytes)
            cost_ledger = CostLedger.model_validate_json(cost_bytes)
            release = ReleaseManifest.model_validate_json(release_bytes)
            self._require_canonical(self.PROJECTION_PATH, projection)
            self._require_canonical(self.PUBLICATION_PATH, publication)
            self._require_canonical(self.COST_LEDGER_PATH, cost_ledger)
            self._require_canonical(self.RELEASE_PATH, release)
            revisions = self._load_revisions(revision_paths)
            allowed_files = {*core_paths, *(path.relative_to(self.root) for path in revision_paths)}
            if public_files != allowed_files or self._unexpected_public_directories(allowed_files):
                raise StaticStateError("generated-content tree contains non-contract entries")
            loaded = GeneratedContent(
                projection=projection,
                publication=publication,
                cost_ledger=cost_ledger,
                release=release,
                revisions=revisions,
            )
            self._validate_consistency(loaded)
            return loaded
        except (OSError, json.JSONDecodeError, ValidationError, ValueError):
            # Contract errors can echo attacker-controlled values. Public workflow logs get
            # only this coarse category; detailed validation belongs in a private workspace.
            raise StaticStateError("invalid generated-content state") from None

    def finalize_candidate(
        self,
        destination: str | Path,
        content: GeneratedContent,
        manifest: ReleaseManifest,
    ) -> GeneratedContent:
        """Atomically persist a complete release with its manifest and active pointer.

        Export and validation happen before this call.  The only persisted candidate has
        the exact exporter manifest attached and its publication pointer advanced in the
        same directory rename, so a half-finalized generated snapshot cannot be observed.
        """
        parent_release_id = content.publication.active_release_id
        if manifest.previous_release_id != parent_release_id:
            raise CompareAndSwapConflict("exported release parent does not match active state")
        finalized = replace(
            content,
            publication=content.publication.model_copy(
                update={"active_release_id": manifest.release_id}
            ),
            release=manifest,
        )
        self.write_candidate(destination, finalized)
        return finalized

    def write_candidate(self, destination: str | Path, content: GeneratedContent) -> Path:
        """Write a new directory atomically; never edit active state in place."""
        destination_path = Path(destination)
        if destination_path.resolve() == self.root.resolve():
            raise StaticStateError("candidate destination cannot be the active state root")
        if destination_path.exists():
            raise StaticStateError("candidate destination already exists")
        self._validate_consistency(content)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{destination_path.name}.", dir=destination_path.parent)
        )
        os.chmod(temporary, 0o700)
        try:
            if content.projection is None or content.release is None:
                raise StaticStateError(
                    "a persisted candidate must contain projection and release data"
                )
            self._write(temporary / self.PROJECTION_PATH, canonical_json_bytes(content.projection))
            self._write(
                temporary / self.PUBLICATION_PATH,
                canonical_json_bytes(content.publication),
            )
            self._write(
                temporary / self.COST_LEDGER_PATH,
                canonical_json_bytes(content.cost_ledger),
            )
            self._write(temporary / self.RELEASE_PATH, canonical_json_bytes(content.release))
            for (case_key, revision_number), stored in sorted(content.revisions.items()):
                expected = canonical_json_bytes(stored.record)
                if stored.serialized != expected:
                    raise StaticStateError("case revision bytes are not canonical")
                revision_path = self._revision_path(case_key, revision_number)
                # Writing the retained bytes, not a model dump, guarantees exact carry-forward.
                self._write(temporary / revision_path, stored.serialized)
            os.replace(temporary, destination_path)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return destination_path

    def merge_accepted_case(
        self,
        content: GeneratedContent,
        case: PublicCaseBrief,
        *,
        watermark: datetime,
        generated_at: datetime,
        processor_sha256: str | None = None,
    ) -> GeneratedContent:
        """Append one immutable revision and replace only that active public case."""
        case_key = public_case_key(case.term, case.primary_docket)
        old_pointer = next(
            (pointer for pointer in content.publication.cases if pointer.case_key == case_key),
            None,
        )
        expected_revision = 1 if old_pointer is None else old_pointer.active_revision + 1
        if case.revisions[-1].revision_number != expected_revision:
            raise StaticStateError("accepted case revision is not append-only")
        if (case_key, expected_revision) in content.revisions:
            raise StaticStateError("accepted case revision already exists")
        if old_pointer is not None:
            prior_case = content.revisions[(case_key, old_pointer.active_revision)].record.case
            if case.revisions[:-1] != prior_case.revisions:
                raise StaticStateError("accepted case rewrites immutable revision history")
        case_bytes = canonical_json_bytes(case)
        case_digest = sha256_hex(case_bytes)
        record = PublicCaseRevisionRecord(
            case_key=case_key,
            revision_number=expected_revision,
            accepted_at=generated_at,
            case_sha256=case_digest,
            previous_case_sha256=(
                old_pointer.active_case_sha256 if old_pointer is not None else None
            ),
            case=case,
        )
        serialized = canonical_json_bytes(record)
        revisions = dict(content.revisions)
        revisions[(case_key, expected_revision)] = StoredCaseRevision(record, serialized)

        from ragchew.scotus.static_contracts import CaseRevisionPointer

        legacy_slugs = set(old_pointer.legacy_slugs if old_pointer is not None else ())
        if old_pointer is not None and old_pointer.active_slug != case.slug:
            legacy_slugs.add(old_pointer.active_slug)
        pointer = CaseRevisionPointer(
            case_key=case_key,
            term=case.term,
            primary_docket=case.primary_docket,
            active_revision=expected_revision,
            active_slug=case.slug,
            active_case_sha256=case_digest,
            processor_sha256=processor_sha256,
            legacy_slugs=tuple(sorted(legacy_slugs)),
        )
        pointers = {
            item.case_key: item for item in content.publication.cases if item.case_key != case_key
        }
        pointers[case_key] = pointer
        publication = content.publication.model_copy(
            update={
                "updated_at": generated_at,
                "cases": tuple(pointers[key] for key in sorted(pointers)),
            }
        )

        active_cases = {
            public_case_key(item.term, item.primary_docket): item
            for item in (content.projection.cases if content.projection is not None else ())
        }
        active_cases[case_key] = case
        projection = ScotusPublicProjection(
            watermark=watermark,
            generated_at=generated_at,
            cases=tuple(active_cases[key] for key in sorted(active_cases)),
            disclosure=(content.projection.disclosure if content.projection is not None else None)
            or ScotusPublicProjection.model_fields["disclosure"].default,
            site_name=(content.projection.site_name if content.projection is not None else None)
            or "SCOTUS Legal Briefs",
        )
        return replace(content, projection=projection, publication=publication, revisions=revisions)

    def update_publication_state(
        self,
        content: GeneratedContent,
        *,
        updated_at: datetime,
        sources: tuple[LogicalSourceState, ...],
        documents: tuple[LogicalDocumentState, ...],
        pending_work: tuple[PendingWork, ...],
        cursors: tuple[CursorState, ...],
        processor: ProcessorFingerprint | None,
        dispositions: tuple[DispositionDiscoveryState, ...] | None = None,
    ) -> GeneratedContent:
        """Apply sanitized checkpoints without changing the active release pointer."""
        publication = PublicationState(
            active_release_id=content.publication.active_release_id,
            updated_at=updated_at,
            sources=sources,
            documents=documents,
            dispositions=(
                content.publication.dispositions
                if dispositions is None
                else dispositions
            ),
            undated_disposition_case_keys=tuple(
                sorted(
                    public_case_key(case.term, case.primary_docket)
                    for case in (
                        content.projection.cases
                        if content.projection is not None
                        else ()
                    )
                    if case.undated_disposition_date_fallback is not None
                )
            ),
            cases=content.publication.cases,
            pending_work=pending_work,
            cursors=cursors,
            processor=processor,
        )
        return replace(content, publication=publication)

    def append_cost_receipt(
        self,
        receipt: ModelAttemptReceipt,
        *,
        expected_digest: str,
    ) -> CostLedger:
        """CAS-update only the opaque ledger; never touch a release or projection pointer."""
        lock_path = self.root.parent / f".{self.root.name}.cost-ledger.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                loaded = self.load()
                current = loaded.cost_ledger
                if contract_digest(current) != expected_digest:
                    raise CompareAndSwapConflict("cost ledger changed before receipt update")
                key = (receipt.stage, receipt.input_fingerprint, receipt.attempt_number)
                existing = next(
                    (
                        item
                        for item in current.receipts
                        if (item.stage, item.input_fingerprint, item.attempt_number) == key
                    ),
                    None,
                )
                if existing is not None:
                    if existing != receipt:
                        raise CompareAndSwapConflict("cost receipt changed for an existing input")
                    return current
                receipts = tuple(
                    sorted(
                        (*current.receipts, receipt),
                        key=lambda item: (
                            item.stage,
                            item.input_fingerprint,
                            item.attempt_number,
                        ),
                    )
                )
                updated = CostLedger(
                    revision=current.revision + 1,
                    updated_at=receipt.attempted_at,
                    receipts=receipts,
                )
                path = self.root / self.COST_LEDGER_PATH
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
                try:
                    self._write(temporary, canonical_json_bytes(updated))
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
                return updated
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def require_release_parent(
        self,
        candidate: GeneratedContent,
        *,
        expected_parent_release_id: str | None,
        expected_parent_digest: str,
    ) -> None:
        """Check branch and candidate parents before a no-secret promotion step."""
        active_content = self.load()
        if generated_public_content_digest(active_content) != expected_parent_digest:
            raise CompareAndSwapConflict("generated-content branch public state changed")
        active = active_content.publication.active_release_id
        if active != expected_parent_release_id:
            raise CompareAndSwapConflict("active generated-content release changed")
        if candidate.release is None:
            raise StaticStateError("candidate has no release manifest")
        if candidate.release.previous_release_id != expected_parent_release_id:
            raise CompareAndSwapConflict("candidate release parent does not match active release")

    def _load_revisions(
        self, revision_paths: list[Path]
    ) -> dict[tuple[str, int], StoredCaseRevision]:
        revisions: dict[tuple[str, int], StoredCaseRevision] = {}
        for absolute in revision_paths:
            relative = absolute.relative_to(self.root)
            case_key = relative.parts[-3]
            try:
                revision_number = int(relative.stem)
            except ValueError as error:
                raise StaticStateError("case revision filename must be an integer") from error
            _, serialized = self._load_json(relative)
            record = PublicCaseRevisionRecord.model_validate_json(serialized)
            if record.case_key != case_key or record.revision_number != revision_number:
                raise StaticStateError("case revision path does not match its contract")
            if serialized != canonical_json_bytes(record):
                raise StaticStateError("case revision is not canonical")
            key = (case_key, revision_number)
            if key in revisions:
                raise StaticStateError("duplicate case revision path")
            revisions[key] = StoredCaseRevision(record, serialized)
        return revisions

    def _validate_consistency(self, content: GeneratedContent) -> None:
        if content.projection is None:
            if content.release is not None or content.revisions or content.publication.cases:
                raise StaticStateError("empty bootstrap contains partial snapshot data")
            return
        projection_cases = {
            public_case_key(case.term, case.primary_docket): case
            for case in content.projection.cases
        }
        pointers = {pointer.case_key: pointer for pointer in content.publication.cases}
        if set(projection_cases) != set(pointers):
            raise StaticStateError("projection cases and publication pointers differ")
        for key, stored in content.revisions.items():
            expected_key = (stored.record.case_key, stored.record.revision_number)
            if key != expected_key:
                raise StaticStateError("revision mapping key differs from its contract")
            if stored.record.case_key not in pointers:
                raise StaticStateError("orphan case revision is not allowed")
            if stored.serialized != canonical_json_bytes(stored.record):
                raise StaticStateError("stored case revision is not canonical")
        for case_key, pointer in pointers.items():
            active_stored = content.revisions.get((case_key, pointer.active_revision))
            if active_stored is None:
                raise StaticStateError("active case revision is missing")
            if active_stored.record.case != projection_cases[case_key]:
                raise StaticStateError("active revision payload differs from projection case")
            if pointer.active_slug != projection_cases[case_key].slug:
                raise StaticStateError("active slug differs from projection case")
            if active_stored.record.case_sha256 != pointer.active_case_sha256:
                raise StaticStateError("active case digest differs from publication pointer")
            numbers = sorted(number for key, number in content.revisions if key == case_key)
            if numbers != list(range(1, pointer.active_revision + 1)):
                raise StaticStateError("stored case revisions are not contiguous")
            for number in numbers[1:]:
                previous = content.revisions[(case_key, number - 1)].record.case_sha256
                if content.revisions[(case_key, number)].record.previous_case_sha256 != previous:
                    raise StaticStateError("case revision digest chain is broken")
        if content.release is not None:
            projection_digest = sha256_hex(canonical_json_bytes(content.projection))
            if content.release.projection_sha256 != projection_digest:
                raise StaticStateError("release projection digest does not match snapshot")
            if content.publication.active_release_id != content.release.release_id:
                raise StaticStateError("publication and release active IDs differ")
            if content.release.case_count != len(content.projection.cases):
                raise StaticStateError("release case count differs from projection")
            if content.release.page_count < content.release.case_count:
                raise StaticStateError("release page count cannot be smaller than its case count")

    def _require_canonical(self, relative: Path, model: Any) -> None:
        serialized = (self.root / relative).read_bytes()
        if serialized != canonical_json_bytes(model):
            raise StaticStateError("generated-content JSON is not canonical")

    def _load_json(self, relative: Path) -> tuple[dict[str, Any], bytes]:
        serialized = (self.root / relative).read_bytes()
        payload = json.loads(serialized)
        if not isinstance(payload, dict):
            raise StaticStateError("generated-content contract must be an object")
        return payload, serialized

    def _unexpected_public_directories(self, allowed_files: set[Path]) -> bool:
        allowed_directories: set[Path] = {Path(".")}
        for path in allowed_files:
            parent = path.parent
            while parent != Path("."):
                allowed_directories.add(parent)
                parent = parent.parent
        for path in self.root.rglob("*"):
            relative = path.relative_to(self.root)
            if relative.parts[0] == ".git":
                continue
            if path.is_symlink():
                return True
            if path.is_dir() and relative not in allowed_directories:
                return True
        return False

    @staticmethod
    def _write(path: Path, value: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _revision_path(case_key: str, revision_number: int) -> Path:
        if not case_key or not all(
            character.isalnum() or character in ".:_-" for character in case_key
        ):
            raise StaticStateError("unsafe case key")
        if revision_number < 1:
            raise StaticStateError("revision number must be positive")
        return Path(f"snapshot/v1/cases/{case_key}/revisions/{revision_number}.json")
