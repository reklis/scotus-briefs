"""Deterministic parsing, planning, and atomic recovery for archived Court PDFs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from contextlib import suppress
from datetime import date
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .archive import ManifestStore, hash_file
from .dockets import docket_sort_key, normalize_docket
from .models import (
    CaseAssociation,
    CaseDates,
    CaseDocumentReference,
    ContractModel,
    DocketNumber,
    DocumentManifest,
    DocumentManifestEntry,
    DocumentType,
    Lifecycle,
    MetadataProvenance,
    NormalizedCase,
    Party,
    Sha256,
)

HISTORICAL_SCHEMA_VERSION = "1.0.0"
DEFAULT_OPENING_PAGES = 3
DEFAULT_MAX_CHARACTERS = 60_000
DEFAULT_MAX_PDF_BYTES = 100 * 1024 * 1024

_HYPHENS = "-\N{HYPHEN}\N{NON-BREAKING HYPHEN}\N{EN DASH}\N{EM DASH}\N{MINUS SIGN}\N{SOFT HYPHEN}"
_HYPHEN_CLASS = re.escape(_HYPHENS)
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "sept": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTH_PATTERN = "|".join(name.title() for name in _MONTHS)
_DATE_PATTERN = rf"(?P<month>{_MONTH_PATTERN})\.?\s+(?P<day>\d{{1,2}}),?\s+(?P<year>\d{{4}})"
_DATE_RE = re.compile(_DATE_PATTERN, re.IGNORECASE)
_DOCKET_TOKEN_RE = re.compile(
    rf"(?<!\w)(?:\d{{2,4}}\s*(?:[{_HYPHEN_CLASS}]\s*\d+|[Aa]\s*\d+)|"
    r"\d{1,3}\s*,?\s*(?:Orig(?:inal)?\.?))(?!\w)",
    re.IGNORECASE,
)
_DOCKET_LABEL_RE = re.compile(r"\bNos?\.?(?=\s)\s*", re.IGNORECASE)
_TERM_RE = re.compile(r"\b(?:OCTOBER\s+)?TERM\s*[,]?\s*(\d{4})\b", re.IGNORECASE)
_ARGUMENT_RE = re.compile(rf"\bArgued\s+{_DATE_PATTERN}", re.IGNORECASE)
_DECISION_RE = re.compile(rf"\bDecided\s+{_DATE_PATTERN}", re.IGNORECASE)
_TRANSCRIPT_DATE_RE = re.compile(rf"\bDate\s*:\s*{_DATE_PATTERN}", re.IGNORECASE)
_WEEKDAY_DATE_RE = re.compile(
    rf"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday),?\s+{_DATE_PATTERN}",
    re.IGNORECASE,
)


class HistoricalParseError(ValueError):
    """Raised when a PDF cannot be read within the parser's safety limits."""


class ConflictSeverity(StrEnum):
    WARNING = "warning"
    BLOCKING = "blocking"


class RecoveryConflict(ContractModel):
    """A deterministic, machine-readable reason that recovery needs review."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    code: str
    message: str
    severity: ConflictSeverity = ConflictSeverity.BLOCKING
    field: str | None = None
    document_hashes: list[Sha256] = Field(default_factory=list)
    docket_numbers: list[DocketNumber] = Field(default_factory=list)


class HistoricalDocumentCandidate(ContractModel):
    """Metadata supported by one archived document and its preserved import path."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    document_hash: Sha256
    archive_path: str | None = None
    import_path: str
    document_type: DocumentType
    historical_groups: list[str] = Field(default_factory=list)
    docket_numbers: list[DocketNumber] = Field(default_factory=list)
    docket_labels: list[str] = Field(default_factory=list)
    title: str | None = None
    embedded_title: str | None = None
    term: Annotated[int, Field(ge=1789, le=2200)] | None = None
    dates: CaseDates = Field(default_factory=CaseDates)
    parties: list[Party] = Field(default_factory=list)
    lifecycle: Lifecycle = Lifecycle.UNRESOLVED
    disposition: Literal["granted", "denied", "dismissed"] | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] = 0.0
    ambiguous: bool = False
    warnings: list[str] = Field(default_factory=list)
    provenance: list[MetadataProvenance] = Field(default_factory=list)
    opening_pages_read: Annotated[int, Field(ge=0)] = 0
    opening_text_truncated: bool = False

    @model_validator(mode="after")
    def lifecycle_has_support(self) -> HistoricalDocumentCandidate:
        if self.lifecycle == Lifecycle.DECIDED and self.dates.decision is None:
            raise ValueError("a decided historical candidate requires a decision date")
        if self.lifecycle == Lifecycle.DISMISSED and (
            self.dates.decision is None or self.disposition != "dismissed"
        ):
            raise ValueError("a dismissed candidate requires an explicit disposition and date")
        return self


class RecoveryComponent(ContractModel):
    """One docket-connected component and its proposed canonical case mutation."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    component_id: str
    document_hashes: list[Sha256] = Field(default_factory=list)
    document_types: dict[str, DocumentType] = Field(default_factory=dict)
    docket_numbers: list[DocketNumber] = Field(default_factory=list)
    historical_groups: list[str] = Field(default_factory=list)
    title: str | None = None
    term: Annotated[int, Field(ge=1789, le=2200)] | None = None
    lifecycle: Lifecycle = Lifecycle.UNRESOLVED
    target_case_id: str | None = None
    existing_case_id: str | None = None
    proposed_case: NormalizedCase | None = None
    conflicts: list[RecoveryConflict] = Field(default_factory=list)

    @model_validator(mode="after")
    def proposal_is_consistent(self) -> RecoveryComponent:
        if self.proposed_case is not None:
            if self.target_case_id != self.proposed_case.case_id:
                raise ValueError("component target_case_id does not match proposed case")
            proposed_hashes = {item.sha256 for item in self.proposed_case.documents}
            # Existing curated cases may already contain documents outside this component.
            if not set(self.document_hashes).issubset(proposed_hashes):
                raise ValueError("proposed case is missing a component document")
        return self


class HistoricalRecoveryPlan(ContractModel):
    """Versioned, reviewable recovery transaction with source-state preconditions."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    source_manifest_hash: Sha256
    source_case_hashes: dict[str, Sha256] = Field(default_factory=dict)
    candidates: list[HistoricalDocumentCandidate] = Field(default_factory=list)
    components: list[RecoveryComponent] = Field(default_factory=list)
    conflicts: list[RecoveryConflict] = Field(default_factory=list)


class HistoricalRecoveryReport(ContractModel):
    """Deterministic coverage, split, merge, conflict, and application report."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    documents_examined: Annotated[int, Field(ge=0)] = 0
    candidates_recovered: Annotated[int, Field(ge=0)] = 0
    unresolved_documents: Annotated[int, Field(ge=0)] = 0
    components_proposed: Annotated[int, Field(ge=0)] = 0
    assigned_document_hashes: list[Sha256] = Field(default_factory=list)
    unresolved_document_hashes: list[Sha256] = Field(default_factory=list)
    case_mappings: dict[str, str] = Field(default_factory=dict)
    split_groups: dict[str, list[str]] = Field(default_factory=dict)
    merged_groups: dict[str, list[str]] = Field(default_factory=dict)
    cases_written: list[str] = Field(default_factory=list)
    unresolved_cases_removed: list[str] = Field(default_factory=list)
    unresolved_cases_reduced: list[str] = Field(default_factory=list)
    manifest_changed: bool = False
    no_op: bool = False
    conflicts: list[RecoveryConflict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# Explicit aliases make the contracts easy to discover without prescribing later planner names.
HistoricalCandidate = HistoricalDocumentCandidate
HistoricalRecoveryComponent = RecoveryComponent
HistoricalConflict = RecoveryConflict
HistoricalRecoveryConflict = RecoveryConflict
RecoveryPlan = HistoricalRecoveryPlan
RecoveryReport = HistoricalRecoveryReport


class PdfOpening(ContractModel):
    """Bounded text and title metadata read from the start of a PDF."""

    text: str
    embedded_title: str | None = None
    pages_read: Annotated[int, Field(ge=0)] = 0
    truncated: bool = False


def classify_historical_document_type(import_path: str) -> DocumentType:
    """Classify only a preserved ``transcript``, ``opinion``, or ``order`` parent.

    Looking only at the immediate parent prevents filenames or unrelated ancestors from
    accidentally becoming authoritative document-type evidence.
    """
    path = PurePosixPath(import_path)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        raise ValueError(f"invalid historical import path: {import_path!r}")
    category = path.parent.name.casefold()
    categories = {
        "transcript": DocumentType.TRANSCRIPT,
        "opinion": DocumentType.OPINION,
        "order": DocumentType.ORDER,
    }
    try:
        return categories[category]
    except KeyError as error:
        raise ValueError(f"unsupported historical document category: {category!r}") from error


# Short name for callers that already operate in a historical-recovery context.
classify_document_type = classify_historical_document_type


def read_pdf_opening(
    path: Path,
    *,
    max_pages: int = DEFAULT_OPENING_PAGES,
    max_characters: int = DEFAULT_MAX_CHARACTERS,
    max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
) -> PdfOpening:
    """Read title metadata and a bounded number of opening pages from ``path``."""
    if max_pages < 1 or max_characters < 1 or max_pdf_bytes < 1:
        raise ValueError("PDF extraction limits must be positive")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise HistoricalParseError(f"cannot stat historical PDF: {error}") from error
    if size > max_pdf_bytes:
        raise HistoricalParseError(
            f"historical PDF is {size} bytes; limit is {max_pdf_bytes} bytes"
        )

    parts: list[str] = []
    characters = 0
    pages_read = 0
    truncated = False
    try:
        with path.open("rb") as source:
            reader = PdfReader(source, strict=True)
            if reader.is_encrypted:
                try:
                    if reader.decrypt("") == 0:
                        raise HistoricalParseError("encrypted historical PDF cannot be read")
                except (NotImplementedError, ValueError) as error:
                    raise HistoricalParseError("encrypted historical PDF cannot be read") from error
            metadata = reader.metadata
            raw_title = metadata.title if metadata is not None else None
            embedded_title = _clean_optional_text(raw_title)
            available = len(reader.pages)
            page_limit = min(available, max_pages)
            for index in range(page_limit):
                raw = reader.pages[index].extract_text() or ""
                normalized = _normalize_text(raw)
                remaining = max_characters - characters
                if len(normalized) > remaining:
                    normalized = normalized[:remaining]
                    truncated = True
                parts.append(normalized)
                characters += len(normalized)
                pages_read += 1
                if characters >= max_characters:
                    truncated = truncated or index + 1 < available
                    break
            if available > pages_read:
                truncated = True
    except HistoricalParseError:
        raise
    except (OSError, PdfReadError, ValueError, TypeError, KeyError) as error:
        raise HistoricalParseError(f"cannot read historical PDF: {error}") from error

    return PdfOpening(
        text="\n\f\n".join(parts),
        embedded_title=embedded_title,
        pages_read=pages_read,
        truncated=truncated,
    )


extract_opening_pages = read_pdf_opening


def extract_historical_candidate(
    entry: DocumentManifestEntry,
    repository_root: Path,
    *,
    max_pages: int = DEFAULT_OPENING_PAGES,
    max_characters: int = DEFAULT_MAX_CHARACTERS,
    max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
) -> HistoricalDocumentCandidate:
    """Read and parse one manifest entry without mutating repository state."""
    import_paths = sorted(
        {source.import_path for source in entry.sources if source.import_path is not None}
    )
    if not import_paths:
        raise ValueError(f"manifest document {entry.sha256} has no preserved import path")
    classified = {path: classify_historical_document_type(path) for path in import_paths}
    document_types = set(classified.values())
    if len(document_types) != 1:
        raise ValueError(f"manifest document {entry.sha256} has conflicting import-path categories")
    # Equivalent duplicate sources are harmless; lexical order keeps output deterministic.
    import_path = import_paths[0]
    opening = read_pdf_opening(
        repository_root / entry.archive_path,
        max_pages=max_pages,
        max_characters=max_characters,
        max_pdf_bytes=max_pdf_bytes,
    )
    groups = sorted(
        {
            association.historical_group
            for association in entry.cases
            if association.historical_group is not None
        }
    )
    return parse_historical_metadata(
        document_hash=entry.sha256,
        archive_path=entry.archive_path,
        import_path=import_path,
        opening_text=opening.text,
        embedded_title=opening.embedded_title,
        historical_groups=groups,
        opening_pages_read=opening.pages_read,
        opening_text_truncated=opening.truncated,
    )


parse_historical_document = extract_historical_candidate


def parse_historical_metadata(
    *,
    document_hash: str,
    import_path: str,
    opening_text: str,
    embedded_title: str | None = None,
    archive_path: str | None = None,
    historical_groups: list[str] | tuple[str, ...] = (),
    opening_pages_read: int = 0,
    opening_text_truncated: bool = False,
) -> HistoricalDocumentCandidate:
    """Parse one already-extracted opening section into a conservative candidate."""
    document_type = classify_historical_document_type(import_path)
    text = _normalize_text(opening_text)
    metadata_title = _clean_optional_text(embedded_title)
    text_dockets, text_labels = _extract_labeled_dockets(text)
    title_dockets, title_labels = _extract_title_dockets(metadata_title)
    warnings: list[str] = []
    ambiguous = False

    if text_dockets and title_dockets and set(text_dockets).isdisjoint(title_dockets):
        ambiguous = True
        warnings.append("embedded title and opening text identify different dockets")
    docket_numbers = _sort_dockets({*text_dockets, *title_dockets})
    docket_labels = _deduplicate([*text_labels, *title_labels])

    argument_dates = _argument_dates(text, document_type)
    decision_dates = _decision_dates(text, metadata_title, document_type)
    argument, argument_conflict = _single_value(argument_dates)
    decision, decision_conflict = _single_value(decision_dates)
    if argument_conflict:
        ambiguous = True
        warnings.append("opening text contains conflicting argument dates")
    if decision_conflict:
        ambiguous = True
        warnings.append("opening text contains conflicting decision dates")

    explicit_terms = {int(match.group(1)) for match in _TERM_RE.finditer(text)}
    if len(explicit_terms) > 1:
        ambiguous = True
        warnings.append("opening text contains conflicting Court terms")
    term = min(explicit_terms) if len(explicit_terms) == 1 else None
    derived_terms = {_court_term(value) for value in (argument, decision) if value is not None}
    if term is not None and derived_terms and term not in derived_terms:
        ambiguous = True
        warnings.append("explicit Court term conflicts with the extracted date")
    elif term is None and len(derived_terms) == 1:
        term = next(iter(derived_terms))
    elif term is None and len(derived_terms) > 1:
        ambiguous = True
        warnings.append("argument and decision dates imply different Court terms")

    metadata_caption = _caption_from_metadata(metadata_title) if title_dockets else None
    text_caption = _extract_caption(text, document_type)
    title = metadata_caption or text_caption
    # Metadata titles routinely abbreviate parties.  Only block clearly unrelated captions.
    if (
        metadata_caption
        and text_caption
        and not _captions_equivalent(metadata_caption, text_caption)
        and not _caption_party_overlap(metadata_caption, text_caption)
    ):
        ambiguous = True
        warnings.append("embedded title and opening text contain conflicting captions")
    parties = _extract_parties(title, text, document_type)

    disposition = _extract_disposition(text) if document_type is DocumentType.ORDER else None
    lifecycle = Lifecycle.UNRESOLVED
    if not ambiguous:
        if document_type is DocumentType.TRANSCRIPT and argument is not None:
            lifecycle = Lifecycle.ARGUED
        elif document_type in {DocumentType.OPINION, DocumentType.ORDER} and decision is not None:
            lifecycle = (
                Lifecycle.DISMISSED if disposition == "dismissed" else Lifecycle.DECIDED
            )

    supported_fields = sum(
        (
            bool(docket_numbers),
            title is not None,
            term is not None,
            argument is not None or decision is not None,
        )
    )
    confidence = 0.0 if ambiguous else supported_fields / 4
    provenance = _candidate_provenance(
        document_hash,
        docket_numbers=bool(docket_numbers),
        title=title is not None,
        term=term is not None,
        parties=bool(parties),
        dates=argument is not None or decision is not None,
        lifecycle=lifecycle is not Lifecycle.UNRESOLVED,
        disposition=disposition is not None,
    )
    if not text.strip():
        warnings.append("opening pages contain no readable text")
    if not docket_numbers:
        warnings.append("no explicitly labeled docket found")

    return HistoricalDocumentCandidate(
        document_hash=document_hash,
        archive_path=archive_path,
        import_path=import_path,
        document_type=document_type,
        historical_groups=sorted(set(historical_groups)),
        docket_numbers=docket_numbers,
        docket_labels=docket_labels,
        title=title,
        embedded_title=metadata_title,
        term=term,
        dates=CaseDates(argument=argument, decision=decision),
        parties=parties,
        lifecycle=lifecycle,
        disposition=disposition,
        confidence=confidence,
        ambiguous=ambiguous,
        warnings=warnings,
        provenance=provenance,
        opening_pages_read=opening_pages_read,
        opening_text_truncated=opening_text_truncated,
    )


def plan_historical_recovery(
    repository_root: Path,
    manifest_store: ManifestStore,
    *,
    plan_path: Path | None = None,
    report_path: Path | None = None,
    max_pages: int = DEFAULT_OPENING_PAGES,
    max_characters: int = DEFAULT_MAX_CHARACTERS,
    max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
) -> tuple[HistoricalRecoveryPlan, HistoricalRecoveryReport]:
    """Scan historical manifest sources once per hash and produce a deterministic plan."""
    with manifest_store.locked():
        manifest = manifest_store.load()
        manifest_hash = _stored_contract_hash(manifest_store.path, manifest)
        case_files, cases = _load_case_repository(repository_root)
        case_hashes = {
            case_id: _stored_contract_hash(case_files[case_id], case)
            for case_id, case in cases.items()
        }

        candidates: list[HistoricalDocumentCandidate] = []
        conflicts: list[RecoveryConflict] = []
        historical_entries = [
            entry
            for entry in sorted(manifest.documents, key=lambda item: item.sha256)
            if any(source.import_path is not None for source in entry.sources)
        ]
        for entry in historical_entries:
            try:
                candidates.append(
                    extract_historical_candidate(
                        entry,
                        repository_root,
                        max_pages=max_pages,
                        max_characters=max_characters,
                        max_pdf_bytes=max_pdf_bytes,
                    )
                )
            except (HistoricalParseError, OSError, ValueError) as error:
                conflicts.append(
                    RecoveryConflict(
                        code="candidate-unusable",
                        message=str(error),
                        document_hashes=[entry.sha256],
                    )
                )

        components, component_conflicts = _build_recovery_components(candidates, cases)
        conflicts.extend(component_conflicts)
        conflicts.extend(_repository_conflicts(manifest, case_files, cases))
        conflicts = sorted(conflicts, key=_conflict_key)
        plan = HistoricalRecoveryPlan(
            source_manifest_hash=manifest_hash,
            source_case_hashes=dict(sorted(case_hashes.items())),
            candidates=sorted(candidates, key=lambda item: item.document_hash),
            components=components,
            conflicts=conflicts,
        )
        report = _planning_report(historical_entries, candidates, components, conflicts)

    if plan_path is not None:
        _atomic_contract(plan_path, plan)
    if report_path is not None:
        _atomic_contract(report_path, report)
    return plan, report


def build_historical_recovery_plan(
    manifest: DocumentManifest,
    candidates: list[HistoricalDocumentCandidate],
    existing_cases: list[NormalizedCase] | dict[str, NormalizedCase] | None = None,
) -> tuple[HistoricalRecoveryPlan, HistoricalRecoveryReport]:
    """Build a plan from parsed candidates (useful for bounded callers and tests)."""
    cases = (
        dict(existing_cases)
        if isinstance(existing_cases, dict)
        else {item.case_id: item for item in existing_cases or []}
    )
    components, conflicts = _build_recovery_components(candidates, cases)
    plan = HistoricalRecoveryPlan(
        source_manifest_hash=_contract_payload_hash(manifest),
        source_case_hashes={
            key: _contract_payload_hash(value) for key, value in sorted(cases.items())
        },
        candidates=sorted(candidates, key=lambda item: item.document_hash),
        components=components,
        conflicts=conflicts,
    )
    historical_entries = [
        entry
        for entry in manifest.documents
        if any(source.import_path is not None for source in entry.sources)
    ]
    return plan, _planning_report(historical_entries, candidates, components, conflicts)


def _build_recovery_components(
    candidates: list[HistoricalDocumentCandidate], existing_cases: dict[str, NormalizedCase]
) -> tuple[list[RecoveryComponent], list[RecoveryConflict]]:
    usable = sorted(
        (item for item in candidates if not item.ambiguous and item.docket_numbers),
        key=lambda item: item.document_hash,
    )
    parent = list(range(len(usable)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    docket_owner: dict[str, int] = {}
    hash_owner: dict[str, int] = {}
    for index, candidate in enumerate(usable):
        for key in [candidate.document_hash, *candidate.docket_numbers]:
            owners = hash_owner if key == candidate.document_hash else docket_owner
            prior = owners.setdefault(key, index)
            union(index, prior)

    # A curated consolidated case is also authoritative evidence that its canonical
    # dockets share one identity.  Coalesce candidate islands before creating proposals.
    for case in sorted(existing_cases.values(), key=lambda item: item.case_id):
        candidate_indexes = [
            docket_owner[docket]
            for docket in case.docket_numbers
            if docket in docket_owner
        ]
        for index in candidate_indexes[1:]:
            union(candidate_indexes[0], index)

    grouped: dict[int, list[HistoricalDocumentCandidate]] = defaultdict(list)
    for index, candidate in enumerate(usable):
        grouped[find(index)].append(candidate)

    docket_cases: dict[str, set[str]] = defaultdict(set)
    for case in existing_cases.values():
        for docket in case.docket_numbers:
            docket_cases[docket].add(case.case_id)

    components: list[RecoveryComponent] = []
    global_conflicts: list[RecoveryConflict] = []
    for members in grouped.values():
        members.sort(key=lambda item: item.document_hash)
        hashes = sorted({item.document_hash for item in members})
        dockets = _sort_dockets({docket for item in members for docket in item.docket_numbers})
        groups = sorted({group for item in members for group in item.historical_groups})
        identifier = hashlib.sha256(("\0".join([*dockets, *hashes])).encode()).hexdigest()[:16]
        component_conflicts: list[RecoveryConflict] = []
        matched_ids = sorted({case_id for docket in dockets for case_id in docket_cases[docket]})
        existing: NormalizedCase | None = None
        if len(matched_ids) > 1:
            component_conflicts.append(
                RecoveryConflict(
                    code="multiple-existing-cases",
                    message="component dockets match multiple existing cases: "
                    + ", ".join(matched_ids),
                    docket_numbers=dockets,
                    document_hashes=hashes,
                )
            )
        elif matched_ids:
            existing = existing_cases[matched_ids[0]]

        title = _resolve_text_field("title", members, existing, component_conflicts)
        term = _resolve_scalar_field("term", members, existing, component_conflicts)
        dates = CaseDates(
            argument=_resolve_date_field("argument", members, existing, component_conflicts),
            decision=_resolve_date_field("decision", members, existing, component_conflicts),
        )
        lifecycle = _resolve_lifecycle(members, existing, dates)
        if lifecycle == Lifecycle.DECIDED and dates.decision is None:
            lifecycle = Lifecycle.UNRESOLVED
        parties = _resolve_parties(members, existing, component_conflicts)
        target_id: str | None = existing.case_id if existing else None
        if existing is None and title is not None and term is not None:
            target_id = _recovered_case_id(term, dockets[0])
            if target_id in existing_cases:
                component_conflicts.append(
                    RecoveryConflict(
                        code="case-id-collision",
                        message=f"generated case ID already exists: {target_id}",
                        docket_numbers=dockets,
                        document_hashes=hashes,
                    )
                )
                target_id = None
        if existing is None and (title is None or term is None):
            component_conflicts.append(
                RecoveryConflict(
                    code="insufficient-case-identity",
                    message="a new case requires an unambiguous title and Court term",
                    docket_numbers=dockets,
                    document_hashes=hashes,
                )
            )

        proposed: NormalizedCase | None = None
        if target_id is not None and not any(
            conflict.severity is ConflictSeverity.BLOCKING for conflict in component_conflicts
        ):
            proposed = _proposed_case(
                target_id,
                existing,
                members,
                dockets,
                title,
                term,
                dates,
                lifecycle,
                parties,
            )
        component = RecoveryComponent(
            component_id=f"component-{identifier}",
            document_hashes=hashes,
            document_types={item.document_hash: item.document_type for item in members},
            docket_numbers=dockets,
            historical_groups=groups,
            title=title,
            term=term,
            lifecycle=lifecycle,
            target_case_id=target_id,
            existing_case_id=existing.case_id if existing else None,
            proposed_case=proposed,
            conflicts=sorted(component_conflicts, key=_conflict_key),
        )
        components.append(component)
        global_conflicts.extend(component.conflicts)

    components.sort(key=lambda item: item.component_id)
    targets: dict[str, list[RecoveryComponent]] = defaultdict(list)
    for component in components:
        if component.target_case_id and component.proposed_case:
            targets[component.target_case_id].append(component)
    for target, values in sorted(targets.items()):
        if len(values) > 1:
            global_conflicts.append(
                RecoveryConflict(
                    code="duplicate-target-case",
                    message=f"multiple components target case {target}",
                    document_hashes=sorted(
                        {item for value in values for item in value.document_hashes}
                    ),
                )
            )
    return components, sorted(global_conflicts, key=_conflict_key)


def _resolve_text_field(
    field: Literal["title"],
    members: list[HistoricalDocumentCandidate],
    existing: NormalizedCase | None,
    conflicts: list[RecoveryConflict],
) -> str | None:
    values = sorted({item.title for item in members if item.title is not None})
    if existing is not None and existing.title and not existing.title.startswith(
        "Unresolved historical group "
    ):
        if values and not all(_captions_equivalent(existing.title, value) for value in values):
            conflicts.append(
                RecoveryConflict(
                    code="existing-metadata-precedence",
                    field=field,
                    message="existing title retained over conflicting extracted metadata",
                    severity=ConflictSeverity.WARNING,
                    document_hashes=sorted(item.document_hash for item in members if item.title),
                )
            )
        return existing.title
    equivalent: list[str] = []
    for value in values:
        if not any(_captions_equivalent(value, prior) for prior in equivalent):
            equivalent.append(value)
    if len(equivalent) > 1:
        conflicts.append(
            RecoveryConflict(
                code="field-conflict",
                field=field,
                message="documents contain conflicting titles",
                document_hashes=sorted(item.document_hash for item in members if item.title),
            )
        )
        return None
    return equivalent[0] if equivalent else None


def _resolve_scalar_field(
    field: Literal["term"],
    members: list[HistoricalDocumentCandidate],
    existing: NormalizedCase | None,
    conflicts: list[RecoveryConflict],
) -> int | None:
    existing_value = existing.term if existing is not None else None
    values = sorted({item.term for item in members if item.term is not None})
    if existing_value is not None:
        if values and any(value != existing_value for value in values):
            conflicts.append(
                RecoveryConflict(
                    code="existing-metadata-precedence",
                    field=field,
                    message="existing term retained over conflicting extracted metadata",
                    severity=ConflictSeverity.WARNING,
                    document_hashes=sorted(item.document_hash for item in members if item.term),
                )
            )
        return existing_value
    if len(values) > 1:
        conflicts.append(
            RecoveryConflict(
                code="field-conflict",
                field=field,
                message=f"documents contain conflicting {field} values",
                document_hashes=sorted(item.document_hash for item in members if item.term),
            )
        )
        return None
    return values[0] if values else None


def _resolve_date_field(
    field: Literal["argument", "decision"],
    members: list[HistoricalDocumentCandidate],
    existing: NormalizedCase | None,
    conflicts: list[RecoveryConflict],
) -> date | None:
    current = (
        existing.dates.argument if existing is not None and field == "argument" else
        existing.dates.decision if existing is not None else None
    )
    values = sorted(
        {
            value
            for item in members
            if (
                value := item.dates.argument if field == "argument" else item.dates.decision
            )
            is not None
        }
    )
    if current is not None:
        if values and any(value != current for value in values):
            conflicts.append(
                RecoveryConflict(
                    code="existing-metadata-precedence",
                    field=f"dates.{field}",
                    message=f"existing {field} date retained over conflicting extracted metadata",
                    severity=ConflictSeverity.WARNING,
                    document_hashes=sorted(
                        item.document_hash
                        for item in members
                        if (item.dates.argument if field == "argument" else item.dates.decision)
                    ),
                )
            )
        return current
    if len(values) > 1:
        conflicts.append(
            RecoveryConflict(
                code="field-conflict",
                field=f"dates.{field}",
                message=f"documents contain conflicting {field} dates",
                severity=ConflictSeverity.WARNING,
                document_hashes=sorted(
                    item.document_hash for item in members if getattr(item.dates, field)
                ),
            )
        )
        return None
    return values[0] if values else None


def _resolve_parties(
    members: list[HistoricalDocumentCandidate],
    existing: NormalizedCase | None,
    conflicts: list[RecoveryConflict],
) -> list[Party]:
    if existing is not None and existing.parties:
        return existing.parties
    alternatives: dict[tuple[tuple[str, str], ...], list[Party]] = {}
    for item in members:
        if not item.parties:
            continue
        key = tuple(sorted((party.name.casefold(), party.role or "") for party in item.parties))
        alternatives[key] = item.parties
    if len(alternatives) > 1:
        conflicts.append(
            RecoveryConflict(
                code="field-conflict",
                field="parties",
                message="documents contain conflicting party lists",
                severity=ConflictSeverity.WARNING,
                document_hashes=sorted(item.document_hash for item in members if item.parties),
            )
        )
        return []
    return next(iter(alternatives.values()), [])


def _resolve_lifecycle(
    members: list[HistoricalDocumentCandidate],
    existing: NormalizedCase | None,
    dates: CaseDates,
) -> Lifecycle:
    rank = {
        Lifecycle.UNRESOLVED: 0,
        Lifecycle.PENDING: 1,
        Lifecycle.SCHEDULED: 2,
        Lifecycle.ARGUED: 3,
        Lifecycle.AWAITING_DECISION: 4,
        Lifecycle.DECIDED: 5,
        Lifecycle.DISMISSED: 6,
    }
    if existing is not None and existing.lifecycle is not Lifecycle.UNRESOLVED:
        return existing.lifecycle
    values = [item.lifecycle for item in members]
    lifecycle = max(values, key=rank.__getitem__, default=Lifecycle.UNRESOLVED)
    if lifecycle is Lifecycle.DISMISSED and dates.decision is None:
        return Lifecycle.UNRESOLVED
    return lifecycle


def _proposed_case(
    target_id: str,
    existing: NormalizedCase | None,
    members: list[HistoricalDocumentCandidate],
    dockets: list[str],
    title: str | None,
    term: int | None,
    dates: CaseDates,
    lifecycle: Lifecycle,
    parties: list[Party],
) -> NormalizedCase:
    assert title is not None
    prior_documents = {item.sha256: item for item in existing.documents} if existing else {}
    for item in members:
        prior_reference = prior_documents.get(item.document_hash)
        prior_documents[item.document_hash] = CaseDocumentReference(
            sha256=item.document_hash,
            document_type=item.document_type,
            current=prior_reference.current if prior_reference is not None else True,
        )
    all_dockets = _sort_dockets(
        {*dockets, *(existing.docket_numbers if existing is not None else [])}
    )
    aliases = list(existing.aliases) if existing else []
    aliases.extend(label for item in members for label in item.docket_labels)
    provenance = list(existing.provenance) if existing else []
    provenance.extend(
        entry
        for item in members
        for entry in item.provenance
        if _candidate_supports_resolved_field(
            item, entry.field, title, term, dates, lifecycle, parties
        )
    )
    provenance = _deduplicate_models(provenance)
    if existing is not None:
        dates = existing.dates.model_copy(
            update={
                key: value
                for key, value in dates.model_dump().items()
                if value is not None and getattr(existing.dates, key) is None
            }
        )
    return NormalizedCase(
        case_id=target_id,
        slug=existing.slug if existing else target_id,
        title=title,
        term=term,
        docket_numbers=all_dockets,
        primary_docket=(
            existing.primary_docket if existing and existing.primary_docket else dockets[0]
        ),
        aliases=sorted(set(aliases)),
        lifecycle=lifecycle,
        dates=dates,
        parties=parties,
        provenance=provenance,
        documents=sorted(prior_documents.values(), key=lambda item: item.sha256),
        unresolved_group=None,
    )


def _candidate_supports_resolved_field(
    candidate: HistoricalDocumentCandidate,
    field: str,
    title: str,
    term: int | None,
    dates: CaseDates,
    lifecycle: Lifecycle,
    parties: list[Party],
) -> bool:
    if field == "title":
        return candidate.title is not None and _captions_equivalent(candidate.title, title)
    if field == "term":
        return candidate.term == term
    if field == "dates":
        candidate_dates = candidate.dates
        return all(
            proposed is None or proposed == resolved
            for proposed, resolved in (
                (candidate_dates.argument, dates.argument),
                (candidate_dates.decision, dates.decision),
            )
        )
    if field == "parties":
        resolved = {(item.name.casefold(), item.role or "") for item in parties}
        supported = {(item.name.casefold(), item.role or "") for item in candidate.parties}
        return bool(supported) and supported == resolved
    if field in {"lifecycle", "disposition"}:
        return candidate.lifecycle == lifecycle
    return field == "docket_numbers"


def _deduplicate_models(values: list[MetadataProvenance]) -> list[MetadataProvenance]:
    keyed = {
        json.dumps(item.model_dump(mode="json"), sort_keys=True, separators=(",", ":")): item
        for item in values
    }
    return [keyed[key] for key in sorted(keyed)]


def _recovered_case_id(term: int, docket: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", docket.casefold()).strip("-")
    return f"{term}-{normalized}"


def _planning_report(
    entries: list[DocumentManifestEntry],
    candidates: list[HistoricalDocumentCandidate],
    components: list[RecoveryComponent],
    conflicts: list[RecoveryConflict],
) -> HistoricalRecoveryReport:
    assigned = sorted(
        {
            document_hash
            for component in components
            if component.proposed_case is not None
            for document_hash in component.document_hashes
        }
    )
    examined = sorted(entry.sha256 for entry in entries)
    unresolved = sorted(set(examined) - set(assigned))
    by_group: dict[str, list[str]] = defaultdict(list)
    merged: dict[str, list[str]] = {}
    mappings: dict[str, str] = {}
    for component in components:
        for group in component.historical_groups:
            by_group[group].append(component.component_id)
        if len(component.historical_groups) > 1:
            merged[component.component_id] = component.historical_groups
        if component.target_case_id:
            mappings[component.component_id] = component.target_case_id
    split = {
        group: sorted(set(component_ids))
        for group, component_ids in sorted(by_group.items())
        if len(set(component_ids)) > 1
    }
    return HistoricalRecoveryReport(
        documents_examined=len(examined),
        candidates_recovered=len(candidates),
        unresolved_documents=len(unresolved),
        components_proposed=sum(item.proposed_case is not None for item in components),
        assigned_document_hashes=assigned,
        unresolved_document_hashes=unresolved,
        case_mappings=dict(sorted(mappings.items())),
        split_groups=split,
        merged_groups=dict(sorted(merged.items())),
        conflicts=sorted(conflicts, key=_conflict_key),
        warnings=sorted(
            f"{candidate.document_hash}: {warning}"
            for candidate in candidates
            for warning in candidate.warnings
        ),
    )


def validate_historical_recovery_plan(
    repository_root: Path,
    manifest_store: ManifestStore,
    plan: HistoricalRecoveryPlan,
) -> None:
    """Fail closed if the plan is stale or would violate repository references."""
    manifest = manifest_store.load()
    if _stored_contract_hash(manifest_store.path, manifest) != plan.source_manifest_hash:
        raise ValueError("historical recovery plan manifest precondition failed")
    case_files, cases = _load_case_repository(repository_root)
    actual_hashes = {
        case_id: _stored_contract_hash(case_files[case_id], case)
        for case_id, case in cases.items()
    }
    if actual_hashes != plan.source_case_hashes:
        raise ValueError("historical recovery plan case repository precondition failed")
    repository_conflicts = _repository_conflicts(manifest, case_files, cases)
    if any(item.severity is ConflictSeverity.BLOCKING for item in repository_conflicts):
        raise ValueError(repository_conflicts[0].message)

    manifest_hashes = {item.sha256 for item in manifest.documents}
    candidate_hashes = [item.document_hash for item in plan.candidates]
    if len(candidate_hashes) != len(set(candidate_hashes)):
        raise ValueError("plan contains duplicate document candidates")
    missing_candidates = sorted(set(candidate_hashes) - manifest_hashes)
    if missing_candidates:
        raise ValueError(f"plan candidates are absent from manifest: {missing_candidates[0]}")
    component_hashes_all = [
        document_hash
        for component in plan.components
        for document_hash in component.document_hashes
    ]
    if len(component_hashes_all) != len(set(component_hashes_all)):
        raise ValueError("plan repeats a document hash across components")
    if set(component_hashes_all) - manifest_hashes:
        raise ValueError("plan component references a document absent from the manifest")
    for component in plan.components:
        if set(component.document_types) != set(component.document_hashes):
            raise ValueError(f"component {component.component_id} has inconsistent document types")
    entries = {entry.sha256: entry for entry in manifest.documents}
    archive_present = (repository_root / "documents").exists()
    for candidate in plan.candidates:
        entry = entries[candidate.document_hash]
        import_paths = {source.import_path for source in entry.sources if source.import_path}
        historical_groups = {
            association.historical_group
            for association in entry.cases
            if association.historical_group is not None
        }
        if candidate.import_path not in import_paths or candidate.archive_path not in {
            None,
            entry.archive_path,
        }:
            raise ValueError(
                f"candidate {candidate.document_hash} does not match its manifest source"
            )
        if not set(candidate.historical_groups).issubset(historical_groups):
            raise ValueError(
                f"candidate {candidate.document_hash} has unknown historical groups"
            )
        if classify_historical_document_type(candidate.import_path) is not candidate.document_type:
            raise ValueError(f"candidate {candidate.document_hash} has an invalid document type")
        archive_path = repository_root / entry.archive_path
        if archive_present:
            if not archive_path.is_file():
                raise ValueError(f"archived document is missing: {entry.archive_path}")
            actual_hash, actual_size = hash_file(archive_path)
            if actual_hash != entry.sha256 or actual_size != entry.byte_size:
                raise ValueError(f"archived document precondition failed: {entry.archive_path}")

    expected_components, _ = _build_recovery_components(plan.candidates, cases)
    if plan.components != expected_components:
        raise ValueError("historical recovery plan components do not match its candidates")

    proposed = [item.proposed_case for item in plan.components if item.proposed_case is not None]
    target_ids = [item.case_id for item in proposed]
    if len(target_ids) != len(set(target_ids)):
        raise ValueError("plan contains duplicate target case IDs")
    primary: dict[str, str] = {}
    references: dict[str, str] = {}
    for case in [*cases.values(), *proposed]:
        if case.case_id in target_ids and case.case_id in cases and case not in proposed:
            continue
        if case.primary_docket:
            owner = primary.setdefault(case.primary_docket, case.case_id)
            if owner != case.case_id:
                raise ValueError(
                    f"duplicate primary docket {case.primary_docket}: {owner}, {case.case_id}"
                )
        hashes = [item.sha256 for item in case.documents]
        if len(hashes) != len(set(hashes)):
            raise ValueError(f"case {case.case_id} contains duplicate document references")
        for document_hash in hashes:
            if document_hash not in manifest_hashes:
                raise ValueError(f"case {case.case_id} references missing document {document_hash}")
            references.setdefault(document_hash, case.case_id)
    proposed_by_id = {case.case_id: case for case in proposed}
    final_cases = {**cases, **proposed_by_id}
    final_primary: dict[str, str] = {}
    for case in final_cases.values():
        if case.primary_docket is None:
            continue
        owner = final_primary.setdefault(case.primary_docket, case.case_id)
        if owner != case.case_id:
            raise ValueError(
                f"duplicate primary docket {case.primary_docket}: {owner}, {case.case_id}"
            )


def apply_historical_recovery(
    repository_root: Path,
    manifest_store: ManifestStore,
    plan: HistoricalRecoveryPlan,
    *,
    report_path: Path | None = None,
) -> HistoricalRecoveryReport:
    """Validate and atomically apply a recovery plan under the manifest lock."""
    with manifest_store.locked():
        validate_historical_recovery_plan(repository_root, manifest_store, plan)
        manifest = manifest_store.load()
        _, cases = _load_case_repository(repository_root)
        proposed = {
            component.target_case_id: component.proposed_case
            for component in plan.components
            if component.target_case_id is not None and component.proposed_case is not None
        }
        assigned_components = {
            document_hash: component
            for component in plan.components
            if component.proposed_case is not None
            for document_hash in component.document_hashes
        }
        candidate_types = {item.document_hash: item.document_type for item in plan.candidates}
        unresolved_ids = {
            case.case_id for case in cases.values() if case.unresolved_group is not None
        }

        updated_entries: list[DocumentManifestEntry] = []
        manifest_changed = False
        for entry in manifest.documents:
            document_type = candidate_types.get(entry.sha256, entry.document_type)
            associations = list(entry.cases)
            component = assigned_components.get(entry.sha256)
            if component is not None and component.target_case_id is not None:
                enriched: list[CaseAssociation] = []
                replaced = False
                for association in associations:
                    if association.historical_group is not None and (
                        association.case_id is None or association.case_id in unresolved_ids
                    ):
                        enriched.append(
                            CaseAssociation(
                                case_id=component.target_case_id,
                                docket_numbers=component.docket_numbers,
                                historical_group=association.historical_group,
                            )
                        )
                        replaced = True
                    else:
                        enriched.append(association)
                if not replaced:
                    if component.historical_groups:
                        for group in component.historical_groups:
                            enriched.append(
                                CaseAssociation(
                                    case_id=component.target_case_id,
                                    docket_numbers=component.docket_numbers,
                                    historical_group=group,
                                )
                            )
                    else:
                        enriched.append(
                            CaseAssociation(
                                case_id=component.target_case_id,
                                docket_numbers=component.docket_numbers,
                            )
                        )
                associations = _deduplicate_associations(enriched)
            updated = entry.model_copy(
                update={"document_type": document_type, "cases": associations}
            )
            manifest_changed = manifest_changed or updated != entry
            updated_entries.append(updated)
        updated_manifest = DocumentManifest(documents=updated_entries)

        assigned_hashes = set(assigned_components)
        removals: list[str] = []
        reductions: list[str] = []
        resulting_cases = dict(cases)
        for case_id, case in sorted(cases.items()):
            if case.unresolved_group is None or case_id in proposed:
                continue
            remaining = [item for item in case.documents if item.sha256 not in assigned_hashes]
            if len(remaining) == len(case.documents):
                continue
            if remaining:
                resulting_cases[case_id] = case.model_copy(update={"documents": remaining})
                reductions.append(case_id)
            else:
                resulting_cases.pop(case_id)
                removals.append(case_id)
        for case_id, case in proposed.items():
            assert case_id is not None and case is not None
            resulting_cases[case_id] = case

        resulting_paths = {
            case_id: repository_root / "data" / "cases" / f"{case_id}.json"
            for case_id in resulting_cases
        }
        resulting_conflicts = _repository_conflicts(
            updated_manifest, resulting_paths, resulting_cases
        )
        blocking = [
            item for item in resulting_conflicts if item.severity is ConflictSeverity.BLOCKING
        ]
        if blocking:
            raise ValueError(f"recovery result is invalid: {blocking[0].message}")

        changed_cases = sorted(
            case_id
            for case_id, case in resulting_cases.items()
            if case_id not in cases or _model_hash(case) != _model_hash(cases[case_id])
        )
        no_op = not manifest_changed and not changed_cases and not removals
        base = _planning_report(
            [
                entry
                for entry in manifest.documents
                if any(source.import_path is not None for source in entry.sources)
            ],
            plan.candidates,
            plan.components,
            plan.conflicts,
        )
        report = base.model_copy(
            update={
                "cases_written": changed_cases,
                "unresolved_cases_removed": removals,
                "unresolved_cases_reduced": reductions,
                "manifest_changed": manifest_changed,
                "no_op": no_op,
            }
        )
        if no_op:
            if report_path is not None:
                _atomic_contract(report_path, report)
        else:
            _commit_recovery_transaction(
                repository_root,
                manifest_store,
                manifest,
                updated_manifest,
                cases,
                resulting_cases,
                removals,
                report_path=report_path,
                report=report,
            )
        return report


def _commit_recovery_transaction(
    root: Path,
    store: ManifestStore,
    old_manifest: DocumentManifest,
    new_manifest: DocumentManifest,
    old_cases: dict[str, NormalizedCase],
    new_cases: dict[str, NormalizedCase],
    removals: list[str],
    *,
    report_path: Path | None,
    report: HistoricalRecoveryReport,
) -> None:
    case_dir = root / "data" / "cases"
    case_dir.mkdir(parents=True, exist_ok=True)
    affected = sorted(
        set(removals)
        | {
            case_id
            for case_id, case in new_cases.items()
            if case_id not in old_cases or _model_hash(case) != _model_hash(old_cases[case_id])
        }
    )
    originals: dict[Path, bytes | None] = {}
    staged: dict[Path, Path] = {}
    try:
        for case_id in affected:
            destination = case_dir / f"{case_id}.json"
            originals[destination] = destination.read_bytes() if destination.exists() else None
            if case_id not in new_cases:
                continue
            descriptor, raw = tempfile.mkstemp(prefix=destination.name, suffix=".tmp", dir=case_dir)
            temporary = Path(raw)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(new_cases[case_id].model_dump_json(indent=2) + "\n")
                output.flush()
                os.fsync(output.fileno())
            staged[destination] = temporary
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            originals[report_path] = report_path.read_bytes() if report_path.exists() else None
            descriptor, raw = tempfile.mkstemp(
                prefix=report_path.name, suffix=".tmp", dir=report_path.parent
            )
            temporary = Path(raw)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(report.model_dump_json(indent=2) + "\n")
                output.flush()
                os.fsync(output.fileno())
            staged[report_path] = temporary
        for destination in sorted(originals, key=str):
            staged_temporary = staged.get(destination)
            if staged_temporary is None:
                destination.unlink(missing_ok=True)
            else:
                os.replace(staged_temporary, destination)
        store.save(new_manifest)
    except BaseException:
        for destination, content in originals.items():
            if content is None:
                destination.unlink(missing_ok=True)
            else:
                destination.write_bytes(content)
        if _model_hash(store.load()) != _model_hash(old_manifest):
            store.save(old_manifest)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _repository_conflicts(
    manifest: DocumentManifest,
    case_files: dict[str, Path],
    cases: dict[str, NormalizedCase],
) -> list[RecoveryConflict]:
    conflicts: list[RecoveryConflict] = []
    primary: dict[str, str] = {}
    manifest_hashes = {entry.sha256 for entry in manifest.documents}
    for case_id, case in sorted(cases.items()):
        path = case_files[case_id]
        if path.stem != case_id:
            conflicts.append(
                RecoveryConflict(
                    code="case-id-filename-mismatch",
                    message=f"{path.name} contains case ID {case_id}",
                )
            )
        if case.primary_docket:
            prior = primary.setdefault(case.primary_docket, case_id)
            if prior != case_id:
                conflicts.append(
                    RecoveryConflict(
                        code="duplicate-primary-docket",
                        message=(
                            f"primary docket {case.primary_docket} occurs in {prior} and {case_id}"
                        ),
                        docket_numbers=[case.primary_docket],
                    )
                )
        hashes = [item.sha256 for item in case.documents]
        if len(hashes) != len(set(hashes)):
            conflicts.append(
                RecoveryConflict(
                    code="duplicate-case-reference",
                    message=f"case {case_id} repeats a document reference",
                )
            )
        missing = sorted(set(hashes) - manifest_hashes)
        if missing:
            conflicts.append(
                RecoveryConflict(
                    code="missing-manifest-reference",
                    message=f"case {case_id} references documents absent from the manifest",
                    document_hashes=missing,
                )
            )
    known_ids = set(cases)
    seen_associations: set[tuple[str, str]] = set()
    for entry in manifest.documents:
        for association in entry.cases:
            key = (
                entry.sha256,
                json.dumps(association.model_dump(mode="json"), sort_keys=True),
            )
            if key in seen_associations:
                conflicts.append(
                    RecoveryConflict(
                        code="duplicate-manifest-association",
                        message=f"document {entry.sha256} repeats a case association",
                        document_hashes=[entry.sha256],
                    )
                )
            seen_associations.add(key)
            if association.case_id and association.case_id not in known_ids:
                conflicts.append(
                    RecoveryConflict(
                        code="dangling-case-association",
                        message=(
                            f"document {entry.sha256} references missing case "
                            f"{association.case_id}"
                        ),
                        document_hashes=[entry.sha256],
                    )
                )
    return sorted(conflicts, key=_conflict_key)


def _load_case_repository(
    repository_root: Path,
) -> tuple[dict[str, Path], dict[str, NormalizedCase]]:
    paths: dict[str, Path] = {}
    cases: dict[str, NormalizedCase] = {}
    directory = repository_root / "data" / "cases"
    if not directory.exists():
        return paths, cases
    for path in sorted(directory.glob("*.json")):
        case = NormalizedCase.model_validate_json(path.read_text())
        if case.case_id in cases:
            raise ValueError(f"duplicate case ID {case.case_id}")
        paths[case.case_id] = path
        cases[case.case_id] = case
    return paths, cases


def _deduplicate_associations(values: list[CaseAssociation]) -> list[CaseAssociation]:
    keyed = {
        json.dumps(item.model_dump(mode="json"), sort_keys=True, separators=(",", ":")): item
        for item in values
    }
    return [keyed[key] for key in sorted(keyed)]


def _model_hash(model: ContractModel) -> str:
    payload = json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _contract_payload_hash(model: ContractModel) -> str:
    return hashlib.sha256((model.model_dump_json(indent=2) + "\n").encode()).hexdigest()


def _stored_contract_hash(path: Path, fallback: ContractModel) -> str:
    if path.exists():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return _contract_payload_hash(fallback)


def _conflict_key(conflict: RecoveryConflict) -> tuple[object, ...]:
    return (
        conflict.severity.value,
        conflict.code,
        conflict.field or "",
        conflict.message,
        tuple(conflict.document_hashes),
        tuple(conflict.docket_numbers),
    )


def _atomic_contract(path: Path, model: ContractModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(model.model_dump_json(indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _normalize_text(value: str) -> str:
    translations = str.maketrans(
        {
            "\ufb00": "ff",
            "\ufb01": "fi",
            "\ufb02": "fl",
            "\u00a0": " ",
            "\r": "\n",
        }
    )
    value = value.translate(translations)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(lines)


def _clean_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def parse_historical_dockets(text: str) -> tuple[list[str], list[str]]:
    """Return canonical dockets and their explicit source labels from opening text."""
    return _extract_labeled_dockets(_normalize_text(text))


def _extract_labeled_dockets(text: str) -> tuple[list[str], list[str]]:
    dockets: list[str] = []
    labels: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        for marker in _DOCKET_LABEL_RE.finditer(line):
            tail = line[marker.end() :]
            # Some converted PDFs put the docket itself on the following line.
            if not tail.strip() and index + 1 < len(lines):
                tail = lines[index + 1]
            # A label's docket list never legitimately continues into prose.
            tail = re.split(r"[;)]|\b(?:Argued|Decided|Petitioner|Respondent)\b", tail)[0]
            for match in _DOCKET_TOKEN_RE.finditer(tail):
                raw = match.group(0).strip().rstrip(".,;")
                canonical = _canonical_docket(raw)
                if canonical is not None:
                    dockets.append(canonical)
                    labels.append(f"{marker.group(0).strip()} {raw}".strip())
    return _sort_dockets(set(dockets)), _deduplicate(labels)


def _extract_title_dockets(title: str | None) -> tuple[list[str], list[str]]:
    if title is None:
        return [], []
    prefix = re.match(
        rf"^\s*(?P<docket>\d{{2,4}}\s*(?:[{_HYPHEN_CLASS}]\s*\d+|[Aa]\s*\d+)|"
        r"\d{1,3}\s*,?\s*Orig(?:inal)?\.?)(?=\s|$)",
        title,
        re.IGNORECASE,
    )
    if prefix is None:
        return _extract_labeled_dockets(title)
    raw = prefix.group("docket")
    canonical = _canonical_docket(raw)
    return ([canonical], [raw]) if canonical is not None else ([], [])


def _canonical_docket(raw: str) -> str | None:
    original = re.fullmatch(r"\s*(\d{1,3})\s*,?\s*Orig(?:inal)?\.?\s*", raw, re.IGNORECASE)
    if original:
        value = f"{int(original.group(1))}O"
    else:
        value = re.sub(rf"\s*[{_HYPHEN_CLASS}]\s*", "-", raw)
        value = re.sub(r"\s+", "", value)
    try:
        return normalize_docket(value)
    except ValueError:
        return None


def _sort_dockets(values: set[str]) -> list[str]:
    return sorted(values, key=docket_sort_key)


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _dates_for_pattern(pattern: re.Pattern[str], value: str) -> set[date]:
    dates: set[date] = set()
    for match in pattern.finditer(value):
        parsed = _date_from_match(match)
        if parsed is not None:
            dates.add(parsed)
    return dates


def _date_from_match(match: re.Match[str]) -> date | None:
    try:
        return date(
            int(match.group("year")),
            _MONTHS[match.group("month").lower().rstrip(".")],
            int(match.group("day")),
        )
    except (ValueError, KeyError):
        return None


def _argument_dates(text: str, document_type: DocumentType) -> set[date]:
    dates = _dates_for_pattern(_ARGUMENT_RE, text)
    if document_type is DocumentType.TRANSCRIPT:
        dates.update(_dates_for_pattern(_TRANSCRIPT_DATE_RE, text))
        dates.update(_dates_for_pattern(_WEEKDAY_DATE_RE, text))
    return dates


def _decision_dates(
    text: str, embedded_title: str | None, document_type: DocumentType
) -> set[date]:
    dates = _dates_for_pattern(_DECISION_RE, text)
    if document_type not in {DocumentType.OPINION, DocumentType.ORDER}:
        return dates
    if embedded_title:
        for month, day, year in re.findall(r"\((\d{1,2})/(\d{1,2})/(\d{4})\)", embedded_title):
            with suppress(ValueError):
                dates.add(date(int(year), int(month), int(day)))
    return dates


def _single_value(values: set[date]) -> tuple[date | None, bool]:
    if len(values) == 1:
        return next(iter(values)), False
    return None, len(values) > 1


def _court_term(value: date) -> int:
    return value.year if value.month >= 10 else value.year - 1


def _caption_from_metadata(title: str | None) -> str | None:
    if title is None:
        return None
    caption = re.sub(
        rf"^\s*(?:No\.?\s*)?(?:\d{{2,4}}\s*(?:[{_HYPHEN_CLASS}]\s*\d+|[Aa]\s*\d+)|"
        r"\d{1,3}\s*,?\s*Orig(?:inal)?\.?)\s*[-:\N{EN DASH}\N{EM DASH}]?\s*",
        "",
        title,
        count=1,
        flags=re.IGNORECASE,
    )
    caption = re.sub(r"\s*\(\d{1,2}/\d{1,2}/\d{4}\)\s*$", "", caption)
    caption = caption.strip(f" :{_HYPHENS}")
    return caption if _looks_like_caption(caption) else None


def _extract_caption(text: str, document_type: DocumentType) -> str | None:
    lines = [line.strip(" |") for line in text.splitlines() if line.strip(" |")]
    candidates: list[str] = []
    for index, line in enumerate(lines):
        if not re.search(r"\bv\.?\s*(?:$|[^a-z])", line, re.IGNORECASE):
            continue
        if len(line) > 220 or re.search(r"\b(?:see|cf\.)\s+\w+\s+v\.", line, re.IGNORECASE):
            continue
        start = index
        while start > 0 and index - start < 2 and _caption_continuation(lines[start - 1]):
            start -= 1
        end = index + 1
        while end < len(lines) and end - index <= 3 and _caption_continuation(lines[end]):
            end += 1
        caption = _clean_caption(" ".join(lines[start:end]))
        if _looks_like_caption(caption):
            candidates.append(caption)
    if candidates:
        # Opinion/order captions are usually complete on one line; transcripts often need
        # the role-aware parser below because the docket occurs between "v." and respondent.
        return max(candidates, key=_caption_score)
    if document_type is DocumentType.TRANSCRIPT:
        return _transcript_caption(lines)
    return None


def _caption_continuation(line: str) -> bool:
    if len(line) > 180 or re.search(
        r"\b(?:No\.|Nos\.|Argued|Decided|Syllabus|Petitioner|Respondent|Washington|Pages?:|Date:)\b",
        line,
        re.IGNORECASE,
    ):
        return False
    letters = [character for character in line if character.isalpha()]
    return bool(letters) and sum(character.isupper() for character in letters) / len(letters) > 0.6


def _clean_caption(value: str) -> str:
    value = re.sub(r"(?<=[A-Z])-\s+(?=[A-Z])", "", value)
    value = re.sub(r"\s*\)\s*", " ", value)
    value = _DOCKET_LABEL_RE.sub(" ", value)
    value = _DOCKET_TOKEN_RE.sub(" ", value)
    value = re.sub(r"\b(?:Petitioners?|Respondents?),?\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" ,;:-")
    value = re.sub(r"\s+v\.?\s+", " v. ", value, flags=re.IGNORECASE)
    return value


def _transcript_caption(lines: list[str]) -> str | None:
    captions = _transcript_captions(lines)
    return captions[0] if captions else None


def _transcript_captions(lines: list[str]) -> list[str]:
    captions: list[str] = []
    lines = [_transcript_layout_line(line) for line in lines]
    for index, line in enumerate(lines):
        if not re.fullmatch(r"v\.?\s*(?:\)\s*)?(?:Nos?\..*)?", line, re.IGNORECASE):
            continue
        left = _nearest_party(lines, index, -1)
        right = _nearest_party(lines, index, 1)
        if left and right:
            captions.append(f"{left} v. {right}")
    return _deduplicate(captions)


def _transcript_layout_line(line: str) -> str:
    line = re.sub(r"^\d{1,2}\s+(?=[A-Za-z-])", "", line).strip()
    # Older RealLegal transcripts use colons as the vertical caption rule.
    line = re.sub(r"\s*:\s*", " ", line)
    line = re.sub(r"\s+", " ", line).strip()
    return "" if re.fullmatch(r"\d{1,2}|[- xX]+", line) else line


def _nearest_party(lines: list[str], origin: int, direction: int) -> str | None:
    collected: list[str] = []
    index = origin + direction
    while 0 <= index < len(lines) and len(collected) < 4:
        line = lines[index]
        if re.search(
            r"\b(?:Petitioner|Respondent|Appellant|Appellee|Applicant)s?\b",
            line,
            re.IGNORECASE,
        ):
            if collected:
                break
            index += direction
            continue
        cleaned = _clean_caption(line)
        stop_pattern = (
            r"SUPREME COURT|IN THE|OF THE UNITED STATES|Official|Pages?:|Place:|"
            r"Date:|Washington|No\."
        )
        if not cleaned or re.search(stop_pattern, cleaned, re.IGNORECASE):
            if collected:
                break
            index += direction
            continue
        letters = [character for character in cleaned if character.isalpha()]
        if not letters or sum(character.isupper() for character in letters) / len(letters) < 0.5:
            break
        collected.append(cleaned)
        index += direction
    if direction < 0:
        collected.reverse()
    return " ".join(collected).strip(" ,;") or None


def _looks_like_caption(value: str) -> bool:
    return bool(re.search(r"\s+v\.?\s+", value, re.IGNORECASE)) and len(value) <= 350


def _caption_score(value: str) -> tuple[int, int]:
    return (int(" v. " in value), len(value))


def _normalized_party_words(caption: str) -> set[str]:
    ignored = {"the", "of", "and", "et", "al", "v"}
    return {
        word.casefold()
        for word in re.findall(r"[A-Za-z]{3,}", caption)
        if word.casefold() not in ignored
    }


def _captions_equivalent(first: str, second: str) -> bool:
    return re.sub(r"[^a-z0-9]", "", first.casefold()) == re.sub(
        r"[^a-z0-9]", "", second.casefold()
    )


def _caption_party_overlap(first: str, second: str) -> bool:
    first_words = _normalized_party_words(first)
    second_words = _normalized_party_words(second)
    return bool(first_words & second_words)


def _extract_parties(
    title: str | None, text: str, document_type: DocumentType
) -> list[Party]:
    if title is None:
        return []
    captions = [title]
    if document_type is DocumentType.TRANSCRIPT:
        lines = [line.strip(" |") for line in text.splitlines() if line.strip(" |")]
        captions.extend(_transcript_captions(lines))
    role_names = ("petitioner", "respondent")
    if re.search(r"\bAppellants?\b", text[:5000], re.IGNORECASE) and re.search(
        r"\bAppellees?\b", text[:5000], re.IGNORECASE
    ):
        role_names = ("appellant", "appellee")
    elif re.search(r"\bApplicants?\b", text[:5000], re.IGNORECASE):
        role_names = ("applicant", "respondent")
    parties: dict[tuple[str, str], Party] = {}
    for caption in captions:
        sides = re.split(r"\s+v\.?\s+", caption, maxsplit=1, flags=re.IGNORECASE)
        if len(sides) != 2:
            continue
        for name, role in zip(sides, role_names, strict=True):
            cleaned = re.sub(
                r"\s+et\s+al\.?$", "", name, flags=re.IGNORECASE
            ).strip(" ,;")
            if cleaned:
                parties[(cleaned.casefold(), role)] = Party(name=cleaned, role=role)
    return list(parties.values())


def _extract_disposition(text: str) -> Literal["granted", "denied", "dismissed"] | None:
    opening = text[:20_000]
    patterns: dict[Literal["granted", "denied", "dismissed"], re.Pattern[str]] = {
        "dismissed": re.compile(
            r"\b(?:petition|appeal|writ|case|motion)[^.]{0,180}\bis dismissed\b",
            re.IGNORECASE,
        ),
        "denied": re.compile(
            r"\b(?:petition|application|motion|request)[^.]{0,180}\bis denied\b",
            re.IGNORECASE,
        ),
        "granted": re.compile(
            r"\b(?:petition|application|motion|request)[^.]{0,180}\bis granted\b",
            re.IGNORECASE,
        ),
    }
    found = [name for name, pattern in patterns.items() if pattern.search(opening)]
    # A denied ancillary motion can accompany an explicit dismissal of the petition.
    # The dismissal is the case-level disposition in that common order layout.
    if "dismissed" in found:
        return "dismissed"
    return found[0] if len(found) == 1 else None


def _candidate_provenance(
    document_hash: str,
    **supported: bool,
) -> list[MetadataProvenance]:
    return [
        MetadataProvenance(
            field=field,
            document_hash=document_hash,
            method="extracted",
            confidence=1.0,
        )
        for field, present in supported.items()
        if present
    ]
