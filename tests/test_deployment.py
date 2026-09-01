from __future__ import annotations

from pathlib import Path

import yaml


def documents(path: str) -> list[dict[str, object]]:
    return [item for item in yaml.safe_load_all(Path(path).read_text()) if item]


def all_workload_containers() -> list[dict[str, object]]:
    containers: list[dict[str, object]] = []
    for path in ("deploy/k8s/base/workloads.yaml", "deploy/k8s/base/schedules.yaml"):
        for document in documents(path):
            kind = document["kind"]
            if kind not in {"Deployment", "CronJob"}:
                continue
            spec = document["spec"]  # type: ignore[index]
            if kind == "Deployment":
                pod = spec["template"]  # type: ignore[index]
            else:
                pod = spec["jobTemplate"]["spec"]["template"]  # type: ignore[index]
            containers.extend(pod["spec"]["containers"])  # type: ignore[index]
    return containers


def test_kubernetes_yaml_parses_and_has_default_deny() -> None:
    paths = list(Path("deploy/k8s/base").glob("*.yaml"))
    assert paths
    for path in paths:
        assert documents(str(path))
    policies = documents("deploy/k8s/base/network-policies.yaml")
    default_deny = next(item for item in policies if item["metadata"]["name"] == "default-deny")  # type: ignore[index]
    assert default_deny["spec"]["podSelector"] == {}  # type: ignore[index]
    assert set(default_deny["spec"]["policyTypes"]) == {"Ingress", "Egress"}  # type: ignore[index]


def test_all_containers_are_non_privileged_and_resource_bounded() -> None:
    for container in all_workload_containers():
        security = container["securityContext"]  # type: ignore[index]
        assert security["allowPrivilegeEscalation"] is False
        assert security["readOnlyRootFilesystem"] is True
        assert security["capabilities"]["drop"] == ["ALL"]
        resources = container["resources"]  # type: ignore[index]
        assert resources["requests"] and resources["limits"]


def test_public_workload_receives_no_private_source_or_model_secret() -> None:
    workloads = documents("deploy/k8s/base/workloads.yaml")
    public = next(
        item
        for item in workloads
        if item.get("kind") == "Deployment"
        and item["metadata"]["name"] == "public"  # type: ignore[index]
    )
    env_from = public["spec"]["template"]["spec"]["containers"][0]["envFrom"]  # type: ignore[index]
    secret_names = {
        entry["secretRef"]["name"] for entry in env_from if "secretRef" in entry
    }
    assert secret_names == {"ragchew-public-db"}
    example = Path("deploy/k8s/base/secrets.example.yaml").read_text()
    assert "replace-me" in example
    assert "sk-" not in example


def test_scotus_uses_official_openai_api_not_internal_model_service() -> None:
    config = Path("config/scotus.yaml").read_text()
    environment = Path("deploy/k8s/base/config.yaml").read_text()
    policies = Path("deploy/k8s/base/network-policies.yaml").read_text()
    publisher = Path("src/ragchew/scotus/publisher.py").read_text()
    assert "provider: openai" in config
    assert "model: gpt-5" in config
    assert "brief_generation_enabled: false" in config
    assert "maximum_brief_api_calls_per_run: 1" in config
    assert "if not config.generation.brief_generation_enabled" in publisher
    assert "https://api.openai.com/v1" in environment
    assert "llm.ragchew.svc" not in environment + policies
    assert "base_url=settings.openai_base_url" not in publisher


def test_scotus_deployment_has_no_audio_or_stt_workload() -> None:
    config = Path("deploy/k8s/base/config.yaml").read_text().lower()
    workloads = Path("deploy/k8s/base/workloads.yaml").read_text()
    schedules = Path("deploy/k8s/base/schedules.yaml").read_text()
    containerfile = Path("Containerfile").read_text().lower()
    assert "scotus_legal_briefs" in config
    assert "stt_model" not in config
    assert "ffmpeg" not in containerfile
    assert "--extra stt" not in containerfile
    for command in (
        "ragchew-scotus-worker",
        "ragchew-scotus-discover",
        "ragchew-scotus-publish",
        "ragchew-scotus-retention",
    ):
        assert command in workloads + schedules
    assert "ragchew-api" not in workloads
    assert "ragchew-maintenance" not in schedules


def test_container_runs_as_nonroot_and_ci_has_security_gates() -> None:
    containerfile = Path("Containerfile").read_text()
    assert "USER 10001:10001" in containerfile
    workflow = Path(".github/workflows/ci.yml").read_text()
    for gate in ("pytest", "mypy", "ruff", "pip-audit", "gitleaks", "trivy"):
        assert gate in workflow.lower()
