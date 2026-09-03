"""Schema-constrained, page/line-grounded Supreme Court legal extraction."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from openai import OpenAI, omit
from openai.lib._pydantic import to_strict_json_schema
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from ragchew.scotus.contracts import (
    AdvocateRole,
    LegalCertainty,
    LegalEvidenceRange,
    LegalObservation,
    LegalObservationType,
    LegalStatus,
    ScotusDocumentKind,
    ScotusSensitivity,
    SpeakerIdentityBasis,
    SpeakerKind,
    TranscriptTurn,
)


class LegalExtractionError(ValueError):
    pass


class LegalEvidenceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str = Field(min_length=1, max_length=200)
    document_revision_id: UUID
    document_kind: ScotusDocumentKind
    official_url: str
    start_file_page: int = Field(ge=1)
    start_line: int = Field(ge=1)
    end_file_page: int = Field(ge=1)
    end_line: int = Field(ge=1)
    text_private: str = Field(min_length=1, max_length=30_000)
    speaker_name: str | None = Field(default=None, max_length=300)
    speaker_kind: SpeakerKind = SpeakerKind.UNKNOWN
    identity_basis: SpeakerIdentityBasis = SpeakerIdentityBasis.ANONYMOUS
    attribution: str | None = Field(default=None, max_length=500)


class ProposedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    quote: str = Field(min_length=1, max_length=4_000)


class ProposedLegalObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_type: LegalObservationType
    legal_status: LegalStatus
    certainty: LegalCertainty
    raw_value: str = Field(min_length=1, max_length=8_000)
    normalized_value: str | None = Field(default=None, max_length=8_000)
    attribution: str | None = Field(default=None, max_length=500)
    speaker_name: str | None = Field(default=None, max_length=300)
    speaker_kind: SpeakerKind = SpeakerKind.UNKNOWN
    identity_basis: SpeakerIdentityBasis = SpeakerIdentityBasis.ANONYMOUS
    authority_citations: tuple[str, ...] = ()
    confidence: float = Field(ge=0, le=1)
    evidence: tuple[ProposedEvidence, ...] = Field(min_length=1)
    supersedes_observation_id: UUID | None = None


class LegalExtractionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observations: list[ProposedLegalObservation]


@dataclass(frozen=True)
class LegalExtractionInput:
    case_id: UUID
    argument_id: UUID | None
    blocks: tuple[LegalEvidenceBlock, ...]
    parser_versions: tuple[str, ...]
    document_revision_ids: tuple[UUID, ...]


class LegalObservationExtractor(Protocol):
    model_name: str

    def extract(self, source: LegalExtractionInput) -> LegalExtractionBatch: ...


class LegalObservationStore(Protocol):
    def save(
        self,
        source: LegalExtractionInput,
        observations: list[LegalObservation],
        *,
        model: str,
        schema_version: str,
        prompt_version: str,
        vocabulary_version: str,
    ) -> list[LegalObservation]: ...


def transcript_turn_block(
    turn: TranscriptTurn, official_url: str
) -> LegalEvidenceBlock:
    return LegalEvidenceBlock(
        block_id=f"turn:{turn.turn_id}",
        document_revision_id=turn.document_revision_id,
        document_kind=ScotusDocumentKind.TRANSCRIPT,
        official_url=official_url,
        start_file_page=turn.start_file_page,
        start_line=turn.start_line,
        end_file_page=turn.end_file_page,
        end_line=turn.end_line,
        text_private=turn.text_private,
        speaker_name=turn.speaker_name,
        speaker_kind=turn.speaker_kind,
        identity_basis=turn.identity_basis,
        attribution=turn.speaker_name or turn.speaker_label_private,
    )


def document_text_block(
    *,
    document_revision_id: UUID,
    kind: ScotusDocumentKind,
    official_url: str,
    file_page: int,
    start_line: int,
    end_line: int,
    text: str,
    label: str,
) -> LegalEvidenceBlock:
    return LegalEvidenceBlock(
        block_id=f"{kind.value}:{document_revision_id}:{file_page}:{start_line}:{label}",
        document_revision_id=document_revision_id,
        document_kind=kind,
        official_url=official_url,
        start_file_page=file_page,
        start_line=start_line,
        end_file_page=file_page,
        end_line=end_line,
        text_private=text,
        attribution=label,
    )


def bounded_contexts(
    blocks: tuple[LegalEvidenceBlock, ...], maximum_characters: int
) -> tuple[tuple[LegalEvidenceBlock, ...], ...]:
    if maximum_characters < 1:
        raise ValueError("maximum context must be positive")
    batches: list[tuple[LegalEvidenceBlock, ...]] = []
    current: list[LegalEvidenceBlock] = []
    size = 0
    for block in blocks:
        block_size = len(block.text_private)
        if block_size > maximum_characters:
            raise LegalExtractionError("single evidence block exceeds model context bound")
        if current and size + block_size > maximum_characters:
            batches.append(tuple(current))
            current = [block]
            size = block_size
        else:
            current.append(block)
            size += block_size
    if current:
        batches.append(tuple(current))
    return tuple(batches)


class DeterministicTranscriptObservationExtractor:
    """Build a conservative private-preview ledger without model inference."""

    PROMPT_VERSION = "deterministic-transcript-observations-v1"
    model_name = "deterministic-private-transcript-v1"

    @staticmethod
    def _proposal(
        block: LegalEvidenceBlock,
        observation_type: LegalObservationType,
        legal_status: LegalStatus,
    ) -> ProposedLegalObservation:
        quote = block.text_private[:4_000].strip()
        return ProposedLegalObservation(
            observation_type=observation_type,
            legal_status=legal_status,
            certainty=LegalCertainty.ATTRIBUTED,
            raw_value=quote,
            normalized_value=quote,
            attribution=block.attribution,
            speaker_name=block.speaker_name,
            speaker_kind=block.speaker_kind,
            identity_basis=block.identity_basis,
            confidence=0.9,
            evidence=(ProposedEvidence(block_id=block.block_id, quote=quote),),
        )

    def extract(self, source: LegalExtractionInput) -> LegalExtractionBatch:
        observations: list[ProposedLegalObservation] = []
        opening = next(
            (
                block
                for block in source.blocks
                if block.speaker_kind in {SpeakerKind.JUSTICE, SpeakerKind.COURT_OFFICIAL}
                and re.search(r"\b(?:hear argument|case|matter)\b", block.text_private, re.I)
            ),
            source.blocks[0] if source.blocks else None,
        )
        if opening is not None:
            observations.append(
                self._proposal(
                    opening,
                    LegalObservationType.PROCEDURAL_POSTURE,
                    LegalStatus.DESCRIBED,
                )
            )

        seen_advocates: set[str] = set()
        for block in source.blocks:
            if block.speaker_kind is not SpeakerKind.ADVOCATE:
                continue
            identity = block.speaker_name or block.attribution or block.block_id
            if identity in seen_advocates:
                continue
            seen_advocates.add(identity)
            observations.append(
                self._proposal(
                    block,
                    LegalObservationType.ADVOCATE_CONTENTION,
                    LegalStatus.ASSERTED,
                )
            )
            if len(seen_advocates) >= 6:
                break

        questions = 0
        for block in source.blocks:
            if block.speaker_kind is not SpeakerKind.JUSTICE:
                continue
            if "?" not in block.text_private and not re.search(
                r"\b(?:what|why|how|whether|would|could|does|do|is|are|can)\b",
                block.text_private,
                re.I,
            ):
                continue
            observations.append(
                self._proposal(
                    block,
                    LegalObservationType.JUSTICE_QUESTION,
                    LegalStatus.QUESTIONED,
                )
            )
            questions += 1
            if questions >= 12:
                break
        return LegalExtractionBatch(observations=observations)


class OpenAILegalObservationExtractor:
    PROMPT_VERSION = "scotus-legal-extraction-v3"

    def __init__(
        self,
        model_name: str,
        client: OpenAI,
        *,
        maximum_output_tokens: int | None = None,
        request_executor: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.client = client
        self.maximum_output_tokens = maximum_output_tokens
        self.request_executor = request_executor

    def request_arguments(self, source: LegalExtractionInput) -> dict[str, Any]:
        """Build the exact provider request so budget checks can precede transport."""
        evidence = [
            {
                "block_id": block.block_id,
                "kind": block.document_kind.value,
                "page_lines": (
                    f"{block.start_file_page}:{block.start_line}-"
                    f"{block.end_file_page}:{block.end_line}"
                ),
                "speaker": block.speaker_name,
                "attribution": block.attribution,
                "text": block.text_private,
            }
            for block in source.blocks
        ]
        token_limit = {
            (
                "max_completion_tokens"
                if self.model_name.startswith("gpt-5")
                else "max_tokens"
            ): self.maximum_output_tokens or omit
        }
        return {
            "model": self.model_name,
            "temperature": omit if self.model_name.startswith("gpt-5") else 0,
            **token_limit,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Extract only legal observations explicitly supported by the supplied "
                        "official Supreme Court evidence. Attribute advocate claims and disputed "
                        "facts. A justice's question is not a vote or holding. Transcript "
                        "evidence cannot establish a Supreme Court order, holding, judgment, "
                        "or disposition. "
                        "Every quote and citation must occur exactly in its evidence block. "
                        "Return no more than four high-value observations. Keep raw_value and "
                        "normalized_value to one sentence of at most 40 words each. Use one "
                        "evidence block per observation and quote no more than 30 words."
                    ),
                },
                {"role": "user", "content": json.dumps(evidence, separators=(",", ":"))},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "scotus_legal_observations",
                    "strict": True,
                    "schema": to_strict_json_schema(LegalExtractionBatch),
                },
            },
        }

    @staticmethod
    def parse_completion(completion: Any) -> LegalExtractionBatch:
        content = completion.choices[0].message.content
        if not content:
            raise LegalExtractionError("legal extraction model returned no structured content")
        return LegalExtractionBatch.model_validate_json(content)

    def extract(self, source: LegalExtractionInput) -> LegalExtractionBatch:
        request = self.request_arguments(source)
        completion = (
            self.request_executor(request)
            if self.request_executor is not None
            else self.client.chat.completions.create(**request)
        )
        return self.parse_completion(completion)


_DOCKET = re.compile(r"\b\d{1,3}A?-\d+[A-Z]*\b", re.IGNORECASE)
_CASE_CITATION = re.compile(
    r"\b[A-Z][A-Za-z.' -]+ v\. [A-Z][A-Za-z.' -]+,?\s+\d+\s+U\.\s*S\.\s+\d+\b"
)
_STATUTE = re.compile(r"\b\d+\s+U\.\s*S\.\s*C\.\s*§?\s*\d+[A-Za-z0-9().-]*\b", re.I)
_VOTE_PREDICTION = re.compile(
    "\\b(?:will|likely to|expected to)\\s+(?:vote|rule|hold)|"
    "\\b\\d\\s*[-\\N{EN DASH}]\\s*\\d\\b",
    re.IGNORECASE,
)
_SENSITIVITY: tuple[tuple[ScotusSensitivity, re.Pattern[str]], ...] = (
    (ScotusSensitivity.MINOR, re.compile(r"\b(?:minor|child|juvenile)\b", re.I)),
    (ScotusSensitivity.VICTIM, re.compile(r"\b(?:victim|survivor)\b", re.I)),
    (ScotusSensitivity.MEDICAL, re.compile(r"\b(?:medical|diagnos|treatment|patient)\w*\b", re.I)),
    (
        ScotusSensitivity.SEALED_OR_REDACTED,
        re.compile(r"\b(?:sealed|redacted|under seal)\b", re.I),
    ),
    (
        ScotusSensitivity.HOME_ADDRESS,
        re.compile(r"\b\d{1,5}\s+[A-Z][A-Za-z ]+\s(?:Street|Road|Avenue|Drive)\b"),
    ),
    (
        ScotusSensitivity.PRIVATE_NAME,
        re.compile(
            r"\b(?:minor|victim|patient)\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b"
        ),
    ),
)


def normalize_docket_reference(value: str) -> str:
    return "".join(value.upper().split()).replace("\N{EN DASH}", "-")


def normalize_legal_citation(value: str) -> str:
    normalized = " ".join(value.split())
    normalized = re.sub(r"U\.\s*S\.", "U.S.", normalized)
    normalized = re.sub(r"U\.\s*S\.\s*C\.", "U.S.C.", normalized)
    return normalized


def normalize_docket_list(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(normalize_docket_reference(value) for value in values))


def normalize_advocate_role(value: str) -> AdvocateRole:
    lowered = " ".join(value.lower().split())
    if "petitioner" in lowered or "appellant" in lowered:
        return AdvocateRole.PETITIONER
    if "respondent" in lowered or "appellee" in lowered:
        return AdvocateRole.RESPONDENT
    if "united states" in lowered or "solicitor general" in lowered:
        return AdvocateRole.UNITED_STATES
    if "amicus" in lowered or "friend of the court" in lowered:
        return AdvocateRole.AMICUS
    return AdvocateRole.UNKNOWN


def normalize_court(value: str) -> str:
    compact = " ".join(value.split())
    replacements = {
        "SCOTUS": "Supreme Court of the United States",
        "U.S. Supreme Court": "Supreme Court of the United States",
        "D.C. Circuit": "U.S. Court of Appeals for the D.C. Circuit",
    }
    return replacements.get(compact, compact)


def normalize_disposition(value: str) -> str:
    lowered = " ".join(value.lower().split())
    mapping = {
        "affirm": "affirmed",
        "affirmed": "affirmed",
        "reverse": "reversed",
        "reversed": "reversed",
        "vacate": "vacated",
        "vacated": "vacated",
        "remand": "remanded",
        "remanded": "remanded",
        "dismiss": "dismissed",
        "dismissed": "dismissed",
    }
    return mapping.get(lowered, lowered)


def normalize_constitutional_reference(value: str) -> str:
    normalized = " ".join(value.split())
    normalized = re.sub(r"\bConst\.", "Constitution", normalized, flags=re.I)
    normalized = re.sub(r"\bAmend\.", "Amendment", normalized, flags=re.I)
    return normalized


def find_supported_legal_references(text: str) -> tuple[str, ...]:
    values = [*(_CASE_CITATION.findall(text)), *(_STATUTE.findall(text))]
    return tuple(dict.fromkeys(normalize_legal_citation(value) for value in values))


def sensitivity_labels(text: str) -> tuple[ScotusSensitivity, ...]:
    return tuple(label for label, pattern in _SENSITIVITY if pattern.search(text))


def _validate_status(item: ProposedLegalObservation, kinds: set[ScotusDocumentKind]) -> None:
    if (
        item.observation_type is LegalObservationType.JUSTICE_QUESTION
        and item.legal_status is not LegalStatus.QUESTIONED
    ):
        raise LegalExtractionError("justice question must retain questioned status")
    if (
        item.observation_type is LegalObservationType.REQUESTED_DISPOSITION
        and item.legal_status is not LegalStatus.REQUESTED
    ):
        raise LegalExtractionError("requested disposition must retain requested status")
    if item.observation_type is LegalObservationType.HOLDING:
        if item.legal_status is not LegalStatus.COURT_HELD:
            raise LegalExtractionError("holding must use Court-held status")
        if ScotusDocumentKind.OPINION not in kinds:
            raise LegalExtractionError("holding requires official opinion evidence")
    if item.observation_type is LegalObservationType.ORDER:
        if item.legal_status is not LegalStatus.COURT_ORDERED:
            raise LegalExtractionError("order must use Court-ordered status")
        if not kinds.intersection({ScotusDocumentKind.ORDER, ScotusDocumentKind.OPINION}):
            raise LegalExtractionError("Court order requires order/opinion evidence")
    final_statuses = {LegalStatus.COURT_HELD, LegalStatus.COURT_ORDERED}
    if item.legal_status in final_statuses and kinds == {ScotusDocumentKind.TRANSCRIPT}:
        raise LegalExtractionError("transcript evidence cannot establish final Court action")


def validate_proposed(
    item: ProposedLegalObservation, blocks: dict[str, LegalEvidenceBlock]
) -> tuple[LegalEvidenceRange, ...]:
    evidence: list[LegalEvidenceRange] = []
    kinds: set[ScotusDocumentKind] = set()
    combined_text: list[str] = []
    for pointer in item.evidence:
        block = blocks.get(pointer.block_id)
        if block is None:
            raise LegalExtractionError("observation references an unknown evidence block")
        if pointer.quote not in block.text_private:
            raise LegalExtractionError("evidence quote does not exactly match source block")
        kinds.add(block.document_kind)
        combined_text.append(block.text_private)
        evidence.append(
            LegalEvidenceRange(
                document_revision_id=block.document_revision_id,
                document_kind=block.document_kind,
                start_file_page=block.start_file_page,
                start_line=block.start_line,
                end_file_page=block.end_file_page,
                end_line=block.end_line,
                quote_private=pointer.quote,
            )
        )
    _validate_status(item, kinds)
    text = " ".join(combined_text)
    normalized_text = normalize_legal_citation(text).lower()
    for citation in item.authority_citations:
        if normalize_legal_citation(citation).lower() not in normalized_text:
            raise LegalExtractionError("authority citation is absent from evidence")
    if item.speaker_name:
        referenced_ids = {pointer.block_id for pointer in item.evidence}
        matching = [
            block for block in blocks.values() if block.block_id in referenced_ids
        ]
        if not any(block.speaker_name == item.speaker_name for block in matching):
            raise LegalExtractionError("speaker name is not supported by evidence identity")
        if item.identity_basis is SpeakerIdentityBasis.ANONYMOUS:
            raise LegalExtractionError("named speaker requires official identity basis")
    attributed = {
        LegalObservationType.ADVOCATE_CONTENTION,
        LegalObservationType.ANSWER,
        LegalObservationType.CONCESSION,
        LegalObservationType.DISPUTED_PREMISE,
        LegalObservationType.REQUESTED_DISPOSITION,
    }
    if item.observation_type in attributed and not item.attribution:
        raise LegalExtractionError("advocate/disputed observation requires attribution")
    if _VOTE_PREDICTION.search(item.raw_value):
        raise LegalExtractionError("vote or outcome prediction is prohibited")
    return tuple(evidence)


class InMemoryLegalObservationStore:
    def __init__(self) -> None:
        self.revisions: dict[tuple[object, ...], list[LegalObservation]] = {}

    def save(
        self,
        source: LegalExtractionInput,
        observations: list[LegalObservation],
        *,
        model: str,
        schema_version: str,
        prompt_version: str,
        vocabulary_version: str,
    ) -> list[LegalObservation]:
        key = (
            source.case_id,
            source.argument_id,
            model,
            schema_version,
            prompt_version,
            vocabulary_version,
            source.parser_versions,
            source.document_revision_ids,
        )
        return self.revisions.setdefault(key, observations)


class PostgresLegalObservationStore:
    def __init__(
        self,
        dsn: str,
        pool: ConnectionPool[Connection[dict[str, Any]]] | None = None,
    ) -> None:
        self.pool = pool or ConnectionPool(
            conninfo=dsn,
            kwargs={"row_factory": dict_row},
            min_size=1,
            max_size=5,
            open=True,
        )

    def save(
        self,
        source: LegalExtractionInput,
        observations: list[LegalObservation],
        *,
        model: str,
        schema_version: str,
        prompt_version: str,
        vocabulary_version: str,
    ) -> list[LegalObservation]:
        parser_versions = json.dumps(source.parser_versions)
        document_ids = json.dumps([str(value) for value in source.document_revision_ids])
        with self.pool.connection() as connection, connection.transaction():
            row = connection.execute(
                """INSERT INTO scotus_extraction_revisions
                   (extraction_revision_id,case_id,argument_id,model,schema_version,
                    prompt_version,vocabulary_version,parser_versions,document_revision_ids)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                   ON CONFLICT(case_id,argument_id,model,schema_version,prompt_version,
                     vocabulary_version,parser_versions,document_revision_ids) DO NOTHING
                   RETURNING extraction_revision_id""",
                (
                    observations[0].extraction_revision_id if observations else uuid4(),
                    source.case_id,
                    source.argument_id,
                    model,
                    schema_version,
                    prompt_version,
                    vocabulary_version,
                    parser_versions,
                    document_ids,
                ),
            ).fetchone()
            if row:
                extraction_id = row["extraction_revision_id"]
                for item in observations:
                    connection.execute(
                        """INSERT INTO scotus_legal_observations
                           (observation_id,extraction_revision_id,case_id,argument_id,
                            observation_type,legal_status,certainty,raw_value_private,
                            normalized_value_private,attribution_private,speaker_name,
                            speaker_kind,identity_basis,authority_citations_private,confidence,
                            evidence_private,sensitivity,supersedes_observation_id)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                                   %s::jsonb,%s::jsonb,%s)""",
                        (
                            item.observation_id,
                            extraction_id,
                            item.case_id,
                            item.argument_id,
                            item.observation_type.value,
                            item.legal_status.value,
                            item.certainty.value,
                            item.raw_value_private,
                            item.normalized_value_private,
                            item.attribution,
                            item.speaker_name,
                            item.speaker_kind.value,
                            item.identity_basis.value,
                            json.dumps(item.authority_citations),
                            item.confidence,
                            json.dumps([e.model_dump(mode="json") for e in item.evidence]),
                            json.dumps([label.value for label in item.sensitivity]),
                            item.supersedes_observation_id,
                        ),
                    )
                connection.execute(
                    """INSERT INTO jobs(stage,input_kind,input_id,input_version,priority)
                       VALUES ('correlate','scotus_extraction',%s,%s,10)
                       ON CONFLICT(stage,input_kind,input_id,input_version) DO NOTHING""",
                    (str(extraction_id), f"{schema_version}:{prompt_version}:{vocabulary_version}"),
                )
            else:
                existing = connection.execute(
                    """SELECT extraction_revision_id FROM scotus_extraction_revisions
                       WHERE case_id=%s AND argument_id IS NOT DISTINCT FROM %s AND model=%s
                         AND schema_version=%s AND prompt_version=%s AND vocabulary_version=%s
                         AND parser_versions=%s::jsonb AND document_revision_ids=%s::jsonb""",
                    (
                        source.case_id,
                        source.argument_id,
                        model,
                        schema_version,
                        prompt_version,
                        vocabulary_version,
                        parser_versions,
                        document_ids,
                    ),
                ).fetchone()
                if existing is None:
                    raise RuntimeError("SCOTUS extraction revision disappeared")
                extraction_id = existing["extraction_revision_id"]
            rows = connection.execute(
                """SELECT * FROM scotus_legal_observations
                   WHERE extraction_revision_id=%s ORDER BY created_at,observation_id""",
                (extraction_id,),
            ).fetchall()
        return [self._observation(row) for row in rows]

    @staticmethod
    def _observation(row: dict[str, Any]) -> LegalObservation:
        evidence = row["evidence_private"]
        sensitivity = row["sensitivity"]
        citations = row["authority_citations_private"]
        return LegalObservation(
            observation_id=row["observation_id"],
            extraction_revision_id=row["extraction_revision_id"],
            case_id=row["case_id"],
            argument_id=row["argument_id"],
            observation_type=LegalObservationType(row["observation_type"]),
            legal_status=LegalStatus(row["legal_status"]),
            certainty=LegalCertainty(row["certainty"]),
            raw_value_private=row["raw_value_private"],
            normalized_value_private=row["normalized_value_private"],
            attribution=row["attribution_private"],
            speaker_name=row["speaker_name"],
            speaker_kind=SpeakerKind(row["speaker_kind"]),
            identity_basis=SpeakerIdentityBasis(row["identity_basis"]),
            authority_citations=tuple(citations),
            confidence=row["confidence"],
            evidence=tuple(LegalEvidenceRange.model_validate(item) for item in evidence),
            sensitivity=tuple(ScotusSensitivity(value) for value in sensitivity),
            supersedes_observation_id=row["supersedes_observation_id"],
        )


class LegalExtractionService:
    SCHEMA_VERSION = "scotus-observation-v1"
    VOCABULARY_VERSION = "scotus-legal-v1"

    def __init__(
        self, extractor: LegalObservationExtractor, store: LegalObservationStore
    ) -> None:
        self.extractor = extractor
        self.store = store

    def process(self, source: LegalExtractionInput) -> list[LegalObservation]:
        batch = self.extractor.extract(source)
        blocks = {block.block_id: block for block in source.blocks}
        prompt_version = getattr(
            self.extractor, "PROMPT_VERSION", "deterministic-scotus-extraction-v1"
        )
        identity = json.dumps(
            {
                "argument_id": str(source.argument_id) if source.argument_id else None,
                "blocks": [block.block_id for block in source.blocks],
                "case_id": str(source.case_id),
                "documents": [str(value) for value in source.document_revision_ids],
                "model": self.extractor.model_name,
                "parser_versions": source.parser_versions,
                "prompt": prompt_version,
                "schema": self.SCHEMA_VERSION,
                "vocabulary": self.VOCABULARY_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        extraction_id = uuid5(NAMESPACE_URL, f"ragchew:scotus-extraction:{identity}")
        observations: list[LegalObservation] = []
        for index, proposed in enumerate(batch.observations):
            try:
                evidence = validate_proposed(proposed, blocks)
            except LegalExtractionError:
                # Reject only the unsupported observation; other independently grounded
                # observations in the structured batch remain usable.
                continue
            combined = " ".join(item.quote_private for item in evidence)
            normalized = proposed.normalized_value
            if proposed.observation_type is LegalObservationType.AUTHORITY_CITATION:
                normalized = normalize_legal_citation(normalized or proposed.raw_value)
            observations.append(
                LegalObservation(
                    observation_id=uuid5(
                        NAMESPACE_URL,
                        f"ragchew:scotus-observation:{extraction_id}:{index}:"
                        f"{proposed.model_dump_json()}",
                    ),
                    extraction_revision_id=extraction_id,
                    case_id=source.case_id,
                    argument_id=source.argument_id,
                    observation_type=proposed.observation_type,
                    legal_status=proposed.legal_status,
                    certainty=proposed.certainty,
                    raw_value_private=proposed.raw_value,
                    normalized_value_private=normalized,
                    attribution=proposed.attribution,
                    speaker_name=proposed.speaker_name,
                    speaker_kind=proposed.speaker_kind,
                    identity_basis=proposed.identity_basis,
                    authority_citations=tuple(
                        normalize_legal_citation(value)
                        for value in proposed.authority_citations
                    ),
                    confidence=proposed.confidence,
                    evidence=evidence,
                    sensitivity=sensitivity_labels(combined),
                    supersedes_observation_id=proposed.supersedes_observation_id,
                )
            )
        return self.store.save(
            source,
            observations,
            model=self.extractor.model_name,
            schema_version=self.SCHEMA_VERSION,
            prompt_version=prompt_version,
            vocabulary_version=self.VOCABULARY_VERSION,
        )
