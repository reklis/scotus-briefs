from __future__ import annotations

from pathlib import Path

import yaml


def documents(path: str) -> list[dict[str, object]]:
    return [item for item in yaml.safe_load_all(Path(path).read_text()) if item]


def test_active_kustomization_has_no_dynamic_production_resources() -> None:
    kustomization = yaml.safe_load(Path("deploy/k8s/base/kustomization.yaml").read_text())
    assert kustomization["resources"] == ["namespace.yaml"]
    active_documents = []
    for relative in kustomization["resources"]:
        active_documents.extend(documents(f"deploy/k8s/base/{relative}"))
    assert {document["kind"] for document in active_documents} == {"Namespace"}
    serialized = yaml.safe_dump_all(active_documents).casefold()
    for obsolete in (
        "deployment",
        "service",
        "ingress",
        "cronjob",
        "secret",
        "fastapi",
        "postgres",
        "minio",
        "ragchew-public",
        "scotus-analyzer",
    ):
        assert obsolete not in serialized


def test_legacy_kubernetes_manifests_are_explicitly_dormant() -> None:
    readme = Path("deploy/k8s/dormant/README.md").read_text().casefold()
    assert "not a production overlay" in readme
    assert "static github pages" in readme
    for path in Path("deploy/k8s/dormant").glob("*.yaml"):
        assert path.read_text().startswith("# DORMANT:")
    assert not (Path("deploy/k8s/base") / "ingress.yaml").exists()
    assert not (Path("deploy/k8s/base") / "secrets.example.yaml").exists()


def test_pages_jobs_never_receive_obsolete_backend_reader_credentials() -> None:
    workflow = Path(".github/workflows/publish-pages.yml").read_text()
    deploy = workflow[
        workflow.index("\n  deploy:\n") : workflow.index("\n  promote:\n")
    ]
    for forbidden in (
        "RAGCHEW_DATABASE_DSN",
        "RAGCHEW_S3_ENDPOINT",
        "RAGCHEW_S3_ACCESS_KEY",
        "RAGCHEW_S3_SECRET_KEY",
        "OPENAI_API_KEY",
        "ragchew-public",
    ):
        assert forbidden not in deploy
    assert "pages: write" in deploy
    assert "id-token: write" in deploy
    assert "contents: write" not in deploy


def test_pages_workflow_wires_ephemeral_live_adapter_and_serializes_mutations() -> None:
    workflow = Path(".github/workflows/publish-pages.yml").read_text()
    assert "services:" not in workflow
    assert "postgres:" not in workflow and "minio:" not in workflow
    assert "environment: scotus-publication" in workflow
    assert "runs-on: [self-hosted]" in workflow
    assert "OPENAI_API_KEY" not in workflow
    assert "RAGCHEW_OLLAMA_BASE_URL: http://127.0.0.1:11434/v1" in workflow
    assert "qwen3.8:27b" in workflow
    assert "ragchew.scotus.live_static:LiveStaticBatchAdapter" in workflow
    assert "options: [fixture, nightly, bootstrap, activity-migration" in workflow
    assert "Build no-model activity-contract migration" in workflow
    assert "migrate-activity-contracts" in workflow
    assert "inputs.mode != 'activity-migration'" in workflow
    assert "RAGCHEW_SOURCE_USER_AGENT" in workflow
    assert "github.com/reklis/scotus-briefs" in workflow
    assert "  CANONICAL_ORIGIN: https://scotusbriefs.us\n" in workflow
    assert "  PROJECT_BASE_PATH: /\n" in workflow
    assert (
        'canonical_release_url="${CANONICAL_ORIGIN}${PROJECT_BASE_PATH}release/v1/release.json"'
        in workflow
    )
    assert "ReleaseManifest.model_validate_json" in workflow
    assert "canonical_release_url/https:/http:" in workflow
    assert "curl --proto '=https'" in workflow
    assert "curl --proto '=http'" in workflow
    assert "example.invalid" not in workflow
    assert "release_id=fixture" not in workflow
    assert "test ! -e candidate-site" in workflow
    assert "test ! -e candidate-state" in workflow
    assert "install -d -m 700 candidate-site" not in workflow
    assert workflow.index("Validate opaque receipts before any upload") < workflow.index(
        "Upload validated opaque cost receipts"
    ) < workflow.index("Upload exact Pages artifact")
    assert "    timeout-minutes: 330\n" in workflow
    live_step = workflow[
        workflow.index("- name: Run reviewed bounded live adapter") :
        workflow.index("- name: Build fixture, validate, and exit")
    ]
    assert "timeout-minutes: 315" in live_step
    receipt_upload = workflow[
        workflow.index("- name: Upload validated opaque cost receipts") :
        workflow.index("- name: Safe build summary")
    ]
    assert "github.event_name == 'schedule' || inputs.deploy == true" in receipt_upload
    receipt_job = workflow[
        workflow.index("\n  persist-cost-receipts:\n") : workflow.index("\n  deploy:\n")
    ]
    assert "github.event_name == 'schedule' || inputs.deploy == true" in receipt_job
    assert "needs.build.outputs.receipts_present == 'true'" in receipt_job
    assert (
        "needs.build.outputs.expected_parent_commit != '' && "
        "(github.event_name == 'schedule' || inputs.deploy == true)"
    ) in receipt_job
    assert "if: needs.build.outputs.receipts_present == 'true'" in receipt_job
    assert "continue-on-error: true" not in receipt_job
    assert "retained for guarded deployment without model reprocessing" in workflow
    assert "git -C generated-content-input rev-parse HEAD" in workflow
    assert "path: source" in workflow and "path: generated-content" in workflow
    assert "needs: [build, persist-cost-receipts, deploy]" in workflow
    assert "needs: [build, persist-cost-receipts]" in workflow
    assert "stopped before network/model use" not in workflow
    assert "Reconcile release IDs before live source access" in workflow
    assert "Verified live release ID for a protected pre-TLS manual run" in workflow
    assert '"$PUBLICATION_MODE" != "poc-import"' in workflow
    assert '"$PUBLICATION_MODE" != "nightly"' in workflow
    assert '"$EXPECTED_LIVE_RELEASE_ID" =~ ^[0-9a-f]{64}$' in workflow
    assert 'live_args=(--live-release-id "$EXPECTED_LIVE_RELEASE_ID")' in workflow
    assert "Explicitly replay unchanged local-model input after reviewed failure" in workflow
    assert '"$PUBLICATION_MODE" != "nightly"' in workflow
    assert '"${{ github.event_name }}" != "workflow_dispatch"' in workflow
    assert "replay_args+=(--authorized-replay)" in workflow
    assert "case_args+=(--maximum-cases \"$MAXIMUM_CASES\")" in workflow
    assert "Run reviewed bounded live adapter" in workflow
    assert "if: always()" in workflow and "Clean persistent runner after build" in workflow
    assert "Clean persistent runner before build" in workflow
    assert "github.event_name != 'pull_request'" in workflow
    assert "include-hidden-files: true" in workflow
    assert "needs: [build, persist-cost-receipts]" in workflow
    assert "needs.persist-cost-receipts.result == 'success'" in workflow
    assert "Freshness: discovered=%s" in workflow
    for safe_field in (
        "newest_discovered_activity_date",
        "newest_published_activity_date",
        "newest_deferred_activity_date",
        "newest_failed_activity_date",
        "newest_pending_activity_date",
    ):
        assert safe_field in workflow


def test_retained_candidate_deploy_revalidates_without_spark_or_model() -> None:
    workflow = Path(
        ".github/workflows/deploy-validated-candidate.yml"
    ).read_text()
    assert "runs-on: ubuntu-24.04" in workflow
    assert "self-hosted" not in workflow
    assert "RAGCHEW_OLLAMA_BASE_URL" not in workflow
    assert "candidate_run_id" in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert "ragchew-scotus-static validate" in workflow
    assert "--privacy-scan" in workflow
    assert "source_run_id" in workflow
    assert "expected_parent_commit" in workflow
    assert "persist-cost-receipts" in workflow
    assert "actions: read" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert "contents: write" in workflow
    assert "deploy-pages@cd2ce8fcbc39b97be8ca5fce6e763baed58fa128" in workflow


def test_container_is_nonroot_and_uses_immutable_base_images() -> None:
    containerfile = Path("Containerfile").read_text()
    assert "USER 10001:10001" in containerfile
    from_lines = [line for line in containerfile.splitlines() if line.startswith("FROM ")]
    assert from_lines
    assert all("@sha256:" in line for line in from_lines)


def test_ci_has_frozen_install_and_security_gates() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text().casefold()
    assert "uv sync --frozen --dev" in workflow
    for gate in (
        "pytest",
        "mypy",
        "ruff",
        "pip-audit",
        "pip-licenses",
        "gitleaks",
        "trivy",
        "check-public-repository",
    ):
        assert gate in workflow
