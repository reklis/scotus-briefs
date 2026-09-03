#!/usr/bin/env python3
"""Create a deterministic, sanitized empty generated-content branch candidate."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from ragchew.scotus.legacy_export import export_legacy_bootstrap
from ragchew.scotus.public_contracts import ScotusPublicProjection


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("build epoch must include a UTC offset")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--build-epoch", required=True, type=_timestamp)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output must not already exist")
    projection = ScotusPublicProjection(
        watermark=args.build_epoch,
        generated_at=args.build_epoch,
        cases=(),
    )
    path = export_legacy_bootstrap(
        (projection,),
        args.output,
        source_commit=args.source_commit,
        config_sha256=args.config_sha256,
        build_epoch=args.build_epoch,
        tool_version="empty-bootstrap-v1",
    )
    print(f"sanitized_empty_bootstrap={path}")


if __name__ == "__main__":
    main()
