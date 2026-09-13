from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from ragchew.config import ProceedingsConfig, ScotusConfig, ServiceSettings
from ragchew.proceedings.contracts import DocumentType
from ragchew.proceedings.discovery import ConditionalRequest
from ragchew.proceedings.sources.http import (
    RequestRateLimiter,
    SourceFetchError,
    SourceResponse,
)
from ragchew.proceedings.sources.supreme_court import SupremeCourtAdapter
from ragchew.scotus.briefs import BriefValidationError
from ragchew.scotus.contracts import (
    LegalObservationType,
    LegalStatus,
    ScotusDocumentKind,
    SpeakerIdentityBasis,
    SpeakerKind,
)
from ragchew.scotus.discovery import DiscoveryMode
from ragchew.scotus.extraction import LegalEvidenceBlock
from ragchew.scotus.live_static import (
    LiveStaticBatchAdapter,
    LiveStaticDiscovery,
    _case_documents,
    _CaseInput,
    _court_action_observation,
    _default_ollama_client,
    _descriptor_for_public_argument,
    _legal_analysis_observations,
    _opinion_page_attribution,
    _outstanding_supported_case_keys,
    _procedural_path_observation,
    _repair_diagnostic,
    _TransientEvidenceTransport,
)
from ragchew.scotus.public_contracts import (
    PublicCaseBrief,
    ScotusPublicProjection,
    public_case_key,
)
from ragchew.scotus.reader_guides import ReaderGuideFieldKind, ReaderGuideFieldPath
from ragchew.scotus.static_contracts import (
    CanaryAggregate,
    CanaryFailureCount,
    CanaryReviewerDecision,
    ConditionalValidators,
    ContentIntegrity,
    CostLedger,
    CostReceiptBundle,
    EditorialBackfillState,
    EditorialRolloutStage,
    LogicalSourceState,
    ModelAttemptOutcome,
    ModelRetryStatus,
    PendingModelRetry,
    PendingReason,
    PendingWork,
    RetryFailureCode,
    canonical_json_bytes,
    sha256_hex,
)
from ragchew.scotus.static_pipeline import (
    PublicationGateDenied,
    StaticBatchResult,
    UnifiedRunBudget,
)
from ragchew.scotus.static_state import GeneratedContent, StaticStateStore, StoredCaseRevision
from ragchew.scotus.transcript_parser import TranscriptParseError

NOW = datetime(2026, 8, 28, 2, tzinfo=UTC)
MODEL_DIGEST = "8f2632d0faa422ff60435bc0095575d032a8b4a0f728df034d90ea654ffb60bb"


def _pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _text_pdf(*lines: str) -> bytes:
    lines = (*lines, "The Court explained its reasoning for the result.")
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    commands = ["BT /F1 12 Tf 72 720 Td"]
    for index, line in enumerate(escaped):
        if index:
            commands.append("0 -18 Td")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream = DecodedStreamObject()
    stream.set_data(" ".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
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
        self.slip_etag = '"slip-1"'
        self.rows = [("25-1", "Example v. Agency", "4/20/26", "25-1.pdf")]
        self.slip_rows: list[tuple[str, str, str, str, str, str]] = []
        self.documents: dict[str, tuple[str, bytes, str]] = {
            "/pdfs/transcripts/2025/25-1.pdf": (
                '"transcript-1"',
                _pdf(1),
                "application/pdf",
            ),
            "/docket/docketfiles/html/public/25-1.html": (
                '"docket-1"',
                b"<!doctype html><html><body>Docket 25-1. "
                b"Whether the law limits agency power.</body></html>",
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

    def slip_html(self) -> bytes:
        rows = "".join(
            f"<tr><td>{release}</td><td>{date}</td><td>{docket}</td>"
            f"<td><a href='/opinions/25pdf/{filename}'>{caption}</a></td>"
            f"<td>{author}</td><td></td></tr>"
            for release, date, docket, caption, author, filename in self.slip_rows
        )
        return (
            "<!doctype html><table><tr><th>R-</th><th>Date</th>"
            "<th>Docket</th><th>Name</th><th>J.</th><th>Citation</th></tr>"
            f"{rows}</table>"
        ).encode()

    def source_get(self, url: str, conditional: ConditionalRequest | None = None) -> SourceResponse:
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
        if "slipopinion" in url:
            if conditional and conditional.etag == self.slip_etag:
                return SourceResponse(304, url, {"etag": self.slip_etag}, b"")
            return SourceResponse(
                200,
                url,
                {"content-type": "text/html", "etag": self.slip_etag},
                self.slip_html(),
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

    def get(self, url: str, conditional: ConditionalRequest | None = None) -> SourceResponse:
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
    def __init__(
        self,
        *,
        model_name: str = "cogito:70b",
        model_digest: str = MODEL_DIGEST,
    ) -> None:
        self.chat = SimpleNamespace(completions=self)
        self.models = SimpleNamespace(
            list=lambda: SimpleNamespace(data=[SimpleNamespace(id=model_name)])
        )
        self.inventory = {"models": [{"name": model_name, "digest": model_digest}]}
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    def create(self, **request: Any) -> object:
        self.requests.append(request)
        name = request["response_format"]["json_schema"]["name"]
        user = json.loads(request["messages"][1]["content"])
        if name == "scotus_legal_observations":
            content = self._extraction(user["evidence"])
        elif name == "compact_reader_guide":
            content = self._compact_brief(user)
        elif name == "reader_guide_field_repair":
            claims = user["support_packet"]["claims"]
            support = " ".join(claim["value"] for claim in claims)
            if "injunction" in support.casefold() and "stay" in support.casefold():
                content = {
                    "text": (
                        "The Supreme Court temporarily stayed the District Court's injunction, "
                        "a court "
                        "order that blocks government action, while the case continues."
                    )
                }
            else:
                content = {"text": support}
        else:
            content = self._brief(user)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))]
        )

    @staticmethod
    def _extraction(evidence: list[dict[str, Any]]) -> dict[str, object]:
        if not any("hear argument" in item["text"] for item in evidence):
            docket = next(item for item in evidence if item["kind"] == "docket")
            opinion = next(item for item in evidence if item["kind"] == "opinion")
            return {
                "observations": [
                    {
                        "observation_type": "procedural_posture",
                        "legal_status": "described",
                        "certainty": "direct",
                        "raw_value": "The official docket identifies this emergency application.",
                        "normalized_value": (
                            "The official docket identifies this emergency application."
                        ),
                        "attribution": docket["attribution"],
                        "speaker_name": None,
                        "speaker_kind": "unknown",
                        "identity_basis": "anonymous",
                        "authority_citations": [],
                        "confidence": 1,
                        "evidence": [{"block_id": docket["block_id"], "quote": "Docket 25A810"}],
                        "supersedes_observation_id": None,
                    },
                    {
                        "observation_type": "case_background",
                        "legal_status": "described",
                        "certainty": "direct",
                        "raw_value": "Emergency Applicant sought relief from Agency.",
                        "normalized_value": "Emergency Applicant sought relief from Agency.",
                        "attribution": opinion["attribution"],
                        "speaker_name": None,
                        "speaker_kind": "unknown",
                        "identity_basis": "anonymous",
                        "authority_citations": [],
                        "confidence": 1,
                        "evidence": [
                            {
                                "block_id": opinion["block_id"],
                                "quote": "Emergency Applicant sought relief from Agency.",
                            }
                        ],
                        "supersedes_observation_id": None,
                    },
                    {
                        "observation_type": "requested_disposition",
                        "legal_status": "requested",
                        "certainty": "direct",
                        "raw_value": "Emergency Applicant sought relief from Agency.",
                        "normalized_value": "Emergency Applicant sought relief from Agency.",
                        "attribution": opinion["attribution"],
                        "speaker_name": None,
                        "speaker_kind": "unknown",
                        "identity_basis": "anonymous",
                        "authority_citations": [],
                        "confidence": 1,
                        "evidence": [
                            {
                                "block_id": opinion["block_id"],
                                "quote": "Emergency Applicant sought relief from Agency.",
                            }
                        ],
                        "supersedes_observation_id": None,
                    },
                    {
                        "observation_type": "question_presented",
                        "legal_status": "described",
                        "certainty": "direct",
                        "raw_value": "Emergency Applicant sought relief from Agency.",
                        "normalized_value": "The legal issue concerns emergency relief.",
                        "attribution": opinion["attribution"],
                        "speaker_name": None,
                        "speaker_kind": "unknown",
                        "identity_basis": "anonymous",
                        "authority_citations": [],
                        "confidence": 1,
                        "evidence": [
                            {
                                "block_id": opinion["block_id"],
                                "quote": "Emergency Applicant sought relief from Agency.",
                            }
                        ],
                        "supersedes_observation_id": None,
                    },
                    {
                        "observation_type": "doctrinal_theme",
                        "legal_status": "described",
                        "certainty": "direct",
                        "raw_value": "The Court explained its reasoning for the result.",
                        "normalized_value": "The Court explained its reasoning for the result.",
                        "attribution": opinion["attribution"],
                        "speaker_name": None,
                        "speaker_kind": "unknown",
                        "identity_basis": "anonymous",
                        "authority_citations": [],
                        "confidence": 1,
                        "evidence": [
                            {
                                "block_id": opinion["block_id"],
                                "quote": "The Court explained its reasoning for the result.",
                            }
                        ],
                        "supersedes_observation_id": None,
                    },
                    {
                        "observation_type": "holding",
                        "legal_status": "court_held",
                        "certainty": "direct",
                        "raw_value": "The Court granted the application.",
                        "normalized_value": "The Court granted the application.",
                        "attribution": opinion["attribution"],
                        "speaker_name": None,
                        "speaker_kind": "unknown",
                        "identity_basis": "anonymous",
                        "authority_citations": [],
                        "confidence": 1,
                        "evidence": [
                            {
                                "block_id": opinion["block_id"],
                                "quote": "The Court granted the application.",
                            }
                        ],
                        "supersedes_observation_id": None,
                    },
                ]
            }
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
                    "speaker_name": opening["speaker_name"],
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
                    "speaker_name": advocate["speaker_name"],
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
                    "speaker_name": question["speaker_name"],
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
    def _compact_brief(user: dict[str, Any]) -> dict[str, object]:
        def prose(claims: list[dict[str, Any]]) -> str:
            return " ".join(dict.fromkeys(str(claim["value"]) for claim in claims))

        return {
            "dek": prose(user["summary"]),
            "section_paragraphs": [
                prose(
                    [
                        claim
                        for claim in section["claims"]
                        if len(str(claim["value"])) > 10
                        and not str(claim["value"]).casefold().startswith("docket ")
                    ]
                    or section["claims"]
                )
                for section in user["sections"]
            ],
            "argument_paragraphs": [
                [
                    prose(argument["claims"][::2]),
                    prose(argument["claims"][1::2] or argument["claims"][:1]),
                ]
                for argument in user["arguments"]
            ],
        }

    @staticmethod
    def _brief(user: dict[str, Any]) -> dict[str, object]:
        claims = user.get("claims") or [
            claim for section in user.get("section_plan", []) for claim in section["claims"]
        ]
        all_ids = [claim["claim_id"] for claim in claims]
        if not user.get("argument_sessions"):
            ids_by_type = {
                claim_type: [claim["claim_id"] for claim in claims if claim["type"] == claim_type]
                for claim_type in {
                    "case_background",
                    "procedural_posture",
                    "requested_disposition",
                    "lower_court_action",
                    "doctrinal_theme",
                    "question_presented",
                    "holding",
                    "order",
                }
            }
            background_ids = ids_by_type["case_background"]
            path_ids = [
                *ids_by_type["procedural_posture"],
                *ids_by_type["requested_disposition"],
                *ids_by_type["lower_court_action"],
            ]
            issue_ids = ids_by_type["question_presented"] or ids_by_type["doctrinal_theme"][:1]
            reasoning_ids = [
                claim_id for claim_id in ids_by_type["doctrinal_theme"] if claim_id not in issue_ids
            ]
            action_ids = [*ids_by_type["holding"], *ids_by_type["order"]]
            action_values = " ".join(
                claim["value"] for claim in claims if claim["claim_id"] in action_ids
            ).casefold()
            action_paragraph = (
                "The Supreme Court stayed the injunction, a court order that prevented an "
                "action, temporarily while the case continues."
                if "stay" in action_values
                else "The Supreme Court granted the application."
            )
            return {
                "title": user["caption"],
                "title_claim_ids": ids_by_type["procedural_posture"],
                "dek": "The case concerns emergency relief from an Agency action.",
                "dek_claim_ids": background_ids,
                "sections": [
                    {
                        "heading": "What this case is about",
                        "paragraphs": ["Emergency Applicant sought relief from Agency."],
                        "claim_ids": background_ids,
                    },
                    {
                        "heading": "Why this case reached the Court",
                        "paragraphs": [
                            "Emergency Applicant asked the Supreme Court for emergency relief."
                        ],
                        "claim_ids": path_ids,
                    },
                    {
                        "heading": "The legal issue",
                        "paragraphs": ["The legal issue concerns emergency relief."],
                        "claim_ids": issue_ids,
                    },
                    {
                        "heading": "What the Supreme Court did",
                        "paragraphs": [action_paragraph],
                        "claim_ids": action_ids,
                    },
                    {
                        "heading": "Why the Court did it",
                        "paragraphs": ["The Court explained its reasoning for the result."],
                        "claim_ids": reasoning_ids,
                    },
                ],
                "argument_analyses": [],
            }
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
            "model_runtime_approved": True,
            "launch_approved": True,
        }
    )
    return config.model_copy(
        update={
            "enabled": True,
            "generation": config.generation.model_copy(update={"brief_generation_enabled": True}),
            "publication": config.publication.model_copy(
                update={"enabled": True, "dry_run": False}
            ),
            "approvals": approvals,
            "discovery": config.discovery.model_copy(
                update={"terms": ["2025"], "historical_rechecks_per_run": 0}
            ),
        }
    )


def proceedings_config() -> ProceedingsConfig:
    config = ProceedingsConfig.from_yaml("config/proceedings.yaml")
    sources = dict(config.sources)
    sources["supreme_court"] = sources["supreme_court"].model_copy(update={"enabled": True})
    return config.model_copy(update={"sources": sources})


def _immediate_rate_limiter(_interval: float) -> RequestRateLimiter:
    return RequestRateLimiter(0)


def build_adapter(
    court: CourtFixture,
    model: MockOpenAI,
    *,
    backend: type[FixtureBackend] = FixtureBackend,
    rate_limiter_factory: Any = _immediate_rate_limiter,
    inventory_factory: Any | None = None,
    clock: Any | None = None,
) -> LiveStaticBatchAdapter:
    settings = ServiceSettings(
        ollama_base_url="http://127.0.0.1:11434/v1",
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
        ollama_client_factory=lambda _settings, _config: model,
        ollama_inventory_factory=(
            inventory_factory
            if inventory_factory is not None
            else lambda _settings, _config: model.inventory
        ),
        parser_backend_factory=backend,
        rate_limiter_factory=rate_limiter_factory,
        clock=clock if clock is not None else lambda: NOW,
    )


def run(
    tmp_path: Path,
    store: MemoryStateStore,
    court: CourtFixture,
    model: MockOpenAI,
    *,
    config: ScotusConfig | None = None,
    backend: type[FixtureBackend] = FixtureBackend,
    scheduled_retries: bool = False,
    now: datetime = NOW,
) -> StaticBatchResult:
    return build_adapter(court, model, backend=backend, clock=lambda: now).run(
        state_store=store,
        config=config or live_config(),
        mode=DiscoveryMode.NIGHTLY,
        runner_temp=tmp_path / "private",
        authorized_replay=False,
        scheduled_retries=scheduled_retries,
    )


def _replace_active_case(content: GeneratedContent, case: PublicCaseBrief) -> GeneratedContent:
    """Replace one test fixture's active revision while preserving state integrity."""
    assert content.projection is not None
    case_key = public_case_key(case.term, case.primary_docket)
    revision_key = (case_key, case.revisions[-1].revision_number)
    stored = content.revisions[revision_key]
    digest = sha256_hex(canonical_json_bytes(case, privacy_check=False))
    record = stored.record.__class__.model_validate(
        {
            **stored.record.model_dump(mode="python"),
            "case_sha256": digest,
            "case": case,
        }
    )
    revisions = dict(content.revisions)
    revisions[revision_key] = StoredCaseRevision(record, canonical_json_bytes(record))
    publication = content.publication.model_copy(
        update={
            "cases": tuple(
                pointer.model_copy(update={"active_case_sha256": digest})
                if pointer.case_key == case_key
                else pointer
                for pointer in content.publication.cases
            )
        }
    )
    projection = content.projection.model_copy(
        update={
            "cases": tuple(
                case if public_case_key(item.term, item.primary_docket) == case_key else item
                for item in content.projection.cases
            )
        }
    )
    return replace(
        content,
        projection=projection,
        publication=publication,
        revisions=revisions,
    )


def test_live_adapter_checks_all_gates_before_factories_or_traffic(tmp_path: Path) -> None:
    called = False

    def settings() -> ServiceSettings:
        nonlocal called
        called = True
        raise AssertionError

    adapter = LiveStaticBatchAdapter(settings_factory=settings)
    config = ScotusConfig.from_yaml("config/scotus.yaml").model_copy(update={"enabled": False})
    with pytest.raises(PublicationGateDenied):
        adapter.run(
            state_store=StaticStateStore(tmp_path / "state"),
            config=config,
            mode=DiscoveryMode.NIGHTLY,
            runner_temp=tmp_path,
            authorized_replay=False,
        )
    assert not called
    assert not list(tmp_path.iterdir())


def test_opinion_page_attribution_tracks_court_and_separate_opinions() -> None:
    assert _opinion_page_attribution("PER CURIAM\nThe Court explains its decision.") == (
        "Opinion of the Court"
    )
    assert (
        _opinion_page_attribution(
            "JACKSON, J., dissenting\nI would deny relief.",
            "Opinion of the Court",
        )
        == "Justice Jackson, dissenting"
    )
    assert (
        _opinion_page_attribution(
            "JUSTICE KAGAN, dissenting\nI would deny relief.",
            "Opinion of the Court",
        )
        == "Justice Kagan, dissenting"
    )
    assert (
        _opinion_page_attribution(
            "JUSTICE KAGAN, with whom JUSTICE SOTOMAYOR joins, dissenting\nReasoning.",
            "Opinion of the Court",
        )
        == "Justice Kagan, dissenting"
    )
    assert (
        _opinion_page_attribution(
            "CHIEF JUSTICE ROBERTS, dissenting\nReasoning.",
            "Opinion of the Court",
        )
        == "Justice Roberts, dissenting"
    )
    assert (
        _opinion_page_attribution(
            "JACKSON, J., dissenting\nThe analysis continues.",
            "Justice Jackson, dissenting",
        )
        == "Justice Jackson, dissenting"
    )
    assert (
        _opinion_page_attribution(
            "SOTOMAYOR, J., concurring in part and dissenting in part\nSeparate reasoning.",
            "Justice Jackson, dissenting",
        )
        == "Justice Sotomayor, concurring in part and dissenting in part"
    )


def test_legal_analysis_fallback_uses_distinct_controlling_exact_sentences() -> None:
    text = (
        "Standing and ripeness are the controlling doctrines. "
        "Article III prohibits courts from deciding hypothetical disputes."
    )
    block = LegalEvidenceBlock(
        block_id="opinion-page-2",
        document_revision_id=uuid4(),
        document_kind=ScotusDocumentKind.OPINION,
        official_url="https://www.supremecourt.gov/opinion.pdf",
        start_file_page=2,
        start_line=1,
        end_file_page=2,
        end_line=4,
        text_private=text,
        speaker_name=None,
        speaker_kind=SpeakerKind.UNKNOWN,
        identity_basis=SpeakerIdentityBasis.ANONYMOUS,
        attribution="Opinion of the Court",
    )

    observations = _legal_analysis_observations(
        case_id=uuid4(), blocks=(block,), excluded_values=set()
    )

    assert tuple(item.observation_type for item in observations) == (
        LegalObservationType.QUESTION_PRESENTED,
        LegalObservationType.DOCTRINAL_THEME,
    )
    assert tuple(item.raw_value_private for item in observations) == tuple(
        item.evidence[0].quote_private for item in observations
    )
    assert len({item.raw_value_private for item in observations}) == 2
    assert (
        _legal_analysis_observations(
            case_id=uuid4(),
            blocks=(block.model_copy(update={"attribution": "Justice Kagan, dissenting"}),),
            excluded_values=set(),
        )
        == ()
    )


def test_court_action_fallback_skips_separate_opinions_and_recognizes_final_action() -> None:
    controlling = LegalEvidenceBlock(
        block_id="opinion-page-2",
        document_revision_id=uuid4(),
        document_kind=ScotusDocumentKind.OPINION,
        official_url="https://www.supremecourt.gov/opinion.pdf",
        start_file_page=2,
        start_line=1,
        end_file_page=2,
        end_line=1,
        text_private="The Court affirmed the judgment.",
        attribution="Opinion of the Court",
    )
    separate = controlling.model_copy(
        update={
            "block_id": "opinion-page-1",
            "start_file_page": 1,
            "end_file_page": 1,
            "text_private": "The Court reversed the judgment.",
            "attribution": "Justice Kagan, dissenting",
        }
    )

    observation = _court_action_observation(
        case_id=uuid4(),
        blocks=(separate, controlling),
    )

    assert observation.observation_type is LegalObservationType.HOLDING
    assert observation.legal_status is LegalStatus.COURT_HELD
    assert observation.raw_value_private == controlling.text_private


def test_procedural_path_fallback_uses_only_controlling_exact_source_text() -> None:
    block = LegalEvidenceBlock(
        block_id="opinion-page-1",
        document_revision_id=uuid4(),
        document_kind=ScotusDocumentKind.OPINION,
        official_url="https://www.supremecourt.gov/opinion.pdf",
        start_file_page=1,
        start_line=1,
        end_file_page=1,
        end_line=4,
        text_private="The District Court entered an injunction against the policy.",
        speaker_name=None,
        speaker_kind=SpeakerKind.UNKNOWN,
        identity_basis=SpeakerIdentityBasis.ANONYMOUS,
        attribution="Opinion of the Court",
    )

    observation = _procedural_path_observation(case_id=uuid4(), blocks=(block,))

    assert observation is not None
    assert observation.observation_type is LegalObservationType.LOWER_COURT_ACTION
    assert observation.legal_status is LegalStatus.LOWER_COURT_HELD
    assert observation.raw_value_private == block.text_private
    assert observation.evidence[0].quote_private == block.text_private
    assert (
        _procedural_path_observation(
            case_id=uuid4(),
            blocks=(block.model_copy(update={"attribution": "Justice Kagan, dissenting"}),),
        )
        is None
    )


def test_default_ollama_sdk_client_is_loopback_and_ignores_proxy_environment() -> None:
    client = _default_ollama_client(
        ServiceSettings(_env_file=None),
        ScotusConfig.from_yaml("config/scotus.yaml"),
    )
    try:
        transport = cast(Any, client._client)
        assert str(client.base_url) == "http://127.0.0.1:11434/v1/"
        assert client.api_key == "ollama-local-no-secret"
        assert transport.follow_redirects is False
        assert transport._trust_env is False
    finally:
        client.close()


@pytest.mark.parametrize(
    ("inventory", "message"),
    (
        (
            {"models": [{"name": "another:70b", "digest": MODEL_DIGEST}]},
            "tag is not installed",
        ),
        (
            {
                "models": [
                    {"name": "cogito:70b", "digest": "f" * 64},
                    {"name": "fallback:70b", "digest": MODEL_DIGEST},
                ]
            },
            "digest does not match",
        ),
    ),
)
def test_live_adapter_requires_exact_local_model_before_court_traffic(
    tmp_path: Path, inventory: dict[str, object], message: str
) -> None:
    court = CourtFixture()
    model = MockOpenAI()
    model.inventory = inventory
    adapter = build_adapter(court, model)
    with pytest.raises(PublicationGateDenied, match=message):
        adapter.run(
            state_store=MemoryStateStore(tmp_path / "state"),
            config=live_config(),
            mode=DiscoveryMode.NIGHTLY,
            runner_temp=tmp_path / "private",
            authorized_replay=False,
        )
    assert court.source_requests == []
    assert court.document_requests == []
    assert model.requests == []
    # Exact inventory preflight runs before the model-client factory is invoked.
    assert not model.closed


def test_transient_evidence_transport_replays_exact_private_response(
    tmp_path: Path,
) -> None:
    content = b"official current evidence"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "etag": '"current"'},
            content=content,
            request=request,
        )

    recorded = _TransientEvidenceTransport(
        tmp_path / "cache",
        "record",
        maximum_response_bytes=100,
        maximum_cache_bytes=2_000,
    )
    recorded.delegate = httpx.MockTransport(handler)
    request = httpx.Request("GET", "https://www.supremecourt.gov/test")
    response = recorded.handle_request(request)
    assert response.read() == content
    recorded.close()

    replayed = _TransientEvidenceTransport(
        tmp_path / "cache",
        "replay",
        maximum_response_bytes=100,
        maximum_cache_bytes=2_000,
    )
    replay = replayed.handle_request(request)
    assert replay.read() == content
    assert replay.headers["etag"] == '"current"'
    (tmp_path / "cache" / "replay-cursor").unlink()
    next((tmp_path / "cache").glob("*.body")).write_bytes(b"tampered")
    with pytest.raises(SourceFetchError, match="integrity"):
        _TransientEvidenceTransport(
            tmp_path / "cache",
            "replay",
            maximum_response_bytes=100,
            maximum_cache_bytes=2_000,
        ).handle_request(request)


def test_transient_evidence_replay_requires_exact_complete_sequence(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "sequence"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=request.url.path.encode(), request=request)

    recorded = _TransientEvidenceTransport(
        cache, "record", maximum_response_bytes=100, maximum_cache_bytes=2_000
    )
    recorded.delegate = httpx.MockTransport(handler)
    first = httpx.Request("GET", "https://www.supremecourt.gov/first")
    second = httpx.Request("GET", "https://www.supremecourt.gov/second")
    recorded.handle_request(first).read()
    recorded.handle_request(second).read()
    recorded.close()

    wrong_order = _TransientEvidenceTransport(
        cache, "replay", maximum_response_bytes=100, maximum_cache_bytes=2_000
    )
    with pytest.raises(SourceFetchError, match="sequence differs"):
        wrong_order.handle_request(second)
    (cache / "replay-cursor").write_bytes(b"1" * 33)
    with pytest.raises(SourceFetchError, match="cursor exceeds"):
        wrong_order.handle_request(first)
    (cache / "replay-cursor").unlink()

    replayed = _TransientEvidenceTransport(
        cache, "replay", maximum_response_bytes=100, maximum_cache_bytes=2_000
    )
    replayed.handle_request(first).read()
    with pytest.raises(SourceFetchError, match="omitted"):
        replayed.require_complete_replay()
    replayed.handle_request(second).read()
    replayed.require_complete_replay()
    with pytest.raises(SourceFetchError, match="extra request"):
        replayed.handle_request(second)


def test_transient_evidence_recording_enforces_response_bound(tmp_path: Path) -> None:
    recorded = _TransientEvidenceTransport(
        tmp_path / "bounded",
        "record",
        maximum_response_bytes=3,
        maximum_cache_bytes=1_000,
    )
    request = httpx.Request("GET", "https://www.supremecourt.gov/too-large")
    recorded.delegate = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"four", request=request)
    )

    with pytest.raises(httpx.ReadError, match="response exceeds"):
        recorded.handle_request(request)
    assert (tmp_path / "bounded" / "sequence.jsonl").is_file()
    replayed = _TransientEvidenceTransport(
        tmp_path / "bounded",
        "replay",
        maximum_response_bytes=3,
        maximum_cache_bytes=1_000,
    )
    with pytest.raises(httpx.ConnectError, match="recorded Court"):
        replayed.handle_request(request)
    replayed.require_complete_replay()


def test_transient_evidence_replays_recorded_transport_failure(tmp_path: Path) -> None:
    cache = tmp_path / "failure"
    request = httpx.Request("GET", "https://www.supremecourt.gov/failure")

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic", request=request)

    recorded = _TransientEvidenceTransport(
        cache, "record", maximum_response_bytes=100, maximum_cache_bytes=1_000
    )
    recorded.delegate = httpx.MockTransport(fail)
    with pytest.raises(httpx.ConnectError):
        recorded.handle_request(request)

    replayed = _TransientEvidenceTransport(
        cache, "replay", maximum_response_bytes=100, maximum_cache_bytes=1_000
    )
    with pytest.raises(httpx.ConnectError, match="recorded Court"):
        replayed.handle_request(request)
    replayed.require_complete_replay()


def test_qwen_control_cannot_run_outside_publication_disabled_canary(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    model = MockOpenAI(
        model_name="qwen3.8:27b",
        model_digest=(
            "22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643"
        ),
    )
    base = live_config()
    generation = type(base.generation).model_validate(
        {
            **base.generation.model_dump(mode="python"),
            "runtime_role": "control",
            "model": "qwen3.8:27b",
            "model_digest": (
                "22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643"
            ),
        }
    )
    invalid = base.model_copy(
        update={
            "generation": generation,
            "publication": base.publication.model_copy(update={"dry_run": True}),
        }
    )

    with pytest.raises(PublicationGateDenied, match="Qwen control is limited"):
        build_adapter(court, model).run(
            state_store=MemoryStateStore(tmp_path / "state"),
            config=invalid,
            mode=DiscoveryMode.NIGHTLY,
            runner_temp=tmp_path / "private",
            authorized_replay=False,
        )
    assert court.source_requests == []
    assert model.requests == []


def test_model_preflight_precedes_every_court_and_model_client_factory(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    settings = ServiceSettings(
        ollama_base_url="http://127.0.0.1:11434/v1",
        proceedings_config_path="unused",
        source_user_agent="ragchew-test contact=test@example.test",
    )

    def forbidden_factory(name: str) -> Any:
        def factory(_settings: ServiceSettings, _config: ScotusConfig) -> Any:
            calls.append(name)
            raise AssertionError(f"{name} factory must not run")

        return factory

    adapter = LiveStaticBatchAdapter(
        settings_factory=lambda: settings,
        proceedings_loader=lambda _path: proceedings_config(),
        source_fetcher_factory=forbidden_factory("Court source"),
        document_client_factory=forbidden_factory("Court document"),
        ollama_client_factory=forbidden_factory("model client"),
        ollama_inventory_factory=lambda _settings, _config: {"models": []},
        clock=lambda: NOW,
    )

    with pytest.raises(PublicationGateDenied, match="tag is not installed"):
        adapter.run(
            state_store=MemoryStateStore(tmp_path / "state"),
            config=live_config(),
            mode=DiscoveryMode.NIGHTLY,
            runner_temp=tmp_path / "private",
            authorized_replay=False,
        )
    assert calls == []


def test_model_identity_is_rechecked_after_each_completion(tmp_path: Path) -> None:
    court = CourtFixture()
    model = MockOpenAI()
    inventory_calls = 0

    def mutable_inventory(_settings: ServiceSettings, _config: ScotusConfig) -> Any:
        nonlocal inventory_calls
        inventory_calls += 1
        if inventory_calls < 3:
            return model.inventory
        return {"models": [{"name": "cogito:70b", "digest": "f" * 64}]}

    adapter = build_adapter(court, model, inventory_factory=mutable_inventory)
    with pytest.raises(RuntimeError, match="detail=PublicationGateDenied"):
        adapter.run(
            state_store=MemoryStateStore(tmp_path / "state"),
            config=live_config(),
            mode=DiscoveryMode.NIGHTLY,
            runner_temp=tmp_path / "private",
            authorized_replay=False,
        )

    assert inventory_calls == 3
    assert len(model.requests) == 1
    assert model.closed


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
    processor = result.content.publication.processor
    assert processor is not None
    assert processor.model == (f"ollama:cogito:70b@sha256:{MODEL_DIGEST}@http://127.0.0.1:11434/v1")
    assert processor.extractor_version == (
        "scotus-observation-v2:scotus-legal-v1:scotus-legal-extraction-v10:"
        "official-document-text-v4"
    )
    assert processor.policy_version == "scotus-brief-policy-v61"
    assert processor.prompt_version == (
        "scotus-reader-guide-compact-v1;repair=scotus-reader-guide-field-repair-v2;"
        "planner=reader-guide-plan-v3;reader_prose=scotus-reader-prose-v2"
    )
    assert [request["response_format"]["json_schema"]["name"] for request in model.requests] == [
        "scotus_legal_observations",
        "compact_reader_guide",
    ]
    assert all(request["extra_body"] == {"think": False} for request in model.requests)
    assert model.requests[0]["max_tokens"] == 8_000
    assert model.requests[1]["max_tokens"] == 8_000
    assert all(request["reasoning_effort"] == "none" for request in model.requests)
    extraction_payload = json.loads(model.requests[0]["messages"][1]["content"])
    assert extraction_payload["mode"] == "/no_think"
    extraction_evidence = extraction_payload["evidence"]
    assert {
        "speaker_name",
        "speaker_kind",
        "identity_basis",
        "attribution",
    }.issubset(extraction_evidence[0])
    brief_payload = json.loads(model.requests[1]["messages"][1]["content"])
    assert brief_payload["task"] == "translate approved facts into everyday language"
    assert "caption" not in brief_payload
    assert "docket" not in brief_payload
    brief_schema = model.requests[1]["response_format"]["json_schema"]["schema"]
    assert "$defs" not in brief_schema
    receipts = CostReceiptBundle.model_validate_json(
        (tmp_path / "private/public-cost-receipts.json").read_bytes()
    )
    assert {receipt.stage for receipt in receipts.receipts} == {"extraction", "brief"}
    assert all(receipt.outcome is ModelAttemptOutcome.SUCCEEDED for receipt in receipts.receipts)
    assert sum(receipt.call_count for receipt in receipts.receipts) == len(model.requests)
    assert not list((tmp_path / "private").glob("ragchew-*"))


def test_model_digest_changes_processor_and_request_fingerprints_only(
    tmp_path: Path,
) -> None:
    first = run(
        tmp_path / "first",
        MemoryStateStore(tmp_path / "first-state"),
        CourtFixture(),
        MockOpenAI(),
    )
    drift_digest = "f" * 64
    drift_config = live_config().model_copy(
        update={
            "generation": live_config().generation.model_copy(update={"model_digest": drift_digest})
        }
    )
    second = run(
        tmp_path / "second",
        MemoryStateStore(tmp_path / "second-state"),
        CourtFixture(),
        MockOpenAI(model_digest=drift_digest),
        config=drift_config,
    )

    first_processor = first.content.publication.processor
    second_processor = second.content.publication.processor
    assert first_processor is not None and second_processor is not None
    assert first_processor.config_sha256 != second_processor.config_sha256
    assert first_processor.composite_sha256 != second_processor.composite_sha256
    assert first_processor.model != second_processor.model
    assert first_processor.prompt_version == second_processor.prompt_version
    assert first_processor.policy_version == second_processor.policy_version

    first_receipts = CostReceiptBundle.model_validate_json(
        (tmp_path / "first/private/public-cost-receipts.json").read_bytes()
    )
    second_receipts = CostReceiptBundle.model_validate_json(
        (tmp_path / "second/private/public-cost-receipts.json").read_bytes()
    )
    assert [item.stage for item in first_receipts.receipts] == [
        item.stage for item in second_receipts.receipts
    ]
    assert all(
        first_item.input_fingerprint != second_item.input_fingerprint
        for first_item, second_item in zip(
            first_receipts.receipts, second_receipts.receipts, strict=True
        )
    )


def test_status_changing_opinion_rewrites_complete_argument_case(tmp_path: Path) -> None:
    court = CourtFixture()
    store = MemoryStateStore(tmp_path / "state")
    first = run(tmp_path, store, court, MockOpenAI())
    assert first.publishable
    assert first.content.projection is not None
    prior = first.content.projection.cases[0]
    prospective = prior.model_copy(
        update={
            "sections": (
                prior.sections[0].model_copy(
                    update={
                        "paragraphs": (
                            "The parties await the Supreme Court's decision about agency power.",
                        )
                    }
                ),
            )
        }
    )
    assert "await" in prospective.sections[0].paragraphs[0]
    store.content = _replace_active_case(first.content, prospective)
    court.document_requests.clear()

    court.slip_etag = '"slip-2"'
    court.slip_rows = [
        (
            "21",
            "6/30/26",
            "25-1",
            "Example v. Agency",
            "K",
            "25-1_example.pdf",
        )
    ]
    court.documents["/opinions/25pdf/25-1_example.pdf"] = (
        '"opinion-25-1"',
        _text_pdf(
            "No. 25-1 Example v. Agency.",
            "The Court affirmed the judgment.",
            "The Court explained that the law permits the agency action.",
        ),
        "application/pdf",
    )

    class OpinionAwareModel(MockOpenAI):
        @staticmethod
        def _extraction(evidence: list[dict[str, Any]]) -> dict[str, object]:
            opinion = next((item for item in evidence if item["kind"] == "opinion"), None)
            if opinion is None:
                return MockOpenAI._extraction(evidence)
            return {
                "observations": [
                    {
                        "observation_type": "holding",
                        "legal_status": "court_held",
                        "certainty": "direct",
                        "raw_value": "The Court affirmed the judgment.",
                        "normalized_value": "The Court affirmed the judgment.",
                        "attribution": opinion["attribution"],
                        "speaker_name": opinion["speaker_name"],
                        "speaker_kind": opinion["speaker_kind"],
                        "identity_basis": opinion["identity_basis"],
                        "authority_citations": [],
                        "confidence": 1,
                        "evidence": [
                            {
                                "block_id": opinion["block_id"],
                                "quote": "The Court affirmed the judgment.",
                            }
                        ],
                        "supersedes_observation_id": None,
                    },
                    {
                        "observation_type": "doctrinal_theme",
                        "legal_status": "described",
                        "certainty": "direct",
                        "raw_value": (
                            "The Court explained that the law permits the agency action."
                        ),
                        "normalized_value": (
                            "The Court explained that the law permits the agency action."
                        ),
                        "attribution": opinion["attribution"],
                        "speaker_name": opinion["speaker_name"],
                        "speaker_kind": opinion["speaker_kind"],
                        "identity_basis": opinion["identity_basis"],
                        "authority_citations": [],
                        "confidence": 1,
                        "evidence": [
                            {
                                "block_id": opinion["block_id"],
                                "quote": (
                                    "The Court explained that the law permits the agency action."
                                ),
                            }
                        ],
                        "supersedes_observation_id": None,
                    },
                ]
            }

    class DeterministicActionModel(OpinionAwareModel):
        @staticmethod
        def _extraction(evidence: list[dict[str, Any]]) -> dict[str, object]:
            payload = OpinionAwareModel._extraction(evidence)
            if not any(item["kind"] == "opinion" for item in evidence):
                return payload
            observations = cast(list[dict[str, Any]], payload["observations"])
            return {
                "observations": [
                    item
                    for item in observations
                    if item["observation_type"] not in {"holding", "order"}
                ]
            }

    update_model = DeterministicActionModel()
    updated = run(tmp_path, store, court, update_model)

    assert updated.publishable
    assert updated.changed_case_keys == ("2025-25-1",)
    assert updated.content.projection is not None
    case = updated.content.projection.cases[0]
    assert case.latest_court_document_date == datetime(2026, 6, 30, tzinfo=UTC)
    assert case.case_status.value == "decided"
    assert case.maturity.value == "post_opinion"
    assert case.case_history[-1].status.value == "decided"
    assert case.revisions[-1].maturity.value == "post_opinion"
    assert case.dispositions[0].official_url.endswith("/25-1_example.pdf")
    assert [item.revision_number for item in case.revisions] == [1, 2]
    assert all(
        "await" not in paragraph.casefold()
        for section in case.sections
        for paragraph in section.paragraphs
    )
    assert [
        request["response_format"]["json_schema"]["name"] for request in update_model.requests
    ] == ["scotus_legal_observations", "scotus_legal_observations", "compact_reader_guide"]
    assert any("transcripts" in request.url.path for request in court.document_requests)


def test_failed_opinion_rewrite_keeps_complete_prior_argument_case(tmp_path: Path) -> None:
    court = CourtFixture()
    store = MemoryStateStore(tmp_path / "state")
    first = run(tmp_path, store, court, MockOpenAI())
    assert first.content.projection is not None
    prior = first.content.projection.cases[0]
    store.content = first.content
    court.slip_etag = '"slip-2"'
    court.slip_rows = [("21", "6/30/26", "25-1", "Example v. Agency", "K", "25-1_example.pdf")]
    court.documents["/opinions/25pdf/25-1_example.pdf"] = (
        '"opinion-25-1"',
        _text_pdf("No. 25-1 Example v. Agency.", "The Court affirmed the judgment."),
        "application/pdf",
    )

    failed = run(tmp_path, store, court, MockOpenAI(), backend=FailingBackend)

    assert failed.content.projection is not None
    assert failed.content.projection.cases[0] == prior
    assert failed.changed_case_keys == ()
    assert failed.pending_case_keys == ("2025-25-1",)
    assert failed.content.publication.dispositions[0].case_key == "2025-25-1"
    assert failed.content.publication.dispositions[0].publication_date == datetime(
        2026, 6, 30, tzinfo=UTC
    )


def test_disposition_only_emergency_opinion_publishes_without_argument(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    court.rows = []
    court.slip_rows = [
        (
            "17",
            "3/04/26",
            "25A810",
            "Emergency Applicant v. Agency",
            "PC",
            "25a810_example.pdf",
        )
    ]
    court.documents["/docket/docketfiles/html/public/25A810.html"] = (
        '"docket-a810"',
        b"<!doctype html><html><body>Docket 25A810. Emergency Applicant v. Agency.</body></html>",
        "text/html",
    )
    court.documents["/opinions/25pdf/25a810_example.pdf"] = (
        '"opinion-a810"',
        _text_pdf(
            "No. 25A810 Emergency Applicant v. Agency.",
            "Emergency Applicant sought relief from Agency.",
            "The Court granted the application.",
        ),
        "application/pdf",
    )
    model = MockOpenAI()
    result = run(tmp_path, MemoryStateStore(tmp_path / "state"), court, model)

    assert result.publishable
    assert result.pending_case_keys == ()
    assert result.changed_case_keys == ("2025-25a810",)
    assert result.content.projection is not None
    case = result.content.projection.cases[0]
    assert case.arguments == ()
    assert case.argument_date is None
    assert case.official_detail_url is None
    assert case.case_status.value == "decided"
    assert case.latest_court_document_date == datetime(2026, 3, 4, tzinfo=UTC)
    assert [item.kind for item in case.dispositions] == ["per_curiam"]
    names = [request["response_format"]["json_schema"]["name"] for request in model.requests]
    assert names == ["scotus_legal_observations", "compact_reader_guide"]
    brief_request = model.requests[-1]
    brief_schema = brief_request["response_format"]["json_schema"]["schema"]
    assert brief_schema["properties"]["argument_paragraphs"]["maxItems"] == 0
    assert tuple(section.heading for section in case.sections) == (
        "What this case is about",
        "Why this case reached the Court",
        "The legal issue",
        "What the Supreme Court did",
        "Why the Court did it",
    )
    assert case.sections[3].paragraphs == ("The Court granted the application.",)
    disposition = result.content.publication.dispositions[0]
    assert disposition.primary_docket == "25A810"
    assert disposition.publication_date == datetime(2026, 3, 4, tzinfo=UTC)
    assert disposition.case_key == result.changed_case_keys[0]
    slip_requests = [url for url, _ in court.source_requests if "slipopinion" in url]
    assert slip_requests == ["https://www.supremecourt.gov/opinions/slipopinion/25"]


def test_disposition_only_case_derives_exact_docket_identity_when_model_omits_it(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    court.rows = []
    court.slip_rows = [
        (
            "17",
            "3/04/26",
            "25A810",
            "Emergency Applicant v. Agency",
            "PC",
            "25a810_example.pdf",
        )
    ]
    court.documents["/docket/docketfiles/html/public/25A810.html"] = (
        '"docket-a810"',
        b"<!doctype html><body>Docket 25A810. Emergency Applicant v. Agency.</body>",
        "text/html",
    )
    court.documents["/opinions/25pdf/25a810_example.pdf"] = (
        '"opinion-a810"',
        _text_pdf(
            "No. 25A810 Emergency Applicant v. Agency.",
            "Emergency Applicant sought relief from Agency.",
            (
                "The Government's application to stay the District Court's injunction "
                "in this case is granted."
            ),
        ),
        "application/pdf",
    )

    class BackgroundOnlyModel(MockOpenAI):
        @staticmethod
        def _extraction(evidence: list[dict[str, Any]]) -> dict[str, object]:
            batch = MockOpenAI._extraction(evidence)
            batch["observations"] = [
                item
                for item in cast(list[dict[str, Any]], batch["observations"])
                if item["observation_type"] not in {"procedural_posture", "holding", "order"}
            ]
            return batch

    result = run(
        tmp_path,
        MemoryStateStore(tmp_path / "state"),
        court,
        BackgroundOnlyModel(),
    )
    assert result.publishable
    assert result.content.projection is not None
    assert result.content.projection.cases[0].arguments == ()


def test_disposition_revision_date_recomputes_immutable_public_revision(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    court.rows = []
    court.slip_rows = [
        (
            "17",
            "3/04/26",
            "25A810",
            "Emergency Applicant v. Agency",
            "PC",
            "25a810_example.pdf",
        )
    ]
    court.documents["/docket/docketfiles/html/public/25A810.html"] = (
        '"docket-a810"',
        b"<!doctype html><body>Docket 25A810. Emergency Applicant v. Agency.</body>",
        "text/html",
    )
    court.documents["/opinions/25pdf/25a810_example.pdf"] = (
        '"opinion-a810"',
        _text_pdf(
            "No. 25A810 Emergency Applicant v. Agency.",
            "Emergency Applicant sought relief from Agency.",
            "The Court granted the application.",
        ),
        "application/pdf",
    )
    store = MemoryStateStore(tmp_path / "state")
    first = run(tmp_path, store, court, MockOpenAI())
    store.content = first.content
    assert first.content.projection is not None
    prior_case = first.content.projection.cases[0]
    original = first.content.revisions[("2025-25a810", 1)].serialized
    court.slip_etag = '"slip-2"'
    court.slip_html = lambda: (
        b"<!doctype html><table><tr><th>R-</th><th>Date</th><th>Docket</th>"
        b"<th>Name</th><th>J.</th><th>Citation</th></tr><tr><td>17</td>"
        b"<td>3/04/26</td><td>25A810</td><td>"
        b"<a href='/opinions/25pdf/25a810_example.pdf'>Emergency Applicant v. Agency</a>"
        b"<br><b>Revisions</b>: <a href='/opinions/25pdf/25a810_diff.pdf'>3/05/26</a>"
        b"</td><td>PC</td><td></td></tr></table>"
    )
    revised_model = MockOpenAI()
    revised = run(tmp_path, store, court, revised_model)

    assert revised.changed_case_keys == ("2025-25a810",)
    assert revised.content.projection is not None
    case = revised.content.projection.cases[0]
    assert [item.revision_number for item in case.revisions] == [1, 2]
    assert case.dispositions[0].revision_date == datetime(2026, 3, 5, tzinfo=UTC)
    assert case.latest_court_document_date == datetime(2026, 3, 5, tzinfo=UTC)
    assert case.case_status is prior_case.case_status
    assert case.maturity is prior_case.maturity
    assert case.title == prior_case.title
    assert case.dek == prior_case.dek
    assert case.sections == prior_case.sections
    assert revised.content.revisions[("2025-25a810", 1)].serialized == original
    assert revised_model.requests == []
    assert not list((tmp_path / "private").glob("ragchew-*"))


def test_disposition_failure_is_case_local_and_preserves_pending_metadata(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    court.rows = []
    court.slip_rows = [
        ("16", "3/03/26", "25A800", "Missing v. Agency", "PC", "25a800.pdf"),
        (
            "17",
            "3/04/26",
            "25A810",
            "Emergency Applicant v. Agency",
            "PC",
            "25a810_example.pdf",
        ),
    ]
    court.documents["/opinions/25pdf/25a800.pdf"] = (
        '"opinion-a800"',
        _text_pdf("No. 25A800 Missing v. Agency.", "The Court denied relief."),
        "application/pdf",
    )
    court.documents["/docket/docketfiles/html/public/25A810.html"] = (
        '"docket-a810"',
        b"<!doctype html><body>Docket 25A810. Emergency Applicant v. Agency.</body>",
        "text/html",
    )
    court.documents["/opinions/25pdf/25a810_example.pdf"] = (
        '"opinion-a810"',
        _text_pdf(
            "No. 25A810 Emergency Applicant v. Agency.",
            "Emergency Applicant sought relief from Agency.",
            "The Court granted the application.",
        ),
        "application/pdf",
    )

    store = MemoryStateStore(tmp_path / "state")
    result = run(tmp_path, store, court, MockOpenAI())

    assert result.publishable
    assert result.changed_case_keys == ("2025-25a810",)
    assert result.pending_case_keys == ("2025-25a800",)
    assert {item.case_key for item in result.content.publication.dispositions} == {
        "2025-25a800",
        "2025-25a810",
    }
    assert not list((tmp_path / "private").glob("ragchew-*"))

    store.content = result.content
    court.document_requests.clear()
    retried = run(tmp_path, store, court, MockOpenAI())
    assert retried.pending_case_keys == ("2025-25a800",)
    # Unchanged non-model failures do not bypass the finite scheduled cooldown.
    assert not any(
        request.url.path == "/docket/docketfiles/html/public/25A800.html"
        for request in court.document_requests
    )


def test_first_slip_poll_ignores_legacy_generic_opinion_checkpoint(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    court.rows = []
    court.slip_rows = [
        (
            "17",
            "3/04/26",
            "25A810",
            "Emergency Applicant v. Agency",
            "PC",
            "25a810_example.pdf",
        )
    ]
    legacy = LogicalSourceState(
        logical_key="opinions:2025",
        source_kind="opinions",
        official_url="https://www.supremecourt.gov/opinions/slipopinion/25",
        validators=ConditionalValidators(etag=court.slip_etag),
        integrity=ContentIntegrity(sha256="a" * 64, byte_count=1),
        checked_at=NOW,
    )
    content = GeneratedContent.empty()
    content = replace(
        content,
        publication=content.publication.model_copy(update={"sources": (legacy,)}),
    )
    store = MemoryStateStore(tmp_path / "state", content)

    result = run(tmp_path, store, court, MockOpenAI())

    assert len(result.content.publication.dispositions) == 1
    assert {item.logical_key for item in result.content.publication.sources} == {
        "argument-index:2025",
        "opinions:2025",
        "orders:2025",
        "slip-opinions:2025",
    }
    slip_conditional = next(
        conditional for url, conditional in court.source_requests if "slipopinion" in url
    )
    assert slip_conditional == ConditionalRequest()


def test_supported_activity_newer_than_public_projection_is_deferred() -> None:
    projection_payload = json.loads(
        Path("tests/fixtures/static/one-case.json").read_text(encoding="utf-8")
    )["projection"]
    public_case = ScotusPublicProjection.model_validate(projection_payload).cases[0]
    case_key = public_case_key(public_case.term, public_case.primary_docket)
    latest = public_case.latest_court_document_date
    assert latest is not None

    assert _outstanding_supported_case_keys({case_key: public_case}, {case_key: latest}) == set()
    assert _outstanding_supported_case_keys(
        {case_key: public_case},
        {case_key: latest + timedelta(days=1), "2025-25-999": latest},
    ) == {case_key, "2025-25-999"}
    legacy = public_case.model_copy(update={"latest_court_document_date": None})
    assert _outstanding_supported_case_keys({case_key: legacy}, {case_key: latest}) == {
        case_key
    }


def test_live_discovery_canonicalizes_multi_primary_consolidation() -> None:
    court = CourtFixture()
    court.rows.append(("25A85", "Emergency Application", "4/21/26", "25A85.pdf"))
    court.slip_rows = [
        (
            "5",
            "6/04/26",
            "25-1 and 25A85",
            "Consolidated Case",
            "PC",
            "25-1_consolidated.pdf",
        )
    ]
    config = live_config()
    adapter = SupremeCourtAdapter(
        FixtureSourceFetcher(court),
        term="2025",
        clock=lambda: NOW,
        transcript_archive=True,
    )
    discovery = LiveStaticDiscovery(
        adapters={"2025": adapter},
        config=config,
        model_endpoint="http://127.0.0.1:11434/v1",
    )
    result = discovery.discover(
        mode=DiscoveryMode.NIGHTLY,
        content=GeneratedContent.empty(),
        budget=UnifiedRunBudget(config, CostLedger(updated_at=NOW)),
        now=NOW,
    )

    assert [item.case_key for item in result.work] == ["2025-25-1"]
    assert len(result.work[0].sessions) == 2
    assert result.deferred_case_keys == ()
    assert result.checkpoint_safe
    assert result.dispositions[0].case_key == "2025-25-1"


def test_brief_validation_gets_one_bounded_private_field_correction(
    tmp_path: Path,
) -> None:
    class CorrectingModel(MockOpenAI):
        def __init__(self) -> None:
            super().__init__()
            self.brief_calls = 0

        def create(self, **request: Any) -> object:
            completion = super().create(**request)
            name = request["response_format"]["json_schema"]["name"]
            if name not in {"compact_reader_guide", "reader_guide_field_repair"}:
                return completion
            self.brief_calls += 1
            if name != "compact_reader_guide":
                return completion
            payload = json.loads(completion.choices[0].message.content)
            payload["dek"] = "The language model output says the Court heard argument."
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    model = CorrectingModel()
    config = live_config()
    config = config.model_copy(
        update={
            "generation": config.generation.model_copy(
                update={"maximum_brief_validation_attempts_per_case": 2}
            )
        }
    )
    result = run(
        tmp_path,
        MemoryStateStore(tmp_path / "state"),
        CourtFixture(),
        model,
        config=config,
    )

    assert result.publishable
    assert model.brief_calls == 2
    repair_request = next(
        request
        for request in model.requests
        if request["response_format"]["json_schema"]["name"] == "reader_guide_field_repair"
    )
    repair_payload = json.loads(repair_request["messages"][1]["content"])
    assert repair_payload["field_path"] == {
        "kind": "dek",
        "section_index": None,
        "argument_index": None,
        "paragraph_index": None,
    }
    assert set(repair_payload) == {
        "field_path",
        "rejected_text",
        "support_packet",
        "diagnostic",
        "fixed_claim_ids",
    }
    assert repair_payload["diagnostic"]["rule"] == "internal_process_language"


@pytest.mark.parametrize(
    "repair_text",
    [
        "The Supreme Court reversed the judgment.",
        "The case does not concern statutory authority.",
        "The case concerns statutory authority and a tax exemption.",
    ],
)
def test_style_warning_retains_original_without_requesting_unprovable_repair(
    tmp_path: Path, repair_text: str
) -> None:
    original = "The case concerns statutory authority."

    class StyleRepairModel(MockOpenAI):
        def create(self, **request: Any) -> object:
            completion = super().create(**request)
            name = request["response_format"]["json_schema"]["name"]
            if name == "compact_reader_guide":
                payload = json.loads(completion.choices[0].message.content)
                payload["dek"] = original
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
                )
            if name == "reader_guide_field_repair":
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=json.dumps({"text": repair_text})
                            )
                        )
                    ]
                )
            return completion

    config = live_config().model_copy(
        update={
            "generation": live_config().generation.model_copy(
                update={"maximum_brief_validation_attempts_per_case": 2}
            )
        }
    )
    model = StyleRepairModel()
    result = run(
        tmp_path,
        MemoryStateStore(tmp_path / "state"),
        CourtFixture(),
        model,
        config=config,
    )

    assert result.publishable
    assert result.content.projection is not None
    assert result.content.projection.cases[0].dek == original
    assert all(
        request["response_format"]["json_schema"]["name"] != "reader_guide_field_repair"
        for request in model.requests
    )


def test_term_repair_diagnostic_uses_reviewed_ordinary_alternative() -> None:
    diagnostic = _repair_diagnostic(
        ReaderGuideFieldPath(kind=ReaderGuideFieldKind.DEK),
        BriefValidationError(
            "a legal term lacks an immediate explanation",
            safe_code="unexplained_legal_term_separation_of_powers",
        ),
    )

    assert diagnostic.offending_term == "separation of powers"
    assert "how government branches divide and limit their power" in (
        diagnostic.required_transformation
    )


def test_disposition_action_validation_repairs_only_rejected_field(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    court.rows = []
    court.slip_rows = [
        (
            "17",
            "3/04/26",
            "25A810",
            "Emergency Applicant v. Agency",
            "PC",
            "25a810_example.pdf",
        )
    ]
    court.documents["/docket/docketfiles/html/public/25A810.html"] = (
        '"docket-a810"',
        b"<!doctype html><body>Docket 25A810. Emergency Applicant v. Agency.</body>",
        "text/html",
    )
    court.documents["/opinions/25pdf/25a810_example.pdf"] = (
        '"opinion-a810"',
        _text_pdf(
            "No. 25A810 Emergency Applicant v. Agency.",
            "Emergency Applicant sought relief from Agency.",
            "The Court granted the application.",
        ),
        "application/pdf",
    )

    class CorrectingDispositionModel(MockOpenAI):
        def __init__(self) -> None:
            super().__init__()
            self.brief_calls = 0

        def create(self, **request: Any) -> object:
            completion = super().create(**request)
            name = request["response_format"]["json_schema"]["name"]
            if name not in {"compact_reader_guide", "reader_guide_field_repair"}:
                return completion
            self.brief_calls += 1
            if name != "compact_reader_guide":
                return completion
            payload = json.loads(completion.choices[0].message.content)
            user = json.loads(request["messages"][1]["content"])
            action_index = next(
                index
                for index, section in enumerate(user["sections"])
                if section["purpose"] == "court_action"
            )
            payload["section_paragraphs"][action_index] = (
                "The Supreme Court denied the application."
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    model = CorrectingDispositionModel()
    config = live_config().model_copy(
        update={
            "generation": live_config().generation.model_copy(
                update={"maximum_brief_validation_attempts_per_case": 2}
            )
        }
    )
    result = run(
        tmp_path,
        MemoryStateStore(tmp_path / "state"),
        court,
        model,
        config=config,
    )

    assert result.publishable
    assert model.brief_calls == 2
    repair_request = next(
        request
        for request in model.requests
        if request["response_format"]["json_schema"]["name"] == "reader_guide_field_repair"
    )
    repair_payload = json.loads(repair_request["messages"][1]["content"])
    assert repair_payload["diagnostic"]["rule"] == "unsupported_court_action"
    assert repair_payload["rejected_text"] == "The Supreme Court denied the application."


def test_unchanged_disposition_reuses_guide_without_model_call(tmp_path: Path) -> None:
    court = CourtFixture()
    court.rows = []
    court.slip_rows = [
        (
            "17",
            "3/04/26",
            "25A810",
            "Emergency Applicant v. Agency",
            "PC",
            "25a810_example.pdf",
        )
    ]
    court.documents["/docket/docketfiles/html/public/25A810.html"] = (
        '"docket-a810"',
        b"<!doctype html><body>Docket 25A810. Emergency Applicant v. Agency.</body>",
        "text/html",
    )
    court.documents["/opinions/25pdf/25a810_example.pdf"] = (
        '"opinion-a810"',
        _text_pdf(
            "No. 25A810 Emergency Applicant v. Agency.",
            "Emergency Applicant sought relief from Agency.",
            "The Court granted the application.",
        ),
        "application/pdf",
    )
    store = MemoryStateStore(tmp_path / "state")
    first = run(tmp_path, store, court, MockOpenAI())
    assert first.publishable
    store.content = first.content
    court.document_requests.clear()

    model = MockOpenAI()
    unchanged = run(tmp_path, store, court, model)

    assert unchanged.publishable and unchanged.no_public_change
    assert unchanged.changed_case_keys == ()
    assert model.requests == []
    assert court.document_requests == []


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
    assert names.count("compact_reader_guide") == 1


@pytest.mark.parametrize("stale_processor", [None, "f" * 64])
def test_unselected_stale_processor_fingerprints_do_not_enqueue_complete_corpus(
    tmp_path: Path,
    stale_processor: str | None,
) -> None:
    court = CourtFixture()
    court.rows.append(("25-2", "Second v. Agency", "4/21/26", "25-2.pdf"))
    court.documents["/pdfs/transcripts/2025/25-2.pdf"] = (
        '"transcript-2"',
        _pdf(1),
        "application/pdf",
    )
    court.documents["/docket/docketfiles/html/public/25-2.html"] = (
        '"docket-2"',
        b"<!doctype html><body>Docket 25-2. Whether the law limits agency power.</body>",
        "text/html",
    )
    store = MemoryStateStore(tmp_path / "state")
    first = run(tmp_path, store, court, MockOpenAI())
    assert first.content.projection is not None
    case_keys = tuple(item.case_key for item in first.content.publication.cases)
    assert len(case_keys) == 2
    legacy = replace(
        first.content,
        publication=first.content.publication.model_copy(
            update={
                "cases": tuple(
                    item.model_copy(update={"processor_sha256": stale_processor})
                    for item in first.content.publication.cases
                ),
                # This is the exact zero-attempt marker emitted by the failed global
                # migration experiment; safe-baseline discovery cleans it rather than
                # treating it as selected editorial work.
                "pending_work": tuple(
                    PendingWork(
                        case_key=case_key,
                        reason=PendingReason.BUDGET_EXHAUSTED,
                        attempts=0,
                        first_seen_at=NOW,
                        authoritative_activity_date=next(
                            case.latest_court_document_date
                            for case in first.content.projection.cases
                            if public_case_key(case.term, case.primary_docket) == case_key
                        ),
                    )
                    for case_key in case_keys
                ),
            }
        ),
    )
    store.content = legacy

    model = MockOpenAI()
    result = run(tmp_path, store, court, model)

    assert result.publishable
    assert result.changed_case_keys == ()
    assert result.pending_case_keys == ()
    assert {item.processor_sha256 for item in result.content.publication.cases} == {stale_processor}
    assert result.content.projection is not None
    assert all(len(case.revisions) == 1 for case in result.content.projection.cases)
    assert model.requests == []


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


def test_pending_new_argument_is_reconsidered_after_index_checkpoint_304(
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
        b"<!doctype html><body>Docket 25-2. Second v. Agency.</body>",
        "text/html",
    )

    failed = run(tmp_path, store, court, MockOpenAI(), backend=FailingBackend)
    assert failed.pending_case_keys == ("2025-25-2",)
    assert failed.checkpointable
    store.content = failed.content
    court.source_requests.clear()

    retried = run(tmp_path, store, court, MockOpenAI())

    assert retried.pending_case_keys == ()
    assert "2025-25-2" in retried.changed_case_keys
    _, conditional = next(
        request
        for request in court.source_requests
        if request[0].endswith("/oral_arguments/argument_transcript/2025")
    )
    assert conditional == ConditionalRequest()


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
    assert [item.official_url for item in candidate.docket_documents] == [case.official_docket_url]
    assert [item.document_type for item in candidate.related_documents] == [DocumentType.ORDER]
    source = _CaseInput(
        case_key=public_case_key(case.term, case.primary_docket),
        term=case.term,
        primary_docket=case.primary_docket,
        caption=case.caption,
        sessions=(candidate,),
        dispositions=(),
        prior=case,
        document_logical_keys={
            (DocumentType.OFFICIAL_TRANSCRIPT, transcript.official_url): (transcript.logical_key),
            (DocumentType.ORDER, order_url): order.logical_key,
        },
    )
    reconstructed = _case_documents(source)
    assert {item.logical_key for item in reconstructed} == {
        transcript.logical_key,
        order.logical_key,
        "2025-25-1:docket:25-1",
    }
    assert {item.kind.value for item in reconstructed} == {
        "transcript",
        "docket",
        "order",
    }


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

    for index in range(2, 11):
        court.rows.append(
            (
                f"25-{index}",
                f"Example {index} v. Agency",
                f"4/{19 + index}/26",
                f"25-{index}.pdf",
            )
        )
        court.documents[f"/pdfs/transcripts/2025/25-{index}.pdf"] = (
            f'"transcript-{index}"',
            _pdf(1),
            "application/pdf",
        )
        court.documents[f"/docket/docketfiles/html/public/25-{index}.html"] = (
            f'"docket-{index}"',
            f"<!doctype html><body>Docket 25-{index}. Synthetic docket.</body>".encode(),
            "text/html",
        )
    court.index_etag = '"index-2"'
    second = run(tmp_path, store, court, MockOpenAI())
    store.content = second.content
    old_processor = second.content.publication.processor
    assert old_processor is not None
    assert len(second.content.publication.cases) == 10
    prior_manifest = tuple(
        reversed(tuple(pointer.case_key for pointer in second.content.publication.cases))
    )
    prior_backfill = EditorialBackfillState(
        processor_sha256=old_processor.composite_sha256,
        rollout_stage=EditorialRolloutStage.CANARY_10,
        newest_first_rank_boundary=10,
        selected_case_keys=prior_manifest,
        attempted_count=10,
        failed_count=10,
    )
    prior_report = CanaryAggregate(
        processor_sha256=old_processor.composite_sha256,
        rollout_stage=EditorialRolloutStage.CANARY_10,
        case_keys=prior_manifest,
        attempted_count=10,
        accepted_count=0,
        failed_count=10,
        failure_code_counts=(
            CanaryFailureCount(code=RetryFailureCode.VALIDATION_FAILED, count=10),
        ),
        runtime_seconds=500,
        model_call_count=99,
        reviewer_decision=CanaryReviewerDecision.REJECTED,
    )
    prior_pending = tuple(
        PendingWork(
            case_key=key,
            reason=PendingReason.VALIDATION_FAILED,
            attempts=3,
            first_seen_at=NOW,
            last_attempted_at=NOW,
            retry=PendingModelRetry(
                scope_sha256="e" * 64,
                stage="brief",
                completed_cycles=3,
                last_cycle_at=NOW,
                next_eligible_at=NOW,
                status=ModelRetryStatus.EXHAUSTED,
                failure_code=RetryFailureCode.VALIDATION_FAILED,
            ),
        )
        for key in sorted(prior_manifest)
    )
    store.content = replace(
        second.content,
        publication=second.content.publication.model_copy(
            update={
                "editorial_backfill": prior_backfill,
                "canary_report": prior_report,
                "pending_work": prior_pending,
            }
        ),
    )

    fresh_docket = "25-11"
    fresh_key = public_case_key("2025", fresh_docket)
    court.rows.append((fresh_docket, "Fresh Case v. Agency", "4/30/26", "25-11.pdf"))
    court.documents["/pdfs/transcripts/2025/25-11.pdf"] = (
        '"transcript-11"',
        _pdf(1),
        "application/pdf",
    )
    court.documents["/docket/docketfiles/html/public/25-11.html"] = (
        '"docket-11"',
        b"<!doctype html><body>Docket 25-11. Synthetic docket.</body>",
        "text/html",
    )
    court.index_etag = '"index-3"'

    base = live_config()
    migrating = base.model_copy(
        update={
            "parser": base.parser.model_copy(update={"version": "2"}),
            "runner_limits": base.runner_limits.model_copy(update={"maximum_cases_per_run": 1}),
            "editorial_backfill": base.editorial_backfill.model_copy(
                update={
                    "rollout_stage": "canary_10",
                    "replacement_canary_case_keys": prior_manifest,
                }
            ),
            "publication": base.publication.model_copy(update={"dry_run": True}),
        }
    )
    comparison_docket = prior_manifest[0].removeprefix("2025-")
    comparison_url = f"/pdfs/transcripts/2025/{comparison_docket}.pdf"
    original_comparison_document = court.documents[comparison_url]
    court.documents[comparison_url] = (
        '"comparison-drift"',
        _pdf(2),
        "application/pdf",
    )
    replacement_model = MockOpenAI()
    # A paired control arm must collect current Court evidence. Comparison to the
    # independent candidate now happens against the sanitized post-run baseline.
    current_evidence = run(tmp_path, store, court, replacement_model, config=migrating)
    prior_evidence = {
        item.logical_key: item.integrity.sha256
        for item in second.content.publication.documents
        if item.case_key == prior_manifest[0]
    }
    refreshed_evidence = {
        item.logical_key: item.integrity.sha256
        for item in current_evidence.content.publication.documents
        if item.case_key == prior_manifest[0]
    }
    assert refreshed_evidence != prior_evidence
    assert replacement_model.requests

    court.documents[comparison_url] = original_comparison_document
    partial = run(tmp_path, store, court, replacement_model, config=migrating)
    assert partial.content.publication.processor == old_processor
    assert len(partial.pending_case_keys) == 10
    assert fresh_key in partial.pending_case_keys
    assert partial.changed_case_keys == (prior_manifest[0],)
    partial_backfill = partial.content.publication.editorial_backfill
    assert partial_backfill is not None
    assert partial_backfill.selected_case_keys == prior_manifest
    assert partial_backfill.attempted_count == partial_backfill.accepted_count == 1
    assert partial_backfill.failed_count == 0
    partial_report = partial.content.publication.canary_report
    assert partial_report is not None
    assert partial_report.processor_sha256 == partial_backfill.processor_sha256
    assert partial_report.case_keys == prior_manifest
    assert partial_report.attempted_count == partial_report.accepted_count == 1
    assert partial_report.failed_count == 0
    assert partial_report.failure_code_counts == ()
    assert partial_report.runtime_seconds < prior_report.runtime_seconds
    assert partial_report.model_call_count < prior_report.model_call_count
    assert partial_report.reviewer_decision is CanaryReviewerDecision.PENDING
    fingerprints = {pointer.processor_sha256 for pointer in partial.content.publication.cases}
    assert len(fingerprints) == 2

    store.content = partial.content
    completion_config = migrating.model_copy(
        update={
            "runner_limits": migrating.runner_limits.model_copy(
                update={"maximum_cases_per_run": 10}
            )
        }
    )
    completed = run(tmp_path, store, court, MockOpenAI(), config=completion_config)
    assert completed.pending_case_keys == (fresh_key,)
    promoted = completed.content.publication.processor
    assert promoted is not None and promoted != old_processor
    assert {pointer.processor_sha256 for pointer in completed.content.publication.cases} == {
        promoted.composite_sha256
    }
