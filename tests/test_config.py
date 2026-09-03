from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from ragchew.config import MvpConfig, ProceedingsConfig, ScotusConfig, ServiceSettings


def test_mvp_defaults_are_valid() -> None:
    config = MvpConfig.from_yaml(Path("config/mvp.yaml"))
    assert config.receiver.talkgroups[101] == "01 DISP"
    assert config.receiver.rf_min_hz < config.receiver.rf_max_hz
    assert config.retention.audio_hours == 24
    assert "medical" in config.publication.mandatory_suppression
    assert "structure_fire" in config.publication.allowlist


def test_proceedings_defaults_are_fail_closed() -> None:
    config = ProceedingsConfig.from_yaml(Path("config/proceedings.yaml"))
    assert not any(source.enabled for source in config.sources.values())
    assert config.sources["supreme_court"].discovery_method.value == "official_page"
    assert config.sources["house_floor"].discovery_method.value == "official_page"
    assert config.collection.chunk_overlap_seconds < config.collection.chunk_duration_seconds
    assert config.publication.public_transcript_quotes is False
    assert config.launch.maximum_status_upgrades == 0


def test_scotus_defaults_are_transcript_first_and_fail_closed() -> None:
    config = ScotusConfig.from_yaml(Path("config/scotus.yaml"))
    assert config.product == "scotus_legal_briefs"
    assert config.source_id == "supreme_court"
    assert config.enabled is False
    assert config.documents.download_audio is False
    assert config.documents.stt_enabled is False
    assert config.discovery.terms[0] == "2025"
    assert config.discovery.terms[-1] == "2000"
    assert config.discovery.backfill_case_limit == 200
    assert config.generation.provider == "ollama"
    assert config.generation.model == "qwen3.8:27b"
    assert config.generation.prompt_version == "scotus-brief-plain-language-v14"
    assert config.generation.brief_generation_enabled is False
    assert config.generation.maximum_brief_api_calls_per_run == 1
    assert config.generation.stop_after_brief_validation_failure is True
    assert config.generation.audience == "general_public"
    assert config.generation.maximum_sentence_words == 30
    assert config.publication.case_page_requires_official_transcript is True
    assert config.repository.owner == "reklis"
    assert config.repository.name == "scotus-briefs"
    assert config.static.canonical_origin == "https://reklis.github.io"
    assert config.static.project_base_path == "/scotus-briefs/"
    assert config.static.section_path == "/scotus/"
    assert config.schedule.nightly_cron_utc == "17 3 * * *"
    assert config.bootstrap.maximum_cases_per_run == 1
    assert config.runner_limits.maximum_cases_per_run == 1
    assert config.model_budget.maximum_extraction_calls_per_run == 20
    assert config.model_budget.maximum_brief_calls_per_run == 1
    assert config.model_budget.maximum_total_calls_per_run == 21
    assert config.model_budget.input_cost_usd_per_million_tokens == Decimal("0")
    assert config.model_budget.output_cost_usd_per_million_tokens == Decimal("0")
    assert config.model_budget.maximum_estimated_cost_usd_per_run == Decimal("0")
    assert config.licensing.code_and_documentation == "Apache-2.0"
    assert config.licensing.generated_briefs == "CC-BY-4.0"
    assert not config.approvals.all_live_gates_approved()
    assert config.launch.maximum_status_upgrades == 0


def test_scotus_config_rejects_audio_or_stt() -> None:
    config = ScotusConfig.from_yaml(Path("config/scotus.yaml"))
    values = config.model_dump()
    values["documents"]["download_audio"] = True
    with pytest.raises(ValidationError, match="audio and STT are disabled"):
        ScotusConfig.model_validate(values)


def test_scotus_static_config_normalizes_project_and_custom_domain_paths() -> None:
    config = ScotusConfig.from_yaml(Path("config/scotus.yaml"))
    values = config.model_dump()
    values["static"]["project_base_path"] = "project"
    values["static"]["section_path"] = "/"
    normalized = ScotusConfig.model_validate(values)
    assert normalized.static.project_base_path == "/project/"
    assert normalized.static.section_path == "/"


def test_scotus_static_config_rejects_origins_paths_and_runtime_dependencies() -> None:
    config = ScotusConfig.from_yaml(Path("config/scotus.yaml"))
    for field, value in (
        ("canonical_origin", "http://reklis.github.io"),
        ("canonical_origin", "https://reklis.github.io/scotus-briefs"),
        ("project_base_path", "/project/%2e%2e/escape/"),
        ("section_path", "/scotus?api=true"),
        ("output_path", "../pages"),
        ("generated_state_path", "/tmp/state"),
        ("runtime_api_url", "https://api.example.test"),
    ):
        values = config.model_dump()
        values["static"][field] = value
        with pytest.raises(ValidationError):
            ScotusConfig.model_validate(values)


def test_scotus_static_config_rejects_incompatible_model_budgets() -> None:
    config = ScotusConfig.from_yaml(Path("config/scotus.yaml"))
    values = config.model_dump()
    values["model_budget"]["maximum_total_calls_per_run"] = 20
    with pytest.raises(ValidationError, match="total model-call budget"):
        ScotusConfig.model_validate(values)

    values = config.model_dump()
    values["model_budget"]["input_cost_usd_per_million_tokens"] = "1.00"
    with pytest.raises(ValidationError, match="token spend"):
        ScotusConfig.model_validate(values)

    values = config.model_dump()
    values["bootstrap"]["maximum_cases_per_run"] = 2
    with pytest.raises(ValidationError, match="brief-call capacity"):
        ScotusConfig.model_validate(values)


def test_scotus_live_publication_requires_every_approval() -> None:
    config = ScotusConfig.from_yaml(Path("config/scotus.yaml"))
    dry_run_values = config.model_dump()
    dry_run_values["publication"]["enabled"] = True
    assert ScotusConfig.model_validate(dry_run_values).publication.dry_run

    values = config.model_dump()
    values["publication"].update({"enabled": True, "dry_run": False})
    values["enabled"] = True
    values["generation"]["brief_generation_enabled"] = True
    with pytest.raises(ValidationError, match="live static publication"):
        ScotusConfig.model_validate(values)
    values["approvals"] = {key: True for key in values["approvals"]}
    live = ScotusConfig.model_validate(values)
    assert live.publication.enabled and not live.publication.dry_run

    values["licensing"]["court_materials_excluded"] = False
    with pytest.raises(ValidationError):
        ScotusConfig.model_validate(values)


def test_scotus_config_requires_reviewed_ollama_provider_and_exact_model() -> None:
    config = ScotusConfig.from_yaml(Path("config/scotus.yaml"))
    for provider, model in (("openai", "qwen3.8:27b"), ("ollama", "qwen3:27b")):
        values = config.model_dump()
        values["generation"].update({"provider": provider, "model": model})
        with pytest.raises(ValidationError):
            ScotusConfig.model_validate(values)


def test_source_user_agent_rejects_placeholder_contact() -> None:
    with pytest.raises(ValidationError, match=r"example\.invalid"):
        ServiceSettings(
            _env_file=None,
            source_user_agent="ragchew/1.0 contact=operator@example.invalid",
        )
    assert "github.com/reklis/scotus-briefs" in ServiceSettings(
        _env_file=None
    ).source_user_agent


def test_ollama_endpoint_is_typed_normalized_and_loopback_only() -> None:
    settings = ServiceSettings(
        _env_file=None,
        ollama_base_url="http://localhost:11434/v1/",
    )
    assert settings.ollama_base_url == "http://localhost:11434/v1"
    for endpoint in (
        "https://127.0.0.1:11434/v1",
        "http://192.168.1.2:11434/v1",
        "http://user@127.0.0.1:11434/v1",
        "http://127.0.0.1:11434/v1?token=x",
        "http://127.0.0.1:11434/api",
    ):
        with pytest.raises(ValidationError, match="Ollama base URL"):
            ServiceSettings(_env_file=None, ollama_base_url=endpoint)


def test_ollama_endpoint_environment_name_is_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAGCHEW_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    settings = ServiceSettings(_env_file=None)
    assert settings.ollama_base_url == "http://127.0.0.1:11434/v1"


def test_enabled_proceeding_source_requires_review() -> None:
    config = ProceedingsConfig.from_yaml(Path("config/proceedings.yaml"))
    source = config.sources["dc_mayor"].model_dump()
    source["enabled"] = True
    source["discovery_method"] = "official_page"
    source["access_basis"] = None
    source["access_reviewed_at"] = None
    source["access_reviewed_by"] = None
    with pytest.raises(ValidationError, match="completed access review"):
        ProceedingsConfig.model_validate({**config.model_dump(), "sources": {"dc_mayor": source}})
