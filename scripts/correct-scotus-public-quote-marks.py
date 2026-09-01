#!/usr/bin/env python3
"""Append corrections that remove direct-quotation marks from public SCOTUS prose."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from ragchew.config import ScotusConfig, ServiceSettings
from ragchew.repository import PostgresRepository
from ragchew.scotus.contracts import LegalBriefRevision

_SINGLE_QUOTED = re.compile(r"(?<!\w)'([^'\n]{2,}?)'(?!\w)")


def _clean(value: str) -> tuple[str, bool]:
    cleaned = _SINGLE_QUOTED.sub(r"\1", value)
    return cleaned, cleaned != value


def main() -> None:
    settings = ServiceSettings()
    config = ScotusConfig.from_yaml(settings.scotus_config_path)
    if config.publication.enabled:
        raise RuntimeError("quote-mark correction requires disabled publication")
    repository = PostgresRepository(settings.database_dsn)
    corrected = 0
    try:
        with repository.pool.connection() as connection:
            rows = connection.execute(
                """SELECT DISTINCT ON (case_id) public_payload
                   FROM scotus_brief_revisions
                   ORDER BY case_id,revision_number DESC"""
            ).fetchall()
        for row in rows:
            revision = LegalBriefRevision.model_validate(row["public_payload"])
            changed = False
            title, cleaned = _clean(revision.title)
            changed = changed or cleaned
            dek, cleaned = _clean(revision.dek)
            changed = changed or cleaned
            sections = []
            for section in revision.sections:
                heading, cleaned = _clean(section.heading)
                changed = changed or cleaned
                paragraphs = []
                for paragraph in section.paragraphs:
                    value, cleaned = _clean(paragraph)
                    changed = changed or cleaned
                    paragraphs.append(value)
                sections.append(
                    section.model_copy(
                        update={"heading": heading, "paragraphs": tuple(paragraphs)}
                    )
                )
            analyses = []
            for analysis in revision.argument_analyses:
                heading, cleaned = _clean(analysis.heading)
                changed = changed or cleaned
                paragraphs = []
                for paragraph in analysis.paragraphs:
                    value, cleaned = _clean(paragraph)
                    changed = changed or cleaned
                    paragraphs.append(value)
                analyses.append(
                    analysis.model_copy(
                        update={"heading": heading, "paragraphs": tuple(paragraphs)}
                    )
                )
            if not changed:
                continue
            updated = revision.model_copy(
                update={
                    "revision_id": uuid4(),
                    "revision_number": revision.revision_number + 1,
                    "title": title,
                    "dek": dek,
                    "sections": tuple(sections),
                    "argument_analyses": tuple(analyses),
                    "correction_note": (
                        "Removed direct-quotation marks from public paraphrases."
                    ),
                    "generator_model": "deterministic-public-quote-correction-v1",
                    "created_at": datetime.now(UTC),
                }
            )
            with repository.pool.connection() as connection, connection.transaction():
                connection.execute(
                    """INSERT INTO scotus_brief_revisions
                       (revision_id,brief_id,case_id,argument_id,revision_number,maturity,
                        public_payload,claim_ids,correction_note,generator_model,created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)""",
                    (
                        updated.revision_id,
                        updated.brief_id,
                        updated.case_id,
                        updated.argument_id,
                        updated.revision_number,
                        updated.maturity.value,
                        updated.model_dump_json(),
                        json.dumps([str(value) for value in updated.claim_ids]),
                        updated.correction_note,
                        updated.generator_model,
                        updated.created_at,
                    ),
                )
            corrected += 1
        print(f"corrected={corrected}")
    finally:
        repository.pool.close()


if __name__ == "__main__":
    main()
