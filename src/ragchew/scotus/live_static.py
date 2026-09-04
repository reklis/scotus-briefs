"""Concrete no-backend live adapter for bounded static SCOTUS publication.

Only sanitized public contracts leave this module. Court responses, document copies,
parsed text, observations, prompts, and model responses remain in a run-scoped 0700
workspace or in memory and are discarded before ``run`` returns.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, BinaryIO, Literal, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    DefaultHttpxClient,
    InternalServerError,
    OpenAI,
    RateLimitError,
    omit,
)
from pydantic import ValidationError
from pypdf import PdfReader

from ragchew.config import ProceedingsConfig, ScotusConfig, ServiceSettings
from ragchew.proceedings.contracts import (
    DocumentType,
    OfficialSource,
    SourceAccessMethod,
    SourceHealth,
)
from ragchew.proceedings.discovery import ConditionalRequest, DocumentDescriptor
from ragchew.proceedings.registry import (
    InMemorySourceRegistry,
    SourceAuthorizationError,
    SourceAuthorizer,
)
from ragchew.proceedings.sources.http import (
    HttpxSourceFetcher,
    RequestRateLimiter,
    SourceFetcher,
    SourceResponse,
)
from ragchew.proceedings.sources.supreme_court import (
    SupremeCourtAdapter,
    parse_related_opinion_documents,
)
from ragchew.scotus.briefs import (
    BriefCandidate,
    BriefGenerationService,
    BriefValidationError,
    CaseArgumentSession,
    InMemoryBriefRevisionStore,
    OpenAILegalBriefGenerator,
    evaluate_brief_candidate,
    simple_brief_json_schema,
)
from ragchew.scotus.contracts import (
    LegalObservation,
    ScotusCaseStatus,
    ScotusDocumentKind,
)
from ragchew.scotus.correlation import ScotusCorrelationEngine
from ragchew.scotus.discovery import (
    DiscoveryMode,
    IncrementalDiscoveryOperation,
    ScotusArgumentCandidate,
    candidate_logical_key,
    deterministic_argument_id,
    deterministic_case_id,
    document_logical_key,
    select_discovery_resources,
    select_discovery_work,
    transcript_logical_key,
)
from ragchew.scotus.documents import (
    AcceptedDocument,
    DocumentCollectionError,
    InMemoryDocumentIngestionStore,
    PendingDocument,
    ScotusDocumentCollector,
)
from ragchew.scotus.extraction import (
    InMemoryLegalObservationStore,
    LegalEvidenceBlock,
    LegalExtractionError,
    LegalExtractionInput,
    LegalExtractionService,
    OpenAILegalObservationExtractor,
    bounded_contexts,
    document_text_block,
    transcript_turn_block,
)
from ragchew.scotus.public_contracts import (
    PublicBriefRevisionSummary,
    PublicCaseBrief,
    PublicCaseHistoryEvent,
    ScotusPublicProjection,
    public_case_key,
)
from ragchew.scotus.publishing import build_public_case
from ragchew.scotus.static_contracts import (
    ConditionalValidators,
    ContentIntegrity,
    CostReceiptBundle,
    LogicalDocumentState,
    LogicalSourceState,
    ModelAttemptReceipt,
    ProcessorFingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from ragchew.scotus.static_pipeline import (
    ArgumentSessionWork,
    CaseProcessingResult,
    PublicationGateDenied,
    RunWorkspace,
    StaticBatchOrchestrator,
    StaticBatchResult,
    StaticCaseWork,
    StaticDiscoveryResult,
    UnifiedRunBudget,
    call_with_bounded_transport_retries,
    failure_category,
)
from ragchew.scotus.static_state import GeneratedContent, StaticStateStore
from ragchew.scotus.transcript_parser import (
    PdfTextBackend,
    PypdfTextBackend,
    ScotusTranscriptParser,
    deterministic_parse_revision_id,
)
from ragchew.storage import ObjectMetadata, ObjectStore

POLICY_VERSION = "scotus-brief-policy-v1"
DOCUMENT_TEXT_VERSION = "official-document-text-v1"


class OllamaClientFactory(Protocol):
    def __call__(self, settings: ServiceSettings, config: ScotusConfig) -> Any: ...


class SourceFetcherFactory(Protocol):
    def __call__(
        self, settings: ServiceSettings, config: ScotusConfig
    ) -> SourceFetcher: ...


class DocumentClientFactory(Protocol):
    def __call__(
        self, settings: ServiceSettings, config: ScotusConfig
    ) -> httpx.Client: ...


@dataclass(frozen=True)
class _CaseInput:
    case_key: str
    term: str
    primary_docket: str
    caption: str
    sessions: tuple[ScotusArgumentCandidate, ...]
    prior: PublicCaseBrief | None
    document_logical_keys: Mapping[tuple[DocumentType, str], str]


@dataclass(frozen=True)
class _PrivateDocument:
    logical_key: str
    descriptor: DocumentDescriptor
    argument_id: UUID | None

    @property
    def kind(self) -> ScotusDocumentKind:
        return _kind(self.descriptor)


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.values.append(value)


class _AuthorizedFetcher:
    """Authorize every nested Court fetch before delegating to HTTP."""

    def __init__(self, delegate: SourceFetcher, authorizer: SourceAuthorizer, now: datetime):
        self.delegate = delegate
        self.authorizer = authorizer
        self.now = now

    def get(self, url: str, conditional: Any = None) -> SourceResponse:
        self.authorizer.authorize_url(
            "supreme_court",
            url,
            SourceAccessMethod.OFFICIAL_PAGE,
            self.now,
            media=False,
        )
        return self.delegate.get(url, conditional)


class _LocalObjectStore(ObjectStore):
    """Private filesystem object adapter rooted inside one run workspace."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._metadata: dict[str, ObjectMetadata] = {}

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.root / digest[:2] / digest[2:]

    def create_upload(self, key: str, content_type: str, sha256: str) -> str:
        del key, content_type, sha256
        raise RuntimeError("ephemeral objects do not expose upload URLs")

    def head(self, key: str) -> ObjectMetadata:
        return self._metadata[key]

    def create_download(self, key: str, expires_seconds: int = 300) -> str:
        del key, expires_seconds
        raise RuntimeError("ephemeral objects do not expose download URLs")

    def put_file(self, key: str, file: BinaryIO, content_type: str, sha256: str) -> None:
        target = self._path(key)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        digest = hashlib.sha256()
        count = 0
        file.seek(0)
        with temporary.open("wb") as output:
            os.chmod(temporary, 0o600)
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
                count += len(chunk)
                output.write(chunk)
        if digest.hexdigest() != sha256:
            temporary.unlink(missing_ok=True)
            raise ValueError("private object digest changed during write")
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        self._metadata[key] = ObjectMetadata(count, content_type, sha256)
        file.seek(0)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
        self._metadata.pop(key, None)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def open(self, key: str) -> BinaryIO:
        return cast(BinaryIO, self._path(key).open("rb"))

    def clear(self) -> None:
        self._metadata.clear()


class _ReceiptSink:
    """Persist only opaque, schema-validated receipts outside cleaned private data."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.receipts: dict[tuple[str, str, int], ModelAttemptReceipt] = {}

    def __call__(self, receipt: ModelAttemptReceipt) -> None:
        key = (receipt.stage, receipt.input_fingerprint, receipt.attempt_number)
        prior = self.receipts.get(key)
        if (
            prior is not None
            and prior != receipt
            and (
                prior.outcome.value != "attempted"
                or receipt.outcome.value not in {"succeeded", "failed"}
                or prior.attempted_at != receipt.attempted_at
            )
        ):
            raise RuntimeError("conflicting model receipt identity")
        self.receipts[key] = receipt
        bundle = CostReceiptBundle(
            receipts=tuple(self.receipts[item] for item in sorted(self.receipts))
        )
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(canonical_json_bytes(bundle))
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)


class _BudgetedModelRequest:
    def __init__(
        self,
        *,
        client: Any,
        budget: UnifiedRunBudget,
        stage: Literal["extraction", "brief"],
        document_digests: tuple[str, ...],
        processor_versions: Mapping[str, str],
        output_tokens: int,
        authorized_replay: bool,
    ) -> None:
        self.client = client
        self.budget = budget
        self.stage = stage
        self.document_digests = document_digests
        self.processor_versions = processor_versions
        self.output_tokens = output_tokens
        self.authorized_replay = authorized_replay

    def __call__(self, request: dict[str, Any]) -> Any:
        # Ollama reasoning can consume the whole output/time budget before emitting the
        # required JSON. This production wrapper is Ollama-only, so disable hidden
        # reasoning and include that transport choice in the request fingerprint.
        provider_request = {**request, "extra_body": {"think": False}}
        payload = _request_payload(provider_request)
        serialized = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        # UTF-8 bytes are a deliberately conservative provider-token upper bound and
        # the character count is exact for the request actually sent by the SDK.
        processor_versions = {
            **self.processor_versions,
            "request": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            **(
                {"authorized_replay": str(self.budget.started_monotonic)}
                if self.authorized_replay
                else {}
            ),
        }
        permit = self.budget.authorize_model_request(
            stage=self.stage,
            document_digests=self.document_digests,
            processor_versions=processor_versions,
            input_characters=len(serialized),
            input_tokens=len(serialized.encode("utf-8")),
            output_tokens=self.output_tokens,
            authorized_replay=self.authorized_replay,
        )
        return call_with_bounded_transport_retries(
            lambda: self.client.chat.completions.create(**provider_request),
            permit=permit,
            maximum_attempts=self.budget.config.model_budget.maximum_transport_attempts,
            retryable=_retryable_model_error,
            response_usage=_model_response_usage,
        )


def _model_response_usage(response: Any) -> tuple[int, int] | None:
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "prompt_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    if (
        isinstance(input_tokens, int)
        and input_tokens >= 0
        and isinstance(output_tokens, int)
        and output_tokens >= 0
    ):
        return input_tokens, output_tokens
    return None


def _request_payload(value: Any) -> Any:
    """Mirror SDK omission and return JSON used for exact pre-transport sizing."""
    if value is omit:
        return None
    if isinstance(value, dict):
        return {
            key: _request_payload(item)
            for key, item in value.items()
            if item is not omit
        }
    if isinstance(value, (list, tuple)):
        return [_request_payload(item) for item in value if item is not omit]
    return value


def _retryable_model_error(error: BaseException) -> bool:
    return isinstance(
        error,
        (
            TimeoutError,
            httpx.TransportError,
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
            InternalServerError,
        ),
    )


def _kind(descriptor: DocumentDescriptor) -> ScotusDocumentKind:
    return {
        DocumentType.OFFICIAL_TRANSCRIPT: ScotusDocumentKind.TRANSCRIPT,
        DocumentType.DOCKET: ScotusDocumentKind.DOCKET,
        DocumentType.ORDER: ScotusDocumentKind.ORDER,
        DocumentType.OPINION: ScotusDocumentKind.OPINION,
    }.get(descriptor.document_type, ScotusDocumentKind.OTHER_OFFICIAL)


def _descriptor_from_document_state(state: LogicalDocumentState) -> DocumentDescriptor:
    document_type = {
        "transcript": DocumentType.OFFICIAL_TRANSCRIPT,
        "docket": DocumentType.DOCKET,
        "order": DocumentType.ORDER,
        "opinion": DocumentType.OPINION,
    }[state.document_kind]
    return DocumentDescriptor(
        # The exact logical key is sanitized durable identity. Do not invent a new
        # Court external ID that could change the document's kind or revision lineage.
        external_id=state.logical_key,
        document_type=document_type,
        official_url=state.official_url,
        access_method=SourceAccessMethod.OFFICIAL_PAGE,
        content_type=("text/html" if document_type is DocumentType.DOCKET else "application/pdf"),
    )


def _descriptor_for_public_argument(
    case: PublicCaseBrief,
    index: int,
    documents: Sequence[LogicalDocumentState],
) -> ScotusArgumentCandidate:
    argument = case.arguments[index]
    transcript_key = (
        f"{public_case_key(case.term, case.primary_docket)}:transcript:"
        f"{argument.argument_date.date().isoformat()}:{argument.sequence}"
    )
    transcript_state = next(
        (item for item in documents if item.logical_key == transcript_key),
        None,
    )
    if transcript_state is None:
        transcript = DocumentDescriptor(
            external_id=transcript_key,
            document_type=DocumentType.OFFICIAL_TRANSCRIPT,
            official_url=argument.official_transcript_url,
            access_method=SourceAccessMethod.OFFICIAL_PAGE,
            content_type="application/pdf",
        )
    elif transcript_state.document_kind != "transcript":
        raise ValueError("public case transcript document identity has the wrong kind")
    else:
        transcript = _descriptor_from_document_state(transcript_state)
    shared_by_identity = {
        (item.document_type, item.official_url): item
        for item in (
            _descriptor_from_document_state(state)
            for state in documents
            if state.document_kind != "transcript"
        )
    }
    docket_identity = (DocumentType.DOCKET, case.official_docket_url)
    shared_by_identity.setdefault(
        docket_identity,
        DocumentDescriptor(
            external_id=f"{public_case_key(case.term, case.primary_docket)}:docket:public",
            document_type=DocumentType.DOCKET,
            official_url=case.official_docket_url,
            access_method=SourceAccessMethod.OFFICIAL_PAGE,
            content_type="text/html",
        ),
    )
    for index, url in enumerate(case.official_disposition_urls, start=1):
        document_type = (
            DocumentType.ORDER if "/orders/" in url.casefold() else DocumentType.OPINION
        )
        shared_by_identity.setdefault(
            (document_type, url),
            DocumentDescriptor(
                external_id=(
                    f"{public_case_key(case.term, case.primary_docket)}:"
                    f"{document_type.value}:public:{index}"
                ),
                document_type=document_type,
                official_url=url,
                access_method=SourceAccessMethod.OFFICIAL_PAGE,
                content_type="application/pdf",
            ),
        )
    shared = tuple(
        shared_by_identity[key]
        for key in sorted(shared_by_identity, key=lambda item: (item[0].value, item[1]))
    )
    return ScotusArgumentCandidate(
        term=case.term,
        primary_docket=case.primary_docket,
        caption=case.caption,
        argument_date=argument.argument_date,
        sequence=argument.sequence,
        reargument=argument.reargument,
        official_detail_url=argument.official_detail_url,
        transcript=transcript,
        docket_documents=tuple(
            item for item in shared if item.document_type is DocumentType.DOCKET
        ),
        related_documents=tuple(
            item
            for item in shared
            if item.document_type in {DocumentType.ORDER, DocumentType.OPINION}
        ),
        source_metadata={"public_checkpoint": True},
    )


def _source_from_config(config: ProceedingsConfig) -> OfficialSource:
    source = config.sources.get("supreme_court")
    if source is None:
        raise SourceAuthorizationError("reviewed Supreme Court source is not configured")
    return OfficialSource(
        source_id=source.source_id,
        authority=source.authority,
        jurisdiction=source.jurisdiction,
        display_name="Supreme Court of the United States",
        official_index_url=source.official_index_url,
        adapter=source.adapter,
        discovery_method=source.discovery_method,
        media_method=source.media_method,
        access_basis=source.access_basis,
        access_reviewed_at=source.access_reviewed_at,
        access_reviewed_by=source.access_reviewed_by,
        access_review_expires_at=source.access_review_expires_at,
        allowed_hosts=tuple(source.allowed_hosts),
        poll_interval_seconds=source.poll_interval_seconds,
        expected_schedule=source.expected_schedule,
        enabled=source.enabled,
        health=SourceHealth.HEALTHY if source.enabled else SourceHealth.DISABLED,
    )


def _validate_live_gates(config: ScotusConfig) -> None:
    if not config.enabled:
        raise PublicationGateDenied("SCOTUS live processing gate is closed")
    if not config.generation.brief_generation_enabled or not config.publication.enabled:
        raise PublicationGateDenied("brief-generation and publication gates are closed")
    if not config.approvals.all_live_gates_approved():
        raise PublicationGateDenied("live publication approvals are incomplete")
    if config.generation.prompt_version != OpenAILegalBriefGenerator.PROMPT_VERSION:
        raise PublicationGateDenied("configured brief prompt version is not implemented")


class LiveStaticDiscovery:
    """Conditional Court discovery plus bounded whole-case grouping."""

    def __init__(
        self,
        *,
        adapters: Mapping[str, SupremeCourtAdapter],
        config: ScotusConfig,
        model_endpoint: str,
    ) -> None:
        self.adapters = dict(adapters)
        self.config = config
        self.model_endpoint = model_endpoint
        self.cases: dict[str, _CaseInput] = {}
        self.now: datetime | None = None

    def discover(
        self,
        *,
        mode: DiscoveryMode,
        content: GeneratedContent,
        budget: UnifiedRunBudget,
        now: datetime,
    ) -> StaticDiscoveryResult:
        del budget  # nested fetchers account against this same instance
        self.now = now
        checkpoints = {item.logical_key: item for item in content.publication.sources}
        cursor_key = f"{mode.value}:resource-recheck"
        cursor = next(
            (item for item in content.publication.cursors if item.cursor_key == cursor_key),
            None,
        )
        resources = select_discovery_resources(
            tuple(self.adapters),
            active_term=self.config.discovery.active_term,
            mode=mode,
            historical_limit=self.config.discovery.historical_rechecks_per_run,
            bootstrap_term_limit=self.config.bootstrap.maximum_terms_per_run,
            now=now,
            cursor=cursor,
        )
        incremental = IncrementalDiscoveryOperation(self.adapters, checkpoints).run(
            active_term=self.config.discovery.active_term,
            mode=mode,
            historical_limit=self.config.discovery.historical_rechecks_per_run,
            bootstrap_term_limit=self.config.bootstrap.maximum_terms_per_run,
            now=now,
            cursor=cursor,
        )
        prior_cases = {
            public_case_key(case.term, case.primary_docket): case
            for case in (content.projection.cases if content.projection else ())
        }
        documents_by_case: dict[str, list[LogicalDocumentState]] = {}
        for document in content.publication.documents:
            documents_by_case.setdefault(document.case_key, []).append(document)
        candidates_by_session: dict[tuple[str, str, int], ScotusArgumentCandidate] = {}
        public_transcript_keys: set[str] = set()
        for case_key, case in prior_cases.items():
            case_documents = tuple(documents_by_case.get(case_key, ()))
            for index in range(len(case.arguments)):
                # Sanitized legacy imports lack logical document checkpoints. The public
                # official transcript URL is sufficient to reconstruct safe identity;
                # current Court bytes are still downloaded and validated before reuse.
                item = _descriptor_for_public_argument(case, index, case_documents)
                candidates_by_session[
                    (case_key, item.argument_date.date().isoformat(), item.sequence)
                ] = item
                public_transcript_keys.add(transcript_logical_key(item))
        processor = _processor_contract(self.config, self.model_endpoint)
        pointer_by_case = {
            pointer.case_key: pointer for pointer in content.publication.cases
        }
        # A concrete prior processor fingerprint can be migrated when it changes.
        # Missing fingerprints identify sanitized legacy imports; do not redownload and
        # regenerate those accepted briefs unless current Court metadata actually changes.
        legacy_case_keys = {
            case_key
            for case_key in prior_cases
            if pointer_by_case.get(case_key) is not None
            and pointer_by_case[case_key].processor_sha256 is None
        }
        migration_case_keys = {
            case_key
            for case_key in prior_cases
            if pointer_by_case.get(case_key) is not None
            and pointer_by_case[case_key].processor_sha256 is not None
            and pointer_by_case[case_key].processor_sha256 != processor.composite_sha256
        }
        changed_case_keys: set[str] = set(migration_case_keys)
        source_changed_case_keys: set[str] = set()
        for item in incremental.candidates:
            key = candidate_logical_key(item)
            identity = (key, item.argument_date.date().isoformat(), item.sequence)
            previous = candidates_by_session.get(identity)
            if previous is None or _candidate_metadata_changed(previous, item):
                changed_case_keys.add(key)
                source_changed_case_keys.add(key)
            candidates_by_session[identity] = _merge_candidate(previous, item)

        related_sources, related_changes = self._poll_related_indices(
            terms=resources.terms,
            candidates=candidates_by_session,
            checkpoints=checkpoints,
            now=now,
        )
        changed_case_keys.update(related_changes)
        source_changed_case_keys.update(related_changes)
        combined = tuple(candidates_by_session.values())
        known = {
            *(item.logical_key for item in content.publication.documents),
            *public_transcript_keys,
        }
        selection_known = known - {
            transcript_logical_key(item)
            for item in combined
            if candidate_logical_key(item) in changed_case_keys
        }
        selection_cursor = next(
            (
                item
                for item in content.publication.cursors
                if item.cursor_key == f"{mode.value}:historical"
            ),
            None,
        )
        current_cursor = next(
            (
                item
                for item in content.publication.cursors
                if item.cursor_key == f"{mode.value}:current-recheck"
            ),
            None,
        )
        case_limit = (
            self.config.bootstrap.maximum_cases_per_run
            if mode is DiscoveryMode.BOOTSTRAP
            else self.config.runner_limits.maximum_cases_per_run
        )
        selection = select_discovery_work(
            combined,
            mode=mode,
            now=now,
            active_term=self.config.discovery.active_term,
            known_transcript_keys=selection_known,
            nightly_case_limit=case_limit,
            new_transcript_priority=self.config.discovery.new_transcript_priority,
            historical_priority=self.config.discovery.backfill_priority,
            historical_limit=self.config.discovery.historical_rechecks_per_run,
            recent_lookback_days=self.config.discovery.backfill_lookback_days,
            recent_correction_lookback_days=(
                self.config.discovery.recent_correction_lookback_days
            ),
            recent_opinion_lookback_days=self.config.discovery.recent_opinion_lookback_days,
            cursor=selection_cursor,
            current_cursor=current_cursor,
            bootstrap_term_limit=self.config.bootstrap.maximum_terms_per_run,
        )
        selected: dict[str, tuple[int, str]] = {}
        # Changed and stale-processor cases outrank rotating rechecks. Because each
        # successful migration updates its case pointer, this bounded deterministic
        # prefix drains across later runs even for terms outside bootstrap polling.
        ordered_changes = (
            *sorted(source_changed_case_keys),
            *sorted(changed_case_keys - source_changed_case_keys),
        )
        for key in ordered_changes:
            if len(selected) >= case_limit:
                break
            reason = "source_change" if key in source_changed_case_keys else "processor_migration"
            selected[key] = (self.config.discovery.new_transcript_priority, reason)
        for selected_item in selection.work:
            if len(selected) >= case_limit:
                break
            key = candidate_logical_key(selected_item.candidate)
            if key in legacy_case_keys and key not in source_changed_case_keys:
                continue
            selected_prior = selected.get(key)
            value = (selected_item.priority, selected_item.reason)
            selected[key] = min(selected_prior, value) if selected_prior else value

        work: list[StaticCaseWork] = []
        invalid_case_keys: set[str] = set()
        ordered_selection = sorted(
            selected.items(), key=lambda item: (item[1][0], item[0])
        )
        for key, (priority, reason) in ordered_selection:
            sessions = tuple(
                sorted(
                    (
                        candidate
                        for (case_key, _date, _sequence), candidate in candidates_by_session.items()
                        if case_key == key and candidate.transcript is not None
                    ),
                    key=lambda item: (item.argument_date, item.sequence),
                )
            )
            if not sessions:
                continue
            prior = prior_cases.get(key)
            case_documents = tuple(documents_by_case.get(key, ()))
            self.cases[key] = _CaseInput(
                case_key=key,
                term=sessions[-1].term,
                primary_docket=sessions[-1].primary_docket,
                caption=sessions[-1].caption,
                sessions=sessions,
                prior=prior,
                document_logical_keys={
                    (_descriptor_from_document_state(item).document_type, item.official_url): (
                        item.logical_key
                    )
                    for item in case_documents
                },
            )
            session_work: list[ArgumentSessionWork] = []
            try:
                all_documents = _case_documents(self.cases[key])
            except ValueError:
                # One malformed public/official descriptor must not prevent unrelated
                # new cases from reaching their independent fail-closed validation.
                invalid_case_keys.add(key)
                self.cases.pop(key, None)
                continue
            shared = tuple(
                item.logical_key
                for item in all_documents
                if item.kind is not ScotusDocumentKind.TRANSCRIPT
            )
            for index, session in enumerate(sessions):
                keys = (transcript_logical_key(session), *(shared if index == 0 else ()))
                session_work.append(
                    ArgumentSessionWork(
                        session_key=_session_key(session),
                        document_keys=tuple(dict.fromkeys(keys)),
                    )
                )
            work.append(StaticCaseWork(key, priority, tuple(session_work), reason))

        cursors = tuple(
            item
            for item in (
                incremental.cursor,
                selection.cursor,
                selection.current_cursor,
            )
            if item is not None
        )
        # Source checkpoints describe complete index responses. Case-level rotation may
        # intentionally leave document rechecks for later and does not make that source
        # response unsafe; changed source cases, however, must all fit this run.
        runnable_case_keys = {item.case_key for item in work}
        deferred_case_keys = tuple(
            sorted((changed_case_keys - runnable_case_keys) | invalid_case_keys)
        )
        changed_deferred = bool(set(deferred_case_keys) & source_changed_case_keys)
        return StaticDiscoveryResult(
            work=tuple(work),
            sources=tuple(
                {
                    item.logical_key: item
                    for item in (*incremental.checkpoints, *related_sources)
                }[key]
                for key in sorted(
                    {
                        item.logical_key
                        for item in (*incremental.checkpoints, *related_sources)
                    }
                )
            ),
            cursors=tuple(
                sorted(
                    {item.cursor_key: item for item in cursors}.values(),
                    key=lambda item: item.cursor_key,
                )
            ),
            processor=processor,
            deferred_case_keys=deferred_case_keys,
            resolved_pending_case_keys=tuple(
                sorted(legacy_case_keys - changed_case_keys)
            ),
            checkpoint_safe=not changed_deferred,
        )

    def _poll_related_indices(
        self,
        *,
        terms: tuple[str, ...],
        candidates: dict[tuple[str, str, int], ScotusArgumentCandidate],
        checkpoints: Mapping[str, LogicalSourceState],
        now: datetime,
    ) -> tuple[tuple[LogicalSourceState, ...], set[str]]:
        states: list[LogicalSourceState] = []
        changed_cases: set[str] = set()
        for term in terms:
            adapter = self.adapters[term]
            for source_kind, document_type, url in (
                ("opinions", DocumentType.OPINION, adapter.opinion_index_url),
                ("orders", DocumentType.ORDER, adapter.order_index_url),
            ):
                logical_key = f"{source_kind}:{term}"
                prior = checkpoints.get(logical_key)
                conditional = ConditionalRequest(
                    etag=prior.validators.etag if prior else None,
                    last_modified=prior.validators.last_modified if prior else None,
                )
                response = adapter.fetcher.get(url, conditional)
                validators = ConditionalValidators(
                    etag=response.headers.get("etag")
                    or (prior.validators.etag if prior else None),
                    last_modified=response.headers.get("last-modified")
                    or (prior.validators.last_modified if prior else None),
                )
                if response.status_code == 304:
                    if prior is None:
                        raise ValueError("related index returned 304 without a checkpoint")
                    states.append(
                        prior.model_copy(
                            update={"validators": validators, "checked_at": now}
                        )
                    )
                    continue
                integrity = ContentIntegrity(
                    sha256=sha256_hex(response.content),
                    byte_count=len(response.content),
                )
                states.append(
                    LogicalSourceState(
                        logical_key=logical_key,
                        source_kind=cast(Literal["opinions", "orders"], source_kind),
                        official_url=url,
                        validators=validators,
                        integrity=integrity,
                        checked_at=now,
                    )
                )
                if prior is not None and prior.integrity == integrity:
                    continue
                html = response.text()
                for identity, candidate in tuple(candidates.items()):
                    if candidate.term != term:
                        continue
                    discovered = parse_related_opinion_documents(
                        html,
                        url,
                        candidate.primary_docket,
                        document_type=document_type,
                    )
                    if not discovered:
                        continue
                    prior_same_kind = tuple(
                        item
                        for item in candidate.related_documents
                        if item.document_type is document_type
                    )
                    if prior_same_kind and not _descriptor_urls(
                        prior_same_kind
                    ).issubset(_descriptor_urls(discovered)):
                        # Recompute against only the current official-index descriptors.
                        # The prior public case remains active unless that replacement
                        # independently passes every case and release validator.
                        retained = tuple(
                            item
                            for item in candidate.related_documents
                            if item.document_type is not document_type
                        )
                        related_documents = _merge_descriptors(retained, discovered)
                    else:
                        related_documents = _merge_descriptors(
                            candidate.related_documents, discovered
                        )
                    updated = candidate.model_copy(
                        update={"related_documents": related_documents}
                    )
                    if _candidate_metadata_changed(candidate, updated):
                        changed_cases.add(candidate_logical_key(candidate))
                    candidates[identity] = updated
        return tuple(states), changed_cases

    def close(self) -> None:
        self.cases.clear()
        self.now = None


def _descriptor_urls(values: Sequence[DocumentDescriptor]) -> set[tuple[DocumentType, str]]:
    return {(item.document_type, item.official_url) for item in values}


def _candidate_metadata_changed(
    prior: ScotusArgumentCandidate, current: ScotusArgumentCandidate
) -> bool:
    prior_transcript = prior.transcript.official_url if prior.transcript else None
    current_transcript = current.transcript.official_url if current.transcript else None
    return bool(
        prior.term != current.term
        or prior.primary_docket != current.primary_docket
        or prior.caption != current.caption
        or prior.argument_date != current.argument_date
        or prior.sequence != current.sequence
        or prior.reargument != current.reargument
        or prior.official_detail_url != current.official_detail_url
        or prior_transcript != current_transcript
        or not _descriptor_urls(current.docket_documents).issubset(
            _descriptor_urls(prior.docket_documents)
        )
        or not _descriptor_urls(current.related_documents).issubset(
            _descriptor_urls(prior.related_documents)
        )
    )


def _merge_descriptors(
    old: Sequence[DocumentDescriptor], new: Sequence[DocumentDescriptor]
) -> tuple[DocumentDescriptor, ...]:
    documents = {
        (item.document_type, item.official_url): item for item in (*old, *new)
    }
    return tuple(
        documents[key]
        for key in sorted(documents, key=lambda value: (value[0].value, value[1]))
    )


def _merge_candidate(
    prior: ScotusArgumentCandidate | None, current: ScotusArgumentCandidate
) -> ScotusArgumentCandidate:
    if prior is None:
        return current
    return current.model_copy(
        update={
            "docket_documents": _merge_descriptors(
                prior.docket_documents, current.docket_documents
            ),
            "related_documents": _merge_descriptors(
                prior.related_documents, current.related_documents
            ),
        }
    )


def _session_key(candidate: ScotusArgumentCandidate) -> str:
    return (
        f"{candidate.argument_date.date().isoformat()}:"
        f"{candidate.sequence}:{int(candidate.reargument)}"
    )


def _case_documents(case: _CaseInput) -> tuple[_PrivateDocument, ...]:
    values: dict[str, _PrivateDocument] = {}
    case_id = deterministic_case_id(case.term, case.primary_docket)
    for session in case.sessions:
        argument_id = deterministic_argument_id(
            case_id,
            session.argument_date,
            sequence=session.sequence,
            reargument=session.reargument,
        )
        descriptors: Sequence[DocumentDescriptor] = (
            *((session.transcript,) if session.transcript else ()),
            *session.docket_documents,
            *session.related_documents,
        )
        for descriptor in descriptors:
            kind = _kind(descriptor)
            if kind not in {
                ScotusDocumentKind.TRANSCRIPT,
                ScotusDocumentKind.DOCKET,
                ScotusDocumentKind.ORDER,
                ScotusDocumentKind.OPINION,
            }:
                continue
            key = case.document_logical_keys.get(
                (descriptor.document_type, descriptor.official_url),
                document_logical_key(session, descriptor),
            )
            values[key] = _PrivateDocument(
                logical_key=key,
                descriptor=descriptor,
                argument_id=argument_id if kind is ScotusDocumentKind.TRANSCRIPT else None,
            )
    return tuple(values[key] for key in sorted(values))


def _model_identity(config: ScotusConfig, model_endpoint: str) -> str:
    return f"{config.generation.provider}:{config.generation.model}@{model_endpoint}"


def _processor_contract(config: ScotusConfig, model_endpoint: str) -> ProcessorFingerprint:
    config_digest = sha256_hex(canonical_json_bytes(config, privacy_check=False))
    parser = f"{config.parser.name}:{config.parser.version}"
    model_identity = _model_identity(config, model_endpoint)
    extractor = (
        f"{LegalExtractionService.SCHEMA_VERSION}:"
        f"{OpenAILegalObservationExtractor.PROMPT_VERSION}"
    )
    composite = sha256_hex(
        canonical_json_bytes(
            {
                "config": config_digest,
                "endpoint": model_endpoint,
                "extractor": extractor,
                "model": config.generation.model,
                "provider": config.generation.provider,
                "parser": parser,
                "policy": POLICY_VERSION,
                "prompt": config.generation.prompt_version,
            },
            privacy_check=False,
        )
    )
    return ProcessorFingerprint(
        parser_version=parser,
        extractor_version=extractor,
        policy_version=POLICY_VERSION,
        model=model_identity,
        prompt_version=config.generation.prompt_version,
        config_sha256=config_digest,
        composite_sha256=composite,
    )


class LiveStaticCaseProcessor:
    """Recompute one selected durable case entirely in ephemeral adapters."""

    def __init__(
        self,
        *,
        discovery: LiveStaticDiscovery,
        authorizer: SourceAuthorizer,
        config: ScotusConfig,
        document_client: httpx.Client,
        model_client: Any,
        model_endpoint: str,
        user_agent: str,
        before_court_request: Callable[[], None],
        parser_backend_factory: Callable[[], PdfTextBackend],
    ) -> None:
        self.discovery = discovery
        self.authorizer = authorizer
        self.config = config
        self.document_client = document_client
        self.model_client = model_client
        self.model_endpoint = model_endpoint
        self.user_agent = user_agent
        self.before_court_request = before_court_request
        self.parser_backend_factory = parser_backend_factory
        self._documents: tuple[LogicalDocumentState, ...] = ()
        self._processor_case_fingerprints: dict[str, str | None] = {}

    def process(
        self,
        work: StaticCaseWork,
        *,
        workspace: RunWorkspace,
        budget: UnifiedRunBudget,
        authorized_replay: bool,
    ) -> CaseProcessingResult:
        source = self.discovery.cases[work.case_key]
        now = self.discovery.now
        if now is None:
            raise RuntimeError("case processing started before discovery")
        case_id = deterministic_case_id(source.term, source.primary_docket)
        prior_documents = {
            item.logical_key: item
            for item in self._content_documents(work.case_key)
        }
        store = InMemoryDocumentIngestionStore()
        objects = _LocalObjectStore(workspace.private_path("downloads", work.case_key))
        try:
            self._seed_prior_documents(store, prior_documents, case_id)
            collector = ScotusDocumentCollector(
                self.authorizer,
                store,
                objects,
                self.config,
                user_agent=self.user_agent,
                client=self.document_client,
                reserve_request=budget.reserve_http_request,
                record_download=budget.record_download,
                before_request=self.before_court_request,
                retain_duplicate_bytes=True,
                spool_directory=workspace.downloads,
            )
            outcomes: dict[str, Any] = {}
            states: dict[str, LogicalDocumentState] = {}
            changed_keys: set[str] = set()
            private_documents = _case_documents(source)
            required_transcripts = {
                transcript_logical_key(session) for session in source.sessions
            }
            found_transcripts = {
                item.logical_key
                for item in private_documents
                if item.kind is ScotusDocumentKind.TRANSCRIPT
            }
            if found_transcripts != required_transcripts:
                raise DocumentCollectionError("case does not have every required transcript")

            for item in private_documents:
                prior = prior_documents.get(item.logical_key)
                pending = _pending_document(item, source, case_id, prior, now)
                outcome = collector.collect(
                    pending,
                    now,
                    priority=work.priority,
                    checkpoint=prior,
                    allocate_revision=True,
                )
                if outcome.status not in {"ready", "duplicate"}:
                    raise DocumentCollectionError("official document collection failed")
                if (
                    outcome.sha256 is None
                    or outcome.byte_count is None
                    or outcome.revision_number is None
                ):
                    raise DocumentCollectionError("accepted document has incomplete integrity")
                state = LogicalDocumentState(
                    logical_key=item.logical_key,
                    case_key=work.case_key,
                    document_kind=cast(Any, item.kind.value),
                    official_url=item.descriptor.official_url,
                    revision_number=outcome.revision_number,
                    validators=outcome.validators,
                    integrity=ContentIntegrity(
                        sha256=outcome.sha256,
                        byte_count=outcome.byte_count,
                    ),
                    checked_at=now,
                )
                outcomes[item.logical_key] = outcome
                states[item.logical_key] = state
                if (
                    prior is None
                    or prior.integrity != state.integrity
                    or prior.official_url != state.official_url
                ):
                    changed_keys.add(item.logical_key)

            if (
                not changed_keys
                and source.prior is not None
                and self._processor_case_fingerprints.get(work.case_key)
                == _processor_contract(self.config, self.model_endpoint).composite_sha256
                and not _public_metadata_changed(source)
            ):
                return CaseProcessingResult(
                    case_key=work.case_key,
                    processed_session_keys=tuple(item.session_key for item in work.sessions),
                    public_case=source.prior,
                    changed=False,
                    documents=tuple(states[key] for key in sorted(states)),
                )

            # A changed case must be parsed from all current documents. A 304 probe has
            # no body, so retrieve that accepted revision unconditionally into this run.
            by_key = {item.logical_key: item for item in private_documents}
            for key, outcome in tuple(outcomes.items()):
                object_key = outcome.object_key
                if object_key and objects.exists(object_key):
                    continue
                item = by_key[key]
                pending = _pending_document(
                    item,
                    source,
                    case_id,
                    states[key],
                    now,
                )
                fetched = collector.collect(
                    pending,
                    now,
                    priority=work.priority,
                    checkpoint=None,
                    allocate_revision=True,
                )
                if fetched.status not in {"ready", "duplicate"} or not fetched.object_key:
                    raise DocumentCollectionError("current document body could not be retrieved")
                if (
                    fetched.sha256 is None
                    or fetched.byte_count is None
                    or fetched.revision_number is None
                ):
                    raise DocumentCollectionError(
                        "current document body has incomplete integrity"
                    )
                prior = prior_documents.get(key)
                refreshed = states[key].model_copy(
                    update={
                        "revision_number": fetched.revision_number,
                        "validators": fetched.validators,
                        "integrity": ContentIntegrity(
                            sha256=fetched.sha256,
                            byte_count=fetched.byte_count,
                        ),
                        "checked_at": now,
                    }
                )
                states[key] = refreshed
                if prior is None or prior.integrity != refreshed.integrity:
                    changed_keys.add(key)
                outcomes[key] = fetched

            budget.check_private_disk(workspace)
            observations, document_urls = self._analyze_documents(
                source,
                private_documents,
                outcomes,
                states,
                objects,
                budget,
                authorized_replay,
            )
            return self._generate_public_case(
                source,
                work,
                observations,
                document_urls,
                states,
                changed_keys,
                budget,
                authorized_replay,
            )
        finally:
            store.accepted.clear()
            store.identity.clear()
            store.parse_jobs.clear()
            store.quarantined.clear()
            store.failures.clear()
            objects.clear()

    def _content_documents(self, case_key: str) -> tuple[LogicalDocumentState, ...]:
        # Discovery received the immutable content and retained only public case input.
        # Document checkpoints are injected by the adapter immediately before running.
        return tuple(
            item
            for item in getattr(self, "_documents", ())
            if item.case_key == case_key
        )

    def set_public_state(self, content: GeneratedContent) -> None:
        self._documents = content.publication.documents
        self._processor_case_fingerprints = {
            pointer.case_key: pointer.processor_sha256
            for pointer in content.publication.cases
        }

    @staticmethod
    def _seed_prior_documents(
        store: InMemoryDocumentIngestionStore,
        documents: Mapping[str, LogicalDocumentState],
        case_id: UUID,
    ) -> None:
        for item in documents.values():
            kind = ScotusDocumentKind(item.document_kind)
            revision_id = _document_revision_id(
                case_id, kind, item.logical_key, item.revision_number
            )
            accepted = AcceptedDocument(
                document_revision_id=revision_id,
                case_id=case_id,
                kind=kind,
                external_id=item.logical_key,
                revision_number=item.revision_number,
                official_url=item.official_url,
                content_type=(
                    "text/html"
                    if kind is ScotusDocumentKind.DOCKET
                    else "application/pdf"
                ),
                byte_count=item.integrity.byte_count,
                sha256=item.integrity.sha256,
                object_key=f"prior/{item.logical_key}/{item.integrity.sha256}",
                ready_at=item.checked_at,
            )
            store.accepted[revision_id] = accepted
            store.identity[(case_id, kind, item.logical_key)] = revision_id

    def _analyze_documents(
        self,
        source: _CaseInput,
        documents: tuple[_PrivateDocument, ...],
        outcomes: Mapping[str, Any],
        states: Mapping[str, LogicalDocumentState],
        objects: _LocalObjectStore,
        budget: UnifiedRunBudget,
        authorized_replay: bool,
    ) -> tuple[tuple[LegalObservation, ...], dict[UUID, str]]:
        case_id = deterministic_case_id(source.term, source.primary_docket)
        observations: list[LegalObservation] = []
        urls: dict[UUID, str] = {}
        common_blocks: list[LegalEvidenceBlock] = []
        session_blocks: dict[UUID, list[LegalEvidenceBlock]] = {}
        parser_versions: dict[UUID, str] = {}
        extraction_rejection_codes: list[str] = []
        all_digests = tuple(states[key].integrity.sha256 for key in sorted(states))

        for item in documents:
            outcome = outcomes[item.logical_key]
            if not outcome.object_key or not objects.exists(outcome.object_key):
                raise DocumentCollectionError("private accepted document is unavailable")
            revision_id = outcome.document_revision_id
            urls[revision_id] = item.descriptor.official_url
            with objects.open(outcome.object_key) as file:
                if item.kind is ScotusDocumentKind.TRANSCRIPT:
                    backend = self.parser_backend_factory()
                    parse_id = deterministic_parse_revision_id(
                        revision_id, self.config.parser, backend
                    )
                    parsed = ScotusTranscriptParser(backend, self.config.parser).parse(
                        file,
                        parse_revision_id=parse_id,
                        document_revision_id=revision_id,
                    )
                    if item.argument_id is None:
                        raise ValueError("transcript is missing its argument identity")
                    session_blocks[item.argument_id] = [
                        transcript_turn_block(turn, item.descriptor.official_url)
                        for turn in parsed.turns
                    ]
                    parser_versions[item.argument_id] = (
                        f"{parsed.parser_name}:{parsed.parser_version}:{parsed.config_hash}"
                    )
                else:
                    common_blocks.extend(
                        _document_blocks(
                            file,
                            revision_id=revision_id,
                            kind=item.kind,
                            official_url=item.descriptor.official_url,
                            primary_docket=source.primary_docket,
                        )
                    )

        extraction_store = InMemoryLegalObservationStore()
        for session in source.sessions:
            argument_id = deterministic_argument_id(
                case_id,
                session.argument_date,
                sequence=session.sequence,
                reargument=session.reargument,
            )
            blocks = tuple((*session_blocks.get(argument_id, ()), *common_blocks))
            if not session_blocks.get(argument_id):
                raise ValueError("required transcript did not produce evidence blocks")
            for index, window in enumerate(
                bounded_contexts(blocks, self.config.generation.maximum_context_characters)
            ):
                source_input = LegalExtractionInput(
                    case_id=case_id,
                    argument_id=argument_id,
                    blocks=window,
                    parser_versions=(
                        parser_versions[argument_id],
                        DOCUMENT_TEXT_VERSION,
                        f"window:{index}",
                    ),
                    document_revision_ids=tuple(
                        dict.fromkeys(block.document_revision_id for block in window)
                    ),
                )
                versions = {
                    "endpoint": self.model_endpoint,
                    "extractor": LegalExtractionService.SCHEMA_VERSION,
                    "model": self.config.generation.model,
                    "provider": self.config.generation.provider,
                    "parser": parser_versions[argument_id],
                    "prompt": OpenAILegalObservationExtractor.PROMPT_VERSION,
                    "policy": POLICY_VERSION,
                    "window": str(index),
                }
                executor = _BudgetedModelRequest(
                    client=self.model_client,
                    budget=budget,
                    stage="extraction",
                    document_digests=all_digests,
                    processor_versions=versions,
                    output_tokens=self.config.model_budget.maximum_output_tokens_per_call,
                    authorized_replay=authorized_replay,
                )
                extractor = OpenAILegalObservationExtractor(
                    self.config.generation.model,
                    self.model_client,
                    maximum_output_tokens=(
                        self.config.model_budget.maximum_output_tokens_per_call
                    ),
                    request_executor=executor,
                )
                service = LegalExtractionService(extractor, extraction_store)
                observations.extend(service.process(source_input))
                extraction_rejection_codes.extend(service.rejection_codes)
        if not observations:
            dominant = (
                max(
                    sorted(set(extraction_rejection_codes)),
                    key=extraction_rejection_codes.count,
                )
                if extraction_rejection_codes
                else "empty_batch"
            )
            raise LegalExtractionError(
                "no grounded observations survived extraction validation",
                safe_code=f"empty_grounded_case:{dominant}"[:80],
            )
        observations.sort(key=lambda item: str(item.observation_id))
        return tuple(observations), urls

    def _generate_public_case(
        self,
        source: _CaseInput,
        work: StaticCaseWork,
        observations: tuple[LegalObservation, ...],
        document_urls: dict[UUID, str],
        states: Mapping[str, LogicalDocumentState],
        changed_keys: set[str],
        budget: UnifiedRunBudget,
        authorized_replay: bool,
    ) -> CaseProcessingResult:
        case_id = deterministic_case_id(source.term, source.primary_docket)
        sessions = tuple(
            CaseArgumentSession(
                argument_id=deterministic_argument_id(
                    case_id,
                    item.argument_date,
                    sequence=item.sequence,
                    reargument=item.reargument,
                ),
                argument_date=item.argument_date,
                sequence=item.sequence,
                reargument=item.reargument,
                official_detail_url=item.official_detail_url,
                official_transcript_url=cast(DocumentDescriptor, item.transcript).official_url,
            )
            for item in source.sessions
        )
        prior_status = source.prior.case_status if source.prior else ScotusCaseStatus.DOCKETED
        correlated = ScotusCorrelationEngine().correlate(
            case_id,
            prior_status,
            observations,
            cast(datetime, self.discovery.now),
            reargued=any(item.reargument for item in sessions),
        )
        kinds_changed = {
            ScotusDocumentKind(states[key].document_kind) for key in changed_keys
        }
        status = correlated.aggregate.status
        if any(item.reargument for item in sessions):
            status = ScotusCaseStatus.REARGUED
        elif source.prior is not None and ScotusDocumentKind.TRANSCRIPT in kinds_changed:
            status = ScotusCaseStatus.CORRECTED

        candidate = BriefCandidate(
            case_id=case_id,
            argument_id=sessions[-1].argument_id,
            caption=source.caption,
            primary_docket=source.primary_docket,
            case_status=status,
            official_transcript_complete=True,
            parser_complete=True,
            privacy_blocking_failure=False,
            argument_sessions=sessions,
            observations=observations,
            document_urls=document_urls,
            evaluated_at=cast(datetime, self.discovery.now),
        )
        decision = evaluate_brief_candidate(
            candidate,
            minimum_confidence=self.config.generation.minimum_observation_confidence,
            policy_version=POLICY_VERSION,
        )
        if not decision.eligible:
            raise ValueError("case failed deterministic brief policy")
        all_digests = tuple(states[key].integrity.sha256 for key in sorted(states))
        revision_number = len(source.prior.revisions) + 1 if source.prior else 1
        correction_note = _correction_note(source, kinds_changed)
        validation_feedback_code: str | None = None
        revision = None
        for brief_attempt in range(
            1,
            self.config.generation.maximum_brief_validation_attempts_per_case + 1,
        ):
            request = _BudgetedModelRequest(
                client=self.model_client,
                budget=budget,
                stage="brief",
                document_digests=all_digests,
                processor_versions={
                    "brief_validation_attempt": str(brief_attempt),
                    "endpoint": self.model_endpoint,
                    "extractor": LegalExtractionService.SCHEMA_VERSION,
                    "model": self.config.generation.model,
                    "provider": self.config.generation.provider,
                    "parser": _processor_contract(
                        self.config, self.model_endpoint
                    ).parser_version,
                    "policy": POLICY_VERSION,
                    "prompt": self.config.generation.prompt_version,
                    **(
                        {"validation_feedback": validation_feedback_code}
                        if validation_feedback_code
                        else {}
                    ),
                },
                output_tokens=self.config.model_budget.maximum_output_tokens_per_call,
                authorized_replay=authorized_replay,
            )
            generator = OpenAILegalBriefGenerator(
                self.config.generation.model,
                self.model_client,
                maximum_sentence_words=self.config.generation.maximum_sentence_words,
                maximum_paragraph_words=self.config.generation.maximum_paragraph_words,
                response_schema=simple_brief_json_schema(),
                maximum_output_tokens=(
                    self.config.model_budget.maximum_output_tokens_per_call
                ),
                validation_feedback_code=validation_feedback_code,
                request_executor=request,
            )
            try:
                revision = BriefGenerationService(
                    generator,
                    InMemoryBriefRevisionStore(),
                    public_quotes=self.config.generation.public_quotes,
                    maximum_sentence_words=(
                        self.config.generation.maximum_sentence_words
                    ),
                    maximum_paragraph_words=(
                        self.config.generation.maximum_paragraph_words
                    ),
                ).generate(
                    candidate,
                    decision,
                    revision_number=revision_number,
                    correction_note=correction_note,
                )
                break
            except BriefValidationError as error:
                validation_feedback_code = error.safe_code
                if (
                    not validation_feedback_code
                    or brief_attempt
                    >= self.config.generation.maximum_brief_validation_attempts_per_case
                ):
                    raise
        if revision is None:
            raise BriefValidationError("brief validation attempts produced no revision")
        history = (
            *(source.prior.case_history if source.prior else ()),
            PublicCaseHistoryEvent(
                status=status,
                changed_at=candidate.evaluated_at,
                explanation=_history_explanation(status, source.prior is not None),
            ),
        )
        revision_history = (
            *(source.prior.revisions if source.prior else ()),
            PublicBriefRevisionSummary(
                revision_number=revision_number,
                maturity=revision.maturity,
                created_at=revision.created_at,
                correction_note=correction_note,
            ),
        )
        disposition_urls = tuple(
            sorted(
                {
                    item.descriptor.official_url
                    for item in _case_documents(source)
                    if item.kind in {ScotusDocumentKind.ORDER, ScotusDocumentKind.OPINION}
                }
            )
        )
        public = build_public_case(
            term=source.term,
            primary_docket=source.primary_docket,
            caption=source.caption,
            argument_date=sessions[0].argument_date,
            case_status=status,
            official_detail_url=source.sessions[-1].official_detail_url,
            revision=revision,
            claims=decision.claims,
            argument_sessions=sessions,
            case_history=history,
            revision_history=revision_history,
            official_disposition_urls=disposition_urls,
            topics=source.prior.topics if source.prior else (),
        )
        return CaseProcessingResult(
            case_key=work.case_key,
            processed_session_keys=tuple(item.session_key for item in work.sessions),
            public_case=public,
            changed=True,
            documents=tuple(states[key] for key in sorted(states)),
        )

    def close(self) -> None:
        self._documents = ()
        self._processor_case_fingerprints.clear()


def _document_revision_id(
    case_id: UUID,
    kind: ScotusDocumentKind,
    logical_key: str,
    revision_number: int,
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"ragchew:scotus-document:{case_id}:{kind.value}:{logical_key}:{revision_number}",
    )


def _pending_document(
    document: _PrivateDocument,
    source: _CaseInput,
    case_id: UUID,
    prior: LogicalDocumentState | None,
    now: datetime,
) -> PendingDocument:
    revision = prior.revision_number if prior else 1
    return PendingDocument(
        document_revision_id=_document_revision_id(
            case_id, document.kind, document.logical_key, revision
        ),
        case_id=case_id,
        argument_id=document.argument_id,
        kind=document.kind,
        external_id=document.descriptor.external_id,
        logical_key=document.logical_key,
        revision_number=revision,
        official_url=document.descriptor.official_url,
        expected_content_type=document.descriptor.content_type,
        observed_at=now,
    )


def _document_blocks(
    file: BinaryIO,
    *,
    revision_id: UUID,
    kind: ScotusDocumentKind,
    official_url: str,
    primary_docket: str,
) -> tuple[LegalEvidenceBlock, ...]:
    pages: tuple[str, ...]
    if kind is ScotusDocumentKind.DOCKET:
        parser = _TextCollector()
        parser.feed(file.read().decode("utf-8", "replace"))
        pages = ("\n".join(parser.values),)
    else:
        file.seek(0)
        reader = PdfReader(file, strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentCollectionError("encrypted official document is unsupported")
        pages = tuple(page.extract_text() or "" for page in reader.pages)
        if kind is ScotusDocumentKind.OPINION:
            first = " ".join(pages[:6])
            normalized = " ".join(
                first.replace("\N{EN DASH}", "-").replace("\N{EM DASH}", "-").upper().split()
            )
            docket = " ".join(primary_docket.upper().split())
            if re.search(rf"(?<![0-9A-Z]){re.escape(docket)}(?![0-9A-Z])", normalized) is None:
                raise DocumentCollectionError("official opinion does not identify the case docket")
    blocks: list[LegalEvidenceBlock] = []
    for page_number, text in enumerate(pages, 1):
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        for start in range(0, len(lines), 80):
            selected = lines[start : start + 80]
            value = " ".join(selected)
            if not value:
                continue
            # Evidence block contracts cap text at 30,000 characters. Split again
            # rather than truncating official evidence silently.
            for part, offset in enumerate(range(0, len(value), 29_000)):
                chunk = value[offset : offset + 29_000]
                blocks.append(
                    document_text_block(
                        document_revision_id=revision_id,
                        kind=kind,
                        official_url=official_url,
                        file_page=page_number,
                        start_line=start + 1,
                        end_line=start + len(selected),
                        text=chunk,
                        label=f"Official {kind.value} page {page_number} part {part + 1}",
                    )
                )
    if not blocks:
        raise DocumentCollectionError("official document has no parseable text")
    return tuple(blocks)


def _public_metadata_changed(source: _CaseInput) -> bool:
    prior = source.prior
    if prior is None or prior.caption != source.caption:
        return True
    current = tuple(
        (
            item.argument_date,
            item.sequence,
            item.reargument,
            item.official_detail_url,
            cast(DocumentDescriptor, item.transcript).official_url,
        )
        for item in source.sessions
    )
    published = tuple(
        (
            item.argument_date,
            item.sequence,
            item.reargument,
            item.official_detail_url,
            item.official_transcript_url,
        )
        for item in prior.arguments
    )
    return current != published


def _correction_note(
    source: _CaseInput, changed: set[ScotusDocumentKind]
) -> str | None:
    if source.prior is None:
        return None
    if ScotusDocumentKind.OPINION in changed:
        return "Updated after a new or revised official Supreme Court opinion."
    if ScotusDocumentKind.ORDER in changed:
        return "Updated after a new or revised official Supreme Court order."
    if any(item.reargument for item in source.sessions) and not any(
        item.reargument for item in source.prior.arguments
    ):
        return "Updated after a new official Supreme Court reargument transcript."
    return "Corrected after revised official Supreme Court case material."


def _history_explanation(status: ScotusCaseStatus, update: bool) -> str:
    if status is ScotusCaseStatus.DECIDED:
        return "An official Supreme Court opinion was added to the case record."
    if status is ScotusCaseStatus.ORDER_ISSUED:
        return "An official Supreme Court order was added to the case record."
    if status is ScotusCaseStatus.REARGUED:
        return "The Court held another argument session in this case."
    if status is ScotusCaseStatus.CORRECTED or update:
        return "The brief was updated after revised official Court material."
    return "The Court held oral argument and published an official transcript."


def _default_source_fetcher(
    settings: ServiceSettings, config: ScotusConfig
) -> SourceFetcher:
    return HttpxSourceFetcher(
        user_agent=settings.source_user_agent,
        maximum_bytes=config.documents.maximum_pdf_bytes,
        # Live static publication applies one shared limiter across source pages and
        # document bodies; avoid a second independent clock in this transport.
        minimum_interval_seconds=0,
        timeout_seconds=config.model_budget.request_timeout_seconds,
    )


def _default_document_client(
    settings: ServiceSettings, config: ScotusConfig
) -> httpx.Client:
    del settings
    return httpx.Client(
        follow_redirects=False,
        timeout=config.model_budget.request_timeout_seconds,
        trust_env=False,
    )


def _default_ollama_client(settings: ServiceSettings, config: ScotusConfig) -> OpenAI:
    timeout = config.model_budget.request_timeout_seconds
    return OpenAI(
        api_key="ollama-local-no-secret",
        base_url=settings.ollama_base_url,
        timeout=timeout,
        max_retries=0,
        http_client=DefaultHttpxClient(
            follow_redirects=False,
            timeout=timeout,
            trust_env=False,
        ),
    )


def _verify_exact_ollama_model(client: Any, expected_model: str) -> None:
    try:
        available = client.models.list()
        model_ids = {item.id for item in available.data if isinstance(item.id, str)}
    except Exception:
        raise PublicationGateDenied("local Ollama model preflight failed") from None
    if expected_model not in model_ids:
        raise PublicationGateDenied("configured local Ollama model is not installed")


class LiveStaticBatchAdapter:
    """Reviewed production adapter loaded by ``RAGCHEW_SCOTUS_BATCH_ADAPTER``."""

    def __init__(
        self,
        *,
        settings_factory: Callable[[], ServiceSettings] = ServiceSettings,
        proceedings_loader: Callable[[str | Path], ProceedingsConfig] = ProceedingsConfig.from_yaml,
        source_fetcher_factory: SourceFetcherFactory = _default_source_fetcher,
        document_client_factory: DocumentClientFactory = _default_document_client,
        ollama_client_factory: OllamaClientFactory = _default_ollama_client,
        parser_backend_factory: Callable[[], PdfTextBackend] = PypdfTextBackend,
        rate_limiter_factory: Callable[[float], RequestRateLimiter] = RequestRateLimiter,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        # Construction performs no source/model I/O; the gate check in run is first.
        self.settings_factory = settings_factory
        self.proceedings_loader = proceedings_loader
        self.source_fetcher_factory = source_fetcher_factory
        self.document_client_factory = document_client_factory
        self.ollama_client_factory = ollama_client_factory
        self.parser_backend_factory = parser_backend_factory
        self.rate_limiter_factory = rate_limiter_factory
        self.clock = clock or (lambda: datetime.now(UTC))

    def run(
        self,
        *,
        state_store: StaticStateStore,
        config: ScotusConfig,
        mode: DiscoveryMode,
        runner_temp: str | Path,
        authorized_replay: bool,
    ) -> StaticBatchResult:
        _validate_live_gates(config)
        now = self.clock()
        settings = self.settings_factory()
        proceedings = self.proceedings_loader(settings.proceedings_config_path)
        registry = InMemorySourceRegistry()
        registry.register(_source_from_config(proceedings), "loaded reviewed source configuration")
        authorizer = SourceAuthorizer(registry)
        source = authorizer.require_source("supreme_court", now)
        if (
            source.adapter != "supreme_court"
            or source.discovery_method is not SourceAccessMethod.OFFICIAL_PAGE
        ):
            raise SourceAuthorizationError("Supreme Court source adapter contract changed")

        # No factory capable of network/model use is called until every gate and source
        # authorization check above succeeds. Construction is inside the cleanup scope
        # because a persistent self-hosted runner must not retain partial clients.
        raw_fetcher: SourceFetcher | None = None
        document_client: httpx.Client | None = None
        model_client: Any = None
        adapters: dict[str, SupremeCourtAdapter] = {}
        discovery: LiveStaticDiscovery | None = None
        processor: LiveStaticCaseProcessor | None = None
        try:
            raw_fetcher = self.source_fetcher_factory(settings, config)
            document_client = self.document_client_factory(settings, config)
            model_client = self.ollama_client_factory(settings, config)
            # Inventory is checked before Court evidence retrieval or chat completion.
            _verify_exact_ollama_model(model_client, config.generation.model)
            # The wrapper authorizes each nested index/opinion/order request and the
            # budget wrapper accounts every attempted response body.
            # The orchestrator creates the budget, so adapters are rebound during the
            # discover call through this small proxy.
            crawl_limiter = self.rate_limiter_factory(
                config.discovery.crawl_delay_seconds
            )
            budget_proxy = _DeferredBudgetFetcher(
                raw_fetcher,
                authorizer,
                now,
                before_request=crawl_limiter.wait,
            )
            for term in config.discovery.terms:
                adapters[term] = SupremeCourtAdapter(
                    budget_proxy,
                    term=term,
                    clock=lambda: now,
                    detail_lookback_days=config.discovery.backfill_lookback_days,
                    maximum_detail_requests=config.discovery.backfill_case_limit,
                    transcript_archive=True,
                )
            discovery = LiveStaticDiscovery(
                adapters=adapters,
                config=config,
                model_endpoint=settings.ollama_base_url,
            )
            processor = LiveStaticCaseProcessor(
                discovery=discovery,
                authorizer=authorizer,
                config=config,
                document_client=document_client,
                model_client=model_client,
                model_endpoint=settings.ollama_base_url,
                user_agent=settings.source_user_agent,
                before_court_request=crawl_limiter.wait,
                parser_backend_factory=self.parser_backend_factory,
            )
            original = state_store.load()
            processor.set_public_state(original)
            receipt_sink = _ReceiptSink(Path(runner_temp) / "public-cost-receipts.json")
            orchestrator = StaticBatchOrchestrator(
                state_store=state_store,
                discovery=_BudgetBindingDiscovery(discovery, budget_proxy),
                processor=processor,
                config=config,
                runner_temp=runner_temp,
                receipt_sink=receipt_sink,
            )
            try:
                result = orchestrator.run(
                    mode=mode,
                    now=now,
                    authorized_replay=authorized_replay,
                )
            except Exception as error:
                category = failure_category(error)
                if isinstance(error, ValidationError):
                    detail = "ValidationError[" + ",".join(
                        f"{'.'.join(str(part) for part in item['loc'])}:{item['type']}"
                        for item in error.errors(
                            include_url=False, include_context=False, include_input=False
                        )[:10]
                    ) + "]"
                else:
                    detail = type(error).__name__
                raise RuntimeError(
                    f"live SCOTUS batch failed: {category.value}; detail={detail}"
                ) from None
            if result.content.projection is None:
                projection = ScotusPublicProjection(
                    watermark=now,
                    generated_at=now,
                    cases=(),
                    site_name=config.publication.site_name,
                )
                result = StaticBatchResult(
                    content=result.content.__class__(
                        projection=projection,
                        publication=result.content.publication,
                        cost_ledger=result.content.cost_ledger,
                        release=result.content.release,
                        revisions=result.content.revisions,
                    ),
                    parent_release_id=result.parent_release_id,
                    changed_case_keys=result.changed_case_keys,
                    pending_case_keys=result.pending_case_keys,
                    publishable=result.publishable,
                    no_public_change=result.no_public_change,
                )
            return result
        finally:
            if processor is not None:
                processor.close()
            if discovery is not None:
                discovery.close()
            _close(model_client)
            _close(document_client)
            _close(raw_fetcher)


class _DeferredBudgetFetcher:
    """Bind the orchestrator-owned budget immediately before discovery traffic."""

    def __init__(
        self,
        delegate: SourceFetcher,
        authorizer: SourceAuthorizer,
        now: datetime,
        *,
        before_request: Callable[[], None],
    ) -> None:
        self.delegate = delegate
        self.authorizer = authorizer
        self.now = now
        self.before_request = before_request
        self.budget: UnifiedRunBudget | None = None

    def get(self, url: str, conditional: Any = None) -> SourceResponse:
        if self.budget is None:
            raise RuntimeError("source fetcher has no unified run budget")
        self.budget.reserve_http_request()
        self.before_request()
        authorized = _AuthorizedFetcher(self.delegate, self.authorizer, self.now)
        response = authorized.get(url, conditional)
        self.budget.record_download(len(response.content))
        return response


class _BudgetBindingDiscovery:
    def __init__(
        self,
        discovery: LiveStaticDiscovery,
        fetcher: _DeferredBudgetFetcher,
    ) -> None:
        self.discovery = discovery
        self.fetcher = fetcher

    def discover(
        self,
        *,
        mode: DiscoveryMode,
        content: GeneratedContent,
        budget: UnifiedRunBudget,
        now: datetime,
    ) -> StaticDiscoveryResult:
        self.fetcher.budget = budget
        try:
            return self.discovery.discover(
                mode=mode,
                content=content,
                budget=budget,
                now=now,
            )
        finally:
            self.fetcher.budget = None


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


# Convenient module-level target for environments that prefer an object over a class.
live_static_batch_adapter = LiveStaticBatchAdapter()

__all__ = [
    "LiveStaticBatchAdapter",
    "LiveStaticCaseProcessor",
    "LiveStaticDiscovery",
    "live_static_batch_adapter",
]
