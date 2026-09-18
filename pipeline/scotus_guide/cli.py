"""Operator CLI for archival, extraction, generation, and validation."""

from __future__ import annotations

import argparse
import json
import os
import shlex
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
from .evidence import EvidenceGenerator
from .extraction import CommandOcrEngine, PdfTextExtractor
from .generation import GuideGenerator
from .importer import import_corpus
from .integrity import check_archive
from .models import CANONICAL_MODELS, SCHEMA_VERSION
from .normalization import reconcile_normalized_cases
from .ollama import DEFAULT_MODEL, OllamaClient
from .orchestrator import GenerationOrchestrator, OperationMode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scotus-guide")
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

    ingest = subcommands.add_parser("ingest", help="run configured incremental source ingestion")
    _operation_arguments(ingest)
    ingest.add_argument("--sources", type=Path, default=Path("config/sources.json"))

    extract = subcommands.add_parser("extract", help="extract cited evidence for selected cases")
    _operation_arguments(extract)

    generate = subcommands.add_parser("generate", help="synthesize and verify selected guides")
    _operation_arguments(generate)

    validate = subcommands.add_parser("validate", help="validate checked-in archive and guides")
    validate.add_argument("--root", type=Path, default=Path("."))

    run = subcommands.add_parser("run", help="run generation orchestration end-to-end")
    _operation_arguments(run, aliases=True)

    health = subcommands.add_parser("health", help="check Ollama and required model")
    health.add_argument("--root", type=Path, default=Path("."))
    return parser


def _operation_arguments(parser: argparse.ArgumentParser, *, aliases: bool = False) -> None:
    help_text = (
        "incremental|backfill|case|validate"
        if aliases
        else "incremental|bounded-backfill|case-regeneration|validation-only"
    )
    parser.add_argument("--mode", required=True, help=help_text)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--case-id")
    parser.add_argument("--root", type=Path, default=Path("."))


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
    if args.command == "ingest":
        return _ingest(args)
    if args.command == "validate":
        return _validate(args.root.resolve())
    if args.command == "health":
        with _ollama() as ollama:
            identity = ollama.health()
        print(json.dumps({"model": identity.name, "digest": identity.digest}))
        return 0
    if args.command in {"extract", "generate", "run"}:
        try:
            mode = OperationMode.parse(args.mode)
            _validate_operation(mode, args.batch_size, args.case_id)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
        root = args.root.resolve()
        if mode == OperationMode.VALIDATE:
            return _validate(root)
        orchestrator = GenerationOrchestrator(root)
        had_failures = False
        with _ollama() as ollama:
            identity = ollama.health()
            if args.command in {"extract", "run"}:
                summary = orchestrator.extract(
                    mode,
                    EvidenceGenerator(
                        root,
                        _extractor(),
                        ollama,
                        context_tokens=ollama.context_length,
                        reserved_tokens=min(8_192, ollama.context_length // 4),
                        model_digest=identity.digest,
                    ),
                    batch_size=args.batch_size,
                    case_id=args.case_id,
                )
                had_failures = had_failures or bool(summary.failures)
                print(summary.model_dump_json())
            if args.command in {"generate", "run"} and not had_failures:
                summary = orchestrator.generate(
                    mode,
                    GuideGenerator(root, ollama, identity),
                    batch_size=args.batch_size,
                    case_id=args.case_id,
                )
                had_failures = had_failures or bool(summary.failures)
                print(summary.model_dump_json())
        validation_status = _validate(root) if args.command == "run" else 0
        return 1 if had_failures or validation_status else 0
    return 2


def _ingest(args: argparse.Namespace) -> int:
    """Workflow-compatible configured ingestion; backfill uses already imported sources."""
    root = args.root.resolve()
    mode = OperationMode.parse(args.mode)
    _validate_operation(mode, args.batch_size, args.case_id)
    if mode not in {OperationMode.INCREMENTAL, OperationMode.BACKFILL}:
        raise ValueError("ingest supports only incremental and bounded-backfill modes")
    sources_path = args.sources if args.sources.is_absolute() else root / args.sources
    if mode == OperationMode.BACKFILL:
        _write_json(
            root / "reports" / "ingestion.json",
            {"mode": mode.value, "changed_case_ids": [], "message": "using imported archive"},
        )
        return 0
    if not sources_path.exists():
        print(f"incremental source configuration is missing: {sources_path}", file=sys.stderr)
        return 2
    source_data = _load_json(sources_path)
    sources = [SourcePage.model_validate(item) for item in source_data]
    with _client() as client:
        discovery = ScotusDiscoveryAdapter(client).discover(sources)
        reconcile_discovery_state(root / "manifests" / "discovered-cases.json", discovery)
        reconciled = Reconciler(
            ContentAddressedArchive(root),
            ManifestStore(root / "manifests" / "documents.json"),
            PdfDownloader(client),
            root / ".tmp" / "downloads",
        ).reconcile(discovery.documents, allow_empty=not discovery.documents)
    normalized_changed = reconcile_normalized_cases(
        root,
        discovery.cases,
        ManifestStore(root / "manifests" / "documents.json").load(),
    )
    changed = sorted(discovery.changed_case_ids | reconciled.changed_case_ids | normalized_changed)
    payload = {
        "mode": mode.value,
        "changed_case_ids": changed,
        "discovery": discovery.as_dict(),
        "reconcile": reconciled.as_dict(),
    }
    _write_json(root / "reports" / "ingestion.json", payload)
    # Per-source/candidate failures are reportable partial outcomes. Preserve any
    # successful archive/case updates before later stages; fail only when nothing
    # was accepted from a failed run.
    had_failures = bool(discovery.failures or reconciled.failures)
    accepted_any = bool(reconciled.accepted_hashes or normalized_changed)
    return 1 if had_failures and not accepted_any else 0


def _validate(root: Path) -> int:
    orchestrator = GenerationOrchestrator(root)
    errors = orchestrator.validate()
    integrity = check_archive(root, ManifestStore(root / "manifests" / "documents.json"))
    errors.extend(f"archive {item.kind}: {item.path}: {item.detail}" for item in integrity.issues)
    _write_json(
        root / "reports" / "repository-validation.json", {"valid": not errors, "errors": errors}
    )
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


def _validate_operation(mode: OperationMode, batch_size: int, case_id: str | None) -> None:
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    if mode == OperationMode.CASE and not case_id:
        raise ValueError("case_id is required for case-regeneration")


def _extractor() -> PdfTextExtractor:
    command = os.environ.get("SCOTUS_OCR_COMMAND")
    ocr = CommandOcrEngine(shlex.split(command)) if command else None
    return PdfTextExtractor(ocr=ocr)


def _ollama() -> OllamaClient:
    return OllamaClient(
        os.environ.get("OLLAMA_BASE_URL", "http://192.168.1.41:11434"),
        model=os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL),
        context_length=int(os.environ.get("OLLAMA_CONTEXT_LENGTH", "32768")),
        timeout=float(os.environ.get("OLLAMA_TIMEOUT", "300")),
        attempts=int(os.environ.get("OLLAMA_ATTEMPTS", "3")),
    )


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
