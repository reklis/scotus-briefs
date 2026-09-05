"""Default-deny claim policy and grounded SCOTUS legal brief generation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from openai import OpenAI, omit
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ragchew.scotus.contracts import (
    BriefArgumentAnalysis,
    BriefMaturity,
    BriefSection,
    LegalBriefRevision,
    LegalObservation,
    LegalObservationType,
    LegalStatus,
    ScotusApprovedClaim,
    ScotusCaseStatus,
    ScotusDocumentKind,
    ScotusSensitivity,
)


class BriefPolicyError(ValueError):
    pass


class BriefValidationError(ValueError):
    def __init__(self, message: str, *, safe_code: str | None = None) -> None:
        super().__init__(message)
        self.safe_code = safe_code


@dataclass(frozen=True)
class CaseArgumentSession:
    argument_id: UUID
    argument_date: datetime
    sequence: int
    reargument: bool
    official_detail_url: str
    official_transcript_url: str


@dataclass(frozen=True)
class BriefCandidate:
    case_id: UUID
    argument_id: UUID | None
    caption: str
    primary_docket: str
    case_status: ScotusCaseStatus
    official_transcript_complete: bool
    parser_complete: bool
    privacy_blocking_failure: bool
    argument_sessions: tuple[CaseArgumentSession, ...]
    observations: tuple[LegalObservation, ...]
    document_urls: dict[UUID, str]
    evaluated_at: datetime


@dataclass(frozen=True)
class BriefPolicyDecision:
    eligible: bool
    reasons: tuple[str, ...]
    claims: tuple[ScotusApprovedClaim, ...]
    maturity: BriefMaturity | None


class DraftSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(min_length=1, max_length=120)
    paragraphs: tuple[str, ...] = Field(min_length=1)
    claim_ids: tuple[UUID, ...] = Field(min_length=1)


class DraftArgumentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    argument_id: UUID
    heading: str = Field(min_length=1, max_length=120)
    paragraphs: tuple[str, ...] = Field(min_length=2)
    claim_ids: tuple[UUID, ...] = Field(min_length=1)


class LegalBriefDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=180)
    title_claim_ids: tuple[UUID, ...] = Field(min_length=1)
    dek: str = Field(min_length=1, max_length=500)
    dek_claim_ids: tuple[UUID, ...] = Field(min_length=1)
    sections: tuple[DraftSection, ...] = Field(min_length=1)
    # The disposition-only schema fixes this collection at zero. Argument cases are
    # still checked against every real session by ``validate_brief_draft``.
    argument_analyses: tuple[DraftArgumentAnalysis, ...] = ()


def _normalize_private_schema_payload(
    payload: object,
    candidate: BriefCandidate,
    claims: tuple[ScotusApprovedClaim, ...],
) -> object:
    """Normalize trusted structure without manufacturing claim coverage."""
    if not isinstance(payload, dict):
        return payload
    allowed = {str(claim.claim_id) for claim in claims}

    def supported(values: object) -> object:
        if not isinstance(values, list):
            return values
        return list(
            dict.fromkeys(value for value in values if isinstance(value, str) and value in allowed)
        )

    payload["title_claim_ids"] = supported(payload.get("title_claim_ids"))
    payload["dek_claim_ids"] = supported(payload.get("dek_claim_ids"))
    sections = payload.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict):
                section["claim_ids"] = supported(section.get("claim_ids"))
    analyses = payload.get("argument_analyses")
    if isinstance(analyses, list):
        for index, analysis in enumerate(analyses):
            if not isinstance(analysis, dict):
                continue
            analysis["claim_ids"] = supported(analysis.get("claim_ids"))
            if index < len(candidate.argument_sessions):
                analysis["argument_id"] = str(candidate.argument_sessions[index].argument_id)
    return payload


def simple_brief_json_schema(argument_count: int = 1) -> dict[str, Any]:
    if not 0 <= argument_count <= 10:
        raise ValueError("brief schema argument count must be between zero and ten")
    claim_ids = {
        "type": "array",
        "items": {"type": "string", "maxLength": 36},
        "minItems": 1,
        "maxItems": 32,
    }
    section = {
        "type": "object",
        "properties": {
            "heading": {"type": "string", "maxLength": 120},
            "paragraphs": {
                "type": "array",
                "items": {"type": "string", "maxLength": 800},
                "minItems": 1,
                "maxItems": 1,
            },
            "claim_ids": claim_ids,
        },
        "required": ["heading", "paragraphs", "claim_ids"],
        "additionalProperties": False,
    }
    argument = {
        "type": "object",
        "properties": {
            "argument_id": {"type": "string", "maxLength": 36},
            "heading": {"type": "string", "maxLength": 120},
            "paragraphs": {
                "type": "array",
                "items": {"type": "string", "maxLength": 800},
                "minItems": 2,
                "maxItems": 2,
            },
            "claim_ids": claim_ids,
        },
        "required": ["argument_id", "heading", "paragraphs", "claim_ids"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string", "maxLength": 180},
            "title_claim_ids": claim_ids,
            "dek": {"type": "string", "maxLength": 500},
            "dek_claim_ids": claim_ids,
            "sections": {
                "type": "array",
                "items": section,
                "minItems": 5,
                "maxItems": 5,
            },
            "argument_analyses": {
                "type": "array",
                "items": argument,
                "minItems": argument_count,
                "maxItems": argument_count,
            },
        },
        "required": [
            "title",
            "title_claim_ids",
            "dek",
            "dek_claim_ids",
            "sections",
            "argument_analyses",
        ],
        "additionalProperties": False,
    }


def disposition_only_brief_json_schema() -> dict[str, Any]:
    """Return the strict zero-analysis schema for a case with no real argument."""
    schema = simple_brief_json_schema(0)
    sections = schema["properties"]["sections"]
    sections["minItems"] = 2
    sections["maxItems"] = 5
    return schema


class LegalBriefGenerator(Protocol):
    model_name: str

    def generate(
        self,
        candidate: BriefCandidate,
        claims: tuple[ScotusApprovedClaim, ...],
        maturity: BriefMaturity,
    ) -> LegalBriefDraft: ...


class BriefRevisionStore(Protocol):
    def save(
        self,
        claims: tuple[ScotusApprovedClaim, ...],
        revision: LegalBriefRevision,
    ) -> LegalBriefRevision: ...


class OpenAILegalBriefGenerator:
    PROMPT_VERSION = "scotus-brief-plain-language-v27"

    def __init__(
        self,
        model_name: str,
        client: OpenAI,
        *,
        maximum_sentence_words: int = 30,
        maximum_paragraph_words: int = 120,
        strict_json_schema: bool = True,
        response_schema: dict[str, Any] | None = None,
        maximum_output_tokens: int | None = None,
        reasoning_effort: Literal["none", "low", "medium", "high"] | None = None,
        validation_feedback_code: str | None = None,
        request_executor: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.client = client
        self.maximum_sentence_words = maximum_sentence_words
        self.maximum_paragraph_words = maximum_paragraph_words
        self.strict_json_schema = strict_json_schema
        self.response_schema = response_schema
        self.maximum_output_tokens = maximum_output_tokens
        self.reasoning_effort = reasoning_effort
        if validation_feedback_code is not None and not re.fullmatch(
            r"[a-z0-9_:-]{1,200}", validation_feedback_code
        ):
            raise ValueError("validation feedback must be a fixed safe code")
        self.validation_feedback_code = validation_feedback_code
        self.request_executor = request_executor

    def generate(
        self,
        candidate: BriefCandidate,
        claims: tuple[ScotusApprovedClaim, ...],
        maturity: BriefMaturity,
    ) -> LegalBriefDraft:
        sessions = {session.argument_id: session for session in candidate.argument_sessions}
        ledger = [
            {
                "claim_id": str(claim.claim_id),
                "argument_session": (
                    {
                        "argument_id": str(claim.argument_id),
                        "date": sessions[claim.argument_id].argument_date.date().isoformat(),
                        "sequence": sessions[claim.argument_id].sequence,
                        "reargument": sessions[claim.argument_id].reargument,
                    }
                    if claim.argument_id in sessions
                    else None
                ),
                "type": claim.observation_type.value,
                "status": claim.legal_status.value,
                "certainty": claim.certainty.value,
                "attribution": claim.attribution,
                "position_group": _position_label(claim.attribution),
                "value": claim.public_value,
                "source": claim.public_source_label,
                "page": claim.page_label,
            }
            for claim in claims
        ]
        disposition_only = not candidate.argument_sessions
        response_format: Any = (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "scotus_legal_brief",
                    "strict": True,
                    "schema": self.response_schema
                    or (
                        disposition_only_brief_json_schema()
                        if disposition_only
                        else simple_brief_json_schema(len(candidate.argument_sessions))
                    ),
                },
            }
            if self.strict_json_schema
            else {"type": "json_object"}
        )
        format_instruction = (
            " Return one raw JSON object with no Markdown or code fences. The object must have "
            "exactly these six keys: title, title_claim_ids, dek, dek_claim_ids, sections, and "
            "argument_analyses. Every section must have exactly heading, paragraphs, and "
            "claim_ids. Every argument analysis must have exactly argument_id, heading, "
            "paragraphs, and claim_ids. Use five to seven sections with one short paragraph "
            "each. Use exactly two short paragraphs per argument analysis. Never emit empty "
            "text or empty claim_ids arrays."
            if not self.strict_json_schema
            else ""
        )
        case_mode_instruction = (
            "This is a disposition-only case with no oral-argument session. Return two to five "
            "supported sections and emit exactly zero "
            "argument analyses. Never claim or imply that oral argument occurred, that a "
            "transcript exists, that counsel made an argument, or that a justice asked or "
            "tested a question. Use party names and describe a result only when the cited "
            "approved claims explicitly support them. Omit unavailable detail instead of "
            "adding filler about a missing record or future details. Focus on supported case "
            "background and the Court's action. "
            if disposition_only
            else (
                "Produce one argument analysis for every supplied argument session, in order. "
                "Explain what each side was asking the Court to do, the reasoning each side "
                "offered, what assumptions the justices tested, and what a later reargument "
                "changed or revisited when supported. "
            )
        )
        section_instruction = (
            "Return two to five supported sections with one short paragraph each. "
            if disposition_only
            else (
                "Return five sections with one paragraph each and exactly two short "
                "paragraphs for each supplied argument session. "
            )
        )
        token_limit = {
            (
                "max_completion_tokens" if self.model_name.startswith("gpt-5") else "max_tokens"
            ): self.maximum_output_tokens or omit
        }
        request: dict[str, Any] = dict(
            model=self.model_name,
            temperature=omit if self.model_name.startswith("gpt-5") else 0,
            **token_limit,
            reasoning_effort=self.reasoning_effort or omit,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "/no_think\nExplain this Supreme Court case to a curious reader with no "
                        "legal training. Use only the approved claim ledger and cite claim IDs for "
                        "every title, summary, and paragraph. Begin the title with the supplied "
                        "official case caption; never use a generic heading as the title. Put "
                        "citations only in the matching "
                        "claim_ids arrays, never in public prose. Use direct everyday language, "
                        "active voice, concrete explanations, and short paragraphs. "
                        + section_instruction
                        + f"Keep every sentence at or below {self.maximum_sentence_words} words "
                        "and every "
                        f"paragraph at or below {self.maximum_paragraph_words} words. Do not "
                        "write like a court filing or law-school outline. Avoid labels such as "
                        "petitioner and respondent when a party name or plain description works. "
                        "If a legal concept is unavoidable, explain immediately what it means "
                        "for this case. Prefer headings such as 'What this case is about', 'How "
                        "the case got here', 'What the Court did', 'Why it matters', and 'What "
                        "happens next'. " + case_mode_instruction + "When the ledger has a "
                        "question presented, procedural posture, advocate contention, or justice "
                        "question, the output must use at least one claim of each available type. "
                        "Copy every claim ID exactly from the supplied ledger. Cite only claims "
                        "whose public values support the associated text. In each argument "
                        "analysis, use only claims carrying that analysis's argument_id. Each "
                        "argument analysis must cover every available position_group and the "
                        "questions tested in that session. Different attribution wording can "
                        "identify the "
                        "same position_group; do not create extra sides from those wording "
                        "changes. A null position_group means the official evidence does not "
                        "establish that advocate's side; do not infer one. "
                        "Do not rank winners. Omit unsupported sections. Attribute each "
                        "side's claims and disputed facts. Never fill in a missing side, argument, "
                        "or fact with what it likely said; omit unsupported detail. Do not add a "
                        "person's name, address, medical detail, "
                        "identifier, docket, or citation unless it appears in the supporting "
                        "claim's public value. Paraphrase the evidence and do not use quotation "
                        "marks or direct quotations anywhere in the output. A question is not a "
                        "holding or vote. Describe a requested result as what a side asks the "
                        "Court to do, never as something the Court already did. Identify a "
                        "lower-court result explicitly as the lower court's action. Never infer "
                        "that no ruling exists merely because no disposition is supplied. When "
                        "final-action claims are absent, say only that the article currently "
                        "covers the argument record and link readers to the official docket for "
                        "later activity. Never predict the outcome, score "
                        "ideology or tone, or give personalized legal advice."
                        + (
                            " Prior independent drafts failed these colon-separated fixed "
                            "validator codes: '"
                            + self.validation_feedback_code
                            + "'. Produce a fresh draft that satisfies every listed rule; "
                            "do not mention the validation attempt in public prose."
                            if self.validation_feedback_code
                            else ""
                        )
                        + format_instruction
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "mode": "/no_think",
                            "caption": candidate.caption,
                            "docket": candidate.primary_docket,
                            "maturity": maturity.value,
                            "argument_sessions": [
                                {
                                    "argument_id": str(session.argument_id),
                                    "date": session.argument_date.date().isoformat(),
                                    "sequence": session.sequence,
                                    "reargument": session.reargument,
                                }
                                for session in candidate.argument_sessions
                            ],
                            "claims": ledger,
                            **(
                                {"required_output_schema": (LegalBriefDraft.model_json_schema())}
                                if not self.strict_json_schema
                                else {}
                            ),
                        },
                        separators=(",", ":"),
                    ),
                },
            ],
            response_format=response_format,
        )
        completion = (
            self.request_executor(request)
            if self.request_executor is not None
            else self.client.chat.completions.create(**request)
        )
        choices = getattr(completion, "choices", ())
        if not choices:
            raise BriefValidationError("brief model returned no choice", safe_code="empty_choice")
        choice = choices[0]
        content = getattr(getattr(choice, "message", None), "content", None)
        if not content:
            raise BriefValidationError(
                "brief model returned no structured content", safe_code="empty_content"
            )
        stripped = content.strip()
        if not self.strict_json_schema and stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                stripped = "\n".join(lines[1:-1])
        try:
            if self.response_schema is not None:
                payload = _normalize_private_schema_payload(json.loads(stripped), candidate, claims)
                draft = _plain_language_draft(LegalBriefDraft.model_validate(payload))
            else:
                draft = _plain_language_draft(LegalBriefDraft.model_validate_json(stripped))
        except (json.JSONDecodeError, ValidationError):
            if getattr(choice, "finish_reason", None) == "length":
                raise BriefValidationError(
                    "brief model exhausted its output bound",
                    safe_code="output_truncated",
                ) from None
            raise BriefValidationError(
                "brief model returned invalid structured content",
                safe_code="invalid_schema",
            ) from None
        if len(candidate.argument_sessions) == 1 and draft.argument_analyses:
            session = candidate.argument_sessions[0]
            first = draft.argument_analyses[0]
            draft = draft.model_copy(
                update={
                    "argument_analyses": (
                        first.model_copy(
                            update={
                                "argument_id": session.argument_id,
                                "paragraphs": tuple(
                                    paragraph
                                    for analysis in draft.argument_analyses
                                    for paragraph in analysis.paragraphs
                                )[:6],
                                "claim_ids": tuple(
                                    dict.fromkeys(
                                        claim_id
                                        for analysis in draft.argument_analyses
                                        for claim_id in analysis.claim_ids
                                    )
                                ),
                            }
                        ),
                    )
                }
            )
        elif len(draft.argument_analyses) == len(candidate.argument_sessions):
            draft = draft.model_copy(
                update={
                    "argument_analyses": tuple(
                        analysis.model_copy(update={"argument_id": session.argument_id})
                        for analysis, session in zip(
                            draft.argument_analyses,
                            candidate.argument_sessions,
                            strict=True,
                        )
                    )
                }
            )
        if draft.title.strip().casefold() in {
            "what this case is about",
            "plain-language guide",
            "supreme court case explained",
        }:
            draft = draft.model_copy(update={"title": candidate.caption})
        return draft


_ADDRESS = re.compile(r"\b\d{1,5}\s+[A-Z][A-Za-z ]+\s(?:Street|Road|Avenue|Drive)\b")
_PRIVATE_NAME = re.compile(
    r"\b[A-Z][A-Za-z'\N{RIGHT SINGLE QUOTATION MARK}-]+"
    r"(?:\s+(?:[A-Z]\.|[A-Z][A-Za-z'\N{RIGHT SINGLE QUOTATION MARK}-]+)){1,3}\b"
)
_PREDICTION = re.compile(
    r"\b(?:likely to|expected to|appears poised to)\s+(?:vote|rule|hold|win|lose)|"
    r"\bwill\b[^.!?]{0,40}\b(?:vote|hold|win|lose)|"
    r"\bwill\s+rule\s+(?:for|against|in favor of|that)|"
    r"\b\d\s*[-\N{EN DASH}]\s*\d\b",
    re.IGNORECASE,
)
_QUESTION_AS_HOLDING = re.compile(
    r"\b(?:the justice|justice \w+)\s+(?:held|ruled|decided|voted)\b", re.IGNORECASE
)
_TONE_OR_IDEOLOGY = re.compile(
    r"\b(?:hostile|sympathetic|skeptical tone|liberal bloc|conservative bloc|swing vote)\b",
    re.IGNORECASE,
)
_LEGAL_ADVICE = re.compile(
    r"\b(?:you should|you must|your case|consult this strategy|file a|bring a claim)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_SPECULATION = re.compile(
    r"\b(?:likely|apparently|presumably|seemingly)\b", re.IGNORECASE
)
_QUOTATION = re.compile(r"[\"“”]|(?<!\w)'[^'\n]{2,}'(?!\w)")
_META_OUTPUT = re.compile(
    r"\bclaim_ids?\b|matching (?:array|field)|required_output_schema|"
    r"schema instructions?",
    re.IGNORECASE,
)
_INTERNAL_CLAIM_MARKER = re.compile(
    r"\s*\[[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\]",
    re.IGNORECASE,
)
_CITATION = re.compile(r"\b\d+\s+U\.S\.\s+\d+\b")
_DOCKET = re.compile(r"\b\d{1,3}A?-\d+[A-Z]*\b", re.IGNORECASE)
_WORD = re.compile(r"\b[\w\u2019'-]+\b")
_SENTENCE = re.compile(r"[^.!?]+[.!?]?", re.MULTILINE)
_LEGALESE = re.compile(
    r"\b(?:arguendo|inter alia|hereinafter|aforementioned|pursuant to|sub judice|"
    r"ab initio|the instant case|procedural posture|question presented|requested "
    r"disposition|petitioner|respondent)\b",
    re.IGNORECASE,
)
_PLAIN_LANGUAGE_REPLACEMENTS = (
    (re.compile(r"\bpetitioners\b", re.I), "the sides that brought the case"),
    (re.compile(r"\bpetitioner\b", re.I), "the side that brought the case"),
    (re.compile(r"\brespondents\b", re.I), "the opposing sides"),
    (re.compile(r"\brespondent\b", re.I), "the opposing side"),
    (re.compile(r"\bprocedural posture\b", re.I), "how the case got here"),
    (re.compile(r"\bquestion presented\b", re.I), "main legal question"),
    (re.compile(r"\brequested disposition\b", re.I), "result the side requested"),
    (re.compile(r"\bpursuant to\b", re.I), "under"),
    (re.compile(r"\baforementioned\b", re.I), "earlier"),
    (re.compile(r"\bthe instant case\b", re.I), "this case"),
    (re.compile(r"\bhereinafter\b", re.I), "later called"),
    (re.compile(r"\binter alia\b", re.I), "among other things"),
    (re.compile(r"\barguendo\b", re.I), "for the sake of argument"),
    (re.compile(r"\bsub judice\b", re.I), "still before a court"),
    (re.compile(r"\bab initio\b", re.I), "from the beginning"),
)
_STATUTORY_AUTHORITY = re.compile(r"\bstatutory authority\b", re.IGNORECASE)
_STATUTORY_EXPLANATION = re.compile(
    r"\b(?:power|permission|allowed|allows|gave|gives|granted)\b.*\bCongress\b|"
    r"\bCongress\b.*\b(?:power|permission|allowed|allows|gave|gives|granted)\b",
    re.IGNORECASE,
)
_ADVOCATE_NAME = re.compile(r"^\s*(Mr|Ms|General)\.?\s+([A-Za-z'\u2019\N{EN DASH}-]+)", re.I)
_REQUESTED_ACTION = re.compile(
    r"\b(?:ask(?:s|ed)?|request(?:s|ed)?|want(?:s|ed)?|urge(?:s|d)?|should)\b"
    r"[^.!?]{0,80}\b(?:affirmed|reversed|vacated|ordered)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_NO_DISPOSITION = re.compile(
    r"\b(?:the (?:Supreme )?Court has not (?:yet )?(?:decided|ruled|issued)|"
    r"no (?:decision|ruling|opinion|order) (?:has been|was) (?:issued|entered)|"
    r"there (?:is|was) no (?:decision|ruling|opinion|order))\b",
    re.IGNORECASE,
)
_LOWER_COURT_ACTION = re.compile(
    r"\b(?:lower|appeals|appellate|trial|district) court\b[^.!?]{0,80}"
    r"\b(?:held|ordered|affirmed|reversed|vacated)\b|"
    r"\b(?:held|ordered|affirmed|reversed|vacated)\b[^.!?]{0,80}"
    r"\b(?:lower|appeals|appellate|trial|district) court\b",
    re.IGNORECASE,
)
_INVENTED_ORAL_ARGUMENT = re.compile(
    r"\b(?:oral argument|argument session|argument transcript|official transcript|"
    r"counsel (?:argued|said|told|urged)|(?:a |the )?justice(?:s)? "
    r"(?:asked|questioned|tested)|during argument|at argument)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_FILLER = re.compile(
    r"\b(?:the (?:approved )?record does not (?:say|support)|"
    r"details? (?:are|is) (?:not available|unavailable|unknown)|"
    r"more details? may (?:emerge|follow)|information is unavailable)\b",
    re.IGNORECASE,
)
_NAMED_PHRASE = re.compile(
    r"\b[A-Z][A-Za-z&.'\N{RIGHT SINGLE QUOTATION MARK}-]+"
    r"(?:\s+[A-Z][A-Za-z&.'\N{RIGHT SINGLE QUOTATION MARK}-]+)+\b"
)
_CAPITALIZED_WORD = re.compile(r"\b[A-Z][A-Za-z'\N{RIGHT SINGLE QUOTATION MARK}-]{2,}\b")
_CAPITALIZED_EXEMPT = {
    "a",
    "an",
    "court",
    "docket",
    "how",
    "official",
    "supreme",
    "the",
    "this",
    "what",
    "why",
}
_ACTION_WORD = re.compile(
    r"\b(?:hold|held|order(?:ed)?|grant(?:ed)?|deny|denied|reject(?:ed)?|"
    r"allow(?:ed)?|affirm(?:ed)?|"
    r"uphold|upheld|revers(?:e|ed)|vacat(?:e|ed)|remand(?:ed)?|dismiss(?:ed)?|"
    r"stay(?:ed)?|enjoin(?:ed)?|block(?:ed)?|prevail(?:ed)?|won|lost)\b",
    re.IGNORECASE,
)
_ACTION_CANONICAL = {
    "hold": "hold",
    "held": "hold",
    "order": "order",
    "ordered": "order",
    "grant": "grant",
    "granted": "grant",
    "deny": "deny",
    "denied": "deny",
    "reject": "reject",
    "rejected": "reject",
    "allow": "allow",
    "allowed": "allow",
    "affirm": "affirm",
    "affirmed": "affirm",
    "uphold": "uphold",
    "upheld": "uphold",
    "reverse": "reverse",
    "reversed": "reverse",
    "vacate": "vacate",
    "vacated": "vacate",
    "remand": "remand",
    "remanded": "remand",
    "dismiss": "dismiss",
    "dismissed": "dismiss",
    "stay": "stay",
    "stayed": "stay",
    "enjoin": "enjoin",
    "enjoined": "enjoin",
    "block": "block",
    "blocked": "block",
    "prevail": "prevail",
    "prevailed": "prevail",
    "won": "win",
    "lost": "lose",
}


def _action_words(value: str) -> set[str]:
    return {_ACTION_CANONICAL[item.casefold()] for item in _ACTION_WORD.findall(value)}


def _action_signatures(value: str) -> set[tuple[str, bool]]:
    signatures: set[tuple[str, bool]] = set()
    for match in _ACTION_WORD.finditer(value):
        prefix = value[max(0, match.start() - 35) : match.start()]
        negated = re.search(r"\b(?:not|never|did not|does not)\b[^.!?]{0,24}$", prefix, re.I)
        signatures.add((_ACTION_CANONICAL[match.group(0).casefold()], bool(negated)))
    return signatures


def _unsupported_named_phrase(text: str, support: str, caption: str) -> bool:
    allowed = f"{support} {caption}".casefold()
    generic_prefixes = (
        "what ",
        "how ",
        "why ",
        "official ",
        "supreme court",
        "the court",
    )
    for match in _NAMED_PHRASE.findall(text):
        lowered = match.casefold()
        if lowered.startswith(generic_prefixes):
            continue
        if lowered not in allowed:
            return True
    return any(
        word.casefold() not in _CAPITALIZED_EXEMPT
        and word.casefold() not in allowed
        for word in _CAPITALIZED_WORD.findall(text)
    )


def _attribution_parts(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    lowered = value.casefold().replace("\u2019", "'")
    role: str | None = None
    if "united states" in lowered or "government" in lowered:
        role = "united_states"
    elif "petitioner" in lowered or "for the petitioner" in lowered:
        role = "petitioner"
    elif "respondent" in lowered or "for the respondent" in lowered:
        role = "respondent"
    name_match = _ADVOCATE_NAME.match(value)
    person = (
        f"{name_match.group(1).casefold()} {name_match.group(2).casefold()}" if name_match else None
    )
    return role, person


def _position_label(value: str | None) -> str | None:
    role, _ = _attribution_parts(value)
    return role


def _position_claim_groups(
    claims: tuple[ScotusApprovedClaim, ...],
) -> tuple[set[UUID], ...]:
    attributed = tuple(
        (claim, *_attribution_parts(claim.attribution))
        for claim in claims
        if claim.observation_type is LegalObservationType.ADVOCATE_CONTENTION and claim.attribution
    )
    groups: list[set[UUID]] = []
    for role in sorted({role for _, role, _ in attributed if role}):
        people = {
            person
            for _, item_role, person in attributed
            if item_role == role and person is not None
        }
        groups.append(
            {
                claim.claim_id
                for claim, item_role, person in attributed
                if item_role == role or (person is not None and person in people)
            }
        )
    # A transcript speaker label proves who spoke, not which litigating side that
    # person represented. Require coverage only for roles stated in official evidence;
    # never turn each unknown-role advocate into an invented separate side.
    return tuple(groups)


def _split_long_sentence(sentence: str) -> str:
    pending = [sentence]
    result: list[str] = []
    while pending:
        value = pending.pop(0)
        if len(_WORD.findall(value)) <= 30:
            result.append(value)
            continue
        split: tuple[str, str] | None = None
        for separator, continuation in (
            ("; ", ""),
            (", and ", "And "),
            (", but ", "But "),
        ):
            start = 0
            while (index := value.find(separator, start)) >= 0:
                left = value[:index].strip()
                right = value[index + len(separator) :].strip()
                if len(_WORD.findall(left)) >= 8 and len(_WORD.findall(right)) >= 5:
                    split = (
                        f"{left.rstrip(',. ;')}.",
                        f" {continuation}{right[:1].upper()}{right[1:]}",
                    )
                    break
                start = index + len(separator)
            if split is not None:
                break
        if split is None:
            result.append(value)
        else:
            pending[0:0] = [*split]
    return "".join(result)


def _plain_language_text(text: str) -> str:
    result = _INTERNAL_CLAIM_MARKER.sub("", text)
    result = re.sub(r"\bthe\s+the\s+", "the ", result, flags=re.IGNORECASE)
    for pattern, replacement in _PLAIN_LANGUAGE_REPLACEMENTS:
        result = pattern.sub(replacement, result)
    result = re.sub(r"\bthe\s+the\s+", "the ", result, flags=re.IGNORECASE)
    result = re.sub(
        r"\bthe justices will vote and issue\b",
        "the Court will issue",
        result,
        flags=re.IGNORECASE,
    )
    shortened: list[str] = []
    for sentence_match in _SENTENCE.finditer(result):
        sentence = sentence_match.group(0)
        if len(_WORD.findall(sentence)) > 30 and " and to " in sentence:
            left, right = sentence.split(" and to ", 1)
            sentence = f"{left.rstrip(' ,')}. The same side also seeks to {right.lstrip()}"
        shortened.append(_split_long_sentence(sentence))
    return "".join(shortened)


def _plain_language_draft(draft: LegalBriefDraft) -> LegalBriefDraft:
    grouped_sections: dict[str, DraftSection] = {}
    for section in draft.sections:
        heading = re.sub(r",?\s+continued$", "", _plain_language_text(section.heading), flags=re.I)
        paragraphs = tuple(
            value
            for paragraph in section.paragraphs
            if not _META_OUTPUT.search(paragraph)
            if (value := _plain_language_text(paragraph)).strip()
        )
        if not paragraphs:
            continue
        existing = grouped_sections.get(heading.casefold())
        if existing is None:
            grouped_sections[heading.casefold()] = section.model_copy(
                update={"heading": heading, "paragraphs": paragraphs[:3]}
            )
        else:
            grouped_sections[heading.casefold()] = existing.model_copy(
                update={
                    "paragraphs": tuple(dict.fromkeys((*existing.paragraphs, *paragraphs)))[:3],
                    "claim_ids": tuple(dict.fromkeys((*existing.claim_ids, *section.claim_ids))),
                }
            )
    return draft.model_copy(
        update={
            "title": _plain_language_text(draft.title),
            "dek": _plain_language_text(draft.dek),
            "sections": tuple(grouped_sections.values())[:8],
            "argument_analyses": tuple(
                analysis.model_copy(
                    update={
                        "heading": _plain_language_text(analysis.heading),
                        "paragraphs": tuple(
                            value
                            for paragraph in analysis.paragraphs
                            if not _META_OUTPUT.search(paragraph)
                            if (value := _plain_language_text(paragraph)).strip()
                        )[:6],
                    }
                )
                for analysis in draft.argument_analyses
            ),
        }
    )


def _validate_plain_language(
    text: str,
    *,
    maximum_sentence_words: int,
    maximum_paragraph_words: int,
) -> None:
    if len(_WORD.findall(text)) > maximum_paragraph_words:
        raise BriefValidationError("plain-language paragraph is too long")
    if any(
        len(_WORD.findall(sentence.group(0))) > maximum_sentence_words
        for sentence in _SENTENCE.finditer(text)
    ):
        raise BriefValidationError("plain-language sentence is too long")
    if _LEGALESE.search(text):
        raise BriefValidationError("brief contains unexplained legalese")
    if _STATUTORY_AUTHORITY.search(text) and not _STATUTORY_EXPLANATION.search(text):
        raise BriefValidationError("brief contains an unexplained legal concept")


def _sanitize(value: str, sensitivity: tuple[ScotusSensitivity, ...]) -> str | None:
    labels = set(sensitivity)
    if ScotusSensitivity.SEALED_OR_REDACTED in labels:
        return None
    sanitized = value
    if ScotusSensitivity.HOME_ADDRESS in labels:
        sanitized = _ADDRESS.sub("a private address", sanitized)
    if ScotusSensitivity.PRIVATE_NAME in labels:
        sanitized = _PRIVATE_NAME.sub("a private individual", sanitized)
    if ScotusSensitivity.MINOR in labels:
        sanitized = re.sub(
            r"\b(?:the )?(?:minor|child|juvenile)\b",
            "a minor",
            sanitized,
            flags=re.I,
        )
    if ScotusSensitivity.VICTIM in labels:
        sanitized = re.sub(
            r"\b(?:the )?(?:victim|survivor)\b",
            "a protected individual",
            sanitized,
            flags=re.I,
        )
    if ScotusSensitivity.MEDICAL in labels:
        sanitized = re.sub(
            r"\b(?:diagnosis|medical treatment|treatment details)\b",
            "medical circumstances",
            sanitized,
            flags=re.I,
        )
    compacted = " ".join(sanitized.split())
    if len(compacted) > 2_000:
        compacted = compacted[:2_000].rsplit(" ", 1)[0].rstrip(" ,;:") + "."
    return compacted


def _maturity(candidate: BriefCandidate) -> BriefMaturity:
    if candidate.case_status is ScotusCaseStatus.DECIDED:
        return BriefMaturity.POST_OPINION
    if candidate.case_status is ScotusCaseStatus.ORDER_ISSUED:
        return BriefMaturity.POST_ORDER
    if candidate.case_status is ScotusCaseStatus.CORRECTED:
        return BriefMaturity.CORRECTED
    return BriefMaturity.OFFICIAL_TRANSCRIPT


def evaluate_brief_candidate(
    candidate: BriefCandidate,
    *,
    minimum_confidence: float,
    policy_version: str = "scotus-brief-policy-v1",
) -> BriefPolicyDecision:
    reasons: list[str] = []
    if candidate.argument_sessions and not candidate.official_transcript_complete:
        reasons.append("complete official transcript is required")
    if candidate.argument_sessions and not candidate.parser_complete:
        reasons.append("transcript parser did not complete safely")
    if candidate.privacy_blocking_failure:
        reasons.append("blocking privacy review failure")
    if not candidate.caption.strip() or not candidate.primary_docket.strip():
        reasons.append("case identity is incomplete")
    if candidate.argument_sessions and candidate.argument_id is None:
        reasons.append("argument case is missing its real argument anchor")
    if not candidate.argument_sessions and candidate.argument_id is not None:
        reasons.append("disposition-only case cannot have an argument anchor")
    eligible_observations = tuple(
        item for item in candidate.observations if item.confidence >= minimum_confidence
    )
    if len(eligible_observations) < 3:
        reasons.append("insufficient grounded legal observations")
    observed_sessions = {
        item.argument_id for item in eligible_observations if item.argument_id is not None
    }
    missing_sessions = {
        session.argument_id for session in candidate.argument_sessions
    } - observed_sessions
    if missing_sessions:
        reasons.append("one or more argument sessions lack grounded observations")
    if not candidate.argument_sessions:
        if any(item.argument_id is not None for item in eligible_observations):
            reasons.append("disposition-only evidence cannot reference an argument session")
        evidence_kinds = {
            evidence.document_kind for item in eligible_observations for evidence in item.evidence
        }
        if ScotusDocumentKind.DOCKET not in evidence_kinds:
            reasons.append("disposition-only case lacks grounded docket evidence")
        if not any(
            item.observation_type in {LegalObservationType.HOLDING, LegalObservationType.ORDER}
            and item.legal_status in {LegalStatus.COURT_HELD, LegalStatus.COURT_ORDERED}
            for item in eligible_observations
        ):
            reasons.append("disposition-only case lacks typed Court action evidence")
    claim_types = {item.observation_type for item in eligible_observations}
    if not claim_types.intersection(
        {LegalObservationType.QUESTION_PRESENTED, LegalObservationType.PROCEDURAL_POSTURE}
    ):
        reasons.append("no grounded question presented or procedural posture")
    if not claim_types.intersection(
        {
            LegalObservationType.ADVOCATE_CONTENTION,
            LegalObservationType.JUSTICE_QUESTION,
            LegalObservationType.HOLDING,
            LegalObservationType.ORDER,
        }
    ):
        reasons.append("no grounded argument, question, or holding")
    if reasons:
        return BriefPolicyDecision(False, tuple(reasons), (), None)

    claims: list[ScotusApprovedClaim] = []
    for observation in eligible_observations:
        public_value = _sanitize(
            observation.normalized_value_private or observation.raw_value_private,
            observation.sensitivity,
        )
        if not public_value:
            continue
        first_evidence = observation.evidence[0]
        official_url = candidate.document_urls.get(first_evidence.document_revision_id)
        if official_url is None:
            raise BriefPolicyError("approved observation has no official document URL")
        page_label = (
            f"file page {first_evidence.start_file_page}, lines "
            f"{first_evidence.start_line}-{first_evidence.end_line}"
        )
        claim_id = uuid5(
            NAMESPACE_URL,
            f"ragchew:scotus-claim:{observation.observation_id}:{policy_version}",
        )
        claims.append(
            ScotusApprovedClaim(
                claim_id=claim_id,
                case_id=candidate.case_id,
                argument_id=observation.argument_id,
                observation_type=observation.observation_type,
                legal_status=observation.legal_status,
                certainty=observation.certainty,
                public_value=public_value,
                attribution=observation.attribution,
                official_url=official_url,
                public_source_label=first_evidence.document_kind.value.replace("_", " ").title(),
                page_label=page_label,
                source_observation_ids=(observation.observation_id,),
                approved_at=candidate.evaluated_at,
                policy_version=policy_version,
            )
        )
    if len(claims) < 3:
        return BriefPolicyDecision(
            False,
            ("insufficient claims after sensitivity minimization",),
            (),
            None,
        )
    return BriefPolicyDecision(True, (), tuple(claims), _maturity(candidate))


def _validate_public_text(
    text: str,
    claim_ids: tuple[UUID, ...],
    candidate: BriefCandidate,
    claim_map: dict[UUID, ScotusApprovedClaim],
    *,
    public_quotes: bool,
    maximum_sentence_words: int,
    maximum_paragraph_words: int,
) -> None:
    if any(claim_id not in claim_map for claim_id in claim_ids):
        raise BriefValidationError("text references an unapproved claim")
    support = " ".join(claim_map[value].public_value for value in claim_ids)
    if not text.strip():
        raise BriefValidationError("brief contains empty text")
    if _PREDICTION.search(text):
        raise BriefValidationError("justice vote or outcome prediction is prohibited")
    if _QUESTION_AS_HOLDING.search(text):
        raise BriefValidationError("question is overstated as a holding or vote")
    if _UNSUPPORTED_NO_DISPOSITION.search(text):
        raise BriefValidationError("brief infers no disposition from an incomplete record")
    if _TONE_OR_IDEOLOGY.search(text):
        raise BriefValidationError("tone, sentiment, or ideological scoring is prohibited")
    if _LEGAL_ADVICE.search(text):
        raise BriefValidationError("personalized legal advice is prohibited")
    if _UNSUPPORTED_SPECULATION.search(text):
        raise BriefValidationError("unsupported speculative language is prohibited")
    if not public_quotes and _QUOTATION.search(text):
        raise BriefValidationError("public transcript quotations are disabled")
    if _INTERNAL_CLAIM_MARKER.search(text):
        raise BriefValidationError("public prose contains an internal claim marker")
    if _META_OUTPUT.search(text):
        raise BriefValidationError("public prose contains model or schema instructions")
    if not candidate.argument_sessions:
        if _INVENTED_ORAL_ARGUMENT.search(text):
            raise BriefValidationError(
                "disposition-only brief invents oral argument",
                safe_code="invented_oral_argument",
            )
        if _UNSUPPORTED_FILLER.search(text):
            raise BriefValidationError(
                "disposition-only brief contains unsupported filler",
                safe_code="unsupported_filler",
            )
        if _unsupported_named_phrase(text, support, candidate.caption):
            raise BriefValidationError(
                "disposition-only brief adds an unsupported party",
                safe_code="unsupported_party",
            )
    if re.search(r"\bthe\s+the\b", text, re.I):
        raise BriefValidationError("public prose contains a repeated article")
    for citation in _CITATION.findall(text):
        if citation not in support:
            raise BriefValidationError("text adds an unsupported citation")
    for docket in _DOCKET.findall(text):
        if docket != candidate.primary_docket and docket not in support:
            raise BriefValidationError("text adds an unsupported docket")
    final_action_text = re.sub(
        r"\b(?:the Court )?held oral argument\b|"
        r"\b(?:argument|argument session|session)\s*,?\s*(?:was )?held\b|"
        r"\b(?:they|counsel|the opposing side|the same side) affirmed\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    final_words = re.search(
        r"\b(?:held|ordered|affirmed|reversed|vacated)\b", final_action_text, re.I
    )
    supporting_claims = tuple(claim_map[value] for value in claim_ids)
    court_action_supported = any(
        claim.legal_status in {LegalStatus.COURT_HELD, LegalStatus.COURT_ORDERED}
        for claim in supporting_claims
    )
    requested_action_supported = _REQUESTED_ACTION.search(text) is not None and any(
        claim.legal_status is LegalStatus.REQUESTED for claim in supporting_claims
    )
    lower_court_action_supported = _LOWER_COURT_ACTION.search(text) is not None and any(
        claim.legal_status is LegalStatus.LOWER_COURT_HELD for claim in supporting_claims
    )
    if final_words and not (
        court_action_supported or requested_action_supported or lower_court_action_supported
    ):
        raise BriefValidationError("text overstates final Court action")
    stated_actions = _action_words(text)
    supported_actions = _action_words(support)
    final_action_support = " ".join(
        claim.public_value
        for claim in supporting_claims
        if claim.legal_status in {LegalStatus.COURT_HELD, LegalStatus.COURT_ORDERED}
    )
    if stated_actions and final_action_support:
        action_inconsistent = _action_signatures(text) != _action_signatures(
            final_action_support
        )
    else:
        action_inconsistent = bool(stated_actions - supported_actions)
    if action_inconsistent:
        raise BriefValidationError(
            "text changes or invents the supported disposition",
            safe_code="unsupported_disposition",
        )
    _validate_plain_language(
        text,
        maximum_sentence_words=maximum_sentence_words,
        maximum_paragraph_words=maximum_paragraph_words,
    )


def validate_brief_draft(
    draft: LegalBriefDraft,
    candidate: BriefCandidate,
    claims: tuple[ScotusApprovedClaim, ...],
    *,
    public_quotes: bool,
    maximum_sentence_words: int = 30,
    maximum_paragraph_words: int = 120,
) -> None:
    claim_map = {claim.claim_id: claim for claim in claims}
    if not draft.sections:
        raise BriefValidationError("brief has no supported sections")
    if len(draft.sections) > 8:
        raise BriefValidationError("brief has too many sections")
    if any(len(section.paragraphs) > 3 for section in draft.sections):
        raise BriefValidationError("brief section is too repetitive")
    if any(len(analysis.paragraphs) > 6 for analysis in draft.argument_analyses):
        raise BriefValidationError("argument analysis is too long")
    headings = [section.heading.strip().casefold() for section in draft.sections]
    if len(headings) != len(set(headings)):
        raise BriefValidationError("brief repeats a section heading")
    if draft.title.strip().casefold() in {
        "what this case is about",
        "plain-language guide",
        "supreme court case explained",
    }:
        raise BriefValidationError("brief title is generic")
    total_words = sum(
        len(_WORD.findall(text))
        for text in (
            draft.title,
            draft.dek,
            *(paragraph for section in draft.sections for paragraph in section.paragraphs),
            *(
                paragraph
                for analysis in draft.argument_analyses
                for paragraph in analysis.paragraphs
            ),
        )
    )
    if total_words > 1500:
        raise BriefValidationError("brief is too long for a citizen-facing case page")

    def validate(text: str, claim_ids: tuple[UUID, ...]) -> None:
        _validate_public_text(
            text,
            claim_ids,
            candidate,
            claim_map,
            public_quotes=public_quotes,
            maximum_sentence_words=maximum_sentence_words,
            maximum_paragraph_words=maximum_paragraph_words,
        )

    validate(draft.title, draft.title_claim_ids)
    validate(draft.dek, draft.dek_claim_ids)
    used_claim_ids = {
        *draft.title_claim_ids,
        *draft.dek_claim_ids,
        *(claim_id for section in draft.sections for claim_id in section.claim_ids),
        *(claim_id for analysis in draft.argument_analyses for claim_id in analysis.claim_ids),
    }
    if not candidate.argument_sessions:
        final_claim_ids = {
            claim.claim_id
            for claim in claims
            if claim.observation_type in {LegalObservationType.HOLDING, LegalObservationType.ORDER}
        }
        docket_claim_ids = {
            claim.claim_id
            for claim in claims
            if any(
                observation.observation_id in claim.source_observation_ids
                and any(
                    evidence.document_kind is ScotusDocumentKind.DOCKET
                    for evidence in observation.evidence
                )
                for observation in candidate.observations
            )
        }
        if not final_claim_ids.intersection(used_claim_ids):
            raise BriefValidationError("disposition-only brief omits the Court action")
        if not docket_claim_ids.intersection(used_claim_ids):
            raise BriefValidationError("disposition-only brief omits docket provenance")
    for required_type in (
        LegalObservationType.QUESTION_PRESENTED,
        LegalObservationType.PROCEDURAL_POSTURE,
        LegalObservationType.ADVOCATE_CONTENTION,
        LegalObservationType.JUSTICE_QUESTION,
    ):
        matching = {claim.claim_id for claim in claims if claim.observation_type is required_type}
        if matching and not matching.intersection(used_claim_ids):
            raise BriefValidationError(
                f"brief omits available citizen context: {required_type.value}"
            )
    for matching in _position_claim_groups(claims):
        if not matching.intersection(used_claim_ids):
            raise BriefValidationError("brief omits an available side's position")
    for section in draft.sections:
        validate(section.heading, section.claim_ids)
        for paragraph in section.paragraphs:
            validate(paragraph, section.claim_ids)
    expected_sessions = tuple(session.argument_id for session in candidate.argument_sessions)
    actual_sessions = tuple(item.argument_id for item in draft.argument_analyses)
    if actual_sessions != expected_sessions:
        raise BriefValidationError(
            "brief must analyze every argument session in chronological order"
        )
    for analysis in draft.argument_analyses:
        if any(
            claim_map[claim_id].argument_id != analysis.argument_id
            for claim_id in analysis.claim_ids
            if claim_id in claim_map
        ):
            raise BriefValidationError("argument analysis uses a claim from a different session")
        analysis_ids = set(analysis.claim_ids)
        session_claims = tuple(
            claim for claim in claims if claim.argument_id == analysis.argument_id
        )
        for required_type in (
            LegalObservationType.ADVOCATE_CONTENTION,
            LegalObservationType.JUSTICE_QUESTION,
        ):
            matching = {
                claim.claim_id
                for claim in session_claims
                if claim.observation_type is required_type
            }
            if matching and not matching.intersection(analysis_ids):
                raise BriefValidationError(f"argument breakdown omits {required_type.value}")
        for matching in _position_claim_groups(session_claims):
            if not matching.intersection(analysis_ids):
                raise BriefValidationError("argument breakdown omits one side")
        validate(analysis.heading, analysis.claim_ids)
        for paragraph in analysis.paragraphs:
            validate(paragraph, analysis.claim_ids)


class InMemoryBriefRevisionStore:
    def __init__(self) -> None:
        self.claims: dict[UUID, ScotusApprovedClaim] = {}
        self.revisions: dict[tuple[UUID, int], LegalBriefRevision] = {}

    def save(
        self,
        claims: tuple[ScotusApprovedClaim, ...],
        revision: LegalBriefRevision,
    ) -> LegalBriefRevision:
        for claim in claims:
            self.claims.setdefault(claim.claim_id, claim)
        key = (revision.brief_id, revision.revision_number)
        prior_revision = self.revisions.setdefault(key, revision)
        if prior_revision != revision:
            raise RuntimeError("conflicting brief under deterministic revision identity")
        return prior_revision


class PostgresBriefRevisionStore:
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
        claims: tuple[ScotusApprovedClaim, ...],
        revision: LegalBriefRevision,
    ) -> LegalBriefRevision:
        with self.pool.connection() as connection, connection.transaction():
            for claim in claims:
                connection.execute(
                    """INSERT INTO scotus_approved_claims
                       (claim_id,case_id,argument_id,observation_type,legal_status,certainty,
                        public_value,attribution,official_url,public_source_label,page_label,
                        source_observation_ids,policy_version,approved_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                       ON CONFLICT(claim_id) DO NOTHING""",
                    (
                        claim.claim_id,
                        claim.case_id,
                        claim.argument_id,
                        claim.observation_type.value,
                        claim.legal_status.value,
                        claim.certainty.value,
                        claim.public_value,
                        claim.attribution,
                        claim.official_url,
                        claim.public_source_label,
                        claim.page_label,
                        json.dumps([str(value) for value in claim.source_observation_ids]),
                        claim.policy_version,
                        claim.approved_at,
                    ),
                )
            connection.execute(
                """INSERT INTO scotus_brief_revisions
                   (revision_id,brief_id,case_id,argument_id,revision_number,maturity,
                    public_payload,claim_ids,correction_note,generator_model,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)
                   ON CONFLICT(brief_id,revision_number) DO NOTHING""",
                (
                    revision.revision_id,
                    revision.brief_id,
                    revision.case_id,
                    revision.argument_id,
                    revision.revision_number,
                    revision.maturity.value,
                    revision.model_dump_json(),
                    json.dumps([str(value) for value in revision.claim_ids]),
                    revision.correction_note,
                    revision.generator_model,
                    revision.created_at,
                ),
            )
            row = connection.execute(
                """SELECT public_payload FROM scotus_brief_revisions
                   WHERE brief_id=%s AND revision_number=%s""",
                (revision.brief_id, revision.revision_number),
            ).fetchone()
        if row is None:
            raise RuntimeError("SCOTUS brief revision disappeared")
        return LegalBriefRevision.model_validate(row["public_payload"])


class BriefGenerationService:
    def __init__(
        self,
        generator: LegalBriefGenerator,
        store: BriefRevisionStore,
        *,
        public_quotes: bool = False,
        maximum_sentence_words: int = 30,
        maximum_paragraph_words: int = 120,
    ) -> None:
        self.generator = generator
        self.store = store
        self.public_quotes = public_quotes
        self.maximum_sentence_words = maximum_sentence_words
        self.maximum_paragraph_words = maximum_paragraph_words

    def generate(
        self,
        candidate: BriefCandidate,
        decision: BriefPolicyDecision,
        *,
        revision_number: int,
        correction_note: str | None = None,
    ) -> LegalBriefRevision:
        if not decision.eligible or not decision.claims or decision.maturity is None:
            raise BriefPolicyError("case is not eligible for legal brief generation")
        try:
            draft = self.generator.generate(candidate, decision.claims, decision.maturity)
            validate_brief_draft(
                draft,
                candidate,
                decision.claims,
                public_quotes=self.public_quotes,
                maximum_sentence_words=self.maximum_sentence_words,
                maximum_paragraph_words=self.maximum_paragraph_words,
            )
        except BriefValidationError as error:
            safe_code = (
                error.safe_code or re.sub(r"[^a-z0-9]+", "_", str(error).casefold()).strip("_")[:80]
            )
            raise BriefValidationError(str(error), safe_code=safe_code) from None
        brief_id = uuid5(NAMESPACE_URL, f"ragchew:scotus-case-brief:{candidate.case_id}")
        revision = LegalBriefRevision(
            brief_id=brief_id,
            revision_id=uuid5(
                NAMESPACE_URL,
                f"ragchew:scotus-brief-revision:{brief_id}:{revision_number}",
            ),
            case_id=candidate.case_id,
            argument_id=candidate.argument_id,
            revision_number=revision_number,
            maturity=decision.maturity,
            title=draft.title,
            title_claim_ids=draft.title_claim_ids,
            dek=draft.dek,
            dek_claim_ids=draft.dek_claim_ids,
            sections=tuple(
                BriefSection(
                    heading=section.heading,
                    paragraphs=section.paragraphs,
                    claim_ids=section.claim_ids,
                )
                for section in draft.sections
            ),
            argument_analyses=tuple(
                BriefArgumentAnalysis(
                    argument_id=analysis.argument_id,
                    sequence=session.sequence,
                    argument_date=session.argument_date,
                    reargument=session.reargument,
                    heading=analysis.heading,
                    paragraphs=analysis.paragraphs,
                    claim_ids=analysis.claim_ids,
                )
                for analysis, session in zip(
                    draft.argument_analyses,
                    candidate.argument_sessions,
                    strict=True,
                )
            ),
            claim_ids=tuple(
                dict.fromkeys(
                    (
                        *draft.title_claim_ids,
                        *draft.dek_claim_ids,
                        *(claim_id for section in draft.sections for claim_id in section.claim_ids),
                        *(
                            claim_id
                            for analysis in draft.argument_analyses
                            for claim_id in analysis.claim_ids
                        ),
                    )
                )
            ),
            correction_note=correction_note,
            created_at=candidate.evaluated_at,
            generator_model=self.generator.model_name,
        )
        return self.store.save(decision.claims, revision)
