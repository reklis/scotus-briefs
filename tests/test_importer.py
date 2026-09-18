from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scotus_guide.archive import ManifestStore
from scotus_guide.importer import import_corpus

PDF = b"%PDF-1.4\nhistorical\n%%EOF\n"


def test_import_verifies_jsonl_and_creates_unresolved_group(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    source = corpus / "opaque-uuid" / "transcript.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(PDF)
    digest = hashlib.sha256(PDF).hexdigest()
    jsonl = tmp_path / "manifest.jsonl"
    rows = [
        {
            "path": "opaque-uuid/transcript.pdf",
            "sha256": digest,
            "size": len(PDF),
            "group": "opaque-uuid",
            "type": "transcript",
        },
        {"path": "missing.pdf", "sha256": "0" * 64},
    ]
    jsonl.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    store = ManifestStore(tmp_path / "manifests/documents.json")
    report = import_corpus(jsonl, corpus, tmp_path, store)
    assert report.imported_hashes == [digest]
    assert len(report.problems) == 1
    assert report.as_dict()["problems"]
    entry = store.load().documents[0]
    assert entry.sources[0].import_path == "opaque-uuid/transcript.pdf"
    assert entry.cases[0].historical_group == "opaque-uuid"
    case_path = tmp_path / "data/cases" / f"{report.unresolved_case_ids[0]}.json"
    case = json.loads(case_path.read_text())
    assert case["docket_numbers"] == []
    assert case["lifecycle"] == "unresolved"

    archived = tmp_path / entry.archive_path
    archived.unlink()
    repeated = import_corpus(jsonl, corpus, tmp_path, store)
    assert digest in repeated.unchanged_hashes
    assert archived.read_bytes() == PDF


def test_import_rejects_checksum_mismatch_without_manifest_entry(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "bad.pdf").write_bytes(PDF)
    jsonl = tmp_path / "manifest.jsonl"
    jsonl.write_text(json.dumps({"path": "bad.pdf", "sha256": "0" * 64}) + "\n")
    store = ManifestStore(tmp_path / "manifest.json")
    report = import_corpus(jsonl, corpus, tmp_path, store)
    assert report.problems
    assert store.load().documents == []
