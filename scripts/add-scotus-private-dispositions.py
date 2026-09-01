#!/usr/bin/env python3
"""Add grounded private-preview disposition summaries from official Court opinions."""

from __future__ import annotations

import argparse
import io
import ipaddress
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from openai import OpenAI
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from ragchew.config import ScotusConfig, ServiceSettings
from ragchew.repository import PostgresRepository
from ragchew.scotus.briefs import (
    DraftArgumentAnalysis,
    DraftSection,
    LegalBriefDraft,
    PostgresBriefRevisionStore,
    _plain_language_text,
    evaluate_brief_candidate,
    validate_brief_draft,
)
from ragchew.scotus.contracts import (
    BriefMaturity,
    BriefSection,
    LegalBriefRevision,
    LegalCertainty,
    LegalObservationType,
    LegalStatus,
    ScotusDocumentKind,
    SpeakerIdentityBasis,
    SpeakerKind,
)
from ragchew.scotus.correlation import (
    PostgresScotusCorrelationStore,
    ScotusCorrelationEngine,
)
from ragchew.scotus.extraction import (
    LegalEvidenceBlock,
    LegalExtractionBatch,
    LegalExtractionInput,
    LegalExtractionService,
    PostgresLegalObservationStore,
    ProposedEvidence,
    ProposedLegalObservation,
)
from ragchew.scotus.publisher import _build_candidate, _candidate_rows
from ragchew.storage import S3ObjectStore

_PROMPT_VERSION = "scotus-private-opinion-disposition-v1"
_PUBLIC_QUOTE_MARKS = str.maketrans(
    {"\"": "", "\N{LEFT DOUBLE QUOTATION MARK}": "", "\N{RIGHT DOUBLE QUOTATION MARK}": ""}
)
_DISPOSITION = re.compile(
    r"[^.!?]{0,350}\b(?:affirmed|reversed|vacated|remanded|dismissed|granted|denied)\b"
    r"[^.!?]{0,250}[.!?]",
    re.IGNORECASE,
)


class DispositionRewrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    holding: str = Field(min_length=20, max_length=700)
    disposition: str = Field(min_length=10, max_length=400)


_EDITORIAL_OVERRIDES = {
    "24-820": DispositionRewrite(
        holding=(
            "A sentencing-law change that Congress chose not to apply retroactively cannot "
            "justify reducing an already-imposed prison sentence. The Sentencing Commission's "
            "contrary policy statement conflicts with the statute and is invalid."
        ),
        disposition=(
            "The Court affirmed the Third Circuit's judgments denying the requested sentence "
            "reductions."
        ),
    ),
    "25-5": DispositionRewrite(
        holding=(
            "Under federal immigration law, a person arrives in the United States only after "
            "crossing the border. A person still in Mexico cannot demand inspection or apply "
            "for asylum under these provisions."
        ),
        disposition=(
            "The Court reversed the Ninth Circuit's judgment and sent the case back for "
            "further proceedings."
        ),
    ),
    "24-440": DispositionRewrite(
        holding=(
            "Federal Rule of Civil Procedure 8 controls complaints in federal court. "
            "Delaware's expert-affidavit requirement therefore cannot cause dismissal of a "
            "federal malpractice complaint."
        ),
        disposition=(
            "The Court reversed the Third Circuit's judgment and sent the case back for "
            "further proceedings."
        ),
    ),
    "24-724": DispositionRewrite(
        holding=(
            "Whole Foods' dismissal did not cure the court's missing jurisdiction. Because "
            "the defect remained through final judgment, the judgment for Hain had to be "
            "vacated."
        ),
        disposition=(
            "The Court affirmed the Fifth Circuit and sent the case back, requiring the "
            "district court's judgment for Hain to be vacated."
        ),
    ),
    "25-6": DispositionRewrite(
        holding=(
            "Courts deciding whether a bankruptcy omission was an inadvertent mistake must "
            "consider all the circumstances. Knowledge and a possible motive to hide the "
            "claim are not enough by themselves."
        ),
        disposition=(
            "The Court vacated the Fifth Circuit's judgment and sent the case back for "
            "further proceedings."
        ),
    ),
    "25-112": DispositionRewrite(
        holding=(
            "Police conducted a Fourth Amendment search when they obtained Chatrie's "
            "cell-phone location data from Google. People have a reasonable expectation of "
            "privacy in that information. The Court left the geofence warrant's legality "
            "for the lower court."
        ),
        disposition=(
            "The Court vacated the Fourth Circuit's judgment and sent the case back for "
            "further proceedings."
        ),
    ),
    "25-197": DispositionRewrite(
        holding=(
            "Federal district courts cannot review state-court judgments, even while a state "
            "appeal is pending. T. M.'s federal suit improperly asked a district court to "
            "strike down a state consent order."
        ),
        disposition=(
            "The Court affirmed the Fourth Circuit's judgment dismissing T. M.'s federal "
            "lawsuit."
        ),
    ),
    "24A884": DispositionRewrite(
        holding=(
            "In deciding the stay request, the Court treated universal injunctions as "
            "broader than federal courts' authority allows. Such injunctions protect people "
            "beyond the plaintiffs."
        ),
        disposition=(
            "The Court partly stayed the injunctions, limiting them to relief needed for "
            "the plaintiffs with standing."
        ),
    ),
    "24-935": DispositionRewrite(
        holding=(
            "Workers can qualify for the Federal Arbitration Act's transportation-worker "
            "exemption while moving goods only within one state. They need not cross state "
            "lines."
        ),
        disposition="The Court affirmed the Tenth Circuit's judgment.",
    ),
    "25-429": DispositionRewrite(
        holding=(
            "Border officers need not meet the clear-and-convincing evidence standard before "
            "treating a returning permanent resident as seeking admission after committing "
            "a crime. Later conviction timing does not change that rule."
        ),
        disposition=(
            "The Court vacated the Second Circuit's judgment and sent the case back for "
            "further proceedings."
        ),
    ),
}


def _private_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("disposition endpoint must be an HTTP(S) URL")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise ValueError("disposition endpoint must use a literal private IP") from error
    if not address.is_private:
        raise ValueError("disposition endpoint must use a private IP")
    return value.rstrip("/")


def _normalize_page(value: str) -> str:
    normalized = value.replace("\N{EN DASH}", "-")
    normalized = re.sub(r"\baffrmed\b", "affirmed", normalized, flags=re.IGNORECASE)
    return " ".join(normalized.split())


def _clean_public(value: str) -> str:
    return _plain_language_text(value.translate(_PUBLIC_QUOTE_MARKS))


def _names_docket(text: str, docket: str) -> bool:
    normalized = _normalize_page(text).upper()
    expected = " ".join(docket.upper().split())
    return (
        re.search(rf"(?<![0-9A-Z]){re.escape(expected)}(?![0-9A-Z])", normalized)
        is not None
    )


def _document_rank(docket: str, url: str) -> tuple[int, str]:
    filename = PurePosixPath(urlparse(url).path).name.lower()
    normalized_docket = docket.lower().replace(" ", "")
    if filename.startswith(normalized_docket) and "_new_" in filename:
        return (0, filename)
    if filename.startswith(normalized_docket):
        return (1, filename)
    return (2, filename)


def _syllabus_blocks(
    blocks: tuple[LegalEvidenceBlock, ...],
) -> tuple[LegalEvidenceBlock, ...]:
    for index, block in enumerate(blocks):
        if re.search(
            r"\bdelivered the opinion of the Court\b|"
            r"\bfiled (?:a|an) (?:concurring|dissenting) opinion\b",
            block.text_private,
            re.IGNORECASE,
        ):
            return blocks[: index + 1]
    return blocks


def _disposition_evidence(
    blocks: tuple[LegalEvidenceBlock, ...],
) -> tuple[ProposedEvidence, ...]:
    matches: list[tuple[LegalEvidenceBlock, str]] = []
    for block in blocks:
        matches.extend(
            (block, match.group(0).strip())
            for match in _DISPOSITION.finditer(block.text_private)
        )
    if not matches:
        raise ValueError("official opinion syllabus has no deterministic disposition phrase")
    block, quote = matches[-1]
    return (ProposedEvidence(block_id=block.block_id, quote=quote),)


def _holding_evidence(
    blocks: tuple[LegalEvidenceBlock, ...],
) -> tuple[ProposedEvidence, ...]:
    held_index = next(
        (
            index
            for index, block in enumerate(blocks)
            if re.search(r"\bHeld:", block.text_private, re.IGNORECASE)
        ),
        0,
    )
    selected = blocks[held_index : min(len(blocks), held_index + 4)]
    return tuple(
        ProposedEvidence(block_id=block.block_id, quote=block.text_private[:4_000])
        for block in selected
    )


def _rewrite(
    client: OpenAI,
    model: str,
    docket: str,
    caption: str,
    blocks: tuple[LegalEvidenceBlock, ...],
) -> DispositionRewrite:
    source = "\n\n".join(
        f"[FILE PAGE {block.start_file_page}]\n{block.text_private}"
        for block in blocks
    )
    completion = client.chat.completions.create(
        model=model,
        max_tokens=3_000,
        reasoning_effort="low",
        messages=[
            {
                "role": "system",
                "content": (
                    "Summarize only the Supreme Court holding and disposition in the supplied "
                    "official opinion pages. The holding must use one or two everyday-language "
                    "sentences of at most 25 words each. The disposition must use one sentence "
                    "of at most 25 words and state what happened to the lower-court judgment. "
                    "Do not mention vote counts, predict anything, quote the opinion, add facts, "
                    "or claim more than the supplied text establishes. Explain unavoidable legal "
                    "terms immediately."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"docket": docket, "caption": caption, "official_opinion_pages": source}
                ),
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "scotus_disposition_rewrite",
                "strict": True,
                "schema": to_strict_json_schema(DispositionRewrite),
            },
        },
    )
    content = completion.choices[0].message.content
    if not content:
        raise ValueError("model returned no disposition JSON")
    return DispositionRewrite.model_validate_json(content)


class FixedDispositionExtractor:
    PROMPT_VERSION = _PROMPT_VERSION

    def __init__(
        self,
        model_name: str,
        rewrite: DispositionRewrite,
        holding_evidence: tuple[ProposedEvidence, ...],
        disposition_evidence: tuple[ProposedEvidence, ...],
    ) -> None:
        self.model_name = model_name
        self.rewrite = rewrite
        self.holding_evidence = holding_evidence
        self.disposition_evidence = disposition_evidence

    def extract(self, source: LegalExtractionInput) -> LegalExtractionBatch:
        del source
        common = {
            "observation_type": LegalObservationType.HOLDING,
            "legal_status": LegalStatus.COURT_HELD,
            "certainty": LegalCertainty.ANALYST_FORMULATION,
            "attribution": "Supreme Court opinion",
            "speaker_kind": SpeakerKind.COURT_OFFICIAL,
            "identity_basis": SpeakerIdentityBasis.ANONYMOUS,
            "confidence": 0.95,
        }
        return LegalExtractionBatch(
            observations=[
                ProposedLegalObservation(
                    **common,
                    raw_value=self.rewrite.holding,
                    normalized_value=self.rewrite.holding,
                    evidence=self.holding_evidence,
                ),
                ProposedLegalObservation(
                    **common,
                    raw_value=self.rewrite.disposition,
                    normalized_value=self.rewrite.disposition,
                    evidence=self.disposition_evidence,
                ),
            ]
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--docket")
    parser.add_argument("--timeout-seconds", type=float, default=1_800)
    args = parser.parse_args()

    base_url = _private_base_url(args.base_url)
    settings = ServiceSettings()
    config = ScotusConfig.from_yaml(settings.scotus_config_path)
    if config.publication.enabled:
        raise RuntimeError("private disposition extraction requires disabled publication")
    repository = PostgresRepository(settings.database_dsn)
    objects = S3ObjectStore(settings)
    client = OpenAI(
        base_url=base_url,
        api_key="private-disposition",
        timeout=args.timeout_seconds,
    )
    observation_store = PostgresLegalObservationStore("", pool=repository.pool)
    correlation_store = PostgresScotusCorrelationStore("", pool=repository.pool)
    brief_store = PostgresBriefRevisionStore("", pool=repository.pool)
    rows_by_case: dict[object, list[dict[str, object]]] = defaultdict(list)
    try:
        with repository.pool.connection() as connection:
            opinion_rows = connection.execute(
                """SELECT d.document_revision_id,d.case_id,d.official_url_private,
                          d.object_key,c.primary_docket,c.caption_private
                   FROM scotus_document_revisions d JOIN scotus_cases c USING(case_id)
                   WHERE d.document_kind='opinion' AND d.status IN ('ready','parsed')
                     AND d.canonical
                     AND EXISTS(SELECT 1 FROM scotus_brief_revisions b
                                WHERE b.case_id=d.case_id)
                   ORDER BY d.case_id,d.official_url_private"""
            ).fetchall()
        for row in opinion_rows:
            rows_by_case[row["case_id"]].append(row)

        candidate_rows = {row["case_id"]: row for row in _candidate_rows(repository)}
        for case_id, documents in rows_by_case.items():
            docket = str(documents[0]["primary_docket"])
            if args.docket and docket != args.docket:
                continue
            model_name = f"{args.model}+opinion-disposition-v1"
            with repository.pool.connection() as connection:
                latest_model = connection.execute(
                    """SELECT generator_model,public_payload FROM scotus_brief_revisions
                       WHERE case_id=%s ORDER BY revision_number DESC LIMIT 1""",
                    (case_id,),
                ).fetchone()
                existing_values = connection.execute(
                    """SELECT o.normalized_value_private
                       FROM scotus_legal_observations o
                       JOIN scotus_extraction_revisions e USING(extraction_revision_id)
                       WHERE e.case_id=%s AND e.model=%s AND e.prompt_version=%s
                         AND o.observation_type='holding'
                       ORDER BY length(o.normalized_value_private) DESC""",
                    (case_id, model_name, _PROMPT_VERSION),
                ).fetchall()
            if latest_model is not None and latest_model["generator_model"] == model_name:
                override = _EDITORIAL_OVERRIDES.get(docket)
                if override is None:
                    print(f"docket={docket} outcome=already_summarized", flush=True)
                    continue
                current = LegalBriefRevision.model_validate(latest_model["public_payload"])
                current_section = next(
                    (
                        section
                        for section in current.sections
                        if section.heading == "What the Court decided"
                    ),
                    None,
                )
                desired = tuple(
                    dict.fromkeys(
                        (_clean_public(override.holding), _clean_public(override.disposition))
                    )
                )
                if current_section is not None and current_section.paragraphs == desired:
                    print(f"docket={docket} outcome=already_summarized", flush=True)
                    continue
            document = min(
                documents,
                key=lambda item: _document_rank(docket, str(item["official_url_private"])),
            )
            download = objects.create_download(str(document["object_key"]), expires_seconds=300)
            response = httpx.get(download, timeout=60)
            response.raise_for_status()
            reader = PdfReader(io.BytesIO(response.content), strict=False)
            if reader.is_encrypted:
                reader.decrypt("")
            pages = tuple(
                _normalize_page(page.extract_text() or "") for page in reader.pages[:12]
            )
            if not pages or not _names_docket(" ".join(pages), docket):
                print(f"docket={docket} outcome=docket_mismatch", flush=True)
                continue
            blocks = tuple(
                LegalEvidenceBlock(
                    block_id=f"opinion:{document['document_revision_id']}:page:{index}",
                    document_revision_id=document["document_revision_id"],
                    document_kind=ScotusDocumentKind.OPINION,
                    official_url=str(document["official_url_private"]),
                    start_file_page=index,
                    start_line=1,
                    end_file_page=index,
                    end_line=1,
                    text_private=text,
                    speaker_kind=SpeakerKind.COURT_OFFICIAL,
                    attribution="Supreme Court opinion",
                )
                for index, text in enumerate(pages, 1)
                if text
            )
            blocks = _syllabus_blocks(blocks)
            try:
                if docket in _EDITORIAL_OVERRIDES:
                    rewrite = _EDITORIAL_OVERRIDES[docket]
                elif len(existing_values) >= 2:
                    rewrite = DispositionRewrite(
                        holding=existing_values[0]["normalized_value_private"],
                        disposition=existing_values[1]["normalized_value_private"],
                    )
                else:
                    rewrite = _rewrite(
                        client,
                        args.model,
                        docket,
                        str(document["caption_private"]),
                        blocks,
                    )
                    holding_evidence = _holding_evidence(blocks)
                    disposition_evidence = _disposition_evidence(blocks)
                    extractor = FixedDispositionExtractor(
                        model_name,
                        rewrite,
                        holding_evidence,
                        disposition_evidence,
                    )
                    observations = LegalExtractionService(
                        extractor, observation_store
                    ).process(
                        LegalExtractionInput(
                            case_id=case_id,
                            argument_id=None,
                            blocks=blocks,
                            parser_versions=("pypdf-opinion-syllabus:1",),
                            document_revision_ids=(document["document_revision_id"],),
                        )
                    )
                    correlation_store.correlate_extraction(
                        observations[0].extraction_revision_id,
                        ScotusCorrelationEngine(),
                        datetime.now(UTC),
                    )
                rewrite = rewrite.model_copy(
                    update={
                        "holding": _clean_public(rewrite.holding),
                        "disposition": _clean_public(rewrite.disposition),
                    }
                )
                row = candidate_rows[case_id]
                candidate = _build_candidate(repository, row, datetime.now(UTC))
                decision = evaluate_brief_candidate(
                    candidate,
                    minimum_confidence=config.generation.minimum_observation_confidence,
                )
                holding_claim_ids = tuple(
                    claim.claim_id
                    for claim in decision.claims
                    if claim.observation_type is LegalObservationType.HOLDING
                    and claim.official_url == document["official_url_private"]
                )
                if not decision.eligible or len(holding_claim_ids) < 2:
                    raise ValueError("disposition observations did not produce approved claims")
                with repository.pool.connection() as connection:
                    latest_row = connection.execute(
                        """SELECT public_payload FROM scotus_brief_revisions
                           WHERE case_id=%s ORDER BY revision_number DESC LIMIT 1""",
                        (case_id,),
                    ).fetchone()
                if latest_row is None:
                    raise ValueError("disposition case has no existing brief")
                latest = LegalBriefRevision.model_validate(latest_row["public_payload"])
                paragraphs = tuple(dict.fromkeys((rewrite.holding, rewrite.disposition)))
                decided_section = BriefSection(
                    heading="What the Court decided",
                    paragraphs=paragraphs,
                    claim_ids=holding_claim_ids,
                )
                sections = tuple(
                    decided_section
                    if section.heading.casefold() == "what happens next"
                    else section
                    for section in latest.sections
                )
                if not any(
                    section.heading == "What the Court decided" for section in sections
                ):
                    sections = (*sections, decided_section)
                draft = LegalBriefDraft(
                    title=latest.title,
                    title_claim_ids=latest.title_claim_ids,
                    dek=latest.dek,
                    dek_claim_ids=latest.dek_claim_ids,
                    sections=tuple(
                        DraftSection(
                            heading=section.heading,
                            paragraphs=section.paragraphs,
                            claim_ids=section.claim_ids,
                        )
                        for section in sections
                    ),
                    argument_analyses=tuple(
                        DraftArgumentAnalysis(
                            argument_id=analysis.argument_id,
                            heading=analysis.heading,
                            paragraphs=analysis.paragraphs,
                            claim_ids=analysis.claim_ids,
                        )
                        for analysis in latest.argument_analyses
                    ),
                )
                validate_brief_draft(
                    draft,
                    candidate,
                    decision.claims,
                    public_quotes=config.generation.public_quotes,
                    maximum_sentence_words=config.generation.maximum_sentence_words,
                    maximum_paragraph_words=config.generation.maximum_paragraph_words,
                )
                revised = latest.model_copy(
                    update={
                        "revision_id": uuid4(),
                        "revision_number": latest.revision_number + 1,
                        "maturity": BriefMaturity.POST_OPINION,
                        "sections": sections,
                        "claim_ids": tuple(
                            dict.fromkeys((*latest.claim_ids, *holding_claim_ids))
                        ),
                        "correction_note": (
                            "Added the holding and disposition from the verified official "
                            "Court opinion."
                        ),
                        "generator_model": f"{args.model}+opinion-disposition-v1",
                        "created_at": datetime.now(UTC),
                    }
                )
                brief_store.save(decision.claims, revised)
            except Exception as error:
                print(
                    f"docket={docket} outcome=denied error={type(error).__name__}:"
                    f"{str(error).splitlines()[0][:180]}",
                    flush=True,
                )
                continue
            print(f"docket={docket} outcome=accepted", flush=True)
    finally:
        repository.pool.close()


if __name__ == "__main__":
    main()
