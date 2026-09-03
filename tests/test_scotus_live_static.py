from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from pypdf import PdfWriter

from ragchew.config import ProceedingsConfig, ScotusConfig, ServiceSettings
from ragchew.proceedings.contracts import DocumentType
from ragchew.proceedings.discovery import ConditionalRequest
from ragchew.proceedings.sources.http import RequestRateLimiter, SourceResponse
from ragchew.scotus.discovery import DiscoveryMode
from ragchew.scotus.live_static import (
    LiveStaticBatchAdapter,
    _case_documents,
    _CaseInput,
    _descriptor_for_public_argument,
)
from ragchew.scotus.public_contracts import public_case_key
from ragchew.scotus.static_contracts import CostReceiptBundle, ModelAttemptOutcome
from ragchew.scotus.static_pipeline import PublicationGateDenied, StaticBatchResult
from ragchew.scotus.static_state import GeneratedContent, StaticStateStore
from ragchew.scotus.transcript_parser import TranscriptParseError

NOW = datetime(2026, 8, 28, 2, tzinfo=UTC)


def _pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class MemoryStateStore(StaticStateStore):
    def __init__(self, root: Path, content: GeneratedContent | None = None) -> None:
        super().__init__(root)
        self.content = content or GeneratedContent.empty()

    def load(self) -> GeneratedContent:
        return self.content

    def merge_accepted_case(self, *args: Any, **kwargs: Any) -> GeneratedContent:
        return super().merge_accepted_case(*args, **kwargs)

    def update_publication_state(self, *args: Any, **kwargs: Any) -> GeneratedContent:
        return super().update_publication_state(*args, **kwargs)


class CourtFixture:
    def __init__(self) -> None:
        self.index_etag = '"index-1"'
        self.rows = [("25-1", "Example v. Agency", "4/20/26", "25-1.pdf")]
        self.documents: dict[str, tuple[str, bytes, str]] = {
            "/pdfs/transcripts/2025/25-1.pdf": (
                '"transcript-1"',
                _pdf(1),
                "application/pdf",
            ),
            "/docket/docketfiles/html/public/25-1.html": (
                '"docket-1"',
                b"<!doctype html><html><body>Whether the law limits agency power.</body></html>",
                "text/html",
            ),
        }
        self.source_requests: list[tuple[str, ConditionalRequest | None]] = []
        self.document_requests: list[httpx.Request] = []

    def index_html(self) -> bytes:
        rows = "".join(
            f'<tr><td><a href="/pdfs/transcripts/2025/{filename}">{docket}</a> '
            f"{caption}</td><td>{date}</td></tr>"
            for docket, caption, date, filename in self.rows
        )
        return f"<!doctype html><html><body><table>{rows}</table></body></html>".encode()

    def source_get(
        self, url: str, conditional: ConditionalRequest | None = None
    ) -> SourceResponse:
        self.source_requests.append((url, conditional))
        if "argument_transcript" in url:
            if conditional and conditional.etag == self.index_etag:
                return SourceResponse(304, url, {"etag": self.index_etag}, b"")
            return SourceResponse(
                200,
                url,
                {"content-type": "text/html", "etag": self.index_etag},
                self.index_html(),
            )
        return SourceResponse(
            200,
            url,
            {"content-type": "text/html"},
            b"<!doctype html><html><body><table></table></body></html>",
        )

    def document_response(self, request: httpx.Request) -> httpx.Response:
        self.document_requests.append(request)
        value = self.documents.get(request.url.path)
        if value is None:
            return httpx.Response(404)
        etag, body, content_type = value
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"etag": etag})
        return httpx.Response(
            200,
            headers={
                "content-type": content_type,
                "content-length": str(len(body)),
                "etag": etag,
            },
            content=body,
        )


class FixtureSourceFetcher:
    def __init__(self, court: CourtFixture) -> None:
        self.court = court
        self.closed = False

    def get(
        self, url: str, conditional: ConditionalRequest | None = None
    ) -> SourceResponse:
        return self.court.source_get(url, conditional)

    def close(self) -> None:
        self.closed = True


class FixtureBackend:
    name = "fixture-transcript"
    version = "1"

    def extract_pages(self, _file: object) -> tuple[str, ...]:
        return (
            "\n".join(
                (
                    "1 CHIEF JUSTICE ROBERTS: We hear argument in this case.",
                    "2 MR. SMITH: The law limits the agency power.",
                    "3 JUSTICE KAGAN: Does the law permit that action?",
                    "4 MR. SMITH: The text does not permit that action.",
                )
            ),
        )


class FailingBackend(FixtureBackend):
    def extract_pages(self, _file: object) -> tuple[str, ...]:
        raise TranscriptParseError("synthetic parser failure")


class MockOpenAI:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=self)
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    def create(self, **request: Any) -> object:
        self.requests.append(request)
        name = request["response_format"]["json_schema"]["name"]
        user = json.loads(request["messages"][1]["content"])
        content = (
            self._extraction(user)
            if name == "scotus_legal_observations"
            else self._brief(user)
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))]
        )

    @staticmethod
    def _extraction(evidence: list[dict[str, Any]]) -> dict[str, object]:
        opening = next(item for item in evidence if "hear argument" in item["text"])
        advocate = next(item for item in evidence if "limits the agency" in item["text"])
        question = next(item for item in evidence if "Does the law" in item["text"])
        return {
            "observations": [
                {
                    "observation_type": "procedural_posture",
                    "legal_status": "described",
                    "certainty": "direct",
                    "raw_value": "The Court heard argument in the case.",
                    "normalized_value": "The Court heard argument in the case.",
                    "attribution": None,
                    "speaker_name": opening["speaker"],
                    "speaker_kind": "justice",
                    "identity_basis": "official_transcript_label",
                    "authority_citations": [],
                    "confidence": 1,
                    "evidence": [
                        {
                            "block_id": opening["block_id"],
                            "quote": "We hear argument in this case.",
                        }
                    ],
                    "supersedes_observation_id": None,
                },
                {
                    "observation_type": "advocate_contention",
                    "legal_status": "asserted",
                    "certainty": "attributed",
                    "raw_value": "The law limits the agency power.",
                    "normalized_value": "The law limits the agency power.",
                    "attribution": "Mr. Smith, counsel",
                    "speaker_name": advocate["speaker"],
                    "speaker_kind": "advocate",
                    "identity_basis": "official_transcript_label",
                    "authority_citations": [],
                    "confidence": 1,
                    "evidence": [
                        {
                            "block_id": advocate["block_id"],
                            "quote": "The law limits the agency power.",
                        }
                    ],
                    "supersedes_observation_id": None,
                },
                {
                    "observation_type": "justice_question",
                    "legal_status": "questioned",
                    "certainty": "attributed",
                    "raw_value": "A justice asked whether the law permits the action.",
                    "normalized_value": "A justice asked whether the law permits the action.",
                    "attribution": None,
                    "speaker_name": question["speaker"],
                    "speaker_kind": "justice",
                    "identity_basis": "official_transcript_label",
                    "authority_citations": [],
                    "confidence": 1,
                    "evidence": [
                        {
                            "block_id": question["block_id"],
                            "quote": "Does the law permit that action?",
                        }
                    ],
                    "supersedes_observation_id": None,
                },
            ]
        }

    @staticmethod
    def _brief(user: dict[str, Any]) -> dict[str, object]:
        claims = user["claims"]
        all_ids = [claim["claim_id"] for claim in claims]
        analyses = []
        for position, session in enumerate(user["argument_sessions"], 1):
            session_ids = [
                claim["claim_id"]
                for claim in claims
                if claim["argument_session"]
                and claim["argument_session"]["argument_id"] == session["argument_id"]
            ]
            analyses.append(
                {
                    "argument_id": session["argument_id"],
                    "heading": f"Argument session {position}",
                    "paragraphs": [
                        "A side explained why the law limits agency power.",
                        "The justices tested that explanation against the text.",
                    ],
                    "claim_ids": session_ids,
                }
            )
        return {
            "title": "Agency power under review",
            "title_claim_ids": all_ids,
            "dek": "The case concerns legal limits on agency power.",
            "dek_claim_ids": all_ids,
            "sections": [
                {
                    "heading": "What this case is about",
                    "paragraphs": ["The case concerns legal limits on agency power."],
                    "claim_ids": all_ids,
                }
            ],
            "argument_analyses": analyses,
        }

    def close(self) -> None:
        self.closed = True


def live_config() -> ScotusConfig:
    config = ScotusConfig.from_yaml("config/scotus.yaml")
    approvals = config.approvals.model_copy(
        update={
            "source_review_approved": True,
            "licenses_approved": True,
            "origin_approved": True,
            "publication_secret_configured": True,
            "launch_approved": True,
        }
    )
    return config.model_copy(
        update={
            "enabled": True,
            "generation": config.generation.model_copy(
                update={"brief_generation_enabled": True}
            ),
            "publication": config.publication.model_copy(update={"enabled": True}),
            "approvals": approvals,
            "discovery": config.discovery.model_copy(
                update={"terms": ["2025"], "historical_rechecks_per_run": 0}
            ),
        }
    )


def proceedings_config() -> ProceedingsConfig:
    config = ProceedingsConfig.from_yaml("config/proceedings.yaml")
    sources = dict(config.sources)
    sources["supreme_court"] = sources["supreme_court"].model_copy(
        update={"enabled": True}
    )
    return config.model_copy(update={"sources": sources})


def _immediate_rate_limiter(_interval: float) -> RequestRateLimiter:
    return RequestRateLimiter(0)


def build_adapter(
    court: CourtFixture,
    model: MockOpenAI,
    *,
    backend: type[FixtureBackend] = FixtureBackend,
    rate_limiter_factory: Any = _immediate_rate_limiter,
) -> LiveStaticBatchAdapter:
    settings = ServiceSettings(
        openai_api_key=SecretStr("synthetic-test-key"),
        proceedings_config_path="unused",
        source_user_agent="ragchew-test contact=test@example.test",
    )
    return LiveStaticBatchAdapter(
        settings_factory=lambda: settings,
        proceedings_loader=lambda _path: proceedings_config(),
        source_fetcher_factory=lambda _settings, _config: FixtureSourceFetcher(court),
        document_client_factory=lambda _settings, _config: httpx.Client(
            transport=httpx.MockTransport(court.document_response),
            follow_redirects=False,
        ),
        openai_client_factory=lambda _settings, _config: model,
        parser_backend_factory=backend,
        rate_limiter_factory=rate_limiter_factory,
        clock=lambda: NOW,
    )


def run(
    tmp_path: Path,
    store: MemoryStateStore,
    court: CourtFixture,
    model: MockOpenAI,
    *,
    config: ScotusConfig | None = None,
    backend: type[FixtureBackend] = FixtureBackend,
) -> StaticBatchResult:
    return build_adapter(court, model, backend=backend).run(
        state_store=store,
        config=config or live_config(),
        mode=DiscoveryMode.NIGHTLY,
        runner_temp=tmp_path / "private",
        authorized_replay=False,
    )


def test_live_adapter_checks_all_gates_before_factories_or_traffic(tmp_path: Path) -> None:
    called = False

    def settings() -> ServiceSettings:
        nonlocal called
        called = True
        raise AssertionError

    adapter = LiveStaticBatchAdapter(settings_factory=settings)
    with pytest.raises(PublicationGateDenied):
        adapter.run(
            state_store=StaticStateStore(tmp_path / "state"),
            config=ScotusConfig.from_yaml("config/scotus.yaml"),
            mode=DiscoveryMode.NIGHTLY,
            runner_temp=tmp_path,
            authorized_replay=False,
        )
    assert not called
    assert not list(tmp_path.iterdir())


def test_new_transcript_runs_grounded_pipeline_with_budget_and_cleanup(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    model = MockOpenAI()
    store = MemoryStateStore(tmp_path / "state")
    result = run(tmp_path, store, court, model)

    assert result.publishable
    assert result.changed_case_keys == ("2025-25-1",)
    assert result.content.projection is not None
    case = result.content.projection.cases[0]
    assert len(case.arguments) == 1
    assert len(result.content.publication.documents) == 2
    assert [request["response_format"]["json_schema"]["name"] for request in model.requests] == [
        "scotus_legal_observations",
        "scotus_legal_brief",
    ]
    receipts = CostReceiptBundle.model_validate_json(
        (tmp_path / "private/public-cost-receipts.json").read_bytes()
    )
    assert {receipt.stage for receipt in receipts.receipts} == {"extraction", "brief"}
    assert all(receipt.outcome is ModelAttemptOutcome.SUCCEEDED for receipt in receipts.receipts)
    assert sum(receipt.call_count for receipt in receipts.receipts) == len(model.requests)
    assert not list((tmp_path / "private").glob("ragchew-*"))


def test_conditional_304_carries_exact_case_without_model_call(tmp_path: Path) -> None:
    court = CourtFixture()
    first_model = MockOpenAI()
    store = MemoryStateStore(tmp_path / "state")
    first = run(tmp_path, store, court, first_model)
    store.content = first.content
    prior = first.content.revisions[("2025-25-1", 1)].serialized

    model = MockOpenAI()
    unchanged = run(tmp_path, store, court, model)
    assert unchanged.publishable and unchanged.no_public_change
    assert unchanged.changed_case_keys == ()
    assert unchanged.content.revisions[("2025-25-1", 1)].serialized == prior
    assert model.requests == []
    assert any(request.headers.get("if-none-match") for request in court.document_requests)


@pytest.mark.parametrize("new_url", [False, True])
def test_same_or_new_url_transcript_correction_appends_revision(
    tmp_path: Path, new_url: bool
) -> None:
    court = CourtFixture()
    store = MemoryStateStore(tmp_path / "state")
    first = run(tmp_path, store, court, MockOpenAI())
    store.content = first.content

    transcript_path = "/pdfs/transcripts/2025/25-1.pdf"
    replacement_path = "/pdfs/transcripts/2025/25-1-corrected.pdf"
    if new_url:
        court.rows[0] = ("25-1", "Example v. Agency", "4/20/26", "25-1-corrected.pdf")
        court.index_etag = '"index-2"'
        transcript_path = replacement_path
    court.documents[transcript_path] = (
        '"transcript-2"',
        _pdf(2),
        "application/pdf",
    )

    corrected = run(tmp_path, store, court, MockOpenAI())
    assert corrected.publishable
    assert corrected.content.projection is not None
    case = corrected.content.projection.cases[0]
    assert [revision.revision_number for revision in case.revisions] == [1, 2]
    assert case.revisions[-1].correction_note
    transcript = next(
        item
        for item in corrected.content.publication.documents
        if item.document_kind == "transcript"
    )
    assert transcript.revision_number == 2
    assert transcript.official_url.endswith(Path(transcript_path).name)


def test_reargument_reprocesses_every_session_under_one_case_budget(tmp_path: Path) -> None:
    court = CourtFixture()
    store = MemoryStateStore(tmp_path / "state")
    first = run(tmp_path, store, court, MockOpenAI())
    store.content = first.content
    court.rows.append(("25-1", "Example v. Agency", "6/20/26", "25-1-reargued.pdf"))
    court.index_etag = '"index-2"'
    court.documents["/pdfs/transcripts/2025/25-1-reargued.pdf"] = (
        '"transcript-2"',
        _pdf(1),
        "application/pdf",
    )

    model = MockOpenAI()
    result = run(tmp_path, store, court, model)
    assert result.content.projection is not None
    case = result.content.projection.cases[0]
    assert result.publishable and len(case.arguments) == 2
    assert case.arguments[-1].reargument
    assert case.revisions[-1].correction_note
    names = [request["response_format"]["json_schema"]["name"] for request in model.requests]
    assert names.count("scotus_legal_observations") == 2
    assert names.count("scotus_legal_brief") == 1


def test_failure_and_model_budget_exhaustion_keep_prior_case_active(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    store = MemoryStateStore(tmp_path / "state")
    first = run(tmp_path, store, court, MockOpenAI())
    store.content = first.content
    assert first.content.projection is not None
    prior_case = first.content.projection.cases[0]
    court.documents["/pdfs/transcripts/2025/25-1.pdf"] = (
        '"transcript-2"',
        _pdf(2),
        "application/pdf",
    )

    failed = run(tmp_path, store, court, MockOpenAI(), backend=FailingBackend)
    assert not failed.publishable
    assert failed.content.projection is not None
    assert failed.content.projection.cases[0] == prior_case
    assert failed.pending_case_keys == ("2025-25-1",)
    assert not list((tmp_path / "private").glob("ragchew-*"))

    court.rows.append(("25-1", "Example v. Agency", "6/20/26", "25-1-reargued.pdf"))
    court.index_etag = '"index-2"'
    court.documents["/pdfs/transcripts/2025/25-1-reargued.pdf"] = (
        '"transcript-3"',
        _pdf(1),
        "application/pdf",
    )
    config = live_config()
    config = config.model_copy(
        update={
            "model_budget": config.model_budget.model_copy(
                update={"maximum_extraction_calls_per_run": 1}
            )
        }
    )
    blocked = run(tmp_path, store, court, MockOpenAI(), config=config)
    assert not blocked.publishable
    assert blocked.content.projection is not None
    assert blocked.content.projection.cases[0] == prior_case
    assert blocked.pending_case_keys == ("2025-25-1",)


def test_prior_reconstruction_preserves_typed_document_identity(tmp_path: Path) -> None:
    court = CourtFixture()
    store = MemoryStateStore(tmp_path / "state")
    content = run(tmp_path, store, court, MockOpenAI()).content
    assert content.projection is not None
    case = content.projection.cases[0]
    transcript = next(
        item for item in content.publication.documents if item.document_kind == "transcript"
    )
    prior_docket = next(
        item for item in content.publication.documents if item.document_kind == "docket"
    )
    order_url = "https://www.supremecourt.gov/orders/courtorders/25-1.pdf"
    order = prior_docket.model_copy(
        update={
            "logical_key": "2025-25-1:order:0123456789abcdef01234567",
            "document_kind": "order",
            "official_url": order_url,
        }
    )
    case = case.model_copy(update={"official_disposition_urls": (order_url,)})

    candidate = _descriptor_for_public_argument(case, 0, (transcript, order))
    assert candidate.docket_documents == ()
    assert [item.document_type for item in candidate.related_documents] == [
        DocumentType.ORDER
    ]
    source = _CaseInput(
        case_key=public_case_key(case.term, case.primary_docket),
        term=case.term,
        primary_docket=case.primary_docket,
        caption=case.caption,
        sessions=(candidate,),
        prior=case,
        document_logical_keys={
            (DocumentType.OFFICIAL_TRANSCRIPT, transcript.official_url): (
                transcript.logical_key
            ),
            (DocumentType.ORDER, order_url): order.logical_key,
        },
    )
    reconstructed = _case_documents(source)
    assert {item.logical_key for item in reconstructed} == {
        transcript.logical_key,
        order.logical_key,
    }
    assert {item.kind.value for item in reconstructed} == {"transcript", "order"}


def test_one_shared_crawl_delay_covers_source_and_document_requests(tmp_path: Path) -> None:
    class CountingLimiter:
        def __init__(self, interval: float) -> None:
            self.interval = interval
            self.calls = 0

        def wait(self) -> None:
            self.calls += 1

    limiter: CountingLimiter | None = None

    def limiter_factory(interval: float) -> CountingLimiter:
        nonlocal limiter
        limiter = CountingLimiter(interval)
        return limiter

    court = CourtFixture()
    adapter = build_adapter(court, MockOpenAI(), rate_limiter_factory=limiter_factory)
    adapter.run(
        state_store=MemoryStateStore(tmp_path / "state"),
        config=live_config(),
        mode=DiscoveryMode.NIGHTLY,
        runner_temp=tmp_path / "private",
        authorized_replay=False,
    )
    assert limiter is not None
    assert limiter.interval == 1.0
    assert limiter.calls == len(court.source_requests) + len(court.document_requests)


def test_processor_migration_resumes_bounded_cases_before_global_promotion(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    store = MemoryStateStore(tmp_path / "state")
    first = run(tmp_path, store, court, MockOpenAI())
    store.content = first.content

    court.rows.append(("25-2", "Second v. Agency", "4/21/26", "25-2.pdf"))
    court.index_etag = '"index-2"'
    court.documents["/pdfs/transcripts/2025/25-2.pdf"] = (
        '"transcript-2"',
        _pdf(1),
        "application/pdf",
    )
    court.documents["/docket/docketfiles/html/public/25-2.html"] = (
        '"docket-2"',
        b"<!doctype html><html><body>Second synthetic docket.</body></html>",
        "text/html",
    )
    second = run(tmp_path, store, court, MockOpenAI())
    store.content = second.content
    old_processor = second.content.publication.processor
    assert old_processor is not None
    assert len(second.content.publication.cases) == 2

    base = live_config()
    migrating = base.model_copy(
        update={"parser": base.parser.model_copy(update={"version": "2"})}
    )
    partial = run(tmp_path, store, court, MockOpenAI(), config=migrating)
    assert partial.content.publication.processor == old_processor
    assert len(partial.pending_case_keys) == 1
    fingerprints = {
        pointer.processor_sha256 for pointer in partial.content.publication.cases
    }
    assert len(fingerprints) == 2

    store.content = partial.content
    completed = run(tmp_path, store, court, MockOpenAI(), config=migrating)
    assert completed.pending_case_keys == ()
    promoted = completed.content.publication.processor
    assert promoted is not None and promoted != old_processor
    assert {
        pointer.processor_sha256 for pointer in completed.content.publication.cases
    } == {promoted.composite_sha256}
