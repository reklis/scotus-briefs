"""Supreme Court case, argument-session, and disposition discovery."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, field_validator, model_validator

from ragchew.contracts import StrictModel
from ragchew.proceedings.contracts import DocumentType, SourceAccessMethod, UtcDatetime
from ragchew.proceedings.discovery import (
    ConditionalRequest,
    DiscoveredProceeding,
    DocumentDescriptor,
    OfficialSourceAdapter,
    SourcePollResult,
)
from ragchew.proceedings.registry import SourceAuthorizer, SourceRegistry
from ragchew.proceedings.sources.supreme_court import (
    SlipOpinionEntry,
    SlipOpinionKind,
    SlipOpinionPollResult,
)
from ragchew.scotus.contracts import ScotusDocumentKind
from ragchew.scotus.public_contracts import public_case_key
from ragchew.scotus.static_contracts import (
    ConditionalValidators,
    ContentIntegrity,
    CursorState,
    DispositionDiscoveryState,
    LogicalSourceState,
    sha256_hex,
)

_DOCKET = re.compile(
    r"^(?:[0-9]{1,3}(?:A)?-[0-9A-Z]+(?:\s+ORIG\.)?|"
    r"[0-9]{1,3}A[0-9]+|[0-9]{1,3}\s+ORIG\.)$",
    re.IGNORECASE,
)


def normalize_docket(value: str) -> str:
    normalized = " ".join(value.replace("\N{EN DASH}", "-").strip().upper().split())
    if not _DOCKET.fullmatch(normalized):
        raise ValueError("invalid Supreme Court docket number")
    return normalized


def deterministic_case_id(term: str, primary_docket: str) -> UUID:
    docket = normalize_docket(primary_docket)
    return uuid5(NAMESPACE_URL, f"ragchew:scotus-case:{term}:{docket}")


def deterministic_argument_id(
    case_id: UUID,
    argument_date: datetime,
    *,
    sequence: int = 1,
    reargument: bool = False,
) -> UUID:
    date = argument_date.date().isoformat()
    return uuid5(
        NAMESPACE_URL,
        f"ragchew:scotus-argument:{case_id}:{date}:{sequence}:{int(reargument)}",
    )


class ScotusArgumentCandidate(StrictModel):
    term: str = Field(pattern=r"^\d{4}$")
    primary_docket: str
    consolidated_dockets: tuple[str, ...] = ()
    caption: str = Field(min_length=1, max_length=500)
    argument_date: UtcDatetime
    sequence: int = Field(default=1, ge=1)
    reargument: bool = False
    official_detail_url: str
    transcript: DocumentDescriptor | None = None
    docket_documents: tuple[DocumentDescriptor, ...] = ()
    related_documents: tuple[DocumentDescriptor, ...] = ()
    source_metadata: dict[str, object] = Field(default_factory=dict)


class ScotusDispositionCandidate(StrictModel):
    """Typed case-level work from one supported slip-opinion index row."""

    term: str = Field(pattern=r"^\d{4}$")
    primary_docket: str
    consolidated_dockets: tuple[str, ...] = ()
    caption: str = Field(min_length=1, max_length=500)
    release_number: str = Field(pattern=r"^(?:D)?\d+$")
    kind: SlipOpinionKind
    publication_date: UtcDatetime
    official_url: str
    revision_date: UtcDatetime | None = None
    revision_reference_url: str | None = None

    @field_validator("primary_docket", mode="before")
    @classmethod
    def normalize_primary_docket(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("disposition docket must be text")
        return normalize_docket(value)

    @field_validator("consolidated_dockets", mode="before")
    @classmethod
    def normalize_consolidated_dockets(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("disposition consolidated dockets must be a sequence")
        return tuple(normalize_docket(str(docket)) for docket in value)

    @model_validator(mode="after")
    def validate_disposition(self) -> ScotusDispositionCandidate:
        if self.primary_docket in self.consolidated_dockets or len(
            self.consolidated_dockets
        ) != len(set(self.consolidated_dockets)):
            raise ValueError("disposition dockets must be unique")
        if self.revision_date is not None and self.revision_date <= self.publication_date:
            raise ValueError("disposition revision date must follow publication")
        if (self.revision_date is None) != (self.revision_reference_url is None):
            raise ValueError("disposition revision date and reference must appear together")
        return self

    @property
    def descriptor(self) -> DocumentDescriptor:
        return DocumentDescriptor(
            external_id=disposition_logical_key(self),
            document_type=DocumentType.OPINION,
            official_url=self.official_url,
            access_method=SourceAccessMethod.OFFICIAL_PAGE,
            content_type="application/pdf",
        )


class ScotusCaseDiscoveryCandidate(StrictModel):
    """Merged discovery unit; arguments are deliberately optional."""

    term: str = Field(pattern=r"^\d{4}$")
    primary_docket: str
    consolidated_dockets: tuple[str, ...] = ()
    caption: str = Field(min_length=1, max_length=500)
    arguments: tuple[ScotusArgumentCandidate, ...] = ()
    dispositions: tuple[ScotusDispositionCandidate, ...] = ()


class DiscoveredDocument(StrictModel):
    case_id: UUID
    argument_id: UUID | None = None
    kind: ScotusDocumentKind
    external_id: str
    official_url: str
    content_type: str


class BackfillCheckpoint(StrictModel):
    term: str = Field(pattern=r"^\d{4}$")
    examined: int = Field(default=0, ge=0)
    queued: int = Field(default=0, ge=0)
    last_docket: str | None = None
    complete: bool = False


class DiscoveryApplyResult(StrictModel):
    cases_created: int = Field(default=0, ge=0)
    arguments_created: int = Field(default=0, ge=0)
    metadata_revisions: int = Field(default=0, ge=0)
    transcript_jobs: int = Field(default=0, ge=0)
    documents_discovered: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class CollectionJob:
    argument_id: UUID
    external_id: str
    input_version: str
    priority: int


@dataclass(frozen=True)
class DocumentCollectionJob:
    case_id: UUID
    kind: ScotusDocumentKind
    external_id: str
    input_version: str
    priority: int


class ScotusDiscoveryStore(Protocol):
    def save_candidate(
        self,
        candidate: ScotusArgumentCandidate,
        case_id: UUID,
        argument_id: UUID,
        payload_sha256: str,
        documents: tuple[DiscoveredDocument, ...],
    ) -> tuple[bool, bool, bool]: ...

    def enqueue_transcript(self, job: CollectionJob) -> bool: ...

    def enqueue_document(self, job: DocumentCollectionJob) -> bool: ...

    def get_backfill_checkpoint(self, term: str) -> BackfillCheckpoint: ...

    def save_backfill_checkpoint(self, checkpoint: BackfillCheckpoint) -> None: ...


class InMemoryScotusDiscoveryStore:
    def __init__(self) -> None:
        self.cases: dict[UUID, ScotusArgumentCandidate] = {}
        self.arguments: dict[UUID, ScotusArgumentCandidate] = {}
        self.case_revisions: dict[UUID, list[str]] = {}
        self.argument_revisions: dict[UUID, list[str]] = {}
        self.documents: dict[tuple[UUID, str, str], DiscoveredDocument] = {}
        self.jobs: set[CollectionJob] = set()
        self.document_jobs: set[DocumentCollectionJob] = set()
        self.backfill: dict[str, BackfillCheckpoint] = {}

    def save_candidate(
        self,
        candidate: ScotusArgumentCandidate,
        case_id: UUID,
        argument_id: UUID,
        payload_sha256: str,
        documents: tuple[DiscoveredDocument, ...],
    ) -> tuple[bool, bool, bool]:
        new_case = case_id not in self.cases
        new_argument = argument_id not in self.arguments
        self.cases[case_id] = candidate
        self.arguments[argument_id] = candidate
        case_revisions = self.case_revisions.setdefault(case_id, [])
        argument_revisions = self.argument_revisions.setdefault(argument_id, [])
        revision_created = payload_sha256 not in argument_revisions
        if payload_sha256 not in case_revisions:
            case_revisions.append(payload_sha256)
        if revision_created:
            argument_revisions.append(payload_sha256)
        for document in documents:
            self.documents[(case_id, document.kind.value, document.external_id)] = document
        return new_case, new_argument, revision_created

    def enqueue_transcript(self, job: CollectionJob) -> bool:
        before = len(self.jobs)
        self.jobs.add(job)
        return len(self.jobs) != before

    def enqueue_document(self, job: DocumentCollectionJob) -> bool:
        before = len(self.document_jobs)
        self.document_jobs.add(job)
        return len(self.document_jobs) != before

    def get_backfill_checkpoint(self, term: str) -> BackfillCheckpoint:
        return self.backfill.get(term, BackfillCheckpoint(term=term))

    def save_backfill_checkpoint(self, checkpoint: BackfillCheckpoint) -> None:
        self.backfill[checkpoint.term] = checkpoint


def candidate_from_proceeding(item: DiscoveredProceeding, term: str) -> ScotusArgumentCandidate:
    metadata = item.metadata
    raw_dockets = metadata.get("consolidated_dockets", ())
    if not isinstance(raw_dockets, (list, tuple)):
        raise ValueError("consolidated_dockets metadata must be a list")
    dockets = tuple(normalize_docket(str(value)) for value in raw_dockets)
    primary = normalize_docket(item.external_id)
    consolidated = tuple(value for value in dockets if value != primary)
    transcript: DocumentDescriptor | None = None
    docket_documents: list[DocumentDescriptor] = []
    related: list[DocumentDescriptor] = []
    for document in item.documents:
        if document.document_type is DocumentType.OFFICIAL_TRANSCRIPT:
            transcript = document
        elif document.document_type is DocumentType.DOCKET:
            docket_documents.append(document)
        elif document.document_type in {DocumentType.ORDER, DocumentType.OPINION}:
            related.append(document)
    sequence_value = metadata.get("argument_sequence", 1)
    sequence = int(sequence_value) if isinstance(sequence_value, (int, str)) else 1
    return ScotusArgumentCandidate(
        term=term,
        primary_docket=primary,
        consolidated_dockets=consolidated,
        caption=item.title,
        argument_date=item.scheduled_start_at,
        sequence=sequence,
        reargument=bool(metadata.get("reargument", False)),
        official_detail_url=item.official_url,
        transcript=transcript,
        docket_documents=tuple(docket_documents),
        related_documents=tuple(related),
        source_metadata=metadata,
    )


def _document_kind(document: DocumentDescriptor) -> ScotusDocumentKind:
    mapping = {
        DocumentType.OFFICIAL_TRANSCRIPT: ScotusDocumentKind.TRANSCRIPT,
        DocumentType.DOCKET: ScotusDocumentKind.DOCKET,
        DocumentType.ORDER: ScotusDocumentKind.ORDER,
        DocumentType.OPINION: ScotusDocumentKind.OPINION,
    }
    return mapping.get(document.document_type, ScotusDocumentKind.OTHER_OFFICIAL)


def _without_retrieval_times(value: object) -> object:
    """Remove transport-time metadata from a discovery processing fingerprint."""
    if isinstance(value, dict):
        return {
            key: _without_retrieval_times(item)
            for key, item in value.items()
            if key not in {"source_updated_at", "retrieved_at", "checked_at", "observed_at"}
        }
    if isinstance(value, list):
        return [_without_retrieval_times(item) for item in value]
    return value


def stable_candidate_fingerprint(candidate: ScotusArgumentCandidate) -> str:
    """Fingerprint source meaning, excluding volatile retrieval timestamps."""
    payload = _without_retrieval_times(candidate.model_dump(mode="json"))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def stable_descriptor_fingerprint(descriptor: DocumentDescriptor) -> str:
    """Fingerprint a descriptor without its volatile source timestamp."""
    payload = _without_retrieval_times(descriptor.model_dump(mode="json"))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def disposition_logical_key(candidate: ScotusDispositionCandidate) -> str:
    """Identity a Court row independently of its mutable URL or bytes."""
    docket = re.sub(
        r"[^a-z0-9]+", "-", normalize_docket(candidate.primary_docket).casefold()
    ).strip("-")
    return f"slip:{candidate.term}:{docket}:{candidate.release_number.casefold()}"


def stable_disposition_fingerprint(candidate: ScotusDispositionCandidate) -> str:
    payload = candidate.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def disposition_candidate_from_entry(entry: SlipOpinionEntry) -> ScotusDispositionCandidate:
    return ScotusDispositionCandidate(
        term=entry.term,
        primary_docket=entry.primary_docket,
        consolidated_dockets=entry.consolidated_dockets,
        caption=entry.caption,
        release_number=entry.release_number,
        kind=entry.kind,
        publication_date=entry.publication_date,
        official_url=entry.official_pdf_url,
        revision_date=entry.revision_date,
        revision_reference_url=entry.revision_reference_url,
    )


def disposition_state(
    candidate: ScotusDispositionCandidate, *, case_key: str | None = None
) -> DispositionDiscoveryState:
    return DispositionDiscoveryState(
        logical_key=disposition_logical_key(candidate),
        case_key=case_key or candidate_logical_key(candidate),
        term=candidate.term,
        primary_docket=candidate.primary_docket,
        consolidated_dockets=candidate.consolidated_dockets,
        caption=candidate.caption,
        release_number=candidate.release_number,
        kind=candidate.kind.value,
        official_url=candidate.official_url,
        publication_date=candidate.publication_date,
        revision_date=candidate.revision_date,
        revision_reference_url=candidate.revision_reference_url,
        metadata_sha256=stable_disposition_fingerprint(candidate),
    )


def disposition_candidate_from_state(
    state: DispositionDiscoveryState,
) -> ScotusDispositionCandidate:
    return ScotusDispositionCandidate(
        term=state.term,
        primary_docket=state.primary_docket,
        consolidated_dockets=state.consolidated_dockets,
        caption=state.caption,
        release_number=state.release_number,
        kind=SlipOpinionKind(state.kind),
        publication_date=state.publication_date,
        official_url=state.official_url,
        revision_date=state.revision_date,
        revision_reference_url=state.revision_reference_url,
    )


def _discovery_dockets(
    candidate: ScotusArgumentCandidate | ScotusDispositionCandidate,
) -> frozenset[str]:
    return frozenset(
        normalize_docket(value)
        for value in (candidate.primary_docket, *candidate.consolidated_dockets)
    )


def merge_case_discovery(
    arguments: Sequence[ScotusArgumentCandidate],
    dispositions: Sequence[ScotusDispositionCandidate],
    *,
    preferred_primary_dockets: Sequence[tuple[str, str]] = (),
) -> tuple[ScotusCaseDiscoveryCandidate, ...]:
    """Join independent streams while retaining an already-durable primary docket."""
    items: list[ScotusArgumentCandidate | ScotusDispositionCandidate] = [
        *arguments,
        *dispositions,
    ]
    if not items:
        return ()
    parents = list(range(len(items)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    owners: dict[tuple[str, str], int] = {}
    for index, item in enumerate(items):
        for docket in _discovery_dockets(item):
            identity = (item.term, docket)
            previous = owners.get(identity)
            if previous is not None:
                union(previous, index)
            else:
                owners[identity] = index

    grouped: dict[int, list[ScotusArgumentCandidate | ScotusDispositionCandidate]] = {}
    for index, item in enumerate(items):
        grouped.setdefault(find(index), []).append(item)

    preferred = {
        (term, normalize_docket(docket))
        for term, docket in preferred_primary_dockets
    }
    merged: list[ScotusCaseDiscoveryCandidate] = []
    for values in grouped.values():
        sessions = tuple(
            sorted(
                (item for item in values if isinstance(item, ScotusArgumentCandidate)),
                key=_candidate_sort_key,
            )
        )
        case_dispositions = tuple(
            sorted(
                (item for item in values if isinstance(item, ScotusDispositionCandidate)),
                key=lambda item: (
                    item.publication_date,
                    item.revision_date or item.publication_date,
                    disposition_logical_key(item),
                ),
            )
        )
        all_dockets = sorted({docket for item in values for docket in _discovery_dockets(item)})
        durable_primaries = {
            docket for docket in all_dockets if (values[0].term, docket) in preferred
        }
        argument_primaries = {
            normalize_docket(item.primary_docket) for item in sessions
        }
        if durable_primaries:
            primary = min(durable_primaries)
        elif len(argument_primaries) == 1:
            primary = next(iter(argument_primaries))
        elif argument_primaries:
            # A consolidated disposition can bridge argument rows that previously
            # appeared under separate primary dockets. Prefer the Court row's primary
            # when possible, then use a stable normalized-docket tie-break.
            disposition_primaries = {
                item.primary_docket for item in case_dispositions
            }
            primary = min(
                (argument_primaries & disposition_primaries) or argument_primaries
            )
        else:
            primary = min(
                case_dispositions,
                key=lambda item: (item.publication_date, disposition_logical_key(item)),
            ).primary_docket
        latest_caption_source = max(
            values,
            key=lambda item: (
                item.argument_date
                if isinstance(item, ScotusArgumentCandidate)
                else item.revision_date or item.publication_date,
                item.caption,
            ),
        )
        merged.append(
            ScotusCaseDiscoveryCandidate(
                term=values[0].term,
                primary_docket=primary,
                consolidated_dockets=tuple(
                    docket for docket in all_dockets if docket != primary
                ),
                caption=latest_caption_source.caption,
                arguments=sessions,
                dispositions=case_dispositions,
            )
        )
    return tuple(
        sorted(merged, key=lambda item: (item.term, normalize_docket(item.primary_docket)))
    )


# Private aliases retained for callers written against the MVP implementation.
def _candidate_digest(candidate: ScotusArgumentCandidate) -> str:
    return stable_candidate_fingerprint(candidate)


def _descriptor_digest(descriptor: DocumentDescriptor) -> str:
    return stable_descriptor_fingerprint(descriptor)


class DiscoveryMode(StrEnum):
    NIGHTLY = "nightly"
    BOOTSTRAP = "bootstrap"


@dataclass(frozen=True)
class OneShotDiscoveryResult:
    candidates: tuple[ScotusArgumentCandidate, ...]
    checkpoint: LogicalSourceState
    changed: bool
    not_modified: bool


@dataclass(frozen=True)
class SlipOpinionDiscoveryResult:
    candidates: tuple[ScotusDispositionCandidate, ...]
    states: tuple[DispositionDiscoveryState, ...]
    changed_logical_keys: tuple[str, ...]
    checkpoint: LogicalSourceState
    changed: bool
    not_modified: bool


def discover_slip_opinions_once(
    adapter: object,
    *,
    resource_key: str,
    checkpoint: LogicalSourceState | None,
    prior_states: Sequence[DispositionDiscoveryState],
    now: datetime,
) -> SlipOpinionDiscoveryResult:
    """Conditionally discover and retain first-class active-term dispositions."""
    poll = getattr(adapter, "poll_slip_opinions", None)
    if not callable(poll):
        raise TypeError("discovery adapter must provide poll_slip_opinions(conditional)")
    conditional = ConditionalRequest(
        etag=checkpoint.validators.etag if checkpoint else None,
        last_modified=checkpoint.validators.last_modified if checkpoint else None,
    )
    result = poll(conditional)
    if not isinstance(result, SlipOpinionPollResult):
        raise TypeError("discovery adapter returned an invalid slip-opinion result")
    validators = ConditionalValidators(
        etag=result.etag or (checkpoint.validators.etag if checkpoint else None),
        last_modified=(
            result.last_modified
            or (checkpoint.validators.last_modified if checkpoint else None)
        ),
    )
    retained = {item.logical_key: item for item in prior_states}
    if result.not_modified:
        if checkpoint is None:
            raise ValueError("not-modified slip index requires a prior checkpoint")
        checkpoint_state = checkpoint.model_copy(
            update={"validators": validators, "checked_at": now}
        )
        candidates = tuple(
            disposition_candidate_from_state(retained[key]) for key in sorted(retained)
        )
        return SlipOpinionDiscoveryResult(
            candidates,
            tuple(retained[key] for key in sorted(retained)),
            (),
            checkpoint_state,
            False,
            True,
        )

    current = tuple(disposition_candidate_from_entry(entry) for entry in result.entries)
    payload = json.dumps(
        [
            candidate.model_dump(mode="json")
            for candidate in sorted(current, key=disposition_logical_key)
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    integrity = ContentIntegrity(sha256=sha256_hex(payload), byte_count=len(payload))
    changed_keys: list[str] = []
    for candidate in current:
        observed = disposition_state(candidate)
        previous = retained.get(observed.logical_key)
        if previous is None or previous.metadata_sha256 != observed.metadata_sha256:
            changed_keys.append(observed.logical_key)
        retained[observed.logical_key] = observed
    checkpoint_state = LogicalSourceState(
        logical_key=resource_key,
        source_kind="opinions",
        official_url=result.endpoint_url,
        validators=validators,
        integrity=integrity,
        checked_at=now,
    )
    unchanged = checkpoint is not None and checkpoint.integrity == integrity
    candidates = tuple(
        disposition_candidate_from_state(retained[key]) for key in sorted(retained)
    )
    return SlipOpinionDiscoveryResult(
        candidates=candidates,
        states=tuple(retained[key] for key in sorted(retained)),
        changed_logical_keys=tuple(sorted(changed_keys)),
        checkpoint=checkpoint_state,
        changed=not unchanged,
        not_modified=False,
    )


def discover_once(
    adapter: object,
    *,
    resource_key: str,
    source_kind: Literal[
        "argument_index", "case_detail", "docket", "orders", "opinions"
    ] = "argument_index",
    checkpoint: LogicalSourceState | None,
    now: datetime,
) -> OneShotDiscoveryResult:
    """Poll one reviewed resource with its public conditional checkpoint.

    A stable digest of sanitized descriptors is used when the endpoint provides no
    validators. The operation deliberately returns no candidates for 304 or
    digest-identical responses, so callers cannot accidentally enqueue work.
    """
    poll = getattr(adapter, "poll", None)
    if not callable(poll):
        raise TypeError("discovery adapter must provide poll(conditional)")
    conditional = ConditionalRequest(
        etag=checkpoint.validators.etag if checkpoint else None,
        last_modified=checkpoint.validators.last_modified if checkpoint else None,
    )
    result = poll(conditional)
    if not isinstance(result, SourcePollResult):
        raise TypeError("discovery adapter returned an invalid poll result")
    validators = ConditionalValidators(
        etag=result.etag or (checkpoint.validators.etag if checkpoint else None),
        last_modified=(
            result.last_modified or (checkpoint.validators.last_modified if checkpoint else None)
        ),
    )
    if result.not_modified:
        if checkpoint is None:
            raise ValueError("not-modified response requires a prior checkpoint")
        state = checkpoint.model_copy(update={"validators": validators, "checked_at": now})
        return OneShotDiscoveryResult((), state, False, True)

    candidates = tuple(
        candidate_from_proceeding(item, _candidate_term(item, adapter))
        for item in result.proceedings
        if item.scheduled_start_at is not None
    )
    payload = json.dumps(
        [
            _without_retrieval_times(item.model_dump(mode="json"))
            for item in sorted(candidates, key=_candidate_sort_key)
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    integrity = ContentIntegrity(sha256=sha256_hex(payload), byte_count=len(payload))
    unchanged = checkpoint is not None and checkpoint.integrity == integrity
    state = LogicalSourceState(
        logical_key=resource_key,
        source_kind=source_kind,
        official_url=result.endpoint_url,
        validators=validators,
        integrity=integrity,
        checked_at=now,
    )
    return OneShotDiscoveryResult(
        candidates=() if unchanged else tuple(sorted(candidates, key=_candidate_sort_key)),
        checkpoint=state,
        changed=not unchanged,
        not_modified=False,
    )


def _candidate_term(item: DiscoveredProceeding, adapter: object) -> str:
    term = item.metadata.get("term", getattr(adapter, "term", None))
    if not isinstance(term, str) or not re.fullmatch(r"\d{4}", term):
        raise ValueError("discovered Supreme Court item has no valid term")
    return term


def _candidate_sort_key(item: ScotusArgumentCandidate) -> tuple[str, datetime, int, str]:
    return (item.term, item.argument_date, item.sequence, item.primary_docket)


def _newest_candidate_sort_key(
    item: ScotusArgumentCandidate,
) -> tuple[float, str, str, int]:
    return (
        -item.argument_date.timestamp(),
        item.term,
        item.primary_docket,
        item.sequence,
    )


@dataclass(frozen=True)
class DiscoveryResourceSelection:
    terms: tuple[str, ...]
    cursor: CursorState | None


def select_discovery_resources(
    terms: Sequence[str],
    *,
    active_term: str,
    mode: DiscoveryMode,
    historical_limit: int,
    bootstrap_term_limit: int,
    now: datetime,
    cursor: CursorState | None = None,
) -> DiscoveryResourceSelection:
    """Choose index resources before fetching, so routine runs never scan every term."""
    if historical_limit < 0 or bootstrap_term_limit < 1:
        raise ValueError("resource selection limits are invalid")
    unique = tuple(sorted(set(terms), reverse=True))
    if active_term not in unique:
        raise ValueError("active term is not configured")
    historical = tuple(term for term in unique if term != active_term)
    limit = historical_limit if mode is DiscoveryMode.NIGHTLY else bootstrap_term_limit - 1
    if not historical or limit <= 0:
        return DiscoveryResourceSelection((active_term,), cursor)
    start = (cursor.position if cursor else 0) % len(historical)
    count = min(limit, len(historical))
    selected = tuple(historical[(start + offset) % len(historical)] for offset in range(count))
    raw_position = start + count
    next_cursor = CursorState(
        cursor_key=cursor.cursor_key if cursor else f"{mode.value}:resource-recheck",
        position=raw_position % len(historical),
        wrapped_count=(cursor.wrapped_count if cursor else 0) + raw_position // len(historical),
        updated_at=now,
    )
    return DiscoveryResourceSelection((active_term, *selected), next_cursor)


@dataclass(frozen=True)
class IncrementalDiscoveryResult:
    candidates: tuple[ScotusArgumentCandidate, ...]
    checkpoints: tuple[LogicalSourceState, ...]
    cursor: CursorState | None


class IncrementalDiscoveryOperation:
    """Reusable bounded operation over term-index adapters and public checkpoints."""

    def __init__(
        self,
        adapters: Mapping[str, OfficialSourceAdapter],
        checkpoints: Mapping[str, LogicalSourceState],
    ) -> None:
        self.adapters = dict(adapters)
        self.checkpoints = dict(checkpoints)

    def run(
        self,
        *,
        active_term: str,
        mode: DiscoveryMode,
        historical_limit: int,
        bootstrap_term_limit: int,
        now: datetime,
        cursor: CursorState | None = None,
    ) -> IncrementalDiscoveryResult:
        selected = select_discovery_resources(
            tuple(self.adapters),
            active_term=active_term,
            mode=mode,
            historical_limit=historical_limit,
            bootstrap_term_limit=bootstrap_term_limit,
            now=now,
            cursor=cursor,
        )
        updated = dict(self.checkpoints)
        candidates: list[ScotusArgumentCandidate] = []
        for term in selected.terms:
            key = f"argument-index:{term}"
            result = discover_once(
                self.adapters[term],
                resource_key=key,
                checkpoint=updated.get(key),
                now=now,
            )
            updated[key] = result.checkpoint
            candidates.extend(result.candidates)
        return IncrementalDiscoveryResult(
            candidates=tuple(sorted(candidates, key=_candidate_sort_key)),
            checkpoints=tuple(updated[key] for key in sorted(updated)),
            cursor=selected.cursor,
        )


@dataclass(frozen=True)
class SelectedDiscoveryWork:
    candidate: ScotusArgumentCandidate
    priority: int
    reason: str


@dataclass(frozen=True)
class DiscoverySelection:
    work: tuple[SelectedDiscoveryWork, ...]
    cursor: CursorState | None
    deferred_case_keys: tuple[str, ...] = ()
    current_cursor: CursorState | None = None

    @property
    def checkpoint_safe(self) -> bool:
        """Whether all changed descriptors fit and their source checkpoint may advance."""
        return not self.deferred_case_keys


def select_discovery_work(
    candidates: Sequence[ScotusArgumentCandidate],
    *,
    mode: DiscoveryMode,
    now: datetime,
    active_term: str,
    known_transcript_keys: set[str] | frozenset[str] = frozenset(),
    nightly_case_limit: int,
    new_transcript_priority: int,
    historical_priority: int,
    historical_limit: int,
    recent_lookback_days: int,
    recent_correction_lookback_days: int | None = None,
    recent_opinion_lookback_days: int | None = None,
    cursor: CursorState | None = None,
    current_cursor: CursorState | None = None,
    bootstrap_term_limit: int | None = None,
) -> DiscoverySelection:
    """Select deterministic current work and bounded rotating historical work."""
    if nightly_case_limit < 0 or historical_limit < 0:
        raise ValueError("discovery selection limits cannot be negative")
    ordered = sorted(candidates, key=_candidate_sort_key)
    correction_cutoff = now - timedelta(
        days=recent_correction_lookback_days or recent_lookback_days
    )
    opinion_cutoff = now - timedelta(days=recent_opinion_lookback_days or recent_lookback_days)
    current: list[ScotusArgumentCandidate] = []
    historical: list[ScotusArgumentCandidate] = []
    allowed_bootstrap_terms = set(
        sorted({item.term for item in ordered}, reverse=True)[:bootstrap_term_limit]
        if mode is DiscoveryMode.BOOTSTRAP and bootstrap_term_limit is not None
        else {item.term for item in ordered}
    )
    for item in ordered:
        if item.term not in allowed_bootstrap_terms:
            continue
        is_current = item.term == active_term
        # Court adapter retrieval timestamps are intentionally not treated as source
        # changes; the configured windows are based on the stable argument date.
        is_recent_correction = item.argument_date >= correction_cutoff
        opinion_times = tuple(
            item.argument_date
            for document in item.related_documents
            if document.document_type is DocumentType.OPINION
        )
        is_recent_opinion = any(value >= opinion_cutoff for value in opinion_times)
        if is_current or is_recent_correction or is_recent_opinion:
            current.append(item)
        else:
            historical.append(item)

    chosen: list[SelectedDiscoveryWork] = []
    seen: set[str] = set()
    new_current = sorted(
        (
            item
            for item in current
            if item.transcript is not None
            and transcript_logical_key(item) not in known_transcript_keys
        ),
        key=_newest_candidate_sort_key,
    )
    routine_current = sorted(
        (item for item in current if item not in new_current),
        key=_newest_candidate_sort_key,
    )
    next_current_cursor = current_cursor
    if routine_current:
        start = (current_cursor.position if current_cursor else 0) % len(routine_current)
        routine_current = [
            routine_current[(start + offset) % len(routine_current)]
            for offset in range(len(routine_current))
        ]
        new_case_count = len(
            {candidate_logical_key(item) for item in new_current}
        )
        available = max(0, nightly_case_limit - new_case_count)
        advance = min(available, len(routine_current))
        raw_position = start + advance
        next_current_cursor = CursorState(
            cursor_key=(
                current_cursor.cursor_key
                if current_cursor
                else f"{mode.value}:current-recheck"
            ),
            position=raw_position % len(routine_current),
            wrapped_count=(current_cursor.wrapped_count if current_cursor else 0)
            + raw_position // len(routine_current),
            updated_at=now,
        )
    current = [*new_current, *routine_current]
    deferred: list[str] = []
    for item in current:
        key = candidate_logical_key(item)
        if key in seen:
            continue
        if len(chosen) >= nightly_case_limit:
            deferred.append(key)
            continue
        seen.add(key)
        transcript_key = transcript_logical_key(item)
        is_new = item.transcript is not None and transcript_key not in known_transcript_keys
        chosen.append(
            SelectedDiscoveryWork(
                item,
                new_transcript_priority if is_new else historical_priority,
                "new_transcript" if is_new else "current_recheck",
            )
        )

    historical_slots = min(historical_limit, max(0, nightly_case_limit - len(chosen)))
    next_cursor = cursor
    if historical and historical_slots:
        start = (cursor.position if cursor is not None else 0) % len(historical)
        count = min(historical_slots, len(historical))
        for offset in range(count):
            item = historical[(start + offset) % len(historical)]
            key = candidate_logical_key(item)
            if key not in seen:
                chosen.append(
                    SelectedDiscoveryWork(item, historical_priority, "historical_recheck")
                )
                seen.add(key)
        selected_historical = {
            candidate_logical_key(item.candidate)
            for item in chosen
            if item.reason == "historical_recheck"
        }
        deferred.extend(
            candidate_logical_key(item)
            for item in historical
            if candidate_logical_key(item) not in selected_historical
        )
        raw_position = start + count
        next_cursor = CursorState(
            cursor_key=(cursor.cursor_key if cursor else f"{mode.value}:historical"),
            position=raw_position % len(historical),
            wrapped_count=(cursor.wrapped_count if cursor else 0) + raw_position // len(historical),
            updated_at=now,
        )
    return DiscoverySelection(
        tuple(chosen),
        next_cursor,
        tuple(sorted(set(deferred))),
        next_current_cursor,
    )


def candidate_logical_key(
    candidate: ScotusArgumentCandidate | ScotusDispositionCandidate,
) -> str:
    return public_case_key(candidate.term, normalize_docket(candidate.primary_docket))


def transcript_logical_key(candidate: ScotusArgumentCandidate) -> str:
    return (
        f"{candidate_logical_key(candidate)}:transcript:"
        f"{candidate.argument_date.date().isoformat()}:{candidate.sequence}"
    )


def document_logical_key(candidate: ScotusArgumentCandidate, descriptor: DocumentDescriptor) -> str:
    """Return identity that survives a corrected document URL or source external ID."""
    kind = _document_kind(descriptor)
    if kind is ScotusDocumentKind.TRANSCRIPT:
        return transcript_logical_key(candidate)
    if kind is ScotusDocumentKind.DOCKET:
        docket = descriptor.external_id.split(":", 1)[0]
        try:
            docket = normalize_docket(docket).casefold()
        except ValueError:
            docket = candidate.primary_docket.casefold()
        return f"{candidate_logical_key(candidate)}:docket:{docket}"
    # A case may have multiple orders/opinions. Their Court external ID distinguishes
    # documents, while URL and retrieval timestamps remain revision metadata.
    safe_external = hashlib.sha256(descriptor.external_id.encode()).hexdigest()[:24]
    return f"{candidate_logical_key(candidate)}:{kind.value}:{safe_external}"


class ScotusDiscoveryCoordinator:
    def __init__(self, registry: SourceRegistry, store: ScotusDiscoveryStore) -> None:
        self.registry = registry
        self.authorizer = SourceAuthorizer(registry)
        self.store = store

    def apply(
        self,
        candidate: ScotusArgumentCandidate,
        now: datetime,
        *,
        priority: int,
    ) -> DiscoveryApplyResult:
        source = self.authorizer.require_source("supreme_court", now)
        self.authorizer.authorize_url(
            source.source_id,
            candidate.official_detail_url,
            source.discovery_method,
            now,
            media=False,
        )
        case_id = deterministic_case_id(candidate.term, candidate.primary_docket)
        argument_id = deterministic_argument_id(
            case_id,
            candidate.argument_date,
            sequence=candidate.sequence,
            reargument=candidate.reargument,
        )
        descriptors = (
            *((candidate.transcript,) if candidate.transcript else ()),
            *candidate.docket_documents,
            *candidate.related_documents,
        )
        documents: list[DiscoveredDocument] = []
        for descriptor in descriptors:
            self.authorizer.authorize_url(
                source.source_id,
                descriptor.official_url,
                descriptor.access_method,
                now,
                media=False,
            )
            documents.append(
                DiscoveredDocument(
                    case_id=case_id,
                    argument_id=(
                        argument_id
                        if descriptor.document_type is DocumentType.OFFICIAL_TRANSCRIPT
                        else None
                    ),
                    kind=_document_kind(descriptor),
                    external_id=document_logical_key(candidate, descriptor),
                    official_url=descriptor.official_url,
                    content_type=descriptor.content_type,
                )
            )
        new_case, new_argument, revision = self.store.save_candidate(
            candidate,
            case_id,
            argument_id,
            _candidate_digest(candidate),
            tuple(documents),
        )
        jobs = 0
        if candidate.transcript is not None and self.store.enqueue_transcript(
            CollectionJob(
                argument_id=argument_id,
                external_id=next(
                    document.external_id
                    for document in documents
                    if document.kind is ScotusDocumentKind.TRANSCRIPT
                ),
                input_version=_descriptor_digest(candidate.transcript),
                priority=priority,
            )
        ):
            jobs = 1
        for descriptor, document in zip(descriptors, documents, strict=True):
            if document.kind is ScotusDocumentKind.TRANSCRIPT:
                continue
            self.store.enqueue_document(
                DocumentCollectionJob(
                    case_id=case_id,
                    kind=document.kind,
                    external_id=document.external_id,
                    input_version=_descriptor_digest(descriptor),
                    priority=priority,
                )
            )
        return DiscoveryApplyResult(
            cases_created=int(new_case),
            arguments_created=int(new_argument),
            metadata_revisions=int(revision),
            transcript_jobs=jobs,
            documents_discovered=len(documents),
        )

    def backfill(
        self,
        term: str,
        candidates: tuple[ScotusArgumentCandidate, ...],
        now: datetime,
        *,
        case_limit: int,
        priority: int,
    ) -> DiscoveryApplyResult:
        checkpoint = self.store.get_backfill_checkpoint(term)
        if checkpoint.examined > 0 and (
            checkpoint.examined > len(candidates)
            or candidates[checkpoint.examined - 1].primary_docket != checkpoint.last_docket
        ):
            checkpoint = BackfillCheckpoint(term=term)
        total = DiscoveryApplyResult()
        examined = checkpoint.examined
        queued = checkpoint.queued
        for candidate in candidates[examined:]:
            if examined >= case_limit:
                break
            result = self.apply(candidate, now, priority=priority)
            examined += 1
            queued += result.transcript_jobs
            total = DiscoveryApplyResult(
                cases_created=total.cases_created + result.cases_created,
                arguments_created=total.arguments_created + result.arguments_created,
                metadata_revisions=total.metadata_revisions + result.metadata_revisions,
                transcript_jobs=total.transcript_jobs + result.transcript_jobs,
                documents_discovered=total.documents_discovered + result.documents_discovered,
            )
            self.store.save_backfill_checkpoint(
                BackfillCheckpoint(
                    term=term,
                    examined=examined,
                    queued=queued,
                    last_docket=candidate.primary_docket,
                    complete=examined >= len(candidates),
                )
            )
        if examined >= len(candidates):
            self.store.save_backfill_checkpoint(
                BackfillCheckpoint(
                    term=term,
                    examined=examined,
                    queued=queued,
                    last_docket=(
                        checkpoint.last_docket if not candidates else candidates[-1].primary_docket
                    ),
                    complete=True,
                )
            )
        return total
