"""Tests for Pydantic Settings adapter (Task #1)."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from src.settings.pydantic_adapter import (
    ClawCodexSettings,
    dict_to_settings,
    get_cached_settings,
    invalidate_settings_cache,
    is_pydantic_settings_available,
    load_settings_from_config_manager,
    settings_to_dict,
)


class TestPydanticSettingsAvailable:
    def test_pydantic_settings_is_available(self):
        assert is_pydantic_settings_available() is True


class TestClawCodexSettings:
    def test_default_provider_is_anthropic(self):
        settings = ClawCodexSettings()
        assert settings.default_provider == "anthropic"

    def test_settings_with_custom_values(self):
        settings = ClawCodexSettings(
            default_provider="openai",
            model="gpt-4o",
            max_turns=100,
        )
        assert settings.default_provider == "openai"
        assert settings.model == "gpt-4o"
        assert settings.max_turns == 100

    def test_settings_from_dict(self):
        data = {
            "default_provider": "glm",
            "model": "zai/glm-5",
        }
        settings = dict_to_settings(data)
        assert settings.default_provider == "glm"
        assert settings.model == "zai/glm-5"


class TestSettingsConversion:
    def test_settings_to_dict(self):
        settings = ClawCodexSettings(
            default_provider="openai",
            max_turns=50,
        )
        d = settings_to_dict(settings)
        assert d["default_provider"] == "openai"
        assert d["max_turns"] == 50

    def test_dict_to_settings(self):
        data = {
            "default_provider": "deepseek",
            "model": "deepseek-v4-pro",
        }
        settings = dict_to_settings(data)
        assert settings.default_provider == "deepseek"


class TestLoadFromConfigManager:
    def test_load_from_config_manager(self):
        from src.config import ConfigManager

        settings = load_settings_from_config_manager(
            config_manager=ConfigManager()
        )
        assert settings.default_provider == "anthropic"
        assert isinstance(settings.providers, dict)

    def test_invalidate_cache(self):
        invalidate_settings_cache()
        # Should not raise
        get_cached_settings.cache_clear()


class TestBackwardCompatibility:
    def test_pydantic_settings_module_import(self):
        # Verify the module can be imported
        from src.settings.pydantic_adapter import (
            ClawCodexSettings,
            get_cached_settings,
            is_pydantic_settings_available,
            load_settings_from_config_manager,
        )
        assert is_pydantic_settings_available() is True