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
    assert config.generation.provider == "openai"
    assert config.generation.model == "gpt-5"
    assert config.generation.prompt_version == "scotus-brief-plain-language-v14"
    assert config.generation.brief_generation_enabled is False
    assert config.generation.maximum_brief_api_calls_per_run == 1
    assert config.generation.stop_after_brief_validation_failure is True
    assert config.generation.audience == "general_public"
    assert config.generation.maximum_sentence_words == 30
    assert config.publication.case_page_requires_official_transcript is True
    assert config.launch.maximum_status_upgrades == 0


def test_scotus_config_rejects_audio_or_stt() -> None:
    config = ScotusConfig.from_yaml(Path("config/scotus.yaml"))
    values = config.model_dump()
    values["documents"]["download_audio"] = True
    with pytest.raises(ValidationError, match="audio and STT are disabled"):
        ScotusConfig.model_validate(values)


def test_scotus_config_rejects_local_or_compatible_model_provider() -> None:
    config = ScotusConfig.from_yaml(Path("config/scotus.yaml"))
    values = config.model_dump()
    values["generation"]["provider"] = "local"
    values["generation"]["model"] = "local-model"
    with pytest.raises(ValidationError):
        ScotusConfig.model_validate(values)


def test_standard_openai_api_key_environment_name_is_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RAGCHEW_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    settings = ServiceSettings(_env_file=None)
    assert settings.openai_api_key.get_secret_value() == "test-openai-key"


def test_enabled_proceeding_source_requires_review() -> None:
    config = ProceedingsConfig.from_yaml(Path("config/proceedings.yaml"))
    source = config.sources["dc_mayor"].model_dump()
    source["enabled"] = True
    source["discovery_method"] = "official_page"
    source["access_basis"] = None
    source["access_reviewed_at"] = None
    source["access_reviewed_by"] = None
    with pytest.raises(ValidationError, match="completed access review"):
        ProceedingsConfig.model_validate(
            {**config.model_dump(), "sources": {"dc_mayor": source}}
        )
