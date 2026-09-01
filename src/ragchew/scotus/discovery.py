"""Transcript-first Supreme Court case and argument discovery."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field

from ragchew.contracts import StrictModel
from ragchew.proceedings.contracts import DocumentType, UtcDatetime
from ragchew.proceedings.discovery import DiscoveredProceeding, DocumentDescriptor
from ragchew.proceedings.registry import SourceAuthorizer, SourceRegistry
from ragchew.scotus.contracts import ScotusDocumentKind

_DOCKET = re.compile(
    r"^(?:[0-9]{1,3}(?:A)?-[0-9A-Z]+(?:\s+ORIG\.)?|"
    r"[0-9]{1,3}A[0-9]+|[0-9]{1,3}\s+ORIG\.)$",
    re.IGNORECASE,
)


def normalize_docket(value: str) -> str:
    normalized = " ".join(value.replace("\N{EN DASH}", "-").strip().upper().split())
    if not _DOCKET.fullmatch(normalized):
        raise ValueError(f"invalid Supreme Court docket number: {value}")
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


def _candidate_digest(candidate: ScotusArgumentCandidate) -> str:
    payload = candidate.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _descriptor_digest(descriptor: DocumentDescriptor) -> str:
    payload = descriptor.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


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
                    external_id=descriptor.external_id,
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
                external_id=candidate.transcript.external_id,
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
        if (
            checkpoint.examined > 0
            and (
                checkpoint.examined > len(candidates)
                or candidates[checkpoint.examined - 1].primary_docket
                != checkpoint.last_docket
            )
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
                        checkpoint.last_docket
                        if not candidates
                        else candidates[-1].primary_docket
                    ),
                    complete=True,
                )
            )
        return total
