"""Tests for the data-driven OpenAI-compatible provider registry.

Covers ``src/providers/openai_compatible_specs.py``: registry completeness,
generated-class defaults, alias resolution, API-key env-var fallback, and the
keyless-local-provider path.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.providers import (
    PROVIDER_INFO,
    canonical_provider_name,
    get_provider_class,
    get_provider_info,
    provider_env_vars,
    provider_requires_api_key,
    resolve_api_key,
)
from src.providers.base import ChatMessage
from src.providers.openai_compatible import OpenAICompatibleProvider
from src.providers.openai_compatible_specs import (
    SPECS_BY_ID,
    build_provider_class,
)

# The 18 OpenAI-compatible providers added via the registry.
EXPECTED_NEW_PROVIDERS = {
    "nvidia-nim",
    "atlascloud",
    "wanjie-ark",
    "volcengine",
    "xiaomi-mimo",
    "novita",
    "fireworks",
    "siliconflow",
    "siliconflow-cn",
    "arcee",
    "moonshot",
    "sglang",
    "vllm",
    "ollama",
    "huggingface",
    "together",
    "stepfun",
    "deepinfra",
}

# A sample of (id -> (base_url, default_model)) — each vendor's published
# default — as a regression guard that the registry defaults do not drift.
VENDOR_DEFAULTS = {
    "nvidia-nim": ("https://integrate.api.nvidia.com/v1", "deepseek-ai/deepseek-v4-pro"),
    "together": ("https://api.together.xyz/v1", "deepseek-ai/DeepSeek-V4-Pro"),
    "moonshot": ("https://api.moonshot.ai/v1", "kimi-k2.7-code"),
    "ollama": ("http://localhost:11434/v1", "deepseek-coder:1.3b"),
    "deepinfra": ("https://api.deepinfra.com/v1/openai", "deepseek-ai/DeepSeek-V4-Pro"),
    "stepfun": ("https://api.stepfun.ai/v1", "step-3.7-flash"),
    "siliconflow-cn": ("https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V4-Pro"),
}


class TestRegistryCompleteness(unittest.TestCase):
    def test_all_new_providers_registered(self):
        self.assertEqual(EXPECTED_NEW_PROVIDERS, set(SPECS_BY_ID.keys()))

    def test_new_providers_surface_in_provider_info(self):
        for pid in EXPECTED_NEW_PROVIDERS:
            self.assertIn(pid, PROVIDER_INFO, f"{pid} missing from PROVIDER_INFO")
            info = PROVIDER_INFO[pid]
            self.assertTrue(info["label"])
            self.assertTrue(info["default_base_url"])
            self.assertTrue(info["default_model"])
            self.assertTrue(info["available_models"])

    def test_defaults_match_vendor_published(self):
        for pid, (base_url, model) in VENDOR_DEFAULTS.items():
            spec = SPECS_BY_ID[pid]
            self.assertEqual(spec.default_base_url, base_url, pid)
            self.assertEqual(spec.default_model, model, pid)

    def test_default_model_is_in_available_models(self):
        for spec in SPECS_BY_ID.values():
            self.assertIn(spec.default_model, spec.available_models, spec.id)

    def test_hand_written_providers_not_shadowed(self):
        # The registry must hold only the *new* providers — never override the
        # bespoke hand-written ones (deepseek cache logic, zai GLM aliasing …).
        for pid in ("anthropic", "openai", "deepseek", "zai", "minimax", "openrouter", "gemini"):
            self.assertNotIn(pid, SPECS_BY_ID, f"{pid} should keep its hand-written class")


class TestGeneratedClass(unittest.TestCase):
    def test_build_returns_openai_compatible_subclass(self):
        cls = build_provider_class("together")
        self.assertTrue(issubclass(cls, OpenAICompatibleProvider))

    def test_defaults_applied(self):
        provider = build_provider_class("together")(api_key="k")
        self.assertEqual(provider.base_url, "https://api.together.xyz/v1")
        self.assertEqual(provider.model, "deepseek-ai/DeepSeek-V4-Pro")

    def test_explicit_overrides_win(self):
        provider = build_provider_class("together")(
            api_key="k", base_url="http://proxy/v1", model="custom-model"
        )
        self.assertEqual(provider.base_url, "http://proxy/v1")
        self.assertEqual(provider.model, "custom-model")

    def test_available_models(self):
        provider = build_provider_class("nvidia-nim")(api_key="k")
        self.assertEqual(
            provider.get_available_models(),
            ["deepseek-ai/deepseek-v4-pro", "deepseek-ai/deepseek-v4-flash"],
        )

    def test_class_identity_is_cached(self):
        self.assertIs(build_provider_class("together"), build_provider_class("together"))
        self.assertIs(get_provider_class("together"), build_provider_class("together"))

    def test_generated_class_name(self):
        self.assertEqual(build_provider_class("nvidia-nim").__name__, "NvidiaNimProvider")


class TestProviderResolution(unittest.TestCase):
    def test_get_provider_class_by_canonical_id(self):
        self.assertEqual(get_provider_class("moonshot").__name__, "MoonshotProvider")

    def test_get_provider_class_via_alias(self):
        # Aliases resolve to the canonical provider class.
        self.assertIs(get_provider_class("nim"), get_provider_class("nvidia-nim"))
        self.assertIs(get_provider_class("kimi"), get_provider_class("moonshot"))
        self.assertIs(get_provider_class("hf"), get_provider_class("huggingface"))
        self.assertIs(get_provider_class("deep-infra"), get_provider_class("deepinfra"))

    def test_canonical_provider_name(self):
        self.assertEqual(canonical_provider_name("nim"), "nvidia-nim")
        self.assertEqual(canonical_provider_name("together-ai"), "together")
        self.assertEqual(canonical_provider_name("together"), "together")  # passthrough

    def test_get_provider_info_via_alias(self):
        info = get_provider_info("kimi")
        self.assertEqual(info["default_model"], "kimi-k2.7-code")


class TestApiKeyResolution(unittest.TestCase):
    def test_config_key_wins(self):
        self.assertEqual(
            resolve_api_key("together", {"api_key": "sk-config"}), "sk-config"
        )

    def test_env_fallback_when_config_empty(self):
        with patch.dict("os.environ", {"TOGETHER_API_KEY": "sk-env"}, clear=False):
            self.assertEqual(resolve_api_key("together", {"api_key": ""}), "sk-env")

    def test_env_fallback_respects_candidate_order(self):
        # nvidia-nim accepts DEEPSEEK_API_KEY as a fallback candidate.
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-ds"}, clear=False):
            self.assertEqual(resolve_api_key("nvidia-nim", {"api_key": ""}), "sk-ds")

    def test_returns_empty_when_nothing_found(self):
        with patch("src.secret_store.get_secret", return_value=None):
            self.assertEqual(resolve_api_key("together", {"api_key": ""}), "")

    def test_builtin_provider_env_vars(self):
        self.assertEqual(provider_env_vars("anthropic"), ("ANTHROPIC_API_KEY",))
        self.assertEqual(provider_env_vars("zai"), ("ZAI_API_KEY", "Z_AI_API_KEY"))

    def test_spec_provider_env_vars(self):
        self.assertEqual(
            provider_env_vars("nvidia-nim"),
            ("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY", "DEEPSEEK_API_KEY"),
        )


class TestKeylessLocalProviders(unittest.TestCase):
    def test_local_providers_do_not_require_key(self):
        for pid in ("ollama", "vllm", "sglang"):
            self.assertFalse(provider_requires_api_key(pid), pid)

    def test_remote_providers_require_key(self):
        for pid in ("together", "moonshot", "nvidia-nim"):
            self.assertTrue(provider_requires_api_key(pid), pid)

    @patch("openai.OpenAI")  # deferred import in _create_client → patch at source
    def test_empty_key_becomes_placeholder(self, mock_openai):
        # A keyless local provider must not pass an empty key to the SDK (which
        # would silently fall through to the OPENAI_API_KEY env lookup).
        provider = build_provider_class("ollama")(api_key="")
        provider._create_client()
        kwargs = mock_openai.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "EMPTY")
        self.assertEqual(kwargs["base_url"], "http://localhost:11434/v1")


class TestSpecProviderChat(unittest.TestCase):
    @patch("openai.OpenAI")  # deferred import in _create_client → patch at source
    def test_chat_uses_openai_compatible_path(self, mock_openai):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hi from Together!"
        mock_response.choices[0].message.reasoning_content = None
        mock_response.choices[0].message.tool_calls = None
        mock_response.model = "deepseek-ai/DeepSeek-V4-Pro"
        mock_response.usage = MagicMock(
            prompt_tokens=7, completion_tokens=3, total_tokens=10
        )
        mock_response.choices[0].finish_reason = "stop"
        mock_client.chat.completions.create.return_value = mock_response
        # OpenAICompatibleProvider.client wraps the SDK client via with_options().
        mock_client.with_options.return_value = mock_client
        mock_openai.return_value = mock_client

        provider = build_provider_class("together")(api_key="sk-test")
        response = provider.chat([ChatMessage(role="user", content="Hi")])

        self.assertEqual(response.content, "Hi from Together!")
        self.assertEqual(response.usage["total_tokens"], 10)
        # The configured base URL reached the SDK client.
        self.assertEqual(
            mock_openai.call_args.kwargs["base_url"], "https://api.together.xyz/v1"
        )


class TestColdStartImports(unittest.TestCase):
    def test_openai_not_imported_at_cold_start(self):
        """`openai` (hundreds of submodules) must stay OFF the agent-server
        cold-start import path — it's deferred to first client creation in
        ``openai_compatible_specs._create_client``. Eagerly importing it again
        would re-add ~300ms (and a lot of cold-cache disk I/O) to launch, which
        is the window users feel as first-keystroke lag.
        """
        import os
        import subprocess
        import sys

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = "import sys; import src.cli; raise SystemExit(1 if 'openai' in sys.modules else 0)"
        result = subprocess.run([sys.executable, "-c", code], cwd=repo, capture_output=True)
        self.assertEqual(
            result.returncode,
            0,
            "openai must not be imported when src.cli loads (keep it lazy)",
        )


if __name__ == "__main__":
    unittest.main()
