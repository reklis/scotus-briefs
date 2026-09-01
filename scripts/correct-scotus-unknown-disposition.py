#!/usr/bin/env python3
"""Append corrections for unsupported claims that no Supreme Court disposition exists."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from ragchew.config import ScotusConfig, ServiceSettings
from ragchew.repository import PostgresRepository
from ragchew.scotus.contracts import BriefArgumentAnalysis, BriefSection, LegalBriefRevision

_RECORD_NOTE = (
    "This article currently covers the argument record. "
    "Use the official docket link for later case activity."
)
_UNSUPPORTED = re.compile(
    r"\b(?:the (?:Supreme )?Court (?:has not|hasn't) (?:yet )?(?:decided|ruled|issued)|"
    r"no (?:decision|ruling|opinion|order|outcome) (?:has been|was|can be) "
    r"(?:issued|entered|reached|predicted)|"
    r"no outcome has been reached)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?]+[.!?]?", re.MULTILINE)


def _remove_unsupported(value: str) -> tuple[str, bool]:
    sentences = [item.group(0).strip() for item in _SENTENCE.finditer(value)]
    kept = [item for item in sentences if item and not _UNSUPPORTED.search(item)]
    return " ".join(kept), len(kept) != len(sentences)


def main() -> None:
    settings = ServiceSettings()
    config = ScotusConfig.from_yaml(settings.scotus_config_path)
    if config.publication.enabled:
        raise RuntimeError("disposition correction requires disabled publication")
    repository = PostgresRepository(settings.database_dsn)
    corrected = 0
    try:
        with repository.pool.connection() as connection:
            rows = connection.execute(
                """SELECT DISTINCT ON (case_id) * FROM scotus_brief_revisions
                   ORDER BY case_id,revision_number DESC"""
            ).fetchall()
        for row in rows:
            revision = LegalBriefRevision.model_validate(row["public_payload"])
            changed = False
            sections: list[BriefSection] = []
            for section in revision.sections:
                if section.heading.casefold() == "what happens next":
                    paragraphs = (_RECORD_NOTE,)
                    changed = changed or section.paragraphs != paragraphs
                else:
                    cleaned: list[str] = []
                    for paragraph in section.paragraphs:
                        value, removed = _remove_unsupported(paragraph)
                        changed = changed or removed
                        if value:
                            cleaned.append(value)
                    paragraphs = tuple(cleaned) or section.paragraphs
                sections.append(section.model_copy(update={"paragraphs": paragraphs}))
            analyses: list[BriefArgumentAnalysis] = []
            for analysis in revision.argument_analyses:
                cleaned = []
                for paragraph in analysis.paragraphs:
                    value, removed = _remove_unsupported(paragraph)
                    changed = changed or removed
                    if value:
                        cleaned.append(value)
                analyses.append(
                    analysis.model_copy(
                        update={"paragraphs": tuple(cleaned) or analysis.paragraphs}
                    )
                )
            if not changed:
                continue
            updated = revision.model_copy(
                update={
                    "revision_id": uuid4(),
                    "revision_number": revision.revision_number + 1,
                    "sections": tuple(sections),
                    "argument_analyses": tuple(analyses),
                    "correction_note": (
                        "Corrected unsupported no-ruling language and clarified that the "
                        "article's disposition summary is incomplete."
                    ),
                    "generator_model": "deterministic-disposition-correction-v1",
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
