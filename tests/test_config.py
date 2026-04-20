"""Tests for configuration management (legacy compat tests).

Updated to work with the new ConfigManager-based config system (WS-6 rewrite).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch, Mock
from pathlib import Path
import tempfile
import json
import base64
import os

import src.config as config_module
from src.config import (
    get_config_path,
    get_default_config,
    load_config,
    save_config,
    get_provider_config,
    set_api_key,
    set_default_provider,
    get_default_provider,
    ConfigManager,
)


def _patch_global_config(temp_dir):
    """Helper: patch GLOBAL_CONFIG_FILE and reset the default manager."""
    new_path = Path(temp_dir) / ".clawcodex" / "config.json"
    return patch.object(config_module, "GLOBAL_CONFIG_FILE", new_path)


def _reset_manager():
    """Reset the cached default ConfigManager."""
    config_module._default_manager = None


class TestConfigPath(unittest.TestCase):
    """Test configuration path functions."""

    def test_get_config_path(self):
        """Test getting config path returns the global config path."""
        path = get_config_path()
        self.assertTrue(str(path).endswith("config.json"))

    def test_config_path_is_in_home(self):
        """Test that config path is under home directory."""
        path = get_config_path()
        self.assertIn(".clawcodex", str(path))


class TestDefaultConfig(unittest.TestCase):
    """Test default configuration."""

    def test_get_default_config(self):
        """Test getting default config."""
        config = get_default_config()

        self.assertIn("default_provider", config)
        self.assertIn("providers", config)
        self.assertIn("anthropic", config["providers"])
        self.assertIn("openai", config["providers"])
        self.assertIn("glm", config["providers"])

    def test_default_provider_is_anthropic(self):
        """Test that default provider is Anthropic."""
        config = get_default_config()
        self.assertEqual(config["default_provider"], "anthropic")

    def test_default_models(self):
        """Test default models for providers."""
        config = get_default_config()
        self.assertEqual(
            config["providers"]["anthropic"]["default_model"],
            "claude-sonnet-4-6"
        )
        self.assertEqual(
            config["providers"]["openai"]["default_model"],
            "gpt-5.4"
        )
        self.assertEqual(
            config["providers"]["glm"]["default_model"],
            "zai/glm-5"
        )


class TestLoadSaveConfig(unittest.TestCase):
    """Test loading and saving configuration."""

    def setUp(self):
        _reset_manager()

    def tearDown(self):
        _reset_manager()

    def test_save_and_load_config(self):
        """Test save and load roundtrip."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_global_config(temp_dir):
                _reset_manager()
                config = {
                    "default_provider": "glm",
                    "providers": {
                        "glm": {
                            "api_key": "test_key",
                            "base_url": "https://example.com",
                            "default_model": "glm-4"
                        }
                    }
                }

                save_config(config)
                _reset_manager()
                loaded = load_config()

                self.assertEqual(loaded["default_provider"], "glm")
                self.assertEqual(
                    loaded["providers"]["glm"]["api_key"],
                    "test_key"
                )

    def test_load_config_returns_dict(self):
        """Test that loading config returns a valid dict."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_global_config(temp_dir):
                _reset_manager()
                config = load_config()
                self.assertIsInstance(config, dict)

    @unittest.skipIf(os.name == "nt", "POSIX file permission semantics differ on Windows")
    def test_config_file_permissions_restricted_on_save(self):
        """Test that saved config uses owner-only permissions on POSIX systems."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_global_config(temp_dir):
                _reset_manager()
                config_path = Path(temp_dir) / ".clawcodex" / "config.json"
                save_config(get_default_config())
                mode = config_path.stat().st_mode & 0o777
                self.assertEqual(mode, 0o600)


class TestProviderConfig(unittest.TestCase):
    """Test provider-specific configuration."""

    def setUp(self):
        _reset_manager()

    def tearDown(self):
        _reset_manager()

    def test_get_provider_config(self):
        """Test getting provider config."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_global_config(temp_dir):
                _reset_manager()
                glm_config = get_provider_config("glm")

                self.assertIn("api_key", glm_config)
                self.assertIn("base_url", glm_config)
                self.assertIn("default_model", glm_config)

    def test_get_unknown_provider(self):
        """Test getting unknown provider."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_global_config(temp_dir):
                _reset_manager()
                with self.assertRaises(ValueError) as context:
                    get_provider_config("unknown")

                self.assertIn("Unknown provider", str(context.exception))


class TestSetAPIKey(unittest.TestCase):
    """Test setting API keys."""

    def setUp(self):
        _reset_manager()

    def tearDown(self):
        _reset_manager()

    def test_set_api_key(self):
        """Test setting API key for provider."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_global_config(temp_dir):
                _reset_manager()
                set_api_key("glm", "new_api_key")

                _reset_manager()
                config = load_config()
                self.assertEqual(
                    config["providers"]["glm"]["api_key"],
                    "new_api_key"
                )

    def test_set_api_key_with_options(self):
        """Test setting API key with base URL and model."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_global_config(temp_dir):
                _reset_manager()
                set_api_key(
                    "glm",
                    "new_api_key",
                    base_url="https://custom.url",
                    default_model="custom-model"
                )

                _reset_manager()
                config = load_config()
                self.assertEqual(
                    config["providers"]["glm"]["api_key"],
                    "new_api_key"
                )
                self.assertEqual(
                    config["providers"]["glm"]["base_url"],
                    "https://custom.url"
                )
                self.assertEqual(
                    config["providers"]["glm"]["default_model"],
                    "custom-model"
                )


class TestDefaultProvider(unittest.TestCase):
    """Test default provider management."""

    def setUp(self):
        _reset_manager()

    def tearDown(self):
        _reset_manager()

    def test_set_default_provider(self):
        """Test setting default provider."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_global_config(temp_dir):
                _reset_manager()
                set_default_provider("openai")

                _reset_manager()
                provider = get_default_provider()
                self.assertEqual(provider, "openai")

    def test_get_default_provider(self):
        """Test getting default provider."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_global_config(temp_dir):
                _reset_manager()
                provider = get_default_provider()
                self.assertEqual(provider, "anthropic")


if __name__ == '__main__':
    unittest.main()
