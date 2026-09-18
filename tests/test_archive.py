from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from scotus_guide.archive import (
    ContentAddressedArchive,
    DocumentCandidate,
    EmptyDiscoveryError,
    ManifestStore,
    PdfDownloader,
    Reconciler,
)
from scotus_guide.integrity import check_archive
from scotus_guide.models import CaseAssociation, DocumentType

PDF_ONE = b"%PDF-1.7\nfirst revision\n%%EOF\n"
PDF_TWO = b"%PDF-1.7\ncorrected revision\n%%EOF\n"
URL = "https://www.supremecourt.gov/files/example.pdf"


def candidate(*, expected_hash: str | None = None) -> DocumentCandidate:
    return DocumentCandidate(
        url=URL,
        document_type=DocumentType.OPINION,
        cases=[CaseAssociation(case_id="scotus-24-7", docket_numbers=["24-7", "24-38"])],
        expected_sha256=expected_hash,
    )


def reconciler(tmp_path: Path, handler: object) -> Reconciler:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return Reconciler(
        ContentAddressedArchive(tmp_path),
        ManifestStore(tmp_path / "manifests/documents.json"),
        PdfDownloader(client),
        tmp_path / ".tmp",
    )


def test_download_reconcile_is_idempotent_and_tracks_revision(tmp_path: Path) -> None:
    bodies = iter([PDF_ONE, PDF_ONE, PDF_TWO])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=next(bodies), request=request)

    service = reconciler(tmp_path, handler)
    first = service.reconcile([candidate()])
    digest_one = hashlib.sha256(PDF_ONE).hexdigest()
    assert first.accepted_hashes == [digest_one]
    assert first.changed_case_ids == {"scotus-24-7"}
    assert first.as_dict()["failures"] == []
    assert (tmp_path / f"documents/{digest_one[:2]}/{digest_one}.pdf").read_bytes() == PDF_ONE

    second = service.reconcile([candidate()])
    assert second.unchanged_hashes == [digest_one]
    third = service.reconcile([candidate()])
    digest_two = hashlib.sha256(PDF_TWO).hexdigest()
    assert third.accepted_hashes == [digest_two]
    manifest = ManifestStore(tmp_path / "manifests/documents.json").load()
    assert len(manifest.documents) == 2
    revised = next(entry for entry in manifest.documents if entry.sha256 == digest_two)
    assert revised.supersedes == digest_one
    assert len(revised.cases[0].docket_numbers) == 2


def test_expected_known_hash_skips_network(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=PDF_ONE, request=request)

    service = reconciler(tmp_path, handler)
    digest = hashlib.sha256(PDF_ONE).hexdigest()
    service.reconcile([candidate()])
    known = candidate(expected_hash=digest).model_copy(
        update={"cases": [CaseAssociation(case_id="scotus-24-99", docket_numbers=["24-99"])]}
    )
    result = service.reconcile([known])
    assert result.accepted_hashes == [digest]
    assert result.changed_case_ids == {"scotus-24-99"}
    assert calls == 1
    assert len(ManifestStore(tmp_path / "manifests/documents.json").load().documents[0].cases) == 2


def test_same_bytes_from_another_url_preserve_both_sources(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PDF_ONE, request=request)

    service = reconciler(tmp_path, handler)
    service.reconcile([candidate()])
    alternate = candidate().model_copy(
        update={"url": "https://www.supremecourt.gov/alternate/example.pdf"}
    )
    result = service.reconcile([alternate])
    assert result.accepted_hashes == [hashlib.sha256(PDF_ONE).hexdigest()]
    entry = ManifestStore(tmp_path / "manifests/documents.json").load().documents[0]
    assert {str(source.url) for source in entry.sources} == {URL, str(alternate.url)}


def test_invalid_candidate_does_not_prevent_other_candidate_or_mutate_prior(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = b"not a pdf" if request.url.path.endswith("bad.pdf") else PDF_ONE
        return httpx.Response(200, content=body, request=request)

    service = reconciler(tmp_path, handler)
    bad = candidate().model_copy(update={"url": "https://www.supremecourt.gov/bad.pdf"})
    result = service.reconcile([bad, candidate()])
    assert len(result.failures) == 1
    assert len(result.accepted_hashes) == 1
    assert len(ManifestStore(tmp_path / "manifests/documents.json").load().documents) == 1
    with pytest.raises(EmptyDiscoveryError):
        service.reconcile([])


def test_integrity_detects_missing_and_unreferenced_files(tmp_path: Path) -> None:
    service = reconciler(
        tmp_path,
        lambda request: httpx.Response(200, content=PDF_ONE, request=request),
    )
    service.reconcile([candidate()])
    good = check_archive(tmp_path, ManifestStore(tmp_path / "manifests/documents.json"))
    assert good.valid
    orphan = tmp_path / "documents/ff/orphan.pdf"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(PDF_TWO)
    report = check_archive(tmp_path, ManifestStore(tmp_path / "manifests/documents.json"))
    assert {issue.kind for issue in report.issues} == {"unreferenced"}
    assert report.as_dict()["valid"] is False
