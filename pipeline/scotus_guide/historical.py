"""Deterministic metadata parsing for archived Supreme Court PDFs.

This module deliberately stops at producing recovery candidates and report contracts.  It
never updates the document manifest or normalized case records.
"""

from __future__ import annotations

import re
from contextlib import suppress
from datetime import date
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .dockets import docket_sort_key, normalize_docket
from .models import (
    CaseDates,
    ContractModel,
    DocketNumber,
    DocumentManifestEntry,
    DocumentType,
    Lifecycle,
    MetadataProvenance,
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
    """A proposed case component; construction and reconciliation live in later stages."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    component_id: str
    document_hashes: list[Sha256] = Field(default_factory=list)
    docket_numbers: list[DocketNumber] = Field(default_factory=list)
    historical_groups: list[str] = Field(default_factory=list)
    title: str | None = None
    term: Annotated[int, Field(ge=1789, le=2200)] | None = None
    lifecycle: Lifecycle = Lifecycle.UNRESOLVED
    conflicts: list[RecoveryConflict] = Field(default_factory=list)


class HistoricalRecoveryPlan(ContractModel):
    """Versioned, reviewable output of a future reconciliation planning stage."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    source_manifest_hash: Sha256
    components: list[RecoveryComponent] = Field(default_factory=list)
    conflicts: list[RecoveryConflict] = Field(default_factory=list)


class HistoricalRecoveryReport(ContractModel):
    """Versioned coverage/result report shared by plan and eventual apply operations."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    documents_examined: Annotated[int, Field(ge=0)] = 0
    candidates_recovered: Annotated[int, Field(ge=0)] = 0
    unresolved_documents: Annotated[int, Field(ge=0)] = 0
    components_proposed: Annotated[int, Field(ge=0)] = 0
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
