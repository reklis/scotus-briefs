from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pypdf import PdfWriter
from pypdf import __version__ as pypdf_version

from ragchew.config import ScotusConfig
from ragchew.proceedings.contracts import (
    GovernmentAuthority,
    Jurisdiction,
    OfficialSource,
    SourceAccessMethod,
    SourceHealth,
)
from ragchew.proceedings.registry import InMemorySourceRegistry, SourceAuthorizer
from ragchew.scotus.contracts import ScotusDocumentKind, SpeakerKind
from ragchew.scotus.documents import (
    InMemoryDocumentIngestionStore,
    PendingDocument,
    ScotusDocumentCollector,
)
from ragchew.scotus.static_contracts import (
    ConditionalValidators,
    ContentIntegrity,
    LogicalDocumentState,
)
from ragchew.scotus.transcript_parser import (
    PdfTextBackend,
    ScotusTranscriptParser,
    TranscriptParseError,
)
from ragchew.scotus.worker import _opinion_names_docket
from tests.fakes import FakeObjectStore

NOW = datetime(2026, 8, 28, 2, tzinfo=UTC)


def pdf_bytes(pages: int = 1, *, encrypted: bool = False, empty_password: bool = False) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if encrypted or empty_password:
        writer.encrypt("" if empty_password else "test-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def source() -> OfficialSource:
    return OfficialSource(
        source_id="supreme_court",
        authority=GovernmentAuthority.US_SUPREME_COURT,
        jurisdiction=Jurisdiction.FEDERAL,
        display_name="Supreme Court",
        official_index_url="https://www.supremecourt.gov/oral_arguments/argument_audio.aspx",
        adapter="supreme_court",
        discovery_method=SourceAccessMethod.OFFICIAL_PAGE,
        media_method=SourceAccessMethod.DOWNLOADABLE_FILE,
        access_basis="reviewed Court-hosted pages",
        access_reviewed_at=NOW - timedelta(days=1),
        access_reviewed_by="project-source-access-review",
        access_review_expires_at=NOW + timedelta(days=365),
        allowed_hosts=("www.supremecourt.gov",),
        poll_interval_seconds=900,
        expected_schedule="Court term",
        enabled=True,
        health=SourceHealth.HEALTHY,
    )


def pending(*, revision: int = 1) -> PendingDocument:
    return PendingDocument(
        document_revision_id=uuid4(),
        case_id=uuid4(),
        argument_id=uuid4(),
        kind=ScotusDocumentKind.TRANSCRIPT,
        external_id="2025:25-466:transcript",
        revision_number=revision,
        official_url=(
            "https://www.supremecourt.gov/oral_arguments/argument_transcripts/2025/25-466_ec8f.pdf"
        ),
        expected_content_type="application/pdf",
        observed_at=NOW,
    )


def collector(
    handler: httpx.MockTransport,
) -> tuple[ScotusDocumentCollector, InMemoryDocumentIngestionStore, FakeObjectStore]:
    registry = InMemorySourceRegistry()
    registry.register(source(), "approved Court source")
    store = InMemoryDocumentIngestionStore()
    objects = FakeObjectStore()
    service = ScotusDocumentCollector(
        SourceAuthorizer(registry),
        store,
        objects,
        ScotusConfig.from_yaml("config/scotus.yaml"),
        user_agent="ragchew-test contact=test@example.test",
        client=httpx.Client(transport=handler, follow_redirects=False),
        before_request=lambda: None,
    )
    return service, store, objects


def response(data: bytes, content_type: str = "application/pdf") -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"Content-Type": content_type, "Content-Length": str(len(data))},
            content=data,
        )
    )


def test_valid_pdf_is_private_idempotent_and_enqueues_one_parse() -> None:
    item = pending()
    service, store, objects = collector(response(pdf_bytes()))
    first = service.collect(item, NOW)
    duplicate = service.collect(item.model_copy(update={"document_revision_id": uuid4()}), NOW)
    assert first.status == "ready"
    assert first.parse_job_created
    assert duplicate.status == "duplicate"
    assert len(store.parse_jobs) == 1
    assert len(objects.objects) == 1
    assert first.object_key is not None
    assert first.object_key.startswith(
        f"official/us_supreme_court/supreme_court/{item.case_id}/transcript/"
    )


def test_reviewed_y2k_archive_transcript_path_is_accepted() -> None:
    item = pending().model_copy(
        update={
            "external_id": "2000:00-6374:transcript",
            "official_url": ("https://www.supremecourt.gov/pdfs/transcripts/2000/00-6374.pdf"),
        }
    )
    service, _, _ = collector(response(pdf_bytes()))
    assert service.collect(item, NOW).status == "ready"


def test_conditional_document_304_reuses_accepted_revision() -> None:
    item = pending()
    service, store, _ = collector(response(pdf_bytes()))
    first = service.collect(item, NOW)
    assert first.sha256 is not None and first.byte_count is not None
    checkpoint = LogicalDocumentState(
        logical_key="2025-25-466:transcript:2026-04-20:1",
        case_key="2025-25-466",
        document_kind="transcript",
        official_url=item.official_url,
        revision_number=1,
        validators=ConditionalValidators(etag='"court-v1"'),
        integrity=ContentIntegrity(sha256=first.sha256, byte_count=first.byte_count),
        checked_at=NOW,
    )

    def not_modified(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"court-v1"'
        return httpx.Response(304, headers={"ETag": '"court-v1"'})

    service.client = httpx.Client(
        transport=httpx.MockTransport(not_modified), follow_redirects=False
    )
    result = service.collect(item, NOW + timedelta(days=1), checkpoint=checkpoint)
    assert result.status == "duplicate"
    assert result.not_modified
    assert result.document_revision_id == item.document_revision_id
    assert len(store.parse_jobs) == 1


def test_new_revision_accepts_changed_bytes_and_supersedes_identity() -> None:
    item = pending()
    service, store, objects = collector(response(pdf_bytes()))
    first = service.collect(item, NOW)
    service.client = httpx.Client(transport=response(pdf_bytes(2)), follow_redirects=False)
    revised_item = item.model_copy(update={"document_revision_id": uuid4(), "revision_number": 2})
    revised = service.collect(revised_item, NOW + timedelta(minutes=15))
    assert first.status == "ready"
    assert revised.status == "ready"
    assert revised.sha256 != first.sha256
    assert len(store.parse_jobs) == 2
    assert len(objects.objects) == 2
    current = store.accepted_for_identity(item.case_id, item.kind, item.external_id)
    assert current is not None
    assert current.document_revision_id == revised_item.document_revision_id


def test_static_collection_allocates_changed_logical_document_revision() -> None:
    item = pending().model_copy(update={"logical_key": "2025-25-466:transcript:session-1"})
    service, store, _ = collector(response(pdf_bytes()))
    assert service.collect(item, NOW).revision_number == 1
    service.client = httpx.Client(transport=response(pdf_bytes(2)), follow_redirects=False)
    replacement = item.model_copy(
        update={
            "document_revision_id": uuid4(),
            "external_id": "2025:25-466:transcript:replacement.pdf",
            "official_url": (
                "https://www.supremecourt.gov/oral_arguments/argument_transcripts/"
                "2025/25-466_replacement.pdf"
            ),
        }
    )
    revised = service.collect(replacement, NOW, allocate_revision=True)
    assert revised.status == "ready"
    assert revised.revision_number == 2
    current = store.accepted_for_identity(item.case_id, item.kind, item.logical_key or "")
    assert current is not None and current.official_url == replacement.official_url


def test_changed_bytes_under_same_revision_identity_are_quarantined() -> None:
    item = pending()
    service, store, _ = collector(response(pdf_bytes()))
    assert service.collect(item, NOW).status == "ready"
    service.client = httpx.Client(transport=response(pdf_bytes(2)), follow_redirects=False)
    conflict = service.collect(item.model_copy(update={"document_revision_id": uuid4()}), NOW)
    assert conflict.status == "quarantined"
    assert conflict.document_revision_id in store.quarantined


@pytest.mark.parametrize(
    ("transport", "message"),
    [
        (
            httpx.MockTransport(
                lambda _request: httpx.Response(
                    302, headers={"Location": "https://platform.example/transcript.pdf"}
                )
            ),
            "redirects",
        ),
        (response(b"<html>error</html>"), "PDF signature"),
        (response(b"<html>error</html>", "text/html"), "MIME"),
    ],
)
def test_invalid_or_redirected_documents_fail_closed(
    transport: httpx.MockTransport, message: str
) -> None:
    service, store, objects = collector(transport)
    item = pending()
    result = service.collect(item, NOW)
    assert result.status == "failed"
    assert message.lower() in (result.diagnostic or "").lower()
    assert item.document_revision_id in store.failures
    assert objects.objects == {}


class BrokenStream(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b"%PDF-1.7\n"
        raise httpx.ReadError("connection lost")


def test_public_pdf_with_empty_encryption_password_is_accepted() -> None:
    service, _, _ = collector(response(pdf_bytes(empty_password=True)))
    result = service.collect(pending(), NOW)
    assert result.status == "ready"


def test_encrypted_pdf_fails_closed() -> None:
    service, store, objects = collector(response(pdf_bytes(encrypted=True)))
    item = pending()
    result = service.collect(item, NOW)
    assert result.status == "failed"
    assert "password-protected" in (result.diagnostic or "").lower()
    assert item.document_revision_id in store.failures
    assert objects.objects == {}


def test_interrupted_download_commits_no_object() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, headers={"Content-Type": "application/pdf"}, stream=BrokenStream()
        )
    )
    service, store, objects = collector(transport)
    item = pending()
    result = service.collect(item, NOW)
    assert result.status == "failed"
    assert item.document_revision_id in store.failures
    assert objects.objects == {}


class FakeBackend(PdfTextBackend):
    name = "fixture"
    version = "1"

    def __init__(self, pages: tuple[str, ...]):
        self.pages = pages

    def extract_pages(self, file: object) -> tuple[str, ...]:  # type: ignore[override]
        return self.pages


def test_transcript_parser_preserves_coordinates_artifacts_and_speaker_turns() -> None:
    pages = (
        """Official - Subject to Final Review
1
1                CHIEF JUSTICE ROBERTS: We'll hear argument this morning
2                in Case 25-466, Sripetch v. SEC.
3 MR. SMITH: Mr. Chief Justice, and may it please the Court:
4 Justice, and may it please the Court:
5 The statute does not authorize the action.""",
        """Official - Subject to Final Review
2
1                JUSTICE KAGAN: Is that rule consistent with Smith v. Jones?
2 MR. SMITH: Yes, because the statute addresses a different question.
(Whereupon, at 11:30 a.m., the argument ended.)
I N D E X
A very long appendix that is not part of the spoken argument.""",
    )
    config = ScotusConfig.from_yaml("config/scotus.yaml").parser
    result = ScotusTranscriptParser(FakeBackend(pages), config).parse(
        io.BytesIO(b"fixture"),
        parse_revision_id=uuid4(),
        document_revision_id=uuid4(),
    )
    assert result.page_count == 2
    assert result.line_coverage == 1
    assert any(line.artifact for line in result.lines)
    assert result.turns[0].speaker_name == "Chief Justice Roberts"
    assert result.turns[0].start_file_page == 1
    assert result.turns[0].end_line == 4
    assert result.turns[1].end_line == 7
    assert result.turns[2].speaker_name == "Justice Kagan"
    assert result.turns[2].speaker_kind is SpeakerKind.JUSTICE
    assert result.turns[2].start_file_page == 2
    assert result.turns[2].end_file_page == 2
    assert "appendix" not in result.turns[-1].text_private


def test_transcript_parser_fails_on_empty_page_and_uses_anonymous_fallback() -> None:
    config = ScotusConfig.from_yaml("config/scotus.yaml").parser
    with pytest.raises(TranscriptParseError, match="ambiguous"):
        ScotusTranscriptParser(FakeBackend(("",)), config).parse(
            io.BytesIO(), parse_revision_id=uuid4(), document_revision_id=uuid4()
        )
    result = ScotusTranscriptParser(FakeBackend(("1 unlabeled text",)), config).parse(
        io.BytesIO(), parse_revision_id=uuid4(), document_revision_id=uuid4()
    )
    assert result.turns[0].speaker_name is None
    assert result.turns[0].speaker_kind is SpeakerKind.UNKNOWN


def test_opinion_docket_validation_does_not_confuse_shorter_docket() -> None:
    text = "SUPREME COURT OF THE UNITED STATES No. 25\N{EN DASH}5146. Decided June 11."
    assert _opinion_names_docket(text, "25-5146")
    assert not _opinion_names_docket(text, "25-5")


def test_transcript_parser_supports_y2k_labels_and_stops_before_index() -> None:
    config = ScotusConfig.from_yaml("config/scotus.yaml").parser
    result = ScotusTranscriptParser(
        FakeBackend(
            (
                """GENERAL, ET AL. :
CHIEF JUSTICE: We will hear argument now.
MR.ALBERS: The statute controls.
(Short break at 11:32 a.m.)
INDEX
A concordance appendix that is not spoken text.""",
            )
        ),
        config,
    ).parse(io.BytesIO(), parse_revision_id=uuid4(), document_revision_id=uuid4())
    assert result.turns[0].speaker_name == "Chief Justice"
    assert result.turns[1].speaker_name == "Mr.Albers"
    assert "appendix" not in result.turns[-1].text_private


def test_pypdf_is_pinned_to_supported_major_and_locked() -> None:
    assert pypdf_version.startswith("6.")
    lock = Path("uv.lock").read_text()
    assert 'name = "pypdf"' in lock
    assert 'version = "6.16.2"' in lock
