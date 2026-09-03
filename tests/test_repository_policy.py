from __future__ import annotations

import re
from pathlib import Path

import yaml


def test_required_public_repository_policy_files_exist() -> None:
    for path in (
        "LICENSE",
        "LICENSE.generated-content",
        "NOTICE",
        "CONTRIBUTING.md",
        "SECURITY.md",
        ".dockerignore",
        ".github/CODEOWNERS",
        ".github/dependabot.yml",
        "docs/generated-content-and-source-rights.md",
        "docs/pages-operations.md",
        "docs/repository-governance.md",
        "tests/fixtures/README.md",
    ):
        assert Path(path).is_file(), path


def test_all_workflow_actions_are_immutable() -> None:
    pattern = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)
    for path in Path(".github/workflows").glob("*.yml"):
        references = pattern.findall(path.read_text(encoding="utf-8"))
        assert references
        assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", item) for item in references)


def test_pages_workflow_has_least_privilege_job_boundaries() -> None:
    text = Path(".github/workflows/publish-pages.yml").read_text(encoding="utf-8")
    assert 'cron: "17 3 * * *"' in text
    assert "pull_request:" not in text
    assert "cancel-in-progress: false" in text
    assert "persist-credentials: false" in text
    assert "uv sync --frozen" in text
    assert "retention-days: 1" in text
    assert "ragchew-scotus-static" in text
    assert "if: always()" in text
    assert "--expected-parent-commit" in text
    assert "--checkpoint-only" in text
    assert "--fail-on-split" in text
    assert "name: github-pages" in text
    assert "environment: scotus-publication" in text
    assert "RAGCHEW_SCOTUS_BATCH_ADAPTER: ragchew.scotus.live_static:LiveStaticBatchAdapter" in text
    assert "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}" in text
    assert "before live source access" in text
    assert "fail-closed until every repository gate is enabled" in text

    build = text[text.index("\n  build:\n") : text.index("\n  persist-cost-receipts:\n")]
    assert "secrets.OPENAI_API_KEY" in build

    deploy = text[text.index("\n  deploy:\n") : text.index("\n  promote:\n")]
    assert "pages: write" in deploy and "id-token: write" in deploy
    assert "contents: read" not in deploy and "contents: write" not in deploy
    assert "secrets." not in deploy
    assert "RAGCHEW_SCOTUS_BATCH_ADAPTER" not in deploy
    promote = text[
        text.index("\n  promote:\n") : text.index("\n  checkpoint-only:\n")
    ]
    assert "contents: write" in promote
    assert "pages: write" not in promote and "id-token: write" not in promote
    assert "secrets." not in promote
    assert "RAGCHEW_SCOTUS_BATCH_ADAPTER" not in promote


def test_launch_gates_remain_fail_closed() -> None:
    config = yaml.safe_load(Path("config/scotus.yaml").read_text())
    assert config["publication"]["enabled"] is False
    assert config["generation"]["brief_generation_enabled"] is False
    assert set(config["approvals"].values()) == {False}
