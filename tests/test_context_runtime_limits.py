"""Runtime context-limit parity for 1M and private gateway models."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.models.context import (
    get_context_window_for_model,
    get_model_max_output_tokens,
)
from src.services.compact.pipeline import build_production_pipeline_config
from src.query.query import _get_context_window
from src.settings.types import ModelLimitSettings, SettingsSchema
from src.settings.settings import load_settings


def _settings(limits: dict[str, ModelLimitSettings]) -> SettingsSchema:
    return SettingsSchema(model_limits=limits)


def test_production_pipeline_uses_registered_1m_window() -> None:
    provider = SimpleNamespace(
        model="glm-5.2", base_url="https://api.z.ai/v1"
    )
    context = SimpleNamespace(read_file_fingerprints={})
    cfg = build_production_pipeline_config(provider, context, None)
    assert cfg.context_window == 1_000_000
    assert cfg.max_output_tokens == 8_192
    assert _get_context_window(provider) == 1_000_000


def test_explicit_provider_runtime_window_wins() -> None:
    provider = SimpleNamespace(
        model="unknown", base_url=None, context_window=1_048_576
    )
    assert _get_context_window(provider) == 1_048_576


def test_unknown_model_uses_settings_limits() -> None:
    configured = _settings({
        "qwen3.6-plus": ModelLimitSettings(
            context_window=1_048_576, max_output_tokens=32_768
        )
    })
    with patch("src.settings.settings.get_settings", return_value=configured):
        assert get_context_window_for_model("qwen3.6-plus") == 1_048_576
        assert get_model_max_output_tokens("qwen3.6-plus") == 32_768


def test_exact_key_beats_prefix_and_host_qualifies_equal_match() -> None:
    configured = _settings({
        "qwen": ModelLimitSettings(context_window=300_000),
        "qwen3.6-plus": ModelLimitSettings(context_window=500_000),
        "localhost:4000:qwen3.6-plus": ModelLimitSettings(
            context_window=1_000_000
        ),
    })
    with patch("src.settings.settings.get_settings", return_value=configured):
        assert get_context_window_for_model("qwen3.6-plus") == 500_000
        assert get_context_window_for_model(
            "qwen3.6-plus", base_url="http://localhost:4000/v1"
        ) == 1_000_000


def test_catalog_metadata_remains_above_settings_override() -> None:
    configured = _settings({
        "glm-5.2": ModelLimitSettings(context_window=128_000)
    })
    with patch("src.settings.settings.get_settings", return_value=configured):
        assert get_context_window_for_model("glm-5.2") == 1_000_000


def test_settings_schema_accepts_upstream_camel_case_shape() -> None:
    settings = SettingsSchema.from_dict({
        "modelLimits": {
            "private-model": {
                "contextWindow": 1_000_000,
                "maxOutputTokens": 64_000,
            }
        }
    })
    limit = settings.model_limits["private-model"]
    assert limit.context_window == 1_000_000
    assert limit.max_output_tokens == 64_000


def test_loader_camel_case_overrides_materialized_default() -> None:
    manager = SimpleNamespace(
        load_global=lambda: {
            "settings": {
                "modelLimits": {
                    "gateway-model": {"contextWindow": 1_000_000}
                }
            }
        },
        load_project=lambda: {},
        load_local=lambda: {},
    )
    loaded = load_settings(config_manager=manager)
    assert loaded.model_limits["gateway-model"].context_window == 1_000_000


def test_invalid_settings_limits_fall_back_safely() -> None:
    configured = _settings({
        "private-model": ModelLimitSettings(
            context_window=-1, max_output_tokens=True
        )
    })
    with patch("src.settings.settings.get_settings", return_value=configured):
        assert get_context_window_for_model("private-model") == 200_000
        assert get_model_max_output_tokens("private-model") == 8_192
