from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from ragchew.config import ScotusConfig
from ragchew.scotus.legacy_export import build_legacy_bootstrap
from ragchew.scotus.poc_export import (
    PocExportError,
    PostgresPocBriefReader,
    export_poc_generated_content,
)
from ragchew.scotus.public_contracts import (
    PublicCaseBrief,
    ScotusPublicProjection,
    public_case_slug,
)
from ragchew.scotus.static_state import StaticStateStore
from ragchew.scotus.static_urls import StaticUrlPolicy

NOW = datetime(2026, 8, 28, 3, 17, tzinfo=UTC)
CASE_ID = UUID("10000000-0000-0000-0000-000000000001")
BRIEF_ID = UUID("20000000-0000-0000-0000-000000000001")
ARGUMENT_ID = UUID("30000000-0000-0000-0000-000000000001")
CLAIM_ID = UUID("40000000-0000-0000-0000-000000000001")


class Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class Connection:
    def __init__(self, responses: dict[str, list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def transaction(self) -> Any:
        return nullcontext()

    def execute(self, query: str, _parameters: Any = None) -> Result:
        normalized = " ".join(query.split())
        self.queries.append(normalized)
        if normalized.startswith("SET TRANSACTION"):
            return Result([])
        for marker, rows in self.responses.items():
            if marker in normalized:
                return Result(rows)
        raise AssertionError(f"unexpected recovery query: {normalized}")


class Pool:
    def __init__(self, connection: Connection) -> None:
        self.value = connection

    def connection(self) -> Any:
        return nullcontext(self.value)


def brief_row(number: int) -> dict[str, Any]:
    created_at = NOW + timedelta(hours=number)
    return {
        "brief_id": BRIEF_ID,
        "case_id": CASE_ID,
        "argument_id": ARGUMENT_ID,
        "revision_number": number,
        "maturity": "corrected" if number == 2 else "official_transcript",
        "correction_note": "Corrected accepted analysis." if number == 2 else None,
        "created_at": created_at,
        "term": "2025",
        "primary_docket": "25-466",
        "caption": "Synthetic Example v. Agency",
        "case_status": "corrected",
        "payload_schema_version": "1.0",
        "payload_brief_id": str(BRIEF_ID),
        "payload_case_id": str(CASE_ID),
        "payload_argument_id": str(ARGUMENT_ID),
        "payload_revision_number": str(number),
        "payload_maturity": "corrected" if number == 2 else "official_transcript",
        "payload_correction_note": "Corrected accepted analysis." if number == 2 else None,
        "payload_created_at": created_at.isoformat(),
        "title": f"Accepted public title {number}",
        "title_claim_ids": [str(CLAIM_ID)],
        "dek": "Accepted public summary.",
        "dek_claim_ids": [str(CLAIM_ID)],
        "sections": [
            {
                "heading": "Question presented",
                "paragraphs": ["Accepted public analysis."],
                "claim_ids": [str(CLAIM_ID)],
            }
        ],
        "argument_analyses": [
            {
                "argument_id": str(ARGUMENT_ID),
                "sequence": 1,
                "argument_date": (NOW - timedelta(days=1)).isoformat(),
                "reargument": False,
                "heading": "Oral argument",
                "paragraphs": ["First accepted point.", "Second accepted point."],
                "claim_ids": [str(CLAIM_ID)],
            }
        ],
        "claim_ids": [str(CLAIM_ID)],
    }


def responses(
    *, projection_count: int = 0, include_claim: bool = True
) -> dict[str, list[dict[str, Any]]]:
    return {
        "count(*) AS count FROM scotus_public_projections": [{"count": projection_count}],
        "FROM scotus_brief_revisions b": [brief_row(1), brief_row(2)],
        "FROM scotus_approved_claims": (
            [
                {
                    "claim_id": CLAIM_ID,
                    "case_id": CASE_ID,
                    "official_url": (
                        "https://www.supremecourt.gov/oral_arguments/"
                        "argument_transcripts/2025/25-466.pdf"
                    ),
                    "public_source_label": "transcript",
                    "page_label": "file page 5",
                }
            ]
            if include_claim
            else []
        ),
        "FROM scotus_argument_sessions a": [
            {
                "argument_id": ARGUMENT_ID,
                "case_id": CASE_ID,
                "argument_date": NOW - timedelta(days=1),
                "sequence": 1,
                "reargument": False,
                "official_detail_url": (
                    "https://www.supremecourt.gov/oral_arguments/audio/2025/25-466"
                ),
                "official_transcript_url": (
                    "https://www.supremecourt.gov/oral_arguments/"
                    "argument_transcripts/2025/25-466.pdf"
                ),
            }
        ],
        "FROM scotus_case_history": [
            {
                "case_id": CASE_ID,
                "status": "argued",
                "changed_at": NOW,
                "history_id": 1,
            },
            {
                "case_id": CASE_ID,
                "status": "corrected",
                "changed_at": NOW + timedelta(hours=1, minutes=30),
                "history_id": 2,
            },
        ],
        "FROM scotus_document_revisions WHERE": [
            {
                "case_id": CASE_ID,
                "official_url": "https://www.supremecourt.gov/opinions/25pdf/25-466.pdf",
                "available_at": NOW + timedelta(hours=1, minutes=30),
                "document_kind": "opinion",
            }
        ],
    }


def reader_for(
    values: dict[str, list[dict[str, Any]]],
) -> tuple[PostgresPocBriefReader, Connection]:
    connection = Connection(values)
    return PostgresPocBriefReader("", pool=Pool(connection)), connection  # type: ignore[arg-type]


def parent_content(case: PublicCaseBrief | None = None) -> Any:
    projection = ScotusPublicProjection(
        watermark=NOW, generated_at=NOW, cases=((case,) if case is not None else ())
    )
    return build_legacy_bootstrap(
        (projection,),
        source_commit="a" * 40,
        config_sha256="b" * 64,
        build_epoch=NOW,
        tool_version="empty-bootstrap-v1",
    )


def test_reader_reconstructs_every_revision_from_allowlisted_public_fields() -> None:
    reader, connection = reader_for(responses())
    histories = reader.case_revisions()

    assert len(histories) == 1
    first, corrected = histories[0]
    assert first.title == "Accepted public title 1"
    assert corrected.title == "Accepted public title 2"
    assert len(first.revisions) == 1
    assert len(corrected.revisions) == 2
    assert [event.status.value for event in first.case_history] == ["argued"]
    assert [event.status.value for event in corrected.case_history] == ["argued", "corrected"]
    assert first.official_disposition_urls == ()
    assert corrected.official_disposition_urls == (
        "https://www.supremecourt.gov/opinions/25pdf/25-466.pdf",
    )
    assert corrected.arguments[0].official_transcript_url.endswith("25-466.pdf")

    sql = "\n".join(connection.queries).casefold()
    assert "repeatable read read only" in sql
    assert "public_payload ->> 'title'" in sql
    for forbidden in (
        "generator_model",
        "source_observation_ids",
        "public_value",
        "object_key",
        "payload_private",
        "raw_text_private",
        "text_private",
        "scotus_legal_observations",
        "scotus_transcript_lines",
    ):
        assert forbidden not in sql


def test_reader_refuses_nonempty_projection_or_missing_provenance() -> None:
    reader, connection = reader_for(responses(projection_count=1))
    with pytest.raises(PocExportError, match="requires an empty"):
        reader.case_revisions()
    assert not any("scotus_brief_revisions" in query for query in connection.queries)

    reader, _ = reader_for(responses(include_claim=False))
    with pytest.raises(PocExportError, match="missing or cross-case"):
        reader.case_revisions()


def test_export_merges_history_onto_parent_and_emits_only_validated_state(
    tmp_path: Path,
) -> None:
    reader, _ = reader_for(responses())
    recovered = reader.case_revisions()
    source_case = recovered[0][0]
    unrelated = source_case.model_copy(
        update={
            "term": "2024",
            "primary_docket": "24-999",
            "slug": public_case_slug("2024", "24-999", source_case.caption),
        }
    )
    parent = parent_content(unrelated)
    destination = tmp_path / "poc-candidate"
    site_destination = tmp_path / "site-candidate"
    static = ScotusConfig.from_yaml("config/scotus.yaml").static
    urls = StaticUrlPolicy(
        static.canonical_origin, static.project_base_path, static.section_path
    )
    export_poc_generated_content(
        parent,
        recovered,
        destination,
        site_destination=site_destination,
        urls=urls,
        source_commit="c" * 40,
        config_sha256="d" * 64,
        build_epoch=NOW + timedelta(hours=3),
    )

    loaded = StaticStateStore(destination).load()
    assert loaded.release is not None
    assert loaded.release.previous_release_id == parent.publication.active_release_id
    assert loaded.release.files
    assert loaded.release.page_count > loaded.release.case_count
    assert loaded.cost_ledger == parent.cost_ledger
    assert len(loaded.publication.cases) == 2
    assert loaded.projection is not None and unrelated in loaded.projection.cases
    pointer = next(item for item in loaded.publication.cases if item.case_key == "2025-25-466")
    assert pointer.active_revision == 2
    key = pointer.case_key
    assert loaded.revisions[(key, 1)].record.case.title == "Accepted public title 1"
    assert loaded.revisions[(key, 2)].record.case.title == "Accepted public title 2"
    recovered_active = next(
        case for case in loaded.projection.cases if case.primary_docket == "25-466"
    )
    assert recovered_active.title.endswith("2")

    emitted = b"\n".join(path.read_bytes() for path in destination.rglob("*.json"))
    for forbidden in (
        b"10000000-0000-0000-0000-000000000001",
        b"claim_id",
        b"observation",
        b"generator_model",
        b"object_key",
        b"credential",
    ):
        assert forbidden not in emitted
