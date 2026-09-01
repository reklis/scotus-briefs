"""Versioned page/line and speaker-turn parsing for official Court transcripts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import BinaryIO, Protocol
from uuid import UUID

from pydantic import Field
from pypdf import PdfReader
from pypdf import __version__ as pypdf_version
from pypdf.errors import PdfReadError

from ragchew.config import ScotusParserDefaults
from ragchew.contracts import StrictModel
from ragchew.scotus.contracts import (
    AdvocateRole,
    ParseStatus,
    SpeakerIdentityBasis,
    SpeakerKind,
    TranscriptLine,
    TranscriptTurn,
)

_SPEAKER = re.compile(
    r"^(?P<label>(?:CHIEF JUSTICE(?:\s+[^:]{1,100})?|"
    r"JUSTICE(?:\s+[^:]{1,100})?|MR\.\s*[^:]{1,100}|"
    r"MS\.\s*[^:]{1,100}|MRS\.\s*[^:]{1,100}|"
    r"GENERAL(?:\s+[^:]{1,100})?|DEPUTY SOLICITOR GENERAL\s+[^:]{1,100}|"
    r"SOLICITOR GENERAL(?:\s+[^:]{1,100})?|PROFESSOR\s+[^:]{1,100}|"
    r"THE CLERK(?:\s+[^:]{1,100})?))\s*:\s*(?P<text>.*)$",
    re.IGNORECASE,
)
_MALFORMED_SPEAKER = re.compile(
    r"^(?:CHIEF JUSTICE|JUSTICE|MR\.|MS\.|MRS\.|GENERAL|SOLICITOR GENERAL)\b[^:]*:"
)
_END_OF_ARGUMENT = re.compile(
    r"(?:^[\[(]?Whereupon,?\s+(?:at\s+)?|^\(?Short break at\b|"
    r"\bThe (?:case|matter) is (?:now )?submitted\b|"
    r"^I\s+N\s+D\s+E\s+X\b|^INDEX\b|^REPORTER['\u2019]S CERTIFICATE\b)",
    re.IGNORECASE,
)
_ARTIFACTS = (
    re.compile(r"official\s+-\s+subject\s+to\s+final\s+review", re.IGNORECASE),
    re.compile(r"heritage reporting corporation", re.IGNORECASE),
    re.compile(r"alderson reporting company", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*$"),
)
# Court layout extraction pads printed line numbers with variable-width columns.
_LINE_PREFIX = re.compile(r"^\s*\d{1,2}\s+(?=\S)")


class TranscriptParseError(RuntimeError):
    """Raised when transcript structure cannot support exact evidence ranges."""


class PdfTextBackend(Protocol):
    name: str
    version: str

    def extract_pages(self, file: BinaryIO) -> tuple[str, ...]: ...


class PypdfTextBackend:
    name = "pypdf"
    version = pypdf_version

    def extract_pages(self, file: BinaryIO) -> tuple[str, ...]:
        file.seek(0)
        try:
            reader = PdfReader(file, strict=True)
            if reader.is_encrypted and not reader.decrypt(""):
                raise TranscriptParseError("password-protected transcript is unsupported")
            pages = tuple(
                page.extract_text(extraction_mode="layout") or "" for page in reader.pages
            )
        except (PdfReadError, ValueError) as error:
            raise TranscriptParseError(f"transcript PDF cannot be parsed: {error}") from error
        finally:
            file.seek(0)
        return pages


class TranscriptParseResult(StrictModel):
    parse_revision_id: UUID
    document_revision_id: UUID
    parser_name: str
    parser_version: str
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ParseStatus
    page_count: int = Field(gt=0)
    ambiguous_pages: tuple[int, ...] = ()
    line_coverage: float = Field(ge=0, le=1)
    lines: tuple[TranscriptLine, ...]
    turns: tuple[TranscriptTurn, ...]


@dataclass(frozen=True)
class _TurnBuilder:
    sequence: int
    start_file_page: int
    start_line: int
    speaker_label: str | None
    speaker_name: str | None
    speaker_kind: SpeakerKind
    advocate_role: AdvocateRole | None
    identity_basis: SpeakerIdentityBasis
    text: tuple[str, ...]


def _artifact(raw: str) -> bool:
    return any(pattern.search(raw) for pattern in _ARTIFACTS)


def _normalized(raw: str) -> str:
    normalized = " ".join(_LINE_PREFIX.sub("", raw).split())
    return re.sub(r"^MS\.re\s+", "MS. ", normalized)


def _printed_page(raw_lines: list[str]) -> int | None:
    candidates = [*raw_lines[:5], *raw_lines[-5:]]
    values = [int(line.strip()) for line in candidates if re.fullmatch(r"\s*\d{1,3}\s*", line)]
    return values[-1] if values else None


def _speaker(label: str) -> tuple[str, SpeakerKind, AdvocateRole | None]:
    canonical = " ".join(label.upper().split())
    if canonical == "CHIEF JUSTICE":
        return "Chief Justice", SpeakerKind.JUSTICE, None
    if canonical.startswith("CHIEF JUSTICE "):
        surname = canonical.removeprefix("CHIEF JUSTICE ").title()
        return f"Chief Justice {surname}", SpeakerKind.JUSTICE, None
    if canonical == "JUSTICE":
        return "Justice", SpeakerKind.JUSTICE, None
    if canonical.startswith("JUSTICE "):
        surname = canonical.removeprefix("JUSTICE ").title()
        return f"Justice {surname}", SpeakerKind.JUSTICE, None
    if canonical.startswith("THE CLERK"):
        return "The Clerk", SpeakerKind.COURT_OFFICIAL, None
    return label.title(), SpeakerKind.ADVOCATE, AdvocateRole.UNKNOWN


def _config_hash(config: ScotusParserDefaults, backend: PdfTextBackend) -> str:
    encoded = (
        f"{config.model_dump_json()}:{backend.name}:{backend.version}:"
        "speaker-v1:artifact-v1"
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class ScotusTranscriptParser:
    def __init__(self, backend: PdfTextBackend, config: ScotusParserDefaults) -> None:
        self.backend = backend
        self.config = config

    def parse(
        self,
        file: BinaryIO,
        *,
        parse_revision_id: UUID,
        document_revision_id: UUID,
    ) -> TranscriptParseResult:
        pages = self.backend.extract_pages(file)
        if not pages:
            raise TranscriptParseError("transcript has no pages")
        ambiguous = tuple(index for index, text in enumerate(pages, 1) if not text.strip())
        if len(ambiguous) > self.config.maximum_ambiguous_pages:
            raise TranscriptParseError("transcript has ambiguous or empty pages")

        lines: list[TranscriptLine] = []
        nonartifact = 0
        normalized_nonempty = 0
        argument_ended = False
        for file_page, text in enumerate(pages, 1):
            if argument_ended:
                break
            raw_lines = text.splitlines()
            printed_page = _printed_page(raw_lines)
            for line_number, raw in enumerate(raw_lines, 1):
                if not raw.strip():
                    continue
                artifact = _artifact(raw)
                normalized = None if artifact else _normalized(raw)
                if not artifact:
                    nonartifact += 1
                    if normalized:
                        normalized_nonempty += 1
                lines.append(
                    TranscriptLine(
                        parse_revision_id=parse_revision_id,
                        document_revision_id=document_revision_id,
                        file_page=file_page,
                        printed_page=printed_page,
                        line_number=line_number,
                        raw_text_private=raw,
                        normalized_text_private=normalized,
                        artifact=artifact,
                    )
                )
                if normalized and _END_OF_ARGUMENT.search(normalized):
                    argument_ended = True
                    break
        coverage = normalized_nonempty / nonartifact if nonartifact else 0
        if coverage < self.config.minimum_line_coverage:
            raise TranscriptParseError("transcript line coverage is below configured threshold")
        turns = self._turns(lines, parse_revision_id, document_revision_id)
        if not turns:
            raise TranscriptParseError("transcript contains no parseable speaker turns")
        return TranscriptParseResult(
            parse_revision_id=parse_revision_id,
            document_revision_id=document_revision_id,
            parser_name=self.backend.name,
            parser_version=self.backend.version,
            config_hash=_config_hash(self.config, self.backend),
            status=ParseStatus.COMPLETE,
            page_count=len(pages),
            ambiguous_pages=ambiguous,
            line_coverage=coverage,
            lines=tuple(lines),
            turns=tuple(turns),
        )

    def _turns(
        self,
        lines: list[TranscriptLine],
        parse_revision_id: UUID,
        document_revision_id: UUID,
    ) -> list[TranscriptTurn]:
        builders: list[_TurnBuilder] = []
        current: _TurnBuilder | None = None
        argument_end: tuple[int, int] | None = None
        seen_official_label = False
        for line in lines:
            text = line.normalized_text_private
            if line.artifact or not text:
                continue
            if _END_OF_ARGUMENT.search(text):
                argument_end = (line.file_page, line.line_number)
                break
            match = _SPEAKER.match(text)
            if not match and _MALFORMED_SPEAKER.match(text):
                if seen_official_label:
                    raise TranscriptParseError(
                        f"malformed official speaker label: {text[:120]}"
                    )
                continue
            if match:
                seen_official_label = True
                if current and any(current.text):
                    builders.append(current)
                label = match.group("label")
                name, kind, advocate_role = _speaker(label)
                current = _TurnBuilder(
                    sequence=len(builders),
                    start_file_page=line.file_page,
                    start_line=line.line_number,
                    speaker_label=label,
                    speaker_name=name,
                    speaker_kind=kind,
                    advocate_role=advocate_role,
                    identity_basis=SpeakerIdentityBasis.OFFICIAL_TRANSCRIPT_LABEL,
                    text=(match.group("text"),) if match.group("text") else (),
                )
            elif current is None:
                current = _TurnBuilder(
                    sequence=len(builders),
                    start_file_page=line.file_page,
                    start_line=line.line_number,
                    speaker_label=None,
                    speaker_name=None,
                    speaker_kind=SpeakerKind.UNKNOWN,
                    advocate_role=None,
                    identity_basis=SpeakerIdentityBasis.ANONYMOUS,
                    text=(text,),
                )
            else:
                current = _TurnBuilder(
                    sequence=current.sequence,
                    start_file_page=current.start_file_page,
                    start_line=current.start_line,
                    speaker_label=current.speaker_label,
                    speaker_name=current.speaker_name,
                    speaker_kind=current.speaker_kind,
                    advocate_role=current.advocate_role,
                    identity_basis=current.identity_basis,
                    text=(*current.text, text),
                )
        if current and any(current.text):
            builders.append(current)
        turns: list[TranscriptTurn] = []
        significant = [
            line
            for line in lines
            if not line.artifact
            and line.normalized_text_private
            and (
                argument_end is None
                or (line.file_page, line.line_number) < argument_end
            )
        ]
        positions = {
            (line.file_page, line.line_number): index
            for index, line in enumerate(significant)
        }
        for sequence, builder in enumerate(builders):
            next_builder = builders[sequence + 1] if sequence + 1 < len(builders) else None
            if next_builder:
                next_position = positions[
                    (next_builder.start_file_page, next_builder.start_line)
                ]
                last = significant[max(0, next_position - 1)]
            else:
                last = significant[-1]
            end_file_page = last.file_page
            end_line = last.line_number
            turns.append(
                TranscriptTurn(
                    parse_revision_id=parse_revision_id,
                    document_revision_id=document_revision_id,
                    sequence=sequence,
                    start_file_page=builder.start_file_page,
                    start_line=builder.start_line,
                    end_file_page=end_file_page,
                    end_line=end_line,
                    speaker_label_private=builder.speaker_label,
                    speaker_name=builder.speaker_name,
                    speaker_kind=builder.speaker_kind,
                    advocate_role=builder.advocate_role,
                    identity_basis=builder.identity_basis,
                    text_private=" ".join(builder.text),
                    confidence=1 if builder.speaker_label else 0.5,
                )
            )
        return turns
