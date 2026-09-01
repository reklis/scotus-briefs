#!/usr/bin/env python3
"""Generate private SCOTUS bootstrap briefs with a reviewed local model endpoint."""

from __future__ import annotations

import argparse
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from openai import OpenAI
from pydantic import ValidationError

from ragchew.config import ScotusConfig, ServiceSettings
from ragchew.repository import PostgresRepository
from ragchew.scotus.briefs import (
    BriefGenerationService,
    BriefValidationError,
    OpenAILegalBriefGenerator,
    PostgresBriefRevisionStore,
    _simple_brief_json_schema,
    evaluate_brief_candidate,
)
from ragchew.scotus.publisher import (
    _build_candidate,
    _candidate_rows,
    _complete_generation_attempt,
    _reserve_generation_attempt,
)


def _private_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("bootstrap endpoint must be an HTTP(S) URL")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise ValueError("bootstrap endpoint must use a literal private IP address") from error
    if not address.is_private:
        raise ValueError("bootstrap endpoint must use a private IP address")
    return value.rstrip("/")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--docket")
    parser.add_argument("--private-draft-dir", type=Path)
    parser.add_argument("--workers", type=int, default=1, choices=range(1, 5))
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    args = parser.parse_args()

    base_url = _private_base_url(args.base_url)
    settings = ServiceSettings()
    config = ScotusConfig.from_yaml(settings.scotus_config_path)
    if config.publication.enabled:
        raise RuntimeError("private bootstrap refuses to run while publication is enabled")

    repository = PostgresRepository(settings.database_dsn)
    store = PostgresBriefRevisionStore("", pool=repository.pool)
    prompt_version = f"{OpenAILegalBriefGenerator.PROMPT_VERSION}:private-simple-schema-v6"
    evaluated_at = datetime.now(UTC)
    candidates = []
    try:
        for row in _candidate_rows(repository):
            candidate = _build_candidate(repository, row, evaluated_at)
            if args.docket and candidate.primary_docket != args.docket:
                continue
            decision = evaluate_brief_candidate(
                candidate,
                minimum_confidence=config.generation.minimum_observation_confidence,
            )
            if not decision.eligible:
                print(f"docket={candidate.primary_docket} outcome=ineligible", flush=True)
                continue
            watermark = row["case_watermark"]
            if not isinstance(watermark, datetime):
                raise RuntimeError("SCOTUS case update timestamp is invalid")
            candidates.append((candidate, decision, watermark))

        def generate(item: tuple) -> tuple[str, str]:  # type: ignore[type-arg]
            candidate, decision, watermark = item
            with repository.pool.connection() as connection:
                existing = connection.execute(
                    """SELECT 1 FROM scotus_brief_revisions
                       WHERE case_id=%s AND generator_model=%s LIMIT 1""",
                    (candidate.case_id, args.model),
                ).fetchone()
            if existing is not None:
                return candidate.primary_docket, "already_generated"
            if not _reserve_generation_attempt(
                repository,
                case_id=candidate.case_id,
                case_watermark=watermark,
                model=args.model,
                prompt_version=prompt_version,
            ):
                return candidate.primary_docket, "already_attempted"
            with repository.pool.connection() as connection:
                number_row = connection.execute(
                    """SELECT COALESCE(max(revision_number),0)+1 AS number
                       FROM scotus_brief_revisions WHERE case_id=%s""",
                    (candidate.case_id,),
                ).fetchone()
            if number_row is None:
                raise RuntimeError("SCOTUS brief revision number query failed")
            generator = OpenAILegalBriefGenerator(
                args.model,
                OpenAI(
                    base_url=base_url,
                    api_key="private-bootstrap",
                    timeout=args.timeout_seconds,
                ),
                maximum_sentence_words=15,
                maximum_paragraph_words=60,
                strict_json_schema=True,
                response_schema=_simple_brief_json_schema(),
                maximum_output_tokens=8000,
                reasoning_effort="low",
            )
            generation_source = generator
            if args.private_draft_dir is not None:
                args.private_draft_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

                class CapturingGenerator:
                    model_name = args.model

                    def generate(self, source_candidate, claims, maturity):  # type: ignore[no-untyped-def]
                        draft = generator.generate(source_candidate, claims, maturity)
                        path = args.private_draft_dir / f"{candidate.primary_docket}.json"
                        path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
                        path.chmod(0o600)
                        return draft

                generation_source = CapturingGenerator()  # type: ignore[assignment]
            service = BriefGenerationService(
                generation_source,
                store,
                public_quotes=config.generation.public_quotes,
                maximum_sentence_words=config.generation.maximum_sentence_words,
                maximum_paragraph_words=config.generation.maximum_paragraph_words,
            )
            try:
                service.generate(
                    candidate,
                    decision,
                    revision_number=number_row["number"],
                )
            except (BriefValidationError, ValidationError) as error:
                failure_code = str(error).splitlines()[0][:200]
                if isinstance(error, ValidationError):
                    first = error.errors(include_input=False)[0]
                    failure_code = f"{first['type']}:{'.'.join(map(str, first['loc']))}"[:200]
                _complete_generation_attempt(
                    repository,
                    case_id=candidate.case_id,
                    case_watermark=watermark,
                    model=args.model,
                    prompt_version=prompt_version,
                    outcome="validation_denied",
                    failure_code=failure_code,
                )
                return candidate.primary_docket, "validation_denied"
            except Exception as error:
                _complete_generation_attempt(
                    repository,
                    case_id=candidate.case_id,
                    case_watermark=watermark,
                    model=args.model,
                    prompt_version=prompt_version,
                    outcome="request_failed",
                    failure_code=type(error).__name__,
                )
                return candidate.primary_docket, "request_failed"
            _complete_generation_attempt(
                repository,
                case_id=candidate.case_id,
                case_watermark=watermark,
                model=args.model,
                prompt_version=prompt_version,
                outcome="accepted",
            )
            return candidate.primary_docket, "accepted"

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(generate, item) for item in candidates]
            for future in as_completed(futures):
                docket, outcome = future.result()
                print(f"docket={docket} outcome={outcome}", flush=True)
    finally:
        repository.pool.close()


if __name__ == "__main__":
    main()
