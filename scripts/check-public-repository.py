#!/usr/bin/env python3
"""Fail closed on common private-data and public repository policy mistakes."""

from __future__ import annotations

import re
import subprocess
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
    for gate in (
        "source_review_approved",
        "licenses_approved",
        "origin_approved",
        "publication_secret_configured",
        "launch_approved",
    ):
        if approvals.get(gate) is not False:
            failures.append(f"launch approval must remain fail-closed: approvals.{gate}")
    if config.get("publication", {}).get("enabled") is not False:
        failures.append("static publication must remain disabled before owner launch")
    if config.get("generation", {}).get("brief_generation_enabled") is not False:
        failures.append("paid brief generation must remain disabled before owner launch")
    return failures


def main() -> None:
    failures = check()
    if failures:
        raise SystemExit("public repository policy failed:\n- " + "\n- ".join(failures))
    print("public repository and Docker context policy: ok")


if __name__ == "__main__":
    main()
