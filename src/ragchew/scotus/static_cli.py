"""Command line entry point for static SCOTUS publication tooling."""

from __future__ import annotations

import argparse
import base64
import importlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import replace
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import unquote, urlsplit

import httpx
from pydantic import ValidationError

from ragchew.config import ScotusConfig
from ragchew.scotus.discovery import DiscoveryMode
from ragchew.scotus.public_contracts import ScotusPublicProjection
from ragchew.scotus.static_contracts import (
    CostReceiptBundle,
    ReleaseManifest,
    canonical_json_bytes,
    contract_digest,
    sha256_hex,
)
from ragchew.scotus.static_export import StaticExportResult, StaticSiteExporter
from ragchew.scotus.static_pipeline import (
    FailClosedProductionBatchAdapter,
    ProductionBatchAdapter,
    ProductionBatchUnavailable,
    StaticBatchResult,
)
from ragchew.scotus.static_state import (
    CompareAndSwapConflict,
    GeneratedContent,
    ReconciliationChoice,
    StaticStateStore,
    generated_content_digest,
    reconcile_release_ids,
)
from ragchew.scotus.static_urls import StaticUrlPolicy
from ragchew.scotus.static_validation import (
    StaticValidationError,
    scan_public_files,
    validate_static_candidate,
)

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _projection(path: Path) -> ScotusPublicProjection:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "projection" in payload:
        payload = payload["projection"]
    if not isinstance(payload, dict):
        raise ValueError("projection input must be a JSON object")
    return ScotusPublicProjection.model_validate(payload)


def _epoch(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("build epoch must include a UTC offset")
    return parsed


def _config(
    path: Path,
    *,
    canonical_origin: str | None = None,
    project_base_path: str | None = None,
) -> tuple[ScotusConfig, StaticUrlPolicy, str]:
    config = ScotusConfig.from_yaml(path)
    static_values = config.static.model_dump(mode="python")
    if canonical_origin is not None:
        static_values["canonical_origin"] = canonical_origin
    if project_base_path is not None:
        static_values["project_base_path"] = project_base_path
    static = type(config.static).model_validate(static_values)
    if static != config.static:
        config = config.model_copy(update={"static": static})
    urls = StaticUrlPolicy(
        static.canonical_origin,
        static.project_base_path,
        static.section_path,
    )
    digest = sha256_hex(canonical_json_bytes(static, privacy_check=False))
    return config, urls, digest


def _legacy_slugs(state_path: Path | None) -> dict[str, tuple[str, ...]]:
    if state_path is None:
        return {}
    content = StaticStateStore(state_path).load()
    return {pointer.case_key: pointer.legacy_slugs for pointer in content.publication.cases}


def _write_outputs(path: Path | None, values: dict[str, str | bool | None]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            if value is None:
                rendered = ""
            elif isinstance(value, bool):
                rendered = str(value).lower()
            else:
                rendered = value
            if "\n" in rendered or "\r" in rendered:
                raise ValueError("workflow output values cannot contain newlines")
            handle.write(f"{key}={rendered}\n")


class _ProjectPathHandler(SimpleHTTPRequestHandler):
    project_base_path = "/"

    def translate_path(self, path: str) -> str:
        parsed = unquote(urlsplit(path).path)
        if not parsed.startswith(self.project_base_path):
            return str(Path(self.directory or ".") / "__not_found__")
        relative = parsed[len(self.project_base_path) :].lstrip("/")
        parts = PurePosixPath(relative).parts
        if any(part in {".", ".."} for part in parts):
            return str(Path(self.directory or ".") / "__not_found__")
        root = Path(self.directory or ".").resolve()
        target = root.joinpath(*parts).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return str(root / "__not_found__")
        return str(target)


def _export(args: argparse.Namespace) -> int:
    config, urls, config_digest = _config(args.config)
    if args.live and not _live_publication_ready(config):
        raise ValueError("live export requires every generation, publication, and approval gate")
    if args.state is not None:
        active = StaticStateStore(args.state).load().publication.active_release_id
        if active != _optional_id(args.previous_release_id):
            raise ValueError("previous release ID does not match active generated state")
    result = StaticSiteExporter(urls).export(
        _projection(args.projection),
        args.output,
        source_commit=args.source_commit,
        build_epoch=_epoch(args.build_epoch),
        config_sha256=config_digest,
        previous_release_id=_optional_id(args.previous_release_id),
        legacy_slugs=_legacy_slugs(args.state),
    )
    print(f"exported {result.manifest.release_id} to {result.output}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    _, urls, _ = _config(args.config)
    manifest = validate_static_candidate(args.output, urls, state_root=args.state)
    # Whole-tree scanning is mandatory. The flag exists so workflow policy can make
    # that contract explicit without creating a weaker validation mode.
    if args.privacy_scan:
        scan_public_files(path for path in args.output.rglob("*") if path.is_file())
    print(f"validated {manifest.release_id} ({manifest.page_count} pages)")
    return 0


def _build_fixture(args: argparse.Namespace) -> StaticExportResult:
    _, urls, config_digest = _config(args.config)
    state_path = getattr(args, "state", None)
    previous = None
    if state_path is not None:
        previous = StaticStateStore(state_path).load().publication.active_release_id
    result = StaticSiteExporter(urls).export(
        _projection(args.fixture),
        args.output,
        source_commit=args.source_commit,
        build_epoch=_epoch(args.build_epoch),
        config_sha256=config_digest,
        previous_release_id=previous,
        legacy_slugs=_legacy_slugs(state_path),
    )
    validate_static_candidate(args.output, urls)
    _write_outputs(
        getattr(args, "github_output", None),
        {
            "release_changed": result.manifest.release_id != previous,
            "publication_ready": False,
            "release_id": result.manifest.release_id,
            "expected_parent_release_id": previous,
        },
    )
    return result


def _fixture_preview(args: argparse.Namespace) -> int:
    result = _build_fixture(args)
    print(f"built and validated fixture {result.manifest.release_id} at {result.output}")
    return 0


def _preview(args: argparse.Namespace) -> int:
    _, urls, config_digest = _config(args.config)
    temporary: Path | None = None
    server: ThreadingHTTPServer | None = None
    try:
        directory = args.directory
        if directory is None:
            temporary = Path(tempfile.mkdtemp(prefix="ragchew-static-preview-"))
            directory = temporary / "site"
            StaticSiteExporter(urls).export(
                _projection(args.fixture),
                directory,
                source_commit=args.source_commit,
                build_epoch=_epoch(args.build_epoch),
                config_sha256=config_digest,
            )
        else:
            validate_static_candidate(directory, urls)
        handler_type = type(
            "ProjectPathHandler",
            (_ProjectPathHandler,),
            {"project_base_path": urls.project_base_path},
        )
        handler = partial(handler_type, directory=str(directory))
        server = ThreadingHTTPServer((args.host, args.port), handler)
        print(f"previewing {directory} at http://{args.host}:{args.port}{urls.project_base_path}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.server_close()
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
    return 0


def _live_release_id(args: argparse.Namespace, urls: StaticUrlPolicy) -> str | None:
    if args.live_release_id is not None:
        return _optional_id(args.live_release_id)
    expected_url = urls.canonical(urls.internal("release/v1/release.json")).rstrip("/")
    if args.live_release_url != expected_url:
        raise ValueError("live release URL does not match the configured canonical release marker")
    try:
        with httpx.Client(follow_redirects=False, timeout=10.0, trust_env=False) as client:
            response = client.get(args.live_release_url, headers={"Accept": "application/json"})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise ValueError("live release marker exceeds the public contract size limit")
        manifest = ReleaseManifest.model_validate_json(response.content)
        if response.content != canonical_json_bytes(manifest):
            raise ValueError("live release marker is not canonical")
        return manifest.release_id
    except (httpx.HTTPError, ValidationError, json.JSONDecodeError):
        raise ValueError("live release marker could not be validated") from None


def _is_initial_empty_bootstrap(content: GeneratedContent) -> bool:
    release = content.release
    projection = content.projection
    return bool(
        release is not None
        and projection is not None
        and release.tool_version == "empty-bootstrap-v1"
        and release.previous_release_id is None
        and release.case_count == 0
        and not release.files
        and not projection.cases
        and not content.revisions
        and content.publication.active_release_id == release.release_id
    )


def _reconcile(args: argparse.Namespace) -> int:
    _, urls, _ = _config(args.config)
    content = StaticStateStore(args.state).load()
    branch = content.publication.active_release_id
    try:
        live = _live_release_id(args, urls)
    except ValueError:
        if not args.allow_missing_live_bootstrap or not _is_initial_empty_bootstrap(content):
            raise
        live = None
    if live is None and args.allow_missing_live_bootstrap and _is_initial_empty_bootstrap(content):
        print("initial_empty_bootstrap")
        return 0
    validated: set[str] = set()
    for path in args.validated_state:
        release = StaticStateStore(path).load().release
        if release is not None:
            validated.add(release.release_id)
    choice = reconcile_release_ids(
        live_release_id=live,
        branch_release_id=branch,
        validated_release_ids=validated,
    )
    print(choice.value)
    if args.fail_on_split and choice is not ReconciliationChoice.IN_SYNC:
        raise CompareAndSwapConflict("live Pages and generated-content release IDs differ")
    return 0


def _load_adapter(specification: str | None) -> ProductionBatchAdapter:
    if not specification:
        return FailClosedProductionBatchAdapter()
    if ":" not in specification:
        raise ProductionBatchUnavailable("production batch adapter configuration is invalid")
    module_name, attribute = specification.rsplit(":", 1)
    try:
        target = getattr(importlib.import_module(module_name), attribute)
        candidate = target() if isinstance(target, type) else target
    except Exception:
        raise ProductionBatchUnavailable(
            "production batch adapter could not be loaded; stopped before network/model use"
        ) from None
    if not callable(getattr(candidate, "run", None)):
        raise ProductionBatchUnavailable("production batch adapter does not implement run()")
    return cast(ProductionBatchAdapter, candidate)


def _batch(args: argparse.Namespace) -> int:
    if args.mode == "fixture":
        fixture_args = argparse.Namespace(
            fixture=args.fixture,
            output=args.output,
            config=args.config,
            source_commit=args.source_commit,
            build_epoch=args.build_epoch,
            state=None,
            github_output=args.github_output,
        )
        return _fixture_preview(fixture_args)

    if args.state is None or args.candidate_state is None or args.workspace is None:
        raise ValueError("live batch requires state, candidate-state, and workspace paths")
    # Resolve the reviewed adapter before loading generated state or constructing a
    # workspace. The default schedule therefore exits before Court/OpenAI transport.
    adapter = _load_adapter(os.environ.get("RAGCHEW_SCOTUS_BATCH_ADAPTER"))
    config, urls, config_digest = _config(
        args.config,
        canonical_origin=args.canonical_origin,
        project_base_path=args.project_base_path,
    )
    mode = DiscoveryMode(args.mode)
    state_store = StaticStateStore(args.state)
    original = state_store.load()
    result = adapter.run(
        state_store=state_store,
        config=config,
        mode=mode,
        runner_temp=args.workspace,
        authorized_replay=args.authorized_replay,
    )
    if not isinstance(result, StaticBatchResult):
        raise RuntimeError("production batch adapter returned an invalid result")
    if not result.publishable or result.content.projection is None:
        raise RuntimeError("bounded batch did not produce a publishable public projection")

    export = _export_batch_candidate(
        result,
        original=original,
        output=args.output,
        urls=urls,
        config_digest=config_digest,
        source_commit=args.source_commit,
        build_epoch=_epoch(args.build_epoch),
    )
    release_changed = export.manifest.release_id != original.publication.active_release_id
    candidate_store = StaticStateStore(args.state)
    if release_changed:
        finalized = candidate_store.finalize_candidate(
            args.candidate_state,
            result.content,
            export.manifest,
        )
    else:
        # An identical actual release keeps the prior immutable manifest/pointer while
        # allowing only checkpoint state produced by a safe, completed batch to advance.
        if result.content.release != export.manifest:
            raise RuntimeError("no-change export does not exactly reproduce the active release")
        candidate_store.write_candidate(args.candidate_state, result.content)
        finalized = result.content
    validate_static_candidate(args.output, urls, state_root=args.candidate_state)
    if finalized.release is None:
        raise RuntimeError("finalized generated state has no release")
    publication_ready = _live_publication_ready(config)
    _write_outputs(
        args.github_output,
        {
            "release_changed": release_changed,
            "publication_ready": publication_ready,
            "release_id": export.manifest.release_id,
            "expected_parent_release_id": result.parent_release_id,
        },
    )
    print(f"built validated batch release {export.manifest.release_id}")
    return 0


def _export_batch_candidate(
    result: StaticBatchResult,
    *,
    original: GeneratedContent,
    output: Path,
    urls: StaticUrlPolicy,
    config_digest: str,
    source_commit: str,
    build_epoch: datetime,
) -> StaticExportResult:
    assert result.content.projection is not None
    old_release = original.release
    if result.no_public_change and old_release is not None:
        compare_existing = True
        previous = old_release.previous_release_id
        epoch = old_release.generated_at
        export_source = old_release.source_commit
        export_config = old_release.config_sha256
    else:
        compare_existing = False
        previous = result.parent_release_id
        epoch = build_epoch
        export_source = source_commit
        export_config = config_digest
    exporter = StaticSiteExporter(urls)
    trial = exporter.export(
        result.content.projection,
        output,
        source_commit=export_source,
        build_epoch=epoch,
        config_sha256=export_config,
        previous_release_id=previous,
        legacy_slugs={
            pointer.case_key: pointer.legacy_slugs for pointer in result.content.publication.cases
        },
    )
    if (
        compare_existing
        and trial.manifest.release_id != original.publication.active_release_id
    ):
        shutil.rmtree(output)
        trial = exporter.export(
            result.content.projection,
            output,
            source_commit=source_commit,
            build_epoch=build_epoch,
            config_sha256=config_digest,
            previous_release_id=result.parent_release_id,
            legacy_slugs={
                pointer.case_key: pointer.legacy_slugs
                for pointer in result.content.publication.cases
            },
        )
    return trial


def _receipt_bundle(path: Path) -> CostReceiptBundle:
    try:
        if path.stat().st_size > 1_000_000:
            raise ValueError
        scan_public_files((path,), labels=(path.name,))
        serialized = path.read_bytes()
        bundle = CostReceiptBundle.model_validate_json(serialized)
        if serialized != canonical_json_bytes(bundle):
            raise ValueError
        return bundle
    except (OSError, StaticValidationError, ValidationError, ValueError):
        raise ValueError("cost receipt bundle failed privacy/contract validation") from None


def _persist_cost_receipts(args: argparse.Namespace) -> int:
    bundle = _receipt_bundle(args.receipts)
    if args.validate_only:
        print(f"validated {len(bundle.receipts)} opaque cost receipts")
        return 0
    if args.state is None or args.expected_parent_commit is None:
        raise ValueError("state directory and expected parent commit are required for persistence")
    expected_commit = _require_git_parent(args.state, args.expected_parent_commit)
    store = StaticStateStore(args.state)
    digest = contract_digest(store.load().cost_ledger)
    for receipt in bundle.receipts:
        ledger = store.append_cost_receipt(receipt, expected_digest=digest)
        digest = contract_digest(ledger)
    commit = _commit_and_push(
        args.state,
        expected_commit=expected_commit,
        message="Persist sanitized SCOTUS cost receipts",
    )
    _write_outputs(args.github_output, {"generated_commit": commit})
    print(f"persisted {len(bundle.receipts)} opaque cost receipts")
    return 0


def _promote(args: argparse.Namespace) -> int:
    expected_commit = _require_git_parent(args.state, args.expected_parent_commit)
    store = StaticStateStore(args.state)
    active = store.load()
    candidate = StaticStateStore(args.candidate_state).load()
    if args.checkpoint_only:
        if (
            candidate.release != active.release
            or candidate.projection != active.projection
            or candidate.revisions != active.revisions
            or candidate.publication.cases != active.publication.cases
            or candidate.publication.active_release_id
            != active.publication.active_release_id
        ):
            raise CompareAndSwapConflict("checkpoint-only candidate changes public release data")
        promoted = store.update_publication_state(
            active,
            updated_at=candidate.publication.updated_at,
            sources=candidate.publication.sources,
            documents=candidate.publication.documents,
            pending_work=candidate.publication.pending_work,
            cursors=candidate.publication.cursors,
            processor=candidate.publication.processor,
        )
    else:
        release_id = _optional_id(args.release_id)
        expected_parent = _optional_id(args.expected_parent_release_id)
        if candidate.release is None or candidate.release.release_id != release_id:
            raise CompareAndSwapConflict("candidate release ID differs from deployed release")
        store.require_release_parent(
            candidate,
            expected_parent_release_id=expected_parent,
            expected_parent_digest=generated_content_digest(active),
        )
        # A receipts-only mutation may have landed after the build. Never overwrite it
        # with the older candidate ledger during release promotion.
        promoted = replace(candidate, cost_ledger=active.cost_ledger)
    _install_generated_content(args.state, promoted)
    commit = _commit_and_push(
        args.state,
        expected_commit=expected_commit,
        message=(
            "Advance validated SCOTUS checkpoints"
            if args.checkpoint_only
            else f"Promote SCOTUS release {promoted.publication.active_release_id}"
        ),
    )
    _write_outputs(args.github_output, {"generated_commit": commit})
    print(f"promoted generated-content at {commit}")
    return 0


def _install_generated_content(root: Path, content: GeneratedContent) -> None:
    destination = root.parent / f".{root.name}.install-{os.getpid()}"
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    else:
        shutil.rmtree(destination, ignore_errors=True)
    StaticStateStore(root).write_candidate(destination, content)
    try:
        for name in ("snapshot", "state", "release"):
            shutil.rmtree(root / name, ignore_errors=True)
            shutil.copytree(destination / name, root / name)
    finally:
        shutil.rmtree(destination, ignore_errors=True)
    # Re-load from the checkout to enforce the complete-tree allowlist after copying.
    StaticStateStore(root).load()


def _require_git_parent(root: Path, expected: str) -> str:
    if not _GIT_SHA.fullmatch(expected):
        raise ValueError("expected generated-content commit must be a Git SHA")
    actual = _git(root, "rev-parse", "HEAD")
    if actual != expected:
        raise CompareAndSwapConflict("generated-content branch commit changed")
    return actual


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("generated-content Git operation failed")
    return result.stdout.strip()


def _commit_and_push(root: Path, *, expected_commit: str, message: str) -> str:
    _git(root, "add", "--", "snapshot", "state", "release")
    if not _git(root, "status", "--porcelain", "--", "snapshot", "state", "release"):
        return expected_commit
    _git(
        root,
        "-c",
        "user.name=github-actions[bot]",
        "-c",
        "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit",
        "-m",
        message,
    )
    commit = _git(root, "rev-parse", "HEAD")
    token = os.environ.get("GH_TOKEN")
    command = ["git", "-C", str(root)]
    if token:
        credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        command.extend(
            ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {credential}"]
        )
    command.extend(
        [
            "push",
            f"--force-with-lease=refs/heads/generated-content:{expected_commit}",
            "origin",
            "HEAD:generated-content",
        ]
    )
    pushed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if pushed.returncode != 0:
        raise CompareAndSwapConflict("generated-content push compare-and-swap failed")
    return commit


def _optional_id(value: str | None) -> str | None:
    return value or None


def _live_publication_ready(config: ScotusConfig) -> bool:
    return bool(
        config.enabled
        and config.publication.enabled
        and not config.publication.dry_run
        and config.generation.brief_generation_enabled
        and config.approvals.all_live_gates_approved()
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ragchew-scotus-static")
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export", help="export and validate a complete Pages candidate")
    export.add_argument("--projection", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--config", type=Path, default=Path("config/scotus.yaml"))
    export.add_argument("--source-commit", required=True)
    export.add_argument("--build-epoch", required=True, help="timezone-aware ISO-8601 timestamp")
    export.add_argument("--previous-release-id")
    export.add_argument("--state", "--state-dir", dest="state", type=Path)
    export.add_argument("--live", action="store_true", help="require every live-publication gate")
    export.set_defaults(function=_export)

    validate = commands.add_parser("validate", help="validate an existing static candidate")
    validate.add_argument("--output", type=Path, required=True)
    validate.add_argument("--config", type=Path, default=Path("config/scotus.yaml"))
    validate.add_argument("--state", "--state-dir", dest="state", type=Path)
    validate.add_argument("--privacy-scan", action="store_true")
    validate.set_defaults(function=_validate)

    fixture = commands.add_parser(
        "fixture-preview", help="build and validate a fixture tree, then exit"
    )
    fixture.add_argument(
        "--fixture", type=Path, default=Path("tests/fixtures/static/one-case.json")
    )
    fixture.add_argument("--output", type=Path, required=True)
    fixture.add_argument("--config", type=Path, default=Path("config/scotus.yaml"))
    fixture.add_argument("--source-commit", default="0" * 40)
    fixture.add_argument("--build-epoch", default="1970-01-01T00:00:00Z")
    fixture.add_argument("--state", "--state-dir", dest="state", type=Path)
    fixture.add_argument("--github-output", type=Path)
    fixture.set_defaults(function=_fixture_preview)

    preview = commands.add_parser("preview", help="serve an existing or fixture-backed static tree")
    preview.add_argument("--directory", type=Path)
    preview.add_argument(
        "--fixture", type=Path, default=Path("tests/fixtures/static/one-case.json")
    )
    preview.add_argument("--config", type=Path, default=Path("config/scotus.yaml"))
    preview.add_argument("--source-commit", default="0" * 40)
    preview.add_argument("--build-epoch", default="1970-01-01T00:00:00Z")
    preview.add_argument("--host", default="127.0.0.1")
    preview.add_argument("--port", type=int, default=8000)
    preview.set_defaults(function=_preview)

    reconcile = commands.add_parser("reconcile", help="compare live and generated release IDs")
    reconcile.add_argument("--state-dir", dest="state", type=Path, required=True)
    live = reconcile.add_mutually_exclusive_group(required=True)
    live.add_argument("--live-release-url")
    live.add_argument("--live-release-id")
    reconcile.add_argument(
        "--validated-state-dir",
        dest="validated_state",
        action="append",
        type=Path,
        default=[],
    )
    reconcile.add_argument("--config", type=Path, default=Path("config/scotus.yaml"))
    reconcile.add_argument("--fail-on-split", action="store_true")
    reconcile.add_argument(
        "--allow-missing-live-bootstrap",
        action="store_true",
        help="allow an unavailable live marker only for the initial empty bootstrap",
    )
    reconcile.set_defaults(function=_reconcile)

    batch = commands.add_parser("batch", help="run a configured bounded batch adapter")
    batch.add_argument("--mode", choices=("nightly", "bootstrap", "fixture"), required=True)
    batch.add_argument("--state-dir", dest="state", type=Path)
    batch.add_argument("--candidate-state-dir", dest="candidate_state", type=Path)
    batch.add_argument("--output", type=Path, required=True)
    batch.add_argument("--workspace", type=Path)
    batch.add_argument("--config", type=Path, default=Path("config/scotus.yaml"))
    batch.add_argument("--canonical-origin")
    batch.add_argument("--project-base-path")
    batch.add_argument("--source-commit", default="0" * 40)
    batch.add_argument("--build-epoch", default="1970-01-01T00:00:00Z")
    batch.add_argument("--github-output", type=Path)
    batch.add_argument("--authorized-replay", action="store_true")
    batch.add_argument("--fixture", type=Path, default=Path("tests/fixtures/static/one-case.json"))
    batch.set_defaults(function=_batch)

    receipts = commands.add_parser(
        "persist-cost-receipts", help="validate and CAS-persist opaque cost receipts"
    )
    receipts.add_argument("--state-dir", dest="state", type=Path)
    receipts.add_argument("--receipts", type=Path, required=True)
    receipts.add_argument("--expected-parent-commit")
    receipts.add_argument("--validate-only", action="store_true")
    receipts.add_argument("--github-output", type=Path)
    receipts.set_defaults(function=_persist_cost_receipts)

    promote = commands.add_parser("promote", help="CAS-promote validated generated state")
    promote.add_argument("--state-dir", dest="state", type=Path, required=True)
    promote.add_argument("--candidate-state-dir", dest="candidate_state", type=Path, required=True)
    promote.add_argument("--release-id")
    promote.add_argument("--expected-parent-release-id")
    promote.add_argument("--expected-parent-commit", required=True)
    promote.add_argument("--checkpoint-only", action="store_true")
    promote.add_argument("--github-output", type=Path)
    promote.set_defaults(function=_promote)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = args.function(args)
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"error: {error}") from None
    raise SystemExit(result)


if __name__ == "__main__":
    main()
