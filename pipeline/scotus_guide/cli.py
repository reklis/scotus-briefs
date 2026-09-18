"""Operator CLI for schemas, official discovery, archival, import, and integrity."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from .archive import (
    ContentAddressedArchive,
    DocumentCandidate,
    ManifestStore,
    PdfDownloader,
    Reconciler,
)
from .discovery import ScotusDiscoveryAdapter, SourcePage, reconcile_discovery_state
from .importer import import_corpus
from .integrity import check_archive
from .models import CANONICAL_MODELS, SCHEMA_VERSION


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scotus-pipeline")
    subcommands = parser.add_subparsers(dest="command", required=True)
    schemas = subcommands.add_parser("schemas", help="write canonical JSON Schemas")
    schemas.add_argument("--output", type=Path, default=Path("schemas"))

    discover = subcommands.add_parser("discover", help="discover official source metadata")
    discover.add_argument(
        "--sources", type=Path, required=True, help="JSON array of SourcePage objects"
    )
    discover.add_argument("--output", type=Path, default=Path("reports/discovery.json"))
    discover.add_argument("--state", type=Path, default=Path("manifests/discovered-cases.json"))

    reconcile = subcommands.add_parser("reconcile", help="download and archive candidate PDFs")
    reconcile.add_argument("--candidates", type=Path, required=True)
    reconcile.add_argument("--root", type=Path, default=Path("."))
    reconcile.add_argument("--manifest", type=Path, default=Path("manifests/documents.json"))
    reconcile.add_argument("--report", type=Path, default=Path("reports/reconcile.json"))
    reconcile.add_argument("--allow-empty", action="store_true")

    importer = subcommands.add_parser("import-corpus", help="verify and import historical JSONL")
    importer.add_argument("--jsonl", type=Path, required=True)
    importer.add_argument("--corpus-root", type=Path, required=True)
    importer.add_argument("--root", type=Path, default=Path("."))
    importer.add_argument("--manifest", type=Path, default=Path("manifests/documents.json"))
    importer.add_argument("--report", type=Path, default=Path("reports/import.json"))

    integrity = subcommands.add_parser("integrity", help="verify archive hashes and references")
    integrity.add_argument("--root", type=Path, default=Path("."))
    integrity.add_argument("--manifest", type=Path, default=Path("manifests/documents.json"))
    integrity.add_argument("--report", type=Path, default=Path("reports/integrity.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "schemas":
        args.output.mkdir(parents=True, exist_ok=True)
        for name, model in CANONICAL_MODELS.items():
            schema = _schema_with_invariants(name, model.model_json_schema(mode="serialization"))
            schema["$id"] = f"https://scotusbriefs.us/schemas/{name}/{SCHEMA_VERSION}"
            (args.output / f"{name}.schema.json").write_text(
                json.dumps(schema, indent=2, sort_keys=True) + "\n"
            )
        return 0
    if args.command == "discover":
        source_data = _load_json(args.sources)
        sources = [SourcePage.model_validate(item) for item in source_data]
        with _client() as client:
            discovery_result = ScotusDiscoveryAdapter(client).discover(sources)
        reconcile_discovery_state(args.state, discovery_result)
        _write_json(args.output, discovery_result.as_dict())
        return 1 if discovery_result.failures else 0
    if args.command == "reconcile":
        raw = _load_json(args.candidates)
        if isinstance(raw, dict):
            raw = raw.get("documents", [])
        candidates = [DocumentCandidate.model_validate(item) for item in raw]
        root = args.root.resolve()
        with _client() as client:
            reconciler = Reconciler(
                ContentAddressedArchive(root),
                ManifestStore(root / args.manifest),
                PdfDownloader(client),
                root / ".tmp" / "downloads",
            )
            reconcile_result = reconciler.reconcile(candidates, allow_empty=args.allow_empty)
        _write_json(root / args.report, reconcile_result.as_dict())
        return 1 if reconcile_result.failures else 0
    if args.command == "import-corpus":
        root = args.root.resolve()
        import_result = import_corpus(
            args.jsonl,
            args.corpus_root,
            root,
            ManifestStore(root / args.manifest),
        )
        _write_json(root / args.report, import_result.as_dict())
        return 1 if import_result.problems else 0
    if args.command == "integrity":
        root = args.root.resolve()
        integrity_result = check_archive(root, ManifestStore(root / args.manifest))
        _write_json(root / args.report, integrity_result.as_dict())
        return 0 if integrity_result.valid else 1
    return 2


def _schema_with_invariants(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Add cross-field rules Pydantic cannot emit from model validators."""
    definitions = schema.get("$defs", {})
    source = definitions.get("SourceIdentity")
    if source:
        source["anyOf"] = [
            {"required": ["url"], "properties": {"url": {"type": "string"}}},
            {
                "required": ["import_path"],
                "properties": {"import_path": {"type": "string", "minLength": 1}},
            },
        ]
    if name == "document-manifest":
        schema["properties"]["documents"]["uniqueItems"] = True
    elif name == "normalized-case":
        schema["properties"]["docket_numbers"]["uniqueItems"] = True
        schema["anyOf"] = [
            {"properties": {"docket_numbers": {"minItems": 1}}},
            {
                "required": ["unresolved_group"],
                "properties": {"unresolved_group": {"type": "string", "minLength": 1}},
            },
        ]
        schema["allOf"] = [
            {
                "if": {
                    "properties": {"lifecycle": {"const": "decided"}},
                    "required": ["lifecycle"],
                },
                "then": {
                    "properties": {"dates": {"required": ["decision"]}},
                    "required": ["dates"],
                },
            }
        ]
    elif name == "citizen-guide":
        section = definitions.get("GuideSection")
        if section:
            section["allOf"] = [
                {
                    "if": {
                        "required": ["summary"],
                        "properties": {"summary": {"type": "string", "minLength": 1}},
                    },
                    "then": {
                        "properties": {"summary_citations": {"minItems": 1}},
                        "required": ["summary_citations"],
                    },
                },
                {
                    "if": {
                        "properties": {"status": {"enum": ["pending", "not_applicable"]}},
                        "required": ["status"],
                    },
                    "then": {
                        "properties": {
                            "summary": {"type": "null"},
                            "claims": {"maxItems": 0},
                        }
                    },
                },
            ]
        schema["allOf"] = [
            {
                "if": {
                    "properties": {"lifecycle": {"not": {"const": "decided"}}},
                    "required": ["lifecycle"],
                },
                "then": {
                    "properties": {
                        "decision": {"properties": {"status": {"not": {"const": "complete"}}}}
                    }
                },
            }
        ]
    return schema


def _client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
    )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(path)


if __name__ == "__main__":
    sys.exit(main())
