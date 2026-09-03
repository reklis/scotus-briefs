#!/usr/bin/env python3
"""Recover sanitized accepted POC briefs onto an existing generated-content parent."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from ragchew.config import ScotusConfig
from ragchew.scotus.poc_export import PostgresPocBriefReader, export_poc_generated_content
from ragchew.scotus.static_state import StaticStateStore, generated_content_digest
from ragchew.scotus.static_urls import StaticUrlPolicy


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("build epoch must include a UTC offset")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Operator-only POC recovery: reconstruct accepted public brief revisions with "
            "allowlisted official provenance and merge them onto generated-content state."
        )
    )
    parser.add_argument("--dsn", default=os.getenv("RAGCHEW_DATABASE_DSN"))
    parser.add_argument("--parent-state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="generated state candidate")
    parser.add_argument("--site-output", required=True, type=Path, help="rendered static candidate")
    parser.add_argument("--config", default=Path("config/scotus.yaml"), type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--build-epoch", required=True, type=_timestamp)
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn or RAGCHEW_DATABASE_DSN is required")
    if args.output.exists() or args.site_output.exists():
        parser.error("--output and --site-output must not already exist")
    parent = StaticStateStore(args.parent_state).load()
    static = ScotusConfig.from_yaml(args.config).static
    urls = StaticUrlPolicy(
        static.canonical_origin,
        static.project_base_path,
        static.section_path,
    )
    reader = PostgresPocBriefReader(args.dsn)
    try:
        path = export_poc_generated_content(
            parent,
            reader.case_revisions(),
            args.output,
            site_destination=args.site_output,
            urls=urls,
            source_commit=args.source_commit,
            config_sha256=args.config_sha256,
            build_epoch=args.build_epoch,
        )
    finally:
        reader.close()
    candidate = StaticStateStore(path).load()
    print(f"sanitized_poc_candidate={path}")
    print(f"sanitized_poc_site={args.site_output}")
    print(f"expected_parent_digest={generated_content_digest(parent)}")
    print(f"release_id={candidate.publication.active_release_id}")


if __name__ == "__main__":
    main()
