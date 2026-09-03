#!/usr/bin/env python3
"""Export a one-time sanitized bootstrap from legacy public PostgreSQL projections."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from ragchew.scotus.legacy_export import PostgresLegacyProjectionReader, export_legacy_bootstrap


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("build epoch must include a UTC offset")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Operator-only migration: read retained public projections, remove legacy source-link "
            "claim IDs, and write a new sanitized generated-content candidate."
        )
    )
    parser.add_argument("--dsn", default=os.getenv("RAGCHEW_DATABASE_DSN"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--build-epoch", required=True, type=_timestamp)
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn or RAGCHEW_DATABASE_DSN is required")
    if args.output.exists():
        parser.error("--output must not already exist")

    reader = PostgresLegacyProjectionReader(args.dsn)
    try:
        path = export_legacy_bootstrap(
            reader.projections(),
            args.output,
            source_commit=args.source_commit,
            config_sha256=args.config_sha256,
            build_epoch=args.build_epoch,
        )
    finally:
        reader.close()
    print(f"sanitized_bootstrap={path}")


if __name__ == "__main__":
    main()
