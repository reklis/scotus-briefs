from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import UTC, datetime
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
from ragchew.proceedings.sources.http import RequestRateLimiter, SourceResponse
from ragchew.proceedings.sources.supreme_court import SupremeCourtAdapter
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
    _default_ollama_client,
    _descriptor_for_public_argument,
    _legal_analysis_observations,
    _opinion_page_attribution,
    _procedural_path_observation,
)
from ragchew.scotus.public_contracts import public_case_key
from ragchew.scotus.static_contracts import (
    ConditionalValidators,
    ContentIntegrity,
    CostLedger,
    CostReceiptBundle,
    LogicalSourceState,
    ModelAttemptOutcome,
    PendingReason,
    PendingWork,
)
from ragchew.scotus.static_pipeline import (
    PublicationGateDenied,
    StaticBatchResult,
    UnifiedRunBudget,
)
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
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=self)
        self.models = SimpleNamespace(
            list=lambda: SimpleNamespace(data=[SimpleNamespace(id="qwen3.8:27b")])
        )
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    def create(self, **request: Any) -> object:
        self.requests.append(request)
        name = request["response_format"]["json_schema"]["name"]
        user = json.loads(request["messages"][1]["content"])
        content = (
            self._extraction(user["evidence"])
            if name == "scotus_legal_observations"
            else self._brief(user)
        )
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
    def _brief(user: dict[str, Any]) -> dict[str, object]:
        claims = user["claims"]
        all_ids = [claim["claim_id"] for claim in claims]
        if not user.get("argument_sessions"):
            ids_by_type = {
                claim_type: [
                    claim["claim_id"]
                    for claim in claims
                    if claim["type"] == claim_type
                ]
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
            issue_ids = ids_by_type["question_presented"] or ids_by_type[
                "doctrinal_theme"
            ][:1]
            reasoning_ids = [
                claim_id
                for claim_id in ids_by_type["doctrinal_theme"]
                if claim_id not in issue_ids
            ]
            action_ids = [*ids_by_type["holding"], *ids_by_type["order"]]
            action_values = " ".join(
                claim["value"] for claim in claims if claim["claim_id"] in action_ids
            ).casefold()
            action_paragraph = (
                "The Supreme Court stayed the injunction temporarily while the case continues."
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
                        "paragraphs": [
                            "The Court explained its reasoning for the result."
                        ],
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
    assert _opinion_page_attribution(
        "JACKSON, J., dissenting\nI would deny relief.",
        "Opinion of the Court",
    ) == "Justice Jackson, dissenting"
    assert _opinion_page_attribution(
        "JUSTICE KAGAN, dissenting\nI would deny relief.",
        "Opinion of the Court",
    ) == "Justice Kagan, dissenting"
    assert _opinion_page_attribution(
        "JUSTICE KAGAN, with whom JUSTICE SOTOMAYOR joins, dissenting\nReasoning.",
        "Opinion of the Court",
    ) == "Justice Kagan, dissenting"
    assert _opinion_page_attribution(
        "CHIEF JUSTICE ROBERTS, dissenting\nReasoning.",
        "Opinion of the Court",
    ) == "Justice Roberts, dissenting"
    assert _opinion_page_attribution(
        "JACKSON, J., dissenting\nThe analysis continues.",
        "Justice Jackson, dissenting",
    ) == "Justice Jackson, dissenting"
    assert _opinion_page_attribution(
        "SOTOMAYOR, J., concurring in part and dissenting in part\nSeparate reasoning.",
        "Justice Jackson, dissenting",
    ) == "Justice Sotomayor, concurring in part and dissenting in part"


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


def test_live_adapter_requires_exact_local_model_before_court_traffic(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    model = MockOpenAI()
    model.models = SimpleNamespace(
        list=lambda: SimpleNamespace(data=[SimpleNamespace(id="qwen3.8:14b")])
    )
    adapter = build_adapter(court, model)
    with pytest.raises(PublicationGateDenied, match="not installed"):
        adapter.run(
            state_store=MemoryStateStore(tmp_path / "state"),
            config=live_config(),
            mode=DiscoveryMode.NIGHTLY,
            runner_temp=tmp_path / "private",
            authorized_replay=False,
        )
    assert court.source_requests == []
    assert court.document_requests == []
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
    assert processor.model == "ollama:qwen3.8:27b@http://127.0.0.1:11434/v1"
    assert processor.policy_version == "scotus-brief-policy-v29"
    assert processor.prompt_version == (
        "scotus-brief-plain-language-v31;disposition=scotus-disposition-citizen-guide-v7"
    )
    assert [request["response_format"]["json_schema"]["name"] for request in model.requests] == [
        "scotus_legal_observations",
        "scotus_legal_brief",
    ]
    assert all(request["extra_body"] == {"think": False} for request in model.requests)
    assert model.requests[0]["max_tokens"] == 8_000
    assert model.requests[1]["max_tokens"] == 8_000
    assert model.requests[1]["reasoning_effort"] == "none"
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
    assert brief_payload["mode"] == "/no_think"
    brief_schema = model.requests[1]["response_format"]["json_schema"]["schema"]
    assert "$defs" not in brief_schema
    receipts = CostReceiptBundle.model_validate_json(
        (tmp_path / "private/public-cost-receipts.json").read_bytes()
    )
    assert {receipt.stage for receipt in receipts.receipts} == {"extraction", "brief"}
    assert all(receipt.outcome is ModelAttemptOutcome.SUCCEEDED for receipt in receipts.receipts)
    assert sum(receipt.call_count for receipt in receipts.receipts) == len(model.requests)
    assert not list((tmp_path / "private").glob("ragchew-*"))


def test_existing_argument_brief_adds_disposition_metadata_without_model_replay(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    store = MemoryStateStore(tmp_path / "state")
    first_model = MockOpenAI()
    first = run(tmp_path, store, court, first_model)
    assert first.publishable
    assert first.content.projection is not None
    accepted_sections = first.content.projection.cases[0].sections
    legacy_publication = first.content.publication.model_copy(
        update={
            "cases": tuple(
                pointer.model_copy(update={"processor_sha256": None})
                for pointer in first.content.publication.cases
            )
        }
    )
    store.content = replace(first.content, publication=legacy_publication)
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
        ),
        "application/pdf",
    )
    update_model = MockOpenAI()
    updated = run(tmp_path, store, court, update_model)

    assert updated.publishable
    assert updated.changed_case_keys == ("2025-25-1",)
    assert updated.content.projection is not None
    case = updated.content.projection.cases[0]
    assert case.sections == accepted_sections
    assert case.latest_court_document_date == datetime(2026, 6, 30, tzinfo=UTC)
    assert case.case_status.value == "decided"
    assert case.dispositions[0].official_url.endswith("/25-1_example.pdf")
    assert [item.revision_number for item in case.revisions] == [1, 2]
    assert update_model.requests == []
    assert not any("transcripts" in request.url.path for request in court.document_requests)


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
    assert names == ["scotus_legal_observations", "scotus_legal_brief"]
    brief_request = model.requests[-1]
    brief_schema = brief_request["response_format"]["json_schema"]["schema"]
    assert brief_schema["properties"]["argument_analyses"]["maxItems"] == 0
    assert tuple(section.heading for section in case.sections) == (
        "What this case is about",
        "Why this case reached the Court",
        "The legal issue",
        "What the Supreme Court did",
        "Why the Court did it",
    )
    assert case.sections[3].paragraphs == (
        "The Supreme Court granted the application.",
    )
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
                if item["observation_type"]
                not in {"procedural_posture", "holding", "order"}
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
    assert any(
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


def test_brief_validation_gets_one_bounded_fixed_code_correction(
    tmp_path: Path,
) -> None:
    class CorrectingModel(MockOpenAI):
        def __init__(self) -> None:
            super().__init__()
            self.brief_calls = 0

        def create(self, **request: Any) -> object:
            completion = super().create(**request)
            if request["response_format"]["json_schema"]["name"] != "scotus_legal_brief":
                return completion
            self.brief_calls += 1
            if self.brief_calls != 1:
                return completion
            payload = json.loads(completion.choices[0].message.content)
            user = json.loads(request["messages"][1]["content"])
            justice_ids = {
                claim["claim_id"] for claim in user["claims"] if claim["type"] == "justice_question"
            }
            for analysis in payload["argument_analyses"]:
                analysis["claim_ids"] = [
                    claim_id for claim_id in analysis["claim_ids"] if claim_id not in justice_ids
                ]
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
    brief_requests = [
        request
        for request in model.requests
        if request["response_format"]["json_schema"]["name"] == "scotus_legal_brief"
    ]
    assert (
        "argument_breakdown_omits_justice_question" in brief_requests[1]["messages"][0]["content"]
    )


def test_disposition_guide_validation_retries_with_fixed_structure_code(
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
            if request["response_format"]["json_schema"]["name"] != "scotus_legal_brief":
                return completion
            self.brief_calls += 1
            if self.brief_calls != 1:
                return completion
            payload = json.loads(completion.choices[0].message.content)
            payload["sections"][1], payload["sections"][2] = (
                payload["sections"][2],
                payload["sections"][1],
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
    brief_requests = [
        request
        for request in model.requests
        if request["response_format"]["json_schema"]["name"] == "scotus_legal_brief"
    ]
    assert "invalid_guide_structure" in brief_requests[1]["messages"][0]["content"]


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
    assert names.count("scotus_legal_brief") == 1


def test_legacy_case_without_document_checkpoints_is_not_automatically_reprocessed(
    tmp_path: Path,
) -> None:
    court = CourtFixture()
    store = MemoryStateStore(tmp_path / "state")
    first = run(tmp_path, store, court, MockOpenAI())
    legacy = replace(
        first.content,
        publication=first.content.publication.model_copy(
            update={
                "documents": (),
                "cases": tuple(
                    item.model_copy(update={"processor_sha256": None})
                    for item in first.content.publication.cases
                ),
                "pending_work": (
                    PendingWork(
                        case_key="2025-25-1",
                        reason=PendingReason.BUDGET_EXHAUSTED,
                        attempts=0,
                        first_seen_at=NOW,
                    ),
                ),
            }
        ),
    )
    store.content = legacy

    result = run(tmp_path, store, court, MockOpenAI())

    assert result.publishable
    assert result.no_public_change
    assert result.changed_case_keys == ()
    assert result.content.publication.documents == ()
    assert result.content.publication.pending_work == ()
    assert result.content.projection == first.content.projection


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

    court.rows.append(("25-2", "Second v. Agency", "4/21/26", "25-2.pdf"))
    court.index_etag = '"index-2"'
    court.documents["/pdfs/transcripts/2025/25-2.pdf"] = (
        '"transcript-2"',
        _pdf(1),
        "application/pdf",
    )
    court.documents["/docket/docketfiles/html/public/25-2.html"] = (
        '"docket-2"',
        b"<!doctype html><html><body>Docket 25-2. Second synthetic docket.</body></html>",
        "text/html",
    )
    second = run(tmp_path, store, court, MockOpenAI())
    store.content = second.content
    old_processor = second.content.publication.processor
    assert old_processor is not None
    assert len(second.content.publication.cases) == 2

    base = live_config()
    migrating = base.model_copy(
        update={
            "parser": base.parser.model_copy(update={"version": "2"}),
            "runner_limits": base.runner_limits.model_copy(update={"maximum_cases_per_run": 1}),
        }
    )
    partial = run(tmp_path, store, court, MockOpenAI(), config=migrating)
    assert partial.content.publication.processor == old_processor
    assert len(partial.pending_case_keys) == 1
    fingerprints = {pointer.processor_sha256 for pointer in partial.content.publication.cases}
    assert len(fingerprints) == 2

    store.content = partial.content
    completed = run(tmp_path, store, court, MockOpenAI(), config=migrating)
    assert completed.pending_case_keys == ()
    promoted = completed.content.publication.processor
    assert promoted is not None and promoted != old_processor
    assert {pointer.processor_sha256 for pointer in completed.content.publication.cases} == {
        promoted.composite_sha256
    }
