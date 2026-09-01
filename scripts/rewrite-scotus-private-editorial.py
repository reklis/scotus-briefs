#!/usr/bin/env python3
"""Rewrite private SCOTUS drafts into a compact, fixed editorial structure."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ragchew.config import ScotusConfig, ServiceSettings
from ragchew.repository import PostgresRepository
from ragchew.scotus.briefs import (
    BriefGenerationService,
    DraftArgumentAnalysis,
    DraftSection,
    LegalBriefDraft,
    PostgresBriefRevisionStore,
    _plain_language_draft,
    evaluate_brief_candidate,
)
from ragchew.scotus.contracts import LegalBriefRevision, LegalObservationType
from ragchew.scotus.publisher import (
    _build_candidate,
    _candidate_rows,
    _complete_generation_attempt,
    _reserve_generation_attempt,
)


class EditorialRewrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    dek: str = Field(min_length=1)
    about: str = Field(min_length=1)
    history: str = Field(min_length=1)
    wants: tuple[str, ...] = Field(min_length=1)
    positions: tuple[str, ...] = Field(min_length=1)
    questions: tuple[str, ...] = Field(min_length=1)
    significance: str = Field(min_length=1)
    next_step: str = Field(min_length=1)
    argument: tuple[str, ...] = Field(min_length=2)


def _schema() -> dict[str, object]:
    string = {"type": "string"}
    pair = {"type": "array", "items": string, "minItems": 2, "maxItems": 2}
    return {
        "type": "object",
        "properties": {
            "title": string,
            "dek": string,
            "about": string,
            "history": string,
            "wants": pair,
            "positions": pair,
            "questions": {
                "type": "array",
                "items": string,
                "minItems": 1,
                "maxItems": 2,
            },
            "significance": string,
            "next_step": string,
            "argument": {
                "type": "array",
                "items": string,
                "minItems": 3,
                "maxItems": 4,
            },
        },
        "required": [
            "title",
            "dek",
            "about",
            "history",
            "wants",
            "positions",
            "questions",
            "significance",
            "next_step",
            "argument",
        ],
        "additionalProperties": False,
    }


def _concise(value: str, maximum_words: int = 60) -> str:
    words = value.split()
    if len(words) <= maximum_words:
        return value
    kept: list[str] = []
    word_count = 0
    for sentence in value.replace("!", ".").replace("?", ".").split("."):
        sentence_words = sentence.split()
        if not sentence_words:
            continue
        if kept and word_count + len(sentence_words) > maximum_words:
            break
        kept.append(" ".join(sentence_words[: maximum_words - word_count]))
        word_count += len(sentence_words)
        if word_count >= maximum_words:
            break
    return ". ".join(kept).rstrip(" ,;:") + "."


def _private_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("editorial endpoint must be an HTTP(S) URL")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise ValueError("editorial endpoint must use a literal private IP") from error
    if not address.is_private:
        raise ValueError("editorial endpoint must use a private IP")
    return value.rstrip("/")


def _request_editorial(
    client: OpenAI | httpx.Client,
    model: str,
    caption: str,
    docket: str,
    source: dict[str, object],
    ledger: list[dict[str, object]],
    *,
    ollama_native: bool,
) -> EditorialRewrite:
    system_prompt = (
        "Act as a rigorous public-service editor. Rewrite the supplied Supreme Court draft "
        "for a reader with no legal training. Use only the supplied draft and approved claims. "
        "Do not add facts. Keep section purposes separate. The wants array must contain one "
        "requested result for each opposing side. The positions array must contain one legal "
        "argument for each opposing side. Never put a justice's words in wants, positions, or "
        "significance. Put justice questions only in questions and argument. Significance must "
        "explain the concrete stakes in timeless present tense. Never say a decision, ruling, or "
        "outcome will, would, may, or could determine or affect something. Write a specific, "
        "informative title, "
        "preferably as the concrete "
        "question the Court must answer. Never put Explained, Guide, What Happened at Argument, "
        "a docket number, or a hearing date in the title. The dek must state the dispute. Never "
        "begin it by saying the Supreme Court heard an argument. Keep every string under 80 "
        "words. Use active voice. Keep every sentence under 15 words and count words before "
        "returning the JSON. Identify what each side wants "
        "and why. Attribute disputed claims. Remove repetition, model instructions, source IDs, "
        "throat-clearing, and empty filler. Paraphrase everything and do not use quotation "
        "marks. Describe held, reversed, or affirmed only as a lower-court action or a side's "
        "requested result. Do not use the words held, ordered, affirmed, reversed, or vacated in "
        "any field. Never address the reader as you or your. Define legal terms in everyday "
        "language and avoid unexplained abbreviations. A justice's question is not a vote. Oral "
        "argument is not a decision. Do not predict an outcome or name a winner. Return JSON "
        "only."
    )
    user_prompt = json.dumps(
        {
            "caption": caption,
            "docket": docket,
            "source_draft": source,
            "approved_claims": ledger,
        },
        separators=(",", ":"),
    )
    last_error: Exception | None = None
    for attempt in range(2):
        messages = [
            {
                "role": "system",
                "content": system_prompt
                + (
                    " Every required string and array item must be nonempty."
                    if attempt
                    else ""
                ),
            },
            {"role": "user", "content": user_prompt},
        ]
        if ollama_native:
            if not isinstance(client, httpx.Client):
                raise TypeError("native Ollama mode requires an HTTP client")
            response = client.post(
                "/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "think": False,
                    "stream": False,
                    "format": _schema(),
                    "options": {"temperature": 0, "num_predict": 4_000},
                },
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
        else:
            if not isinstance(client, OpenAI):
                raise TypeError("OpenAI-compatible mode requires an OpenAI client")
            completion = client.chat.completions.create(
                model=model,
                temperature=0,
                reasoning_effort="low",
                max_tokens=4_000,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "citizen_editorial_rewrite",
                        "strict": True,
                        "schema": _schema(),
                    },
                },
            )
            content = completion.choices[0].message.content
        if not content:
            last_error = ValueError("model returned no editorial JSON")
            continue
        try:
            return EditorialRewrite.model_validate_json(content)
        except ValidationError as error:
            last_error = error
    if last_error is None:
        raise RuntimeError("editorial request ended without a result")
    raise last_error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--docket")
    parser.add_argument("--term")
    parser.add_argument("--rewrite-existing", action="store_true")
    parser.add_argument("--ollama-native", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--maximum-runtime-hours", type=float, default=12)
    args = parser.parse_args()

    settings = ServiceSettings()
    config = ScotusConfig.from_yaml(settings.scotus_config_path)
    if config.publication.enabled:
        raise RuntimeError("private editorial rewrite refuses enabled publication")
    repository = PostgresRepository(settings.database_dsn)
    store = PostgresBriefRevisionStore("", pool=repository.pool)
    base_url = _private_url(args.base_url)
    if args.maximum_runtime_hours <= 0:
        raise ValueError("maximum runtime must be positive")
    prompt_version = "scotus-private-editorial-v19"
    generation_model = f"{args.model}+editorial-v19"
    client: OpenAI | httpx.Client
    if args.ollama_native:
        generation_model = f"{args.model}+ollama-no-think+editorial-v19"
        client = httpx.Client(base_url=base_url, timeout=args.timeout_seconds)
    else:
        client = OpenAI(
            base_url=base_url,
            api_key="private-editorial",
            timeout=args.timeout_seconds,
        )
    started = time.monotonic()

    try:
        for row in reversed(_candidate_rows(repository)):
            if time.monotonic() - started >= args.maximum_runtime_hours * 3_600:
                print("outcome=runtime_limit_reached", flush=True)
                break
            candidate = _build_candidate(repository, row, datetime.now(UTC))
            if args.docket and candidate.primary_docket != args.docket:
                continue
            if args.term and row["term"] != args.term:
                continue
            decision = evaluate_brief_candidate(
                candidate,
                minimum_confidence=config.generation.minimum_observation_confidence,
            )
            if not decision.eligible:
                continue
            watermark = row["case_watermark"]
            if not isinstance(watermark, datetime):
                raise RuntimeError("invalid case watermark")
            with repository.pool.connection() as connection:
                latest = connection.execute(
                    """SELECT public_payload,revision_number,generator_model
                       FROM scotus_brief_revisions WHERE case_id=%s
                       ORDER BY revision_number DESC LIMIT 1""",
                    (candidate.case_id,),
                ).fetchone()
            if latest is not None and not args.rewrite_existing:
                print(
                    f"docket={candidate.primary_docket} outcome=already_briefed",
                    flush=True,
                )
                continue
            if not _reserve_generation_attempt(
                repository,
                case_id=candidate.case_id,
                case_watermark=watermark,
                model=generation_model,
                prompt_version=prompt_version,
            ):
                print(
                    f"docket={candidate.primary_docket} outcome=already_attempted",
                    flush=True,
                )
                continue
            selected_claims = []
            type_limits = {
                LegalObservationType.PROCEDURAL_POSTURE: 1,
                LegalObservationType.ADVOCATE_CONTENTION: 4,
                LegalObservationType.JUSTICE_QUESTION: 6,
                LegalObservationType.REQUESTED_DISPOSITION: 2,
            }
            type_counts: dict[LegalObservationType, int] = {}
            for claim in decision.claims:
                limit = type_limits.get(claim.observation_type, 2)
                count = type_counts.get(claim.observation_type, 0)
                if count >= limit:
                    continue
                selected_claims.append(claim)
                type_counts[claim.observation_type] = count + 1
            ledger = [
                {
                    "type": claim.observation_type.value,
                    "status": claim.legal_status.value,
                    "attribution": claim.attribution,
                    "value": _concise(claim.public_value, maximum_words=60),
                }
                for claim in selected_claims
            ]
            if latest is not None:
                source_revision = LegalBriefRevision.model_validate(
                    latest["public_payload"]
                )
                source = {
                    "title": source_revision.title,
                    "dek": source_revision.dek,
                    "sections": [
                        {"heading": item.heading, "paragraphs": item.paragraphs}
                        for item in source_revision.sections
                    ],
                    "argument": [
                        paragraph
                        for item in source_revision.argument_analyses
                        for paragraph in item.paragraphs
                    ],
                }
                revision_number = latest["revision_number"] + 1
            else:
                source = {
                    "title": candidate.caption,
                    "dek": "",
                    "sections": [],
                    "argument": [],
                }
                revision_number = 1
            try:
                editorial = _request_editorial(
                    client,
                    args.model,
                    candidate.caption,
                    candidate.primary_docket,
                    source,
                    ledger,
                    ollama_native=args.ollama_native,
                )
                if any(
                    value in editorial.title.casefold()
                    for value in ("explained", "guide", "what happened at argument")
                ):
                    raise ValueError("editorial title remains generic")
                dek = editorial.dek
                dispute_index = dek.casefold().find("a dispute over")
                if dispute_index >= 0:
                    dek = dek[dispute_index:]
                    dek = dek[:1].upper() + dek[1:]
                if len(editorial.wants) < 2 or len(editorial.positions) < 2:
                    raise ValueError("editorial rewrite omitted one side")
                justice_marker = re.compile(r"\b(?:Chief )?Justice\b", re.IGNORECASE)
                if any(
                    justice_marker.search(value)
                    for value in (
                        *editorial.wants,
                        *editorial.positions,
                        editorial.significance,
                    )
                ):
                    raise ValueError(
                        "editorial section mixes a justice question into side analysis"
                    )
                wanted_result = re.compile(
                    r"\b(?:want|seek|ask|urge|request|reverse|affirm|uphold|reject|"
                    r"block|allow|hold|rule|dismiss|vacate|remand)",
                    re.IGNORECASE,
                )
                if any(not wanted_result.search(value) for value in editorial.wants):
                    raise ValueError("editorial wants section lacks a requested result")
                future_disposition = re.compile(
                    r"\b(?:decision|ruling|outcome)\s+"
                    r"(?:will|would|may|could)\b",
                    re.IGNORECASE,
                )
                if future_disposition.search(editorial.significance):
                    raise ValueError("editorial significance implies a future disposition")
                editorial = editorial.model_copy(
                    update={
                        "dek": _concise(dek),
                        "about": _concise(editorial.about),
                        "history": (
                            "The Supreme Court heard oral argument in this case on "
                            f"{candidate.argument_sessions[-1].argument_date:%B} "
                            f"{candidate.argument_sessions[-1].argument_date.day}, "
                            f"{candidate.argument_sessions[-1].argument_date.year}."
                        ),
                        "wants": tuple(
                            _concise(value) for value in editorial.wants[:2]
                        ),
                        "positions": tuple(
                            _concise(value) for value in editorial.positions[:2]
                        ),
                        "questions": tuple(
                            _concise(value) for value in editorial.questions[:2]
                        ),
                        "significance": _concise(editorial.significance),
                        "next_step": (
                            "This article currently covers the argument record. "
                            "Use the official docket link for later case activity."
                        ),
                        "argument": tuple(
                            _concise(value) for value in editorial.argument[:4]
                        ),
                    }
                )

                approved_claims = decision.claims

                def claim_ids(
                    *types: LegalObservationType,
                    approved: tuple = approved_claims,
                ) -> tuple:
                    values = tuple(
                        claim.claim_id
                        for claim in approved
                        if claim.observation_type in types
                    )
                    return values or tuple(claim.claim_id for claim in approved)

                issue_ids = claim_ids(
                    LegalObservationType.QUESTION_PRESENTED,
                    LegalObservationType.ADVOCATE_CONTENTION,
                )
                history_ids = claim_ids(
                    LegalObservationType.PROCEDURAL_POSTURE,
                    LegalObservationType.LOWER_COURT_ACTION,
                    LegalObservationType.REQUESTED_DISPOSITION,
                )
                position_ids = claim_ids(
                    LegalObservationType.ADVOCATE_CONTENTION,
                    LegalObservationType.REQUESTED_DISPOSITION,
                )
                question_ids = claim_ids(LegalObservationType.JUSTICE_QUESTION)
                session_ids = tuple(
                    claim.claim_id
                    for claim in decision.claims
                    if claim.argument_id == candidate.argument_sessions[0].argument_id
                )
                draft = _plain_language_draft(
                    LegalBriefDraft(
                        title=editorial.title,
                        title_claim_ids=issue_ids,
                        dek=editorial.dek,
                        dek_claim_ids=issue_ids,
                        sections=(
                            DraftSection(
                                heading="What this case is about",
                                paragraphs=(editorial.about,),
                                claim_ids=issue_ids,
                            ),
                            DraftSection(
                                heading="How the case got here",
                                paragraphs=(editorial.history,),
                                claim_ids=history_ids,
                            ),
                            DraftSection(
                                heading="What each side wants",
                                paragraphs=editorial.wants,
                                claim_ids=position_ids,
                            ),
                            DraftSection(
                                heading="What each side says",
                                paragraphs=editorial.positions,
                                claim_ids=position_ids,
                            ),
                            DraftSection(
                                heading="What the justices asked",
                                paragraphs=editorial.questions,
                                claim_ids=question_ids,
                            ),
                            DraftSection(
                                heading="Why it matters",
                                paragraphs=(editorial.significance,),
                                claim_ids=issue_ids,
                            ),
                            DraftSection(
                                heading="What happens next",
                                paragraphs=(editorial.next_step,),
                                claim_ids=tuple(
                                    dict.fromkeys((*history_ids, *question_ids))
                                ),
                            ),
                        ),
                        argument_analyses=(
                            DraftArgumentAnalysis(
                                argument_id=candidate.argument_sessions[0].argument_id,
                                heading=(
                                    "What happened at the argument"
                                    if len(candidate.argument_sessions) == 1
                                    else "What happened in this argument"
                                ),
                                paragraphs=editorial.argument,
                                claim_ids=session_ids,
                            ),
                        ),
                    )
                )

                class SavedGenerator:
                    def __init__(self, saved: LegalBriefDraft, model_name: str) -> None:
                        self.saved = saved
                        self.model_name = model_name

                    def generate(self, _candidate, _claims, _maturity):  # type: ignore[no-untyped-def]
                        return self.saved

                service = BriefGenerationService(
                    SavedGenerator(draft, generation_model),
                    store,
                    public_quotes=config.generation.public_quotes,
                    maximum_sentence_words=config.generation.maximum_sentence_words,
                    maximum_paragraph_words=config.generation.maximum_paragraph_words,
                )
                service.generate(
                    candidate,
                    decision,
                    revision_number=revision_number,
                    correction_note="Rewritten to the concise citizen-facing editorial standard.",
                )
            except Exception as error:
                failure_code = f"{type(error).__name__}:{str(error).splitlines()[0]}"
                if isinstance(error, ValidationError):
                    first = error.errors(include_input=False)[0]
                    failure_code = f"{first['type']}:{'.'.join(map(str, first['loc']))}"
                _complete_generation_attempt(
                    repository,
                    case_id=candidate.case_id,
                    case_watermark=watermark,
                    model=generation_model,
                    prompt_version=prompt_version,
                    outcome="validation_denied",
                    failure_code=failure_code[:200],
                )
                print(
                    f"docket={candidate.primary_docket} outcome=denied",
                    flush=True,
                )
                continue
            _complete_generation_attempt(
                repository,
                case_id=candidate.case_id,
                case_watermark=watermark,
                model=generation_model,
                prompt_version=prompt_version,
                outcome="accepted",
            )
            print(f"docket={candidate.primary_docket} outcome=accepted", flush=True)
    finally:
        if isinstance(client, httpx.Client):
            client.close()
        repository.pool.close()


if __name__ == "__main__":
    main()
