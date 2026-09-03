#!/usr/bin/env python3
"""Fail closed on common private-data and public repository policy mistakes."""

from __future__ import annotations

import re
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

REQUIRED_FILES = {
    "LICENSE",
    "LICENSE.generated-content",
    "NOTICE",
    "CONTRIBUTING.md",
    "SECURITY.md",
    ".dockerignore",
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
    ".github/workflows/publish-pages.yml",
    "docs/generated-content-and-source-rights.md",
    "docs/pages-operations.md",
    "docs/public-repository-review.md",
    "docs/repository-governance.md",
    "tests/fixtures/README.md",
}
PRIVATE_SUFFIXES = {
    ".aac",
    ".backup",
    ".db",
    ".dump",
    ".flac",
    ".m4a",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".p12",
    ".pdf",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".wav",
    ".webm",
}
SECRET_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "OpenAI-style key": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}
ACTION = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)
PINNED_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")
# This module intentionally defines the literal signatures rejected in public output.
SECRET_SIGNATURE_DEFINITION_FILES = {
    "src/ragchew/scotus/static_contracts.py",
    "src/ragchew/scotus/static_validation.py",
}


def repository_files() -> tuple[Path, ...]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"], text=True
    )
    # A local migration may have unstaged tracked deletions before the replacement
    # dormant path is added; scan the actual candidate working tree.
    return tuple(Path(line) for line in output.splitlines() if line and Path(line).is_file())


def check() -> list[str]:
    failures: list[str] = []
    files = repository_files()
    names = {path.as_posix() for path in files}
    for required in sorted(REQUIRED_FILES - names):
        failures.append(f"missing required public repository file: {required}")

    for path in files:
        if path.suffix.casefold() in PRIVATE_SUFFIXES:
            failures.append(f"prohibited private document/media class is tracked: {path}")
            continue
        try:
            data = path.read_bytes()
        except OSError as error:
            failures.append(f"cannot read {path}: {error}")
            continue
        if b"\x00" in data:
            continue
        if path.as_posix() not in SECRET_SIGNATURE_DEFINITION_FILES:
            for label, pattern in SECRET_PATTERNS.items():
                if pattern.search(data):
                    failures.append(f"possible {label} in {path}")
        if path.match(".github/workflows/*.yml"):
            text = data.decode("utf-8")
            for reference in ACTION.findall(text):
                if not PINNED_ACTION.fullmatch(reference):
                    failures.append(f"workflow action is not immutable in {path}: {reference}")

    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    for required_pattern in (
        ".env.*",
        "**/*.key",
        "**/*.pdf",
        "**/*.mp3",
        "source-data",
        "extracted-text",
        "model-dumps",
        "private-workspace",
        "candidate-state",
        "reports",
        "tests",
    ):
        if required_pattern not in dockerignore:
            failures.append(f".dockerignore lacks required exclusion: {required_pattern}")

    for path in (Path("Containerfile"), Path("compose.dev.yaml")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?:FROM|image:)\s+[^\s]+(?::latest|:master)(?:\s|$)", text):
            failures.append(f"mutable image reference in {path}")

    config = yaml.safe_load(Path("config/scotus.yaml").read_text(encoding="utf-8"))
    approvals = config.get("approvals", {})
    gate_names = (
        "source_review_approved",
        "licenses_approved",
        "origin_approved",
        "model_runtime_approved",
        "launch_approved",
    )
    gate_values = [approvals.get(gate) for gate in gate_names]
    if any(value not in {True, False} for value in gate_values):
        failures.append("launch approvals must be explicit booleans")
    publication = config.get("publication", {})
    generation = config.get("generation", {})
    if generation.get("brief_generation_enabled") and (
        not config.get("enabled") or not publication.get("enabled")
    ):
        failures.append("brief generation requires processing and publication switches")
    if publication.get("dry_run") is False and (
        not all(gate_values)
        or not config.get("enabled")
        or not generation.get("brief_generation_enabled")
        or not publication.get("enabled")
    ):
        failures.append("production case publication requires every switch and owner approval")
    static = config.get("static", {})
    if static.get("canonical_origin") != "https://scotusbriefs.us":
        failures.append("SCOTUS canonical origin must be https://scotusbriefs.us")
    if static.get("project_base_path") != "/" or static.get("section_path") != "/scotus/":
        failures.append("SCOTUS custom-domain paths must be root project and /scotus/ section")
    if generation.get("provider") != "ollama" or generation.get("model") != "qwen3.8:27b":
        failures.append("SCOTUS generation must use reviewed Ollama model qwen3.8:27b")
    model_budget = config.get("model_budget", {})
    zero_cost_fields = (
        "input_cost_usd_per_million_tokens",
        "output_cost_usd_per_million_tokens",
        "maximum_estimated_cost_usd_per_run",
    )
    try:
        costs_are_zero = all(
            Decimal(str(model_budget.get(field))) == 0 for field in zero_cost_fields
        )
    except InvalidOperation:
        costs_are_zero = False
    if not costs_are_zero:
        failures.append("local Ollama cost rates and maximum must remain zero")

    workflow = Path(".github/workflows/publish-pages.yml").read_text(encoding="utf-8")
    build = workflow[
        workflow.index("\n  build:\n") : workflow.index("\n  persist-cost-receipts:\n")
    ]
    if (
        "  CANONICAL_ORIGIN: https://scotusbriefs.us\n" not in workflow
        or "  PROJECT_BASE_PATH: /\n" not in workflow
        or '${CANONICAL_ORIGIN}${PROJECT_BASE_PATH}release/v1/release.json' not in workflow
    ):
        failures.append("Pages workflow must publish and reconcile the scotusbriefs.us root")
    if "runs-on: [self-hosted]" not in build:
        failures.append("Pages build must run only on the self-hosted runner")
    if "OPENAI_API_KEY" in workflow or "secrets." in build:
        failures.append("self-hosted Pages build must not receive model secrets")
    if "http://127.0.0.1:11434" not in build or "qwen3.8:27b" not in build:
        failures.append("Pages build must preflight exact local Ollama model qwen3.8:27b")
    if "pull_request:" in workflow or "github.event_name != 'pull_request'" not in build:
        failures.append("Pages build must never run for pull requests")
    if "services:" in workflow:
        failures.append("Pages publication must not start Docker services")
    if not all(
        name in build
        for name in (
            "Clean persistent runner before build",
            "Clean persistent runner after build",
        )
    ):
        failures.append("self-hosted Pages build requires pre/post persistent-runner cleanup")
    hosted_jobs = workflow[workflow.index("\n  persist-cost-receipts:\n") :]
    if "runs-on: [self-hosted]" in hosted_jobs or hosted_jobs.count(
        "runs-on: ubuntu-24.04"
    ) != 4:
        failures.append("receipt, deploy, and promotion jobs must remain Ubuntu-hosted")
    return failures


def main() -> None:
    failures = check()
    if failures:
        raise SystemExit("public repository policy failed:\n- " + "\n- ".join(failures))
    print("public repository and Docker context policy: ok")


if __name__ == "__main__":
    main()
