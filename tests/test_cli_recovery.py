from __future__ import annotations

import json
from pathlib import Path

from scotus_guide.archive import ManifestStore
from scotus_guide.cli import main
from scotus_guide.models import DocumentManifest


def test_recover_plan_and_apply_use_canonical_paths_without_ollama(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path = tmp_path / "manifests" / "documents.json"
    ManifestStore(manifest_path).save(DocumentManifest())
    original_manifest = manifest_path.read_bytes()

    def unexpected_ollama() -> None:
        raise AssertionError("historical recovery must not contact Ollama")

    monkeypatch.setattr("scotus_guide.cli._ollama", unexpected_ollama)

    assert main(["recover", "--plan", "--root", str(tmp_path)]) == 0
    plan_path = tmp_path / "reports" / "recovery" / "plan.json"
    report_path = tmp_path / "reports" / "recovery" / "report.json"
    assert plan_path.is_file()
    assert report_path.is_file()
    assert manifest_path.read_bytes() == original_manifest
    assert not (tmp_path / "data" / "cases").exists()

    assert main(["recover", "--apply", "--root", str(tmp_path)]) == 0
    assert json.loads(report_path.read_text())["no_op"] is True
    assert manifest_path.read_bytes() == original_manifest


def test_recover_apply_fails_cleanly_when_plan_is_missing(tmp_path: Path, capsys) -> None:
    assert main(["recover", "--apply", "--root", str(tmp_path)]) == 2
    assert "cannot load historical recovery plan" in capsys.readouterr().err
