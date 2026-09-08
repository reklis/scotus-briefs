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
from ragchew.scotus.reader_prose import load_reader_prose_policy


class BriefPolicyError(ValueError):
    def __init__(self, message: str, *, safe_code: str | None = None) -> None:
        super().__init__(message)
        self.safe_code = safe_code


class BriefValidationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        safe_code: str | None = None,
        draft: LegalBriefDraft | None = None,
    ) -> None:
        super().__init__(message)
        self.safe_code = safe_code
        self.draft = draft


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


DISPOSITION_GUIDE_HEADINGS = (
    "What this case is about",
    "Why this case reached the Court",
    "The legal issue",
    "What the Supreme Court did",
    "Why the Court did it",
)
DISPOSITION_SEPARATE_OPINIONS_HEADING = "What separate opinions said"


def _disposition_support_by_heading(
    claims: tuple[ScotusApprovedClaim, ...],
) -> dict[str, tuple[UUID, ...]]:
    controlling = tuple(claim for claim in claims if not _is_separate_opinion_claim(claim))

    def ids(*types: LegalObservationType) -> tuple[UUID, ...]:
        return tuple(claim.claim_id for claim in controlling if claim.observation_type in types)

    question_claims = tuple(
        claim
        for claim in controlling
        if claim.observation_type is LegalObservationType.QUESTION_PRESENTED
    )
    doctrine_claims = tuple(
        claim
        for claim in controlling
        if claim.observation_type is LegalObservationType.DOCTRINAL_THEME
    )
    selected_issue_ids = (
        (
            max(
                question_claims,
                key=lambda claim: (
                    sum(
                        bool(re.search(pattern, claim.public_value, re.IGNORECASE))
                        for pattern in (
                            r"\bjurisdiction\b",
                            r"\bjusticiab\w*\b",
                            r"\bripeness\b",
                            r"\bstanding\b",
                        )
                    ),
                    -len(claim.public_value),
                ),
            ).claim_id,
        )
        if question_claims
        else ()
    )
    if not selected_issue_ids and doctrine_claims:
        selected_issue_ids = (doctrine_claims[0].claim_id,)
    issue_values = {
        claim.public_value.casefold()
        for claim in controlling
        if claim.claim_id in selected_issue_ids
    }
    reasoning_ids = tuple(
        claim.claim_id
        for claim in doctrine_claims
        if claim.claim_id not in selected_issue_ids
        and claim.public_value.casefold() not in issue_values
    )
    issue_ids = selected_issue_ids
    return {
        "What this case is about": ids(LegalObservationType.CASE_BACKGROUND),
        "Why this case reached the Court": ids(
            LegalObservationType.PROCEDURAL_POSTURE,
            LegalObservationType.REQUESTED_DISPOSITION,
            LegalObservationType.LOWER_COURT_ACTION,
        ),
        "The legal issue": issue_ids,
        "What the Supreme Court did": ids(
            LegalObservationType.HOLDING,
            LegalObservationType.ORDER,
            LegalObservationType.REQUESTED_DISPOSITION,
            LegalObservationType.LOWER_COURT_ACTION,
        ),
        "Why the Court did it": reasoning_ids,
        DISPOSITION_SEPARATE_OPINIONS_HEADING: tuple(
            claim.claim_id
            for claim in claims
            if _is_separate_opinion_claim(claim)
            and claim.observation_type
            in {LegalObservationType.HOLDING, LegalObservationType.DOCTRINAL_THEME}
        ),
    }


def _unquoted_claim_fallback(value: str) -> str:
    without_double_quotes = value.translate(str.maketrans("", "", '"\u2018\u2019\u201c\u201d'))
    without_standalone_quotes = re.sub(r"(?<!\w)'([^'\n]{2,})'(?!\w)", r"\1", without_double_quotes)
    without_pdf_wraps = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", without_standalone_quotes)
    without_trailing_citation = re.sub(
        r"\s+[A-Z][A-Za-z.&'\u2019 -]{1,60}\s+v\.\s*(?:[A-Z].*)?$",
        "",
        without_pdf_wraps,
    )
    without_trailing_citation = re.sub(
        r"^(?:\d+\s+)?(?:TRUMP\s+v\.\s+)?CALIFORNIA\s+Per Curiam"
        r"(?:\s+[A-Z])?\s+",
        "",
        without_trailing_citation,
        flags=re.IGNORECASE,
    )
    without_trailing_citation = re.sub(
        r"^cannot manufacture standing\b",
        "The States cannot manufacture standing",
        without_trailing_citation,
        flags=re.IGNORECASE,
    )
    without_trailing_citation = re.sub(
        r"^the States\b",
        "The States",
        without_trailing_citation,
    )
    without_trailing_citation = re.sub(
        r"^One is standing,\s+which requires\b",
        "Standing requires",
        without_trailing_citation,
        flags=re.IGNORECASE,
    )
    without_trailing_citation = re.sub(
        r"^It imposes no obligations\b",
        "The challenged provision imposes no obligations",
        without_trailing_citation,
        flags=re.IGNORECASE,
    )
    without_trailing_citation = re.sub(
        r"\bfrom it(?=\s*[.!?]?$)",
        "from that provision",
        without_trailing_citation,
        flags=re.IGNORECASE,
    )
    return _plain_language_text(without_trailing_citation)


def _normalize_disposition_support(
    draft: LegalBriefDraft,
    claims: tuple[ScotusApprovedClaim, ...],
) -> LegalBriefDraft:
    controlling = tuple(claim for claim in claims if not _is_separate_opinion_claim(claim))
    docket_ids = tuple(
        claim.claim_id
        for claim in controlling
        if claim.public_source_label.casefold() == "docket"
        or "/docket/" in claim.official_url.casefold()
    )
    support_by_heading = _disposition_support_by_heading(claims)
    separate_support_ids = set(support_by_heading[DISPOSITION_SEPARATE_OPINIONS_HEADING])
    claim_map = {claim.claim_id: claim for claim in claims}
    background_support = tuple(
        claim_map[claim_id] for claim_id in support_by_heading["What this case is about"]
    )
    issue_support = tuple(claim_map[claim_id] for claim_id in support_by_heading["The legal issue"])
    path_support = tuple(
        claim_map[claim_id] for claim_id in support_by_heading["Why this case reached the Court"]
    )
    reason_support = tuple(
        claim_map[claim_id] for claim_id in support_by_heading["Why the Court did it"]
    )
    original_by_heading = {section.heading.strip(): section for section in draft.sections}
    replacement_paragraphs: dict[str, tuple[str, ...]] = {}
    background_section = original_by_heading.get("What this case is about")
    if background_section is not None and background_support:
        background_text = " ".join(background_section.paragraphs)
        background_is_court_action = (
            re.search(r"\bsupreme court\b", background_text, re.IGNORECASE) is not None
            and _ACTION_WORD.search(background_text) is not None
        )
        if background_is_court_action or any(
            not _guide_paragraph_has_support(paragraph, background_support)
            for paragraph in background_section.paragraphs
        ):
            background_candidates = (
                tuple(
                    claim
                    for claim in background_support
                    if not (
                        re.search(r"\bsupreme court\b", claim.public_value, re.IGNORECASE)
                        and _ACTION_WORD.search(claim.public_value)
                    )
                )
                or background_support
            )
            strongest_background = max(
                background_candidates,
                key=lambda claim: (
                    sum(
                        bool(re.search(pattern, claim.public_value, re.IGNORECASE))
                        for pattern in (
                            r"\bballot\w*\b",
                            r"\bcitizen\w*\b",
                            r"\belection\w*\b",
                            r"\bpostal\b",
                            r"\bprosecut\w*\b",
                            r"\bstate\w*\b",
                        )
                    ),
                    len(claim.public_value),
                ),
            )
            replacement_paragraphs["What this case is about"] = (
                _unquoted_claim_fallback(strongest_background.public_value),
            )
    issue_section = original_by_heading.get("The legal issue")
    if issue_section is not None and issue_support:
        issue_paragraph = " ".join(issue_section.paragraphs)
        issue_needs_fallback = (
            any(
                not _guide_paragraph_has_support(paragraph, issue_support)
                for paragraph in issue_section.paragraphs
            )
            or re.search(
                r"\b(?:whether|legal (?:issue|question)|court (?:must|had to) decide)\b",
                issue_paragraph,
                re.IGNORECASE,
            )
            is None
            or re.search(
                r"\bdoctrines?\b[^.!?]{0,80}\b(?:block|bar|foreclose)\w*\b",
                issue_paragraph,
                re.IGNORECASE,
            )
            is not None
        )
        if issue_needs_fallback:
            issue_context = " ".join(claim.public_value for claim in issue_support)
            if re.search(r"\bjusticiab\w*\b", issue_context, re.IGNORECASE):
                issue_sentences = [
                    (
                        "The legal issue was whether there was a dispute the Court could decide "
                        "because the Order did not harm the States."
                    )
                    if _GUIDE_NEGATION.search(issue_context)
                    else "The legal issue was whether the Court could decide the States' suit."
                ]
                if re.search(r"\bstanding\b", issue_context, re.IGNORECASE) and re.search(
                    r"\b(?:concrete|injury)\b", issue_context, re.IGNORECASE
                ):
                    issue_sentences.append(
                        "One part was whether the States suffered concrete harm that gave them "
                        "the right to bring the case."
                    )
                replacement_paragraphs["The legal issue"] = (" ".join(issue_sentences),)
            else:
                issue_text = re.sub(
                    r"^(?:As a result|In doing so),\s*",
                    "",
                    issue_support[0].public_value,
                    flags=re.IGNORECASE,
                )
                replacement_paragraphs["The legal issue"] = (_unquoted_claim_fallback(issue_text),)
    path_section = original_by_heading.get("Why this case reached the Court")
    if path_section is not None and path_support:
        path_roles = {
            role
            for paragraph in path_section.paragraphs
            for sentence in _SENTENCE.findall(paragraph)
            if _ACTION_WORD.search(sentence) and (role := _action_role(sentence)) is not None
        }
        required_path_roles = {
            role
            for role, statuses in (
                ("requested", {LegalStatus.REQUESTED}),
                ("lower_court", {LegalStatus.LOWER_COURT_HELD}),
            )
            if any(claim.legal_status in statuses for claim in path_support)
        }
        if not required_path_roles.issubset(path_roles):
            ordered_path_claims = tuple(
                claim
                for status in (LegalStatus.LOWER_COURT_HELD, LegalStatus.REQUESTED)
                for claim in path_support
                if claim.legal_status is status
            )
            if ordered_path_claims:
                path_sentences: list[str] = []
                for claim in ordered_path_claims:
                    value = claim.public_value
                    if (
                        claim.legal_status is LegalStatus.LOWER_COURT_HELD
                        and re.search(r"\bdistrict court\b", value, re.IGNORECASE)
                        and re.search(r"\benjoin\w*\b", value, re.IGNORECASE)
                        and re.search(r"\bgovernment\b", value, re.IGNORECASE)
                        and re.search(r"\border\b", value, re.IGNORECASE)
                    ):
                        path_sentences.append(
                            "The District Court blocked the Government from carrying out the Order."
                        )
                    elif (
                        claim.legal_status is LegalStatus.REQUESTED
                        and re.search(r"\bgovernment\b", value, re.IGNORECASE)
                        and re.search(r"\bstay\b", value, re.IGNORECASE)
                        and re.search(r"\binjunction\b", value, re.IGNORECASE)
                    ):
                        path_sentences.append(
                            "The Government asked the Supreme Court for a stay, meaning a "
                            "temporary pause of the lower court's blocking order."
                        )
                    else:
                        path_sentences.append(_unquoted_claim_fallback(value))
                replacement_paragraphs["Why this case reached the Court"] = (
                    " ".join(path_sentences),
                )
    action_section = original_by_heading.get("What the Supreme Court did")
    action_support = tuple(
        claim_map[claim_id] for claim_id in support_by_heading["What the Supreme Court did"]
    )
    court_action_claims = tuple(
        claim
        for claim in action_support
        if claim.legal_status in {LegalStatus.COURT_HELD, LegalStatus.COURT_ORDERED}
    )
    if action_section is not None and court_action_claims:
        action_text = " ".join(action_section.paragraphs)
        action_is_supported = True
        try:
            _validate_action_sentences(action_text, action_support)
        except BriefValidationError:
            action_is_supported = False
        source_grants_stay = any(
            ("grant", False) in _action_signatures(claim.public_value)
            and re.search(r"\bstay\b", claim.public_value, re.IGNORECASE)
            for claim in court_action_claims
        )
        generated_states_stay = (
            re.search(r"\bstay(?:ed)?\b", action_text, re.IGNORECASE) is not None
        )
        generated_has_interim_effect = _INTERIM_EFFECT.search(action_text) is not None
        if source_grants_stay and (
            not action_is_supported or not generated_states_stay or not generated_has_interim_effect
        ):
            replacement_paragraphs["What the Supreme Court did"] = (
                "The Supreme Court granted a stay of the injunction, meaning it temporarily "
                "paused the lower court's blocking order while the appeal continues.",
            )
    reason_section = original_by_heading.get("Why the Court did it")
    if reason_section is not None and reason_support:
        reason_text = " ".join(reason_section.paragraphs)
        reason_context = " ".join(claim.public_value for claim in reason_support)
        reason_needs_fallback = (
            any(
                not _guide_paragraph_has_support(paragraph, reason_support)
                for paragraph in reason_section.paragraphs
            )
            or re.search(
                r"\b(?:directive|order|provision) fails? to meet "
                r"(?:this|the) (?:legal|standing) standard\b",
                reason_text,
                re.IGNORECASE,
            )
            is not None
            or re.search(
                r"\b(?:state|states|government|applicant|respondent|petitioner|agency|"
                r"order|directive|provision|section|injunction)\b",
                reason_text,
                re.IGNORECASE,
            )
            is None
            or (
                re.search(
                    r"\b(?:the )?order "
                    r"(?:imposes no obligations|causes? no concrete harm)",
                    reason_text,
                    re.IGNORECASE,
                )
                is not None
                and re.search(
                    r"\b(?:the )?order "
                    r"(?:imposes no obligations|causes? no concrete harm)",
                    reason_context,
                    re.IGNORECASE,
                )
                is None
            )
        )
        if reason_needs_fallback:
            if (
                re.search(r"\bsection 2\s*\(a\)", reason_context, re.IGNORECASE)
                and re.search(r"\bimposes no obligations\b", reason_context, re.IGNORECASE)
                and re.search(r"\bconcrete harm\b", reason_context, re.IGNORECASE)
            ):
                replacement_paragraphs["Why the Court did it"] = (
                    "For Section 2(a), the Court reasoned that the provision imposes "
                    "no obligations on the States. The States therefore suffer no "
                    "concrete harm from that provision.",
                )
            else:
                ordered_reasons = sorted(
                    reason_support,
                    key=lambda claim: (
                        bool(
                            re.search(
                                r"\b(?:jurisdiction|ripeness|standing)\b",
                                claim.public_value,
                                re.IGNORECASE,
                            )
                        ),
                        bool(
                            re.search(
                                r"\b(?:concrete|harm|injury)\b",
                                claim.public_value,
                                re.IGNORECASE,
                            )
                        ),
                        -len(claim.public_value),
                    ),
                    reverse=True,
                )
                replacement_paragraphs["Why the Court did it"] = (
                    " ".join(
                        _unquoted_claim_fallback(claim.public_value)
                        for claim in ordered_reasons[:2]
                    ),
                )
    sections = tuple(
        section.model_copy(
            update={
                "paragraphs": replacement_paragraphs.get(
                    section.heading.strip(), section.paragraphs
                ),
                "claim_ids": support_by_heading.get(section.heading.strip(), section.claim_ids),
            }
        )
        for section in draft.sections
        if not (
            section.heading.strip() == DISPOSITION_SEPARATE_OPINIONS_HEADING
            and not set(section.claim_ids).issubset(separate_support_ids)
        )
    )
    return draft.model_copy(
        update={
            "title_claim_ids": docket_ids or draft.title_claim_ids,
            "dek_claim_ids": tuple(claim.claim_id for claim in controlling),
            "sections": sections,
        }
    )


def disposition_only_brief_json_schema() -> dict[str, Any]:
    """Return the strict citizen-guide schema for a case with no real argument."""
    schema = simple_brief_json_schema(0)
    sections = schema["properties"]["sections"]
    sections["minItems"] = len(DISPOSITION_GUIDE_HEADINGS)
    sections["maxItems"] = len(DISPOSITION_GUIDE_HEADINGS) + 1
    sections["items"]["properties"]["heading"] = {
        "type": "string",
        "enum": [
            *DISPOSITION_GUIDE_HEADINGS,
            DISPOSITION_SEPARATE_OPINIONS_HEADING,
        ],
    }
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
    PROMPT_VERSION = "scotus-brief-plain-language-v32"
    DISPOSITION_PROMPT_VERSION = "scotus-disposition-citizen-guide-v14"

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
        correction_draft: LegalBriefDraft | None = None,
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
        self.correction_draft = correction_draft
        self.request_executor = request_executor

    def generate(
        self,
        candidate: BriefCandidate,
        claims: tuple[ScotusApprovedClaim, ...],
        maturity: BriefMaturity,
    ) -> LegalBriefDraft:
        sessions = {session.argument_id: session for session in candidate.argument_sessions}
        disposition_only = not candidate.argument_sessions
        # A citizen guide needs the complete typed claim ledger. Requested relief,
        # lower-court action, and Supreme Court action remain distinct through their
        # legal statuses and the role-aware validator; hiding them produced fragmentary
        # disposition pages that could not explain the procedural path or operative relief.
        model_claims = claims
        ledger = [
            {
                "claim_id": str(claim.claim_id),
                "type": claim.observation_type.value,
                "status": claim.legal_status.value,
                "certainty": claim.certainty.value,
                "attribution": claim.attribution,
                "value": claim.public_value,
                "source": claim.public_source_label,
                "page": claim.page_label,
                **(
                    {
                        "argument_session": (
                            {
                                "argument_id": str(claim.argument_id),
                                "date": (
                                    sessions[claim.argument_id].argument_date.date().isoformat()
                                ),
                                "sequence": sessions[claim.argument_id].sequence,
                                "reargument": sessions[claim.argument_id].reargument,
                            }
                            if claim.argument_id in sessions
                            else None
                        ),
                        "position_group": _position_label(claim.attribution),
                    }
                    if not disposition_only
                    else {}
                ),
            }
            for claim in model_claims
        ]
        disposition_section_plan = (
            _disposition_support_by_heading(claims) if disposition_only else {}
        )
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
        format_instruction = ""
        if not self.strict_json_schema:
            format_instruction = (
                " Return one raw JSON object with no Markdown or code fences. The object must "
                "have exactly these six keys: title, title_claim_ids, dek, dek_claim_ids, "
                "sections, and argument_analyses. Every section must have exactly heading, "
                "paragraphs, and claim_ids. "
                + (
                    "Set argument_analyses to an empty array. Return the five required citizen-"
                    "guide sections, plus the optional separate-opinions section when supported, "
                    "with one short paragraph each."
                    if disposition_only
                    else (
                        "Every argument analysis must have exactly argument_id, heading, "
                        "paragraphs, and claim_ids. Use five to seven sections with one short "
                        "paragraph each. Use exactly two short paragraphs per argument analysis."
                    )
                )
            )
        feedback_instruction = (
            " Prior independent drafts failed these colon-separated fixed validator codes: '"
            + self.validation_feedback_code
            + "'. Produce a fresh draft that satisfies every listed rule; keep validation "
            "details out of public prose."
            if self.validation_feedback_code
            else ""
        )
        feedback_code = self.validation_feedback_code or ""
        if disposition_only and "ungrounded_guide_section_" in feedback_code:
            feedback_instruction += (
                " For each ungrounded section, preserve the case-specific people, event, action, "
                "and result from a role-appropriate cited claim, but translate its legal wording."
            )
        if "unexplained_legal_term_" in feedback_code:
            feedback_instruction += (
                " Replace the named legal term with ordinary words. If accuracy requires the "
                "term, define it immediately by saying what it does in this case."
            )
        if disposition_only and "ambiguous_separate_opinion_attribution" in feedback_code:
            feedback_instruction += (
                " In the separate-opinions section, name the opinion author in every sentence "
                "and distinguish the author's own view from any description of the Court."
            )
        if disposition_only and feedback_code in {
            "unsupported_lower_court_action",
            "unsupported_requested_action",
            "unsupported_supreme_court_action",
        }:
            feedback_instruction += (
                " In the affected action sentence, preserve the supported actor, result, and "
                "operative object from the matching typed claim, using an ordinary-language verb."
            )
        if disposition_only and self.correction_draft is not None:
            feedback_instruction += (
                " Preserve the valid prior_draft sections and rewrite only the section named by "
                "the validator code."
            )
        disposition_prompt = (
            "/no_think\nBuild a complete plain-English citizen's guide to this Supreme Court "
            "case using only the supplied approved claims. Set the title to exactly the official "
            "caption. Explain the subject, procedural path, legal issue, operative Supreme Court "
            "action, its immediate effect, and the Court's supported reasoning. Distinguish what "
            "a party requested, what a lower court did, and what the Supreme Court did. Describe "
            "an emergency stay as interim relief, not a final merits judgment, when the claims "
            "support that distinction. Follow section_plan exactly. Return these sections: 'What "
            "this case is about', 'Why this case reached the Court', 'The legal issue', 'What the "
            "Supreme Court did', and 'Why the Court did it'. Add 'What separate opinions said' "
            "as a sixth section only when explicitly attributed dissent or concurrence claims "
            "support it. Never use a separate-opinion claim as the Court's action, legal issue, "
            "or reasoning. Use one short paragraph per section. Copy supporting claim IDs into "
            "the title, dek, and each paragraph's claim_ids array; cite only claims that answer "
            "that section. Every action sentence must name its party, lower court, or Supreme "
            "Court actor; never use only a pronoun. Ground each sentence in the same case-specific "
            "people, event, action, or result as its cited support. Your main job is translation, "
            "not compression: do not copy the Court's legal register. Prefer 'right to bring the "
            "case' to 'standing', 'power to hear the case' to 'jurisdiction', and 'sent the case "
            "back' to 'remanded'. If a precise legal term is essential, use it once and "
            "immediately say what it means here. A reader must not need a law dictionary. "
            f"Keep each sentence at or below {self.maximum_sentence_words} words and each "
            f"paragraph at or below {self.maximum_paragraph_words} words. Use a name only in "
            "the exact form found in a cited claim. Paraphrase instead of quoting, put citations "
            "only in claim_ids arrays, omit unsupported details, and return no argument analyses."
            + feedback_instruction
            + format_instruction
        )
        case_mode_instruction = (
            "Produce one argument analysis for every supplied argument session, in order. "
            "Explain what each side was asking the Court to do, the reasoning each side "
            "offered, what assumptions the justices tested, and what a later reargument "
            "changed or revisited when supported. "
        )
        section_instruction = (
            "Return five sections with one paragraph each and exactly two short "
            "paragraphs for each supplied argument session. "
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
                    "content": disposition_prompt
                    if disposition_only
                    else (
                        "/no_think\nExplain this Supreme Court case to a curious reader with no "
                        "legal training. Use only the approved claim ledger and cite claim IDs for "
                        "every title, summary, and paragraph. Begin the title with the supplied "
                        "official case caption; never use a generic heading as the title. Put "
                        "citations only in the matching "
                        "claim_ids arrays, never in public prose. Use direct everyday language, "
                        "active voice, concrete explanations, and short paragraphs. Your main job "
                        "is to translate the source, not shorten it while keeping lawyer language. "
                        "Prefer 'right to bring the case' to 'standing', 'power to hear the case' "
                        "to 'jurisdiction', and 'sent the case back' to 'remanded'. Avoid doctrine "
                        "names and courtroom shorthand when ordinary words are accurate. If a "
                        "precise legal term is essential, use it once and immediately explain what "
                        "it changes for the people or government in this case. A reader must not "
                        "need a law dictionary. "
                        + section_instruction
                        + f"Keep every sentence at or below {self.maximum_sentence_words} words "
                        "and every "
                        f"paragraph at or below {self.maximum_paragraph_words} words. Do not "
                        "write like a court filing or law-school outline. Avoid labels such as "
                        "petitioner and respondent when a party name or plain description works. "
                        "Prefer headings such as 'What this case is about', 'How "
                        "the case got here', 'What the Court did', 'Why it matters', and 'What "
                        "happens next'. " + case_mode_instruction + "When the ledger has a "
                        "question presented, procedural posture, advocate contention, or justice "
                        "question, the output must use at least one claim of each available type. "
                        "Copy every claim ID exactly from the supplied ledger. Cite only claims "
                        "whose public values support the associated text, but do not mirror their "
                        "legal phrasing merely to show support. In each argument "
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
                        "claim's public value. Translate and paraphrase the evidence; do not use "
                        "quotation marks or direct quotations anywhere in the output. A question "
                        "is not a holding or vote. Describe a requested result as what a side asks "
                        "the "
                        "Court to do, never as something the Court already did. Identify a "
                        "lower-court result explicitly as the lower court's action. Never infer "
                        "that no ruling exists merely because no disposition is supplied. When "
                        "final-action claims are absent, say only that the article currently "
                        "covers the argument record and link readers to the official docket for "
                        "later activity. Never predict the outcome, score "
                        "ideology or tone, or give personalized legal advice."
                        + feedback_instruction
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
                            **(
                                {"prior_draft": self.correction_draft.model_dump(mode="json")}
                                if disposition_only and self.correction_draft is not None
                                else {}
                            ),
                            **(
                                {
                                    "argument_sessions": [
                                        {
                                            "argument_id": str(session.argument_id),
                                            "date": session.argument_date.date().isoformat(),
                                            "sequence": session.sequence,
                                            "reargument": session.reargument,
                                        }
                                        for session in candidate.argument_sessions
                                    ]
                                }
                                if not disposition_only
                                else {}
                            ),
                            **(
                                {
                                    "section_plan": [
                                        {
                                            "heading": heading,
                                            "claims": [
                                                item
                                                for item in ledger
                                                if UUID(str(item["claim_id"])) in claim_ids
                                            ],
                                        }
                                        for heading, claim_ids in disposition_section_plan.items()
                                        if claim_ids
                                    ]
                                }
                                if disposition_only
                                else {"claims": ledger}
                            ),
                            **(
                                {
                                    "required_output_schema": (
                                        disposition_only_brief_json_schema()
                                        if disposition_only
                                        else LegalBriefDraft.model_json_schema()
                                    )
                                }
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
                payload = _normalize_private_schema_payload(
                    json.loads(stripped), candidate, model_claims
                )
                draft = _plain_language_draft(LegalBriefDraft.model_validate(payload))
            else:
                draft = _plain_language_draft(LegalBriefDraft.model_validate_json(stripped))
            if disposition_only:
                draft = _normalize_disposition_support(draft, model_claims)
                feedback_code = self.validation_feedback_code or ""
                target_heading = next(
                    (
                        heading
                        for heading in DISPOSITION_GUIDE_HEADINGS
                        if (
                            "ungrounded_guide_section_"
                            + re.sub(r"[^a-z0-9]+", "_", heading.casefold()).strip("_")
                        )
                        in feedback_code
                    ),
                    None,
                )
                if target_heading is None:
                    target_heading = {
                        "unsupported_lower_court_action": "Why this case reached the Court",
                        "unsupported_requested_action": "Why this case reached the Court",
                        "unsupported_supreme_court_action": "What the Supreme Court did",
                    }.get(feedback_code)
                if self.correction_draft is not None and target_heading is not None:
                    previous_by_heading = {
                        section.heading.strip(): section
                        for section in self.correction_draft.sections
                    }
                    fresh_by_heading = {
                        section.heading.strip(): section for section in draft.sections
                    }
                    draft = self.correction_draft.model_copy(
                        update={
                            "sections": tuple(
                                fresh_by_heading[heading]
                                if heading == target_heading
                                else previous_by_heading[heading]
                                for heading in DISPOSITION_GUIDE_HEADINGS
                            )
                        }
                    )
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
        if disposition_only:
            docket_claim = next(
                (
                    claim
                    for claim in claims
                    if claim.legal_status is LegalStatus.DESCRIBED
                    and (
                        candidate.primary_docket.casefold() in claim.public_value.casefold()
                        or "/docket/" in claim.official_url.casefold()
                    )
                ),
                None,
            )
            if docket_claim is None:
                raise BriefPolicyError(
                    "disposition lacks deterministic docket support",
                    safe_code="missing_docket",
                )
            # Official identity is metadata, not model discretion. The model still owns
            # the citizen-facing summary and sections, which are validated below.
            draft = draft.model_copy(
                update={
                    "title": candidate.caption,
                    "title_claim_ids": (docket_claim.claim_id,),
                    "argument_analyses": (),
                }
            )
        elif draft.title.strip().casefold() in {
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
    r"\bwill\b[^.!?]{0,40}\b(?:vote|win|lose)|"
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
_INTERNAL_CLAIM_MARKER = re.compile(
    r"\s*\[[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\]",
    re.IGNORECASE,
)
_CITATION = re.compile(r"\b\d+\s+U\.S\.\s+\d+\b")
_DOCKET = re.compile(r"\b\d{1,3}A?-\d+[A-Z]*\b", re.IGNORECASE)
_WORD = re.compile(r"\b[\w\u2019'-]+\b")
_SENTENCE = re.compile(r"[^.!?]+[.!?]?", re.MULTILINE)
_READER_PROSE_POLICY = load_reader_prose_policy()
_READER_LEGAL_TERMS = tuple(
    (
        term.label,
        re.compile("|".join(f"(?:{pattern})" for pattern in term.patterns), re.IGNORECASE),
        re.compile(
            "|".join(f"(?:{pattern})" for pattern in term.explanation_patterns),
            re.IGNORECASE,
        ),
    )
    for term in _READER_PROSE_POLICY.terms
)
_FORBIDDEN_READER_PHRASES = tuple(
    (phrase.label, re.compile(phrase.pattern, re.IGNORECASE))
    for phrase in _READER_PROSE_POLICY.forbidden_phrases
)
_PROCESS_LANGUAGE = re.compile(
    "|".join(f"(?:{pattern})" for pattern in _READER_PROSE_POLICY.process_patterns),
    re.IGNORECASE,
)
_PLAIN_LANGUAGE_REPLACEMENTS = tuple(
    (re.compile(phrase.pattern, re.IGNORECASE), phrase.ordinary_alternative)
    for phrase in _READER_PROSE_POLICY.forbidden_phrases
)
_ADVOCATE_NAME = re.compile(r"^\s*(Mr|Ms|General)\.?\s+([A-Za-z'\u2019\N{EN DASH}-]+)", re.I)
_UNSUPPORTED_NO_DISPOSITION = re.compile(
    r"\b(?:the (?:Supreme )?Court has not (?:yet )?(?:decided|ruled|issued)|"
    r"the (?:Supreme )?Court (?:has|had) yet to (?:decide|rule|issue)|"
    r"no (?:decision|ruling|opinion|order) (?:has been|was) (?:issued|entered)|"
    r"no (?:decision|ruling|opinion|order) (?:exists|is available)|"
    r"there (?:is|was) no (?:decision|ruling|opinion|order))\b",
    re.IGNORECASE,
)
_FUTURE_EVENT = re.compile(
    r"\b(?:will|would|is going to|are going to)\b[^.!?]{0,55}"
    r"\b(?:decid(?:e|es)|clarif(?:y|ies)|establish(?:es)?|guid(?:e|es)|"
    r"affect(?:s)?|chang(?:e|es)|rule|hold|determine|resolve|do next)\b",
    re.IGNORECASE,
)
_FUTURE_VERB = re.compile(
    r"\b(?:decid(?:e|es)|clarif(?:y|ies)|establish(?:es)?|guid(?:e|es)|"
    r"affect(?:s)?|chang(?:e|es)|rule|hold|determine|resolve|do next)\b",
    re.IGNORECASE,
)
_LOWER_COURT_ACTOR = re.compile(
    r"\b(?:(?:lower|appeals|appellate|trial|district|circuit|state supreme) court|"
    r"court of appeals)\b",
    re.IGNORECASE,
)
_SUPREME_COURT_ACTOR = re.compile(
    r"\b(?:the Court(?! of Appeals)|the Supreme Court|Supreme Court of the United States)\b",
    re.IGNORECASE,
)
_EXPLICIT_NEGATED_ORAL_ARGUMENT = re.compile(
    r"\bwithout (?:an? )?oral arguments?\b|"
    r"\bno oral arguments? (?:occurred|took place|was held)\b|"
    r"\boral arguments? (?:did not occur|never occurred|was not held)\b",
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
    r"(?:details?|information|records?) (?:are|is) (?:not available|unavailable|unknown)|"
    r"more details? may (?:emerge|follow)|information is unavailable)\b",
    re.IGNORECASE,
)
_NAMED_PHRASE = re.compile(
    r"\b[A-Z][A-Za-z&.'\N{RIGHT SINGLE QUOTATION MARK}-]+"
    r"(?:\s+[A-Z][A-Za-z&.'\N{RIGHT SINGLE QUOTATION MARK}-]+)+\b"
)
_CAPITALIZED_WORD = re.compile(r"\b[A-Z][A-Za-z'\N{RIGHT SINGLE QUOTATION MARK}-]{2,}\b")
_ENTITY_SUFFIXES = {
    "association",
    "committee",
    "company",
    "corporation",
    "inc",
    "llc",
    "organization",
}
_CAPITALIZED_EXEMPT = {
    "a",
    "an",
    "administration",
    "agency",
    "appeal",
    "applicant",
    "application",
    "article",
    "branch",
    "circuit",
    "claims",
    "committee",
    "constitution",
    "court",
    "district",
    "docket",
    "elections",
    "executive",
    "federal",
    "government",
    "governments",
    "how",
    "iii",
    "injunction",
    "law",
    "lower",
    "official",
    "officials",
    "order",
    "policy",
    "president",
    "section",
    "service",
    "state",
    "states",
    "supreme",
    "the",
    "this",
    "trust",
    "what",
    "why",
}
_ACTION_EQUIVALENTS = tuple(
    (
        equivalent.canonical,
        re.compile(equivalent.pattern, re.IGNORECASE),
    )
    for equivalent in _READER_PROSE_POLICY.action_equivalents
)
_ACTION_WORD = re.compile(
    "|".join(f"(?:{equivalent.pattern})" for equivalent in _READER_PROSE_POLICY.action_equivalents),
    re.IGNORECASE,
)
_REQUESTED_ACTION_ROLE = re.compile(
    r"\b(?:ask(?:s|ed|ing)?|request(?:s|ed|ing)?|urge(?:s|d|ing)?|seek(?:s|ing)?|"
    r"sought|want(?:s|ed|ing)?|should)\b[^.!?]{0,100}(?:"
    + "|".join(
        f"(?:{equivalent.pattern})" for equivalent in _READER_PROSE_POLICY.action_equivalents
    )
    + ")",
    re.IGNORECASE,
)


def _canonical_action(value: str) -> str | None:
    normalized = value.strip()
    return next(
        (canonical for canonical, pattern in _ACTION_EQUIVALENTS if pattern.fullmatch(normalized)),
        None,
    )


def _action_signatures(value: str) -> set[tuple[str, bool]]:
    signatures: set[tuple[str, bool]] = set()
    for match in _ACTION_WORD.finditer(value):
        action = _canonical_action(match.group(0))
        if action is None:
            continue
        if action == "order":
            # "Ordered" wraps the operative granted/denied/stayed action and is
            # not independently contradictory.
            continue
        prefix = value[max(0, match.start() - 35) : match.start()]
        negated = re.search(r"\b(?:not|never|did not|does not)\b[^.!?]{0,24}$", prefix, re.I)
        signatures.add((action, bool(negated)))
    return signatures


_ActionRole = Literal["requested", "lower_court", "supreme_court"]
_ACTION_ROLE_STATUSES: dict[_ActionRole, frozenset[LegalStatus]] = {
    "requested": frozenset({LegalStatus.REQUESTED}),
    "lower_court": frozenset({LegalStatus.LOWER_COURT_HELD}),
    "supreme_court": frozenset({LegalStatus.COURT_HELD, LegalStatus.COURT_ORDERED}),
}
_ACTION_ROLE_CODES: dict[_ActionRole, str] = {
    "requested": "unsupported_requested_action",
    "lower_court": "unsupported_lower_court_action",
    "supreme_court": "unsupported_court_action",
}


def _action_role(sentence: str) -> _ActionRole | None:
    if sentence.strip().casefold().startswith("the supreme court action states:"):
        # This prefix is emitted only by deterministic composition from one approved
        # COURT_HELD/COURT_ORDERED claim; lower-court names may occur in its object.
        return "supreme_court"
    if _REQUESTED_ACTION_ROLE.search(sentence):
        # A request normally names the Supreme Court as the recipient. The requesting
        # party remains the actor whose proposed action must be checked.
        return "requested"
    action = _ACTION_WORD.search(sentence)
    if action is None:
        return None
    lower_match = _LOWER_COURT_ACTOR.search(sentence)
    supreme_match = _SUPREME_COURT_ACTOR.search(sentence)
    actors_before_action: list[tuple[int, _ActionRole]] = []
    if lower_match is not None and lower_match.start() < action.start():
        actors_before_action.append((lower_match.start(), "lower_court"))
    if supreme_match is not None and supreme_match.start() < action.start():
        actors_before_action.append((supreme_match.start(), "supreme_court"))
    if actors_before_action:
        return max(actors_before_action)[1]
    lower_court = lower_match is not None
    supreme_court = supreme_match is not None
    if lower_court == supreme_court:
        return None
    return "lower_court" if lower_court else "supreme_court"


def _validate_action_sentences(
    text: str, supporting_claims: tuple[ScotusApprovedClaim, ...]
) -> None:
    for match in _SENTENCE.finditer(text):
        sentence = match.group(0)
        if not _ACTION_WORD.search(sentence):
            continue
        role = _action_role(sentence)
        if role is None:
            raise BriefValidationError(
                "action sentence does not identify one supported actor role",
                safe_code="unsupported_action_role",
            )
        role_support = " ".join(
            claim.public_value
            for claim in supporting_claims
            if claim.legal_status in _ACTION_ROLE_STATUSES[role]
        )
        stated = _action_signatures(sentence)
        supported = _action_signatures(role_support)
        stated_objects = _action_object_pairs(sentence)
        supported_objects = _action_object_pairs(role_support)
        supported_by_action = {
            action: {
                item_object
                for item_action, item_object in supported_objects
                if item_action == action
            }
            for action, _ in stated
        }
        changes_object = any(
            available
            and (
                not {
                    item_object
                    for item_action, item_object in stated_objects
                    if item_action == action
                }
                or not {
                    item_object
                    for item_action, item_object in stated_objects
                    if item_action == action
                }.issubset(available)
            )
            for action, available in supported_by_action.items()
        )
        omits_interim_effect = (
            any(action == "stay" for action, _ in stated)
            and bool(_INTERIM_EFFECT.search(role_support))
            and not bool(
                _INTERIM_EFFECT.search(sentence) or re.search(r"\bpaus(?:e|ed)\b", sentence, re.I)
            )
        )
        if omits_interim_effect:
            raise BriefValidationError(
                "stay summary omits its interim procedural effect",
                safe_code="incomplete_interim_stay_effect",
            )
        if changes_object:
            raise BriefValidationError(
                "action sentence changes its supported operative object",
                safe_code=(
                    "unsupported_supreme_court_action_object"
                    if role == "supreme_court"
                    else _ACTION_ROLE_CODES[role]
                ),
            )
        if not role_support or not stated.issubset(supported):
            message = {
                "requested": "text changes or invents the requested action",
                "lower_court": "text changes or invents the lower-court action",
                "supreme_court": "text overstates final Court action",
            }[role]
            raise BriefValidationError(message, safe_code=_ACTION_ROLE_CODES[role])


def _supported_acronyms(value: str) -> set[str]:
    stop_words = {"and", "for", "in", "of", "the", "to", "v"}
    acronyms: set[str] = set()
    for phrase in _NAMED_PHRASE.findall(value):
        words = re.findall(r"[A-Za-z]+", phrase)
        acronym = "".join(word[0] for word in words if word.casefold() not in stop_words).casefold()
        if len(acronym) >= 2:
            acronyms.add(acronym)
    return acronyms


def _unsupported_named_phrase(text: str, support: str, caption: str) -> bool:
    allowed_value = f"{support} {caption}"
    allowed = allowed_value.casefold().replace("\u2019", "'")

    def canonical_word(value: str) -> str:
        lowered = value.casefold().replace("\u2019", "'")
        return lowered.removesuffix("'s")

    allowed_acronyms = _supported_acronyms(allowed_value)
    generic_prefixes = (
        "what ",
        "how ",
        "why ",
        "official ",
        "supreme court",
        "the court",
        "the supreme court",
    )
    for match in _NAMED_PHRASE.findall(text):
        lowered = match.casefold()
        if lowered.startswith(generic_prefixes):
            continue
        phrase_words = re.findall(r"[A-Za-z]+", match)
        unknown_words = tuple(
            word
            for word in phrase_words
            if canonical_word(word) not in _CAPITALIZED_EXEMPT
            and canonical_word(word) not in allowed
            and canonical_word(word) != "s"
            and not (word.isupper() and canonical_word(word) in allowed_acronyms)
        )
        identity_words = tuple(
            word
            for word in phrase_words
            if (canonical_word(word) not in _CAPITALIZED_EXEMPT and canonical_word(word) in allowed)
            or (word.isupper() and canonical_word(word) in allowed_acronyms)
        )
        if not unknown_words or identity_words or lowered in allowed:
            continue
        if any(
            (word.isupper() and len(word) > 1) or canonical_word(word) in _ENTITY_SUFFIXES
            for word in unknown_words
        ):
            return True
    return False


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
            (" because ", "Because "),
            (" while ", "While "),
            (" and ", "And "),
            (", ", ""),
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
    result = re.sub(
        r"-\s+(?=(?:ding|ing|ed|tion|ment|ly|able|ible|ous|ive|al|ity|ies|er|est)\b)",
        "",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(r"\bthe\s+the\s+", "the ", result, flags=re.IGNORECASE)
    for pattern, replacement in _PLAIN_LANGUAGE_REPLACEMENTS:

        def preserve_initial_case(match: re.Match[str], value: str = replacement) -> str:
            return value[:1].upper() + value[1:] if match.group(0)[:1].isupper() else value

        result = pattern.sub(preserve_initial_case, result)
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
                            if (value := _plain_language_text(paragraph)).strip()
                        )[:6],
                    }
                )
                for analysis in draft.argument_analyses
            ),
        }
    )


def _syllable_count(word: str) -> int:
    value = re.sub(r"[^a-z]", "", word.casefold())
    if not value:
        return 0
    groups = len(re.findall(r"[aeiouy]+", value))
    if len(value) > 3 and value.endswith("e") and not value.endswith(("le", "ye")):
        groups -= 1
    return max(1, groups)


def _readability_grade(text: str) -> float:
    words = _WORD.findall(text)
    sentences = tuple(
        match.group(0) for match in _SENTENCE.finditer(text) if _WORD.search(match.group(0))
    )
    if not words or not sentences:
        return 0.0
    syllables = sum(_syllable_count(word) for word in words)
    return 0.39 * (len(words) / len(sentences)) + 11.8 * (syllables / len(words)) - 15.59


def _validate_plain_language(
    text: str,
    *,
    maximum_sentence_words: int,
    maximum_paragraph_words: int,
    allow_term_explanations: bool = True,
    check_terminology: bool = True,
) -> None:
    words = _WORD.findall(text)
    if len(words) > maximum_paragraph_words:
        raise BriefValidationError("plain-language paragraph is too long")
    sentences = tuple(
        match.group(0).strip() for match in _SENTENCE.finditer(text) if match.group(0).strip()
    )
    if any(len(_WORD.findall(sentence)) > maximum_sentence_words for sentence in sentences):
        raise BriefValidationError("plain-language sentence is too long")
    normalized_sentences = tuple(
        " ".join(_WORD.findall(sentence.casefold())) for sentence in sentences
    )
    if len(normalized_sentences) != len(set(normalized_sentences)):
        raise BriefValidationError(
            "plain-language paragraph repeats a sentence",
            safe_code="repeated_reader_prose",
        )
    # Very short labels do not produce a meaningful grade. The bound is deliberately
    # generous enough for case-specific names while rejecting law-review-style prose.
    if len(words) >= 8 and _readability_grade(text) > 20.0:
        raise BriefValidationError(
            "reader prose exceeds the deterministic readability bound",
            safe_code="reader_prose_readability",
        )
    if not check_terminology:
        return
    for label, pattern in _FORBIDDEN_READER_PHRASES:
        if pattern.search(text):
            raise BriefValidationError(
                f"brief contains unexplained legalese phrase: {label.replace('_', ' ')}",
                safe_code=f"unexplained_legalese_{label}",
            )
    for label, term, explanation in _READER_LEGAL_TERMS:
        if any(
            term.search(sentence)
            and (not allow_term_explanations or not explanation.search(sentence))
            for sentence in sentences
        ):
            raise BriefValidationError(
                f"brief contains unexplained legal concept: {label.replace('_', ' ')}",
                safe_code=f"unexplained_legal_term_{label}",
            )


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
    minimum_observations = 3 if candidate.argument_sessions else 2
    if len(eligible_observations) < minimum_observations:
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
        if not any(
            item.observation_type is LegalObservationType.CASE_BACKGROUND
            and not _is_separate_opinion_material(
                item.attribution,
                item.normalized_value_private or item.raw_value_private,
            )
            for item in eligible_observations
        ):
            reasons.append("disposition-only case lacks case background")
        if not any(
            item.observation_type
            in {LegalObservationType.REQUESTED_DISPOSITION, LegalObservationType.LOWER_COURT_ACTION}
            and not _is_separate_opinion_material(
                item.attribution,
                item.normalized_value_private or item.raw_value_private,
            )
            for item in eligible_observations
        ):
            reasons.append("disposition-only case lacks procedural path")
        if not any(
            item.observation_type
            in {LegalObservationType.QUESTION_PRESENTED, LegalObservationType.DOCTRINAL_THEME}
            and not _is_separate_opinion_material(
                item.attribution,
                item.normalized_value_private or item.raw_value_private,
            )
            for item in eligible_observations
        ):
            reasons.append("disposition-only case lacks controlling legal issue")
        controlling_analysis = tuple(
            item
            for item in eligible_observations
            if item.observation_type
            in {LegalObservationType.QUESTION_PRESENTED, LegalObservationType.DOCTRINAL_THEME}
            and not _is_separate_opinion_material(
                item.attribution,
                item.normalized_value_private or item.raw_value_private,
            )
        )
        reasoning = tuple(
            item
            for item in controlling_analysis
            if item.observation_type is LegalObservationType.DOCTRINAL_THEME
        )
        if not any(
            reason.observation_id != issue.observation_id
            and (reason.normalized_value_private or reason.raw_value_private).casefold()
            != (issue.normalized_value_private or issue.raw_value_private).casefold()
            for reason in reasoning
            for issue in controlling_analysis
        ):
            reasons.append("disposition-only case lacks independent Court reasoning")
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
    minimum_claims = 3 if candidate.argument_sessions else 2
    if len(claims) < minimum_claims:
        return BriefPolicyDecision(
            False,
            ("insufficient claims after sensitivity minimization",),
            (),
            None,
        )
    return BriefPolicyDecision(True, (), tuple(claims), _maturity(candidate))


def _unsupported_future_event(text: str, support: str) -> bool:
    for sentence_match in _SENTENCE.finditer(text):
        sentence = sentence_match.group(0)
        if not _FUTURE_EVENT.search(sentence):
            continue
        stated_verbs = {match.group(0).casefold() for match in _FUTURE_VERB.finditer(sentence)}
        supported = any(
            _FUTURE_EVENT.search(support_sentence.group(0))
            and stated_verbs.intersection(
                match.group(0).casefold()
                for match in _FUTURE_VERB.finditer(support_sentence.group(0))
            )
            for support_sentence in _SENTENCE.finditer(support)
        )
        if not supported:
            return True
    return False


def _validate_public_text(
    text: str,
    claim_ids: tuple[UUID, ...],
    candidate: BriefCandidate,
    claim_map: dict[UUID, ScotusApprovedClaim],
    *,
    public_quotes: bool,
    validation_context: str = "text",
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
    if _UNSUPPORTED_NO_DISPOSITION.search(text) and not _UNSUPPORTED_NO_DISPOSITION.search(support):
        raise BriefValidationError(
            "brief infers no disposition from an incomplete record",
            safe_code="unsupported_no_decision",
        )
    if _unsupported_future_event(text, support):
        raise BriefValidationError(
            "brief makes an unsupported statement about a future event",
            safe_code="unsupported_future_event",
        )
    if _TONE_OR_IDEOLOGY.search(text):
        raise BriefValidationError("tone, sentiment, or ideological scoring is prohibited")
    if _LEGAL_ADVICE.search(text):
        raise BriefValidationError("personalized legal advice is prohibited")
    speculative_terms = {
        match.group(0).casefold() for match in _UNSUPPORTED_SPECULATION.finditer(text)
    }
    speculation_support = support
    if not candidate.argument_sessions:
        speculation_support = " ".join(claim.public_value for claim in claim_map.values())
    if any(term not in speculation_support.casefold() for term in speculative_terms):
        raise BriefValidationError(
            "unsupported speculative language is prohibited",
            safe_code="unsupported_speculation",
        )
    if not public_quotes and _QUOTATION.search(text):
        raise BriefValidationError("public transcript quotations are disabled")
    if _INTERNAL_CLAIM_MARKER.search(text):
        raise BriefValidationError("public prose contains an internal claim marker")
    if _PROCESS_LANGUAGE.search(text):
        raise BriefValidationError(
            "public prose contains internal processing language or model or schema instructions",
            safe_code="internal_process_language",
        )
    if _UNSUPPORTED_FILLER.search(text):
        raise BriefValidationError(
            "brief contains an unsupported absence or filler statement",
            safe_code="unsupported_filler",
        )
    action_text = text
    if not candidate.argument_sessions:
        action_text = _EXPLICIT_NEGATED_ORAL_ARGUMENT.sub("", text)
        if _INVENTED_ORAL_ARGUMENT.search(action_text):
            raise BriefValidationError(
                "disposition-only brief invents oral argument",
                safe_code="invented_oral_argument",
            )
        case_name_support = " ".join(claim.public_value for claim in claim_map.values())
        if _unsupported_named_phrase(action_text, case_name_support, candidate.caption):
            raise BriefValidationError(
                "disposition-only brief adds an unsupported party",
                safe_code=f"unsupported_party_{validation_context}",
            )
    if re.search(r"\bthe\s+the\b", text, re.I):
        raise BriefValidationError("public prose contains a repeated article")
    for citation in _CITATION.findall(text):
        if citation not in support:
            raise BriefValidationError("text adds an unsupported citation")
    for docket in _DOCKET.findall(text):
        if docket != candidate.primary_docket and docket not in support:
            raise BriefValidationError("text adds an unsupported docket")
    supporting_claims = tuple(claim_map[value] for value in claim_ids)
    if candidate.argument_sessions and validation_context in {
        "dek",
        "section_paragraph",
        "argument_paragraph",
    }:
        _validate_action_sentences(action_text, supporting_claims)
    exact_official_caption = (
        validation_context == "title"
        and not candidate.argument_sessions
        and text == candidate.caption
    )
    _validate_plain_language(
        text,
        maximum_sentence_words=maximum_sentence_words,
        maximum_paragraph_words=maximum_paragraph_words,
        allow_term_explanations=validation_context
        not in {"title", "section_heading", "argument_heading"},
        check_terminology=not exact_official_caption,
    )


_SEPARATE_OPINION_ATTRIBUTION = re.compile(
    r"^(?:Justice\s+[^,]+,\s*)?(?:dissenting|concurring)|^separate opinion\b",
    re.IGNORECASE,
)
_SEPARATE_OPINION_VALUE = re.compile(
    r"^(?:Justice\s+[^,]+(?:'s|\N{RIGHT SINGLE QUOTATION MARK}s)?\s+)?"
    r"(?:dissent|concurrence|separate opinion)\b|^The\s+(?:dissent|concurrence)\b",
    re.IGNORECASE,
)
_SEPARATE_SENTENCE_ATTRIBUTION = re.compile(
    r"\b(?:Chief\s+Justice|Justice)\s+[A-Z][A-Za-z'\N{RIGHT SINGLE QUOTATION MARK}-]+\b|"
    r"\b(?:the\s+)?(?:dissent|concurrence|separate opinion)\b",
    re.IGNORECASE,
)
_SEPARATE_CONTROLLING_REPORT = re.compile(
    r"\b(?:agreed|joined|majority|per curiam|the Court)\b",
    re.IGNORECASE,
)
_INTERIM_EFFECT = re.compile(
    r"\b(?:interim|temporary|temporarily|pending|while [^.!?]{0,50}\bappeal|until)\b",
    re.IGNORECASE,
)
_OPERATIVE_OBJECT_PATTERN = (
    r"application|appeal|case|decree|execution|injunction|judgment|mandate|order|petition|"
    r"prosecution|release|relief|removal|rule|stay"
)
_OPERATIVE_OBJECT = re.compile(rf"\b(?:{_OPERATIVE_OBJECT_PATTERN})\b", re.IGNORECASE)


def _is_separate_opinion_material(attribution: str | None, value: str) -> bool:
    return bool(
        (attribution and _SEPARATE_OPINION_ATTRIBUTION.search(attribution))
        or _SEPARATE_OPINION_VALUE.search(value)
    )


def _is_separate_opinion_claim(claim: ScotusApprovedClaim) -> bool:
    return _is_separate_opinion_material(claim.attribution, claim.public_value)


_GUIDE_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
_GUIDE_STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "also",
        "because",
        "before",
        "being",
        "case",
        "court",
        "from",
        "have",
        "into",
        "issue",
        "legal",
        "said",
        "that",
        "their",
        "there",
        "these",
        "they",
        "this",
        "those",
        "under",
        "what",
        "when",
        "where",
        "which",
        "while",
        "with",
        "would",
    }
)


def _canonical_action_object(value: str, match: re.Match[str]) -> str:
    item_object = match.group(0).casefold()
    context = value[max(0, match.start() - 30) : match.end() + 30]
    if item_object == "injunction" or (
        item_object == "order" and re.search(r"\b(?:block(?:ing|ed)?|lower court)\b", context, re.I)
    ):
        return "blocking_order"
    return item_object


def _action_object_pairs(value: str) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for action_match in _ACTION_WORD.finditer(value):
        action = _canonical_action(action_match.group(0))
        if action is None or action == "order":
            continue
        sentence_end = min(
            (position for mark in ".!?" if (position := value.find(mark, action_match.end())) >= 0),
            default=len(value),
        )
        following = _OPERATIVE_OBJECT.search(
            value,
            action_match.end(),
            min(sentence_end, action_match.end() + 60),
        )
        if following is not None:
            pairs.add((action, _canonical_action_object(value, following)))
            continue
        prefix_start = max(0, action_match.start() - 60)
        prefix = value[prefix_start : action_match.start()]
        if re.search(
            r"\b(?:is|was|were|be|been|have|had|get|got)\b(?:\s+\w+){0,3}\s*$",
            prefix,
            re.IGNORECASE,
        ):
            preceding = tuple(_OPERATIVE_OBJECT.finditer(prefix))
            if preceding:
                pairs.add((action, _canonical_action_object(value, preceding[-1])))
        elif action == "remand" and re.search(r"\bcase\b", action_match.group(0), re.I):
            pairs.add((action, "case"))
    return pairs


def _guide_content_words(value: str) -> set[str]:
    words: set[str] = set()
    for match in _GUIDE_WORD.finditer(value):
        word = match.group(0).casefold().strip("'-")
        if len(word) < 4 or word in _GUIDE_STOP_WORDS:
            continue
        canonical = _canonical_action(word) or word
        words.add(_GUIDE_CANONICAL.get(canonical, canonical))
    return words


_GUIDE_CANONICAL = {
    "allowed": "grant",
    "asked": "request",
    "asks": "request",
    "blocked": "block",
    "blocking": "block",
    "came": "request",
    "challenged": "challenge",
    "concerns": "subject",
    "deployed": "deploy",
    "deployment": "deploy",
    "dispute": "subject",
    "enjoined": "block",
    "halted": "stay",
    "litigation": "challenge",
    "lawsuit": "challenge",
    "mobilized": "deploy",
    "paused": "stay",
    "power": "authority",
    "prevented": "block",
    "reached": "request",
    "rejected": "deny",
    "relief": "request",
    "sought": "request",
    "suspended": "stay",
    "troops": "forces",
}


_GUIDE_NEGATION = re.compile(
    r"\b(?:no|not|never|without|lack|lacks|lacked|fail|fails|failed)\b",
    re.IGNORECASE,
)


def _guide_paragraph_has_support(
    paragraph: str,
    supporting_claims: tuple[ScotusApprovedClaim, ...],
    *,
    enforce_negation: bool = True,
) -> bool:
    sentences = tuple(
        match.group(0).strip() for match in _SENTENCE.finditer(paragraph) if match.group(0).strip()
    )
    if not sentences:
        return False
    paragraph_words = _guide_content_words(paragraph)
    all_support_words = set().union(
        *(_guide_content_words(claim.public_value) for claim in supporting_claims)
    )
    paragraph_overlap = min(1, len(all_support_words))
    if paragraph_overlap == 0 or len(paragraph_words & all_support_words) < paragraph_overlap:
        return False
    for sentence in sentences:
        grounded_sentence = _EXPLICIT_NEGATED_ORAL_ARGUMENT.sub("", sentence)
        sentence_words = _guide_content_words(grounded_sentence)
        sentence_negated = _GUIDE_NEGATION.search(grounded_sentence) is not None
        if not any(
            sentence_words & _guide_content_words(claim.public_value)
            and (
                not enforce_negation
                or sentence_negated == (_GUIDE_NEGATION.search(claim.public_value) is not None)
            )
            for claim in supporting_claims
        ):
            return False
    return True


def _section_purpose_types(heading: str) -> frozenset[LegalObservationType] | None:
    lowered = heading.casefold()
    purpose_rules: tuple[tuple[tuple[str, ...], frozenset[LegalObservationType]], ...] = (
        (("each side", "sides say"), frozenset({LegalObservationType.ADVOCATE_CONTENTION})),
        (("justices asked", "court asked"), frozenset({LegalObservationType.JUSTICE_QUESTION})),
        (
            ("reached the court", "got here", "procedural path"),
            frozenset(
                {
                    LegalObservationType.PROCEDURAL_POSTURE,
                    LegalObservationType.REQUESTED_DISPOSITION,
                    LegalObservationType.LOWER_COURT_ACTION,
                }
            ),
        ),
        (
            ("legal issue", "legal question"),
            frozenset(
                {
                    LegalObservationType.QUESTION_PRESENTED,
                    LegalObservationType.DOCTRINAL_THEME,
                }
            ),
        ),
        (
            ("why the court", "court's reasoning"),
            frozenset({LegalObservationType.HOLDING, LegalObservationType.DOCTRINAL_THEME}),
        ),
        (
            ("court did", "supreme court did", "court decided"),
            frozenset({LegalObservationType.HOLDING, LegalObservationType.ORDER}),
        ),
        (
            ("case is about", "background"),
            frozenset(
                {
                    LegalObservationType.CASE_BACKGROUND,
                    LegalObservationType.QUESTION_PRESENTED,
                    LegalObservationType.PROCEDURAL_POSTURE,
                }
            ),
        ),
    )
    return next(
        (types for phrases, types in purpose_rules if any(phrase in lowered for phrase in phrases)),
        None,
    )


def _validate_section_relevance(
    section: DraftSection, claim_map: dict[UUID, ScotusApprovedClaim]
) -> None:
    purpose_types = _section_purpose_types(section.heading)
    if purpose_types is None:
        return
    purpose_support = tuple(
        claim_map[claim_id]
        for claim_id in section.claim_ids
        if claim_id in claim_map and claim_map[claim_id].observation_type in purpose_types
    )
    if not purpose_support:
        raise BriefValidationError(
            "section prose is irrelevant to its stated reader purpose",
            safe_code="irrelevant_reader_section",
        )


def _validate_disposition_guide_structure(
    draft: LegalBriefDraft,
    claims: tuple[ScotusApprovedClaim, ...],
) -> None:
    """Require a coherent reader contract, not merely grounded fragments."""
    actual = tuple(section.heading.strip() for section in draft.sections)
    expected = DISPOSITION_GUIDE_HEADINGS
    if actual not in {
        expected,
        (*expected, DISPOSITION_SEPARATE_OPINIONS_HEADING),
    }:
        raise BriefValidationError(
            "disposition guide has incomplete or misordered sections",
            safe_code="invalid_guide_structure",
        )

    claim_map = {claim.claim_id: claim for claim in claims}
    controlling = tuple(claim for claim in claims if not _is_separate_opinion_claim(claim))
    by_heading = {section.heading.strip(): section for section in draft.sections}
    separate_ids = {claim.claim_id for claim in claims if _is_separate_opinion_claim(claim)}
    for heading in DISPOSITION_GUIDE_HEADINGS:
        section = by_heading[heading]
        if separate_ids.intersection(section.claim_ids):
            raise BriefValidationError(
                "a main guide section relies on separate-opinion material",
                safe_code="separate_opinion_in_main_guide",
            )

    required_types: dict[str, frozenset[LegalObservationType]] = {
        "What this case is about": frozenset({LegalObservationType.CASE_BACKGROUND}),
        "Why this case reached the Court": frozenset(
            {
                LegalObservationType.PROCEDURAL_POSTURE,
                LegalObservationType.REQUESTED_DISPOSITION,
                LegalObservationType.LOWER_COURT_ACTION,
            }
        ),
        "The legal issue": frozenset(
            {
                LegalObservationType.QUESTION_PRESENTED,
                LegalObservationType.DOCTRINAL_THEME,
            }
        ),
        "What the Supreme Court did": frozenset(
            {LegalObservationType.HOLDING, LegalObservationType.ORDER}
        ),
        "Why the Court did it": frozenset(
            {LegalObservationType.HOLDING, LegalObservationType.DOCTRINAL_THEME}
        ),
    }
    required_statuses: dict[str, frozenset[LegalStatus]] = {
        "What the Supreme Court did": frozenset(
            {LegalStatus.COURT_HELD, LegalStatus.COURT_ORDERED}
        ),
    }
    for heading, allowed_types in required_types.items():
        cited = tuple(
            claim_map[claim_id]
            for claim_id in by_heading[heading].claim_ids
            if claim_id in claim_map
        )
        statuses = required_statuses.get(heading)
        relevant = tuple(
            claim
            for claim in cited
            if claim.observation_type in allowed_types
            and (statuses is None or claim.legal_status in statuses)
        )
        if not relevant:
            raise BriefValidationError(
                "disposition guide section lacks role-appropriate support",
                safe_code=(
                    "unsupported_guide_section_"
                    + re.sub(r"[^a-z0-9]+", "_", heading.casefold()).strip("_")
                )[:80],
            )
        if heading not in {
            "Why this case reached the Court",
            "What the Supreme Court did",
        }:
            unsupported = tuple(
                paragraph
                for paragraph in by_heading[heading].paragraphs
                if not _guide_paragraph_has_support(paragraph, relevant)
            )
            if unsupported:
                safe_heading = re.sub(r"[^a-z0-9]+", "_", heading.casefold()).strip("_")
                polarity_only = all(
                    _guide_paragraph_has_support(paragraph, relevant, enforce_negation=False)
                    for paragraph in unsupported
                )
                suffix = "_polarity" if polarity_only else ""
                if not polarity_only:
                    matching_types = sorted(
                        {
                            claim.observation_type.value
                            for paragraph in unsupported
                            for claim in controlling
                            if claim not in relevant
                            and _guide_paragraph_has_support(
                                paragraph, (claim,), enforce_negation=False
                            )
                        }
                    )
                    if matching_types:
                        suffix = f"_matches_{matching_types[0]}"
                raise BriefValidationError(
                    "disposition guide paragraph does not express its cited support",
                    safe_code=f"ungrounded_guide_section_{safe_heading}{suffix}"[:80],
                )

    issue_claims = tuple(
        claim_map[claim_id]
        for claim_id in by_heading["The legal issue"].claim_ids
        if claim_id in claim_map
        and claim_map[claim_id].observation_type
        in {LegalObservationType.QUESTION_PRESENTED, LegalObservationType.DOCTRINAL_THEME}
    )
    reasoning_claims = tuple(
        claim_map[claim_id]
        for claim_id in by_heading["Why the Court did it"].claim_ids
        if claim_id in claim_map
        and claim_map[claim_id].observation_type
        in {LegalObservationType.HOLDING, LegalObservationType.DOCTRINAL_THEME}
    )
    issue_values = {claim.public_value.casefold() for claim in issue_claims}
    reasoning_values = {claim.public_value.casefold() for claim in reasoning_claims}
    if {claim.claim_id for claim in issue_claims} & {
        claim.claim_id for claim in reasoning_claims
    } or issue_values & reasoning_values:
        raise BriefValidationError(
            "legal issue and Court reasoning require independent support",
            safe_code="nonindependent_court_reasoning",
        )

    path_section = by_heading["Why this case reached the Court"]
    path_claims = tuple(
        claim_map[claim_id] for claim_id in path_section.claim_ids if claim_id in claim_map
    )
    for paragraph in path_section.paragraphs:
        _validate_action_sentences(paragraph, path_claims)
    path_text = " ".join(path_section.paragraphs)
    path_is_grounded = all(
        _guide_paragraph_has_support(paragraph, path_claims)
        for paragraph in path_section.paragraphs
    )
    if not path_is_grounded and _ACTION_WORD.search(path_text) is None:
        raise BriefValidationError(
            "procedural-path section lacks grounded prose or a supported action",
            safe_code="ungrounded_guide_section_why_this_case_reached_the_court",
        )

    separate = by_heading.get(DISPOSITION_SEPARATE_OPINIONS_HEADING)
    if separate is not None:
        separate_support = tuple(
            claim_map[claim_id]
            for claim_id in separate.claim_ids
            if claim_id in separate_ids and claim_id in claim_map
        )
        if not separate_support:
            raise BriefValidationError(
                "separate-opinions section lacks separately attributed support",
                safe_code="unsupported_separate_opinions_section",
            )
        controlling_reason_ids = set(
            _disposition_support_by_heading(controlling)["Why the Court did it"]
        )
        controlling_reasons = tuple(
            claim for claim in controlling if claim.claim_id in controlling_reason_ids
        )
        for paragraph in separate.paragraphs:
            for match in _SENTENCE.finditer(paragraph):
                sentence = match.group(0).strip()
                if not _SEPARATE_SENTENCE_ATTRIBUTION.search(sentence):
                    raise BriefValidationError(
                        "separate-opinion sentence lacks explicit attribution",
                        safe_code="ambiguous_separate_opinion_attribution",
                    )
                sentence_words = _guide_content_words(sentence)
                sentence_negated = _GUIDE_NEGATION.search(sentence) is not None
                if not _SEPARATE_CONTROLLING_REPORT.search(sentence) and any(
                    sentence_words & _guide_content_words(claim.public_value)
                    and sentence_negated == (_GUIDE_NEGATION.search(claim.public_value) is not None)
                    for claim in controlling_reasons
                ):
                    raise BriefValidationError(
                        "separate opinion ambiguously adopts controlling reasoning",
                        safe_code="ambiguous_separate_opinion_attribution",
                    )
        if any(
            not _guide_paragraph_has_support(paragraph, separate_support)
            for paragraph in separate.paragraphs
        ):
            raise BriefValidationError(
                "separate-opinions paragraph does not express its cited support",
                safe_code="ungrounded_separate_opinions_section",
            )

    action_section = by_heading["What the Supreme Court did"]
    action_support = tuple(
        claim_map[claim_id] for claim_id in action_section.claim_ids if claim_id in claim_map
    )
    action_claims = tuple(
        claim
        for claim in action_support
        if claim.legal_status in {LegalStatus.COURT_HELD, LegalStatus.COURT_ORDERED}
    )
    action_text = " ".join(action_section.paragraphs)
    grounded_action_text = _EXPLICIT_NEGATED_ORAL_ARGUMENT.sub("", action_text)
    _validate_action_sentences(grounded_action_text, action_support)
    if any(
        not _guide_paragraph_has_support(
            _EXPLICIT_NEGATED_ORAL_ARGUMENT.sub("", paragraph), action_support
        )
        for paragraph in action_section.paragraphs
    ):
        raise BriefValidationError(
            "disposition guide paragraph does not express its cited support",
            safe_code="ungrounded_guide_section_what_the_supreme_court_did",
        )
    if _ACTION_WORD.search(grounded_action_text) is None:
        raise BriefValidationError(
            "Supreme Court action section does not state an action",
            safe_code="missing_supreme_court_action_prose",
        )
    source_states_stay = any(
        re.search(r"\bstay(?:ed)?\b", claim.public_value, re.IGNORECASE) for claim in action_claims
    )
    generated_states_stay = re.search(r"\bstay(?:ed)?\b", action_text, re.IGNORECASE) is not None
    if source_states_stay and (
        not generated_states_stay or _INTERIM_EFFECT.search(action_text) is None
    ):
        raise BriefValidationError(
            "stay summary omits its interim procedural effect",
            safe_code="incomplete_interim_stay_effect",
        )
    supported_action_objects = {
        pair
        for claim in claims
        if claim.legal_status
        in {
            LegalStatus.REQUESTED,
            LegalStatus.LOWER_COURT_HELD,
            LegalStatus.COURT_HELD,
            LegalStatus.COURT_ORDERED,
        }
        and not _is_separate_opinion_claim(claim)
        for pair in _action_object_pairs(claim.public_value)
    }
    generated_action_objects = _action_object_pairs(
        _EXPLICIT_NEGATED_ORAL_ARGUMENT.sub("", action_text)
    )
    if supported_action_objects and (
        not generated_action_objects
        or not generated_action_objects.issubset(supported_action_objects)
    ):
        raise BriefValidationError(
            "Supreme Court action section changes or omits the operative object",
            safe_code="unsupported_supreme_court_action_object",
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

    def validate(text: str, claim_ids: tuple[UUID, ...], *, context: str) -> None:
        _validate_public_text(
            text,
            claim_ids,
            candidate,
            claim_map,
            public_quotes=public_quotes,
            validation_context=context,
            maximum_sentence_words=maximum_sentence_words,
            maximum_paragraph_words=maximum_paragraph_words,
        )

    validate(draft.title, draft.title_claim_ids, context="title")
    validate(draft.dek, draft.dek_claim_ids, context="dek")
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
        validate(section.heading, section.claim_ids, context="section_heading")
        for paragraph in section.paragraphs:
            validate(paragraph, section.claim_ids, context="section_paragraph")
        if candidate.argument_sessions:
            _validate_section_relevance(section, claim_map)
    if not candidate.argument_sessions:
        _validate_disposition_guide_structure(draft, claims)
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
        validate(analysis.heading, analysis.claim_ids, context="argument_heading")
        for paragraph in analysis.paragraphs:
            validate(paragraph, analysis.claim_ids, context="argument_paragraph")


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
        draft: LegalBriefDraft | None = None
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
            raise BriefValidationError(str(error), safe_code=safe_code, draft=draft) from None
        assert draft is not None
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
