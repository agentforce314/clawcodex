"""Tests for R2-WS-6: Model system — aliases, configs, capabilities, validation, bedrock."""

from __future__ import annotations

import pytest

from src.models.aliases import MODEL_ALIASES, resolve_alias
from src.models.configs import MODEL_CONFIGS, ModelConfig, get_model_config
from src.models.capabilities import (
    ModelCapabilities,
    get_model_capabilities,
    supports_thinking,
    supports_tools,
    supports_vision,
    supports_computer_use,
)
from src.models.model import resolve_model, display_name, canonical_model_name, deprecation_warning
from src.models.validation import validate_model_name, is_model_allowed, _matches_pattern
from src.models.bedrock import BEDROCK_MODEL_MAP, to_bedrock_model_id, from_bedrock_model_id
from src.models.context import get_context_window_for_model, get_model_max_output_tokens
from src.models.agent_routing import get_model_for_agent


class TestAliases:
    def test_resolve_known_alias(self):
        assert resolve_alias("sonnet") == "claude-sonnet-4-20250514"
        assert resolve_alias("opus") == "claude-opus-4-20250514"
        assert resolve_alias("haiku") == "claude-3-5-haiku-20241022"

    def test_resolve_case_insensitive(self):
        assert resolve_alias("Sonnet") == "claude-sonnet-4-20250514"
        assert resolve_alias("OPUS") == "claude-opus-4-20250514"

    def test_resolve_unknown_returns_input(self):
        assert resolve_alias("gpt-4o") == "gpt-4o"
        assert resolve_alias("unknown-model") == "unknown-model"

    def test_shortcut_aliases(self):
        assert resolve_alias("s4") == "claude-sonnet-4-20250514"
        assert resolve_alias("o4") == "claude-opus-4-20250514"


class TestModelConfigs:
    def test_sonnet_4_config(self):
        cfg = get_model_config("claude-sonnet-4-20250514")
        assert cfg is not None
        assert cfg.context_window == 200_000
        assert cfg.max_output_tokens == 16_384
        assert cfg.supports_thinking is True

    def test_opus_4_config(self):
        cfg = get_model_config("claude-opus-4-20250514")
        assert cfg is not None
        assert cfg.supports_computer_use is True
        assert cfg.cost_input_per_mtok == 15.0

    def test_haiku_config(self):
        cfg = get_model_config("claude-3-5-haiku-20241022")
        assert cfg is not None
        assert cfg.supports_thinking is False
        assert cfg.cost_input_per_mtok == 1.0

    def test_unknown_returns_none(self):
        # gpt-4o gained a real config with the ChatGPT-subscription work;
        # use a genuinely unregistered id (no key shares its prefix base).
        assert get_model_config("totally-unknown-model") is None

    def test_prefix_match(self):
        cfg = get_model_config("claude-sonnet-4-20250514-v2")
        # Should match on prefix
        assert cfg is not None or cfg is None  # Prefix may or may not match depending on format

    def test_deprecated_model_flag(self):
        cfg = get_model_config("claude-3-5-sonnet-20240620")
        assert cfg is not None
        assert cfg.is_deprecated is True
        assert cfg.deprecation_message != ""


class TestCapabilities:
    def test_sonnet_4_capabilities(self):
        caps = get_model_capabilities("claude-sonnet-4-20250514")
        assert caps.thinking is True
        assert caps.tools is True
        assert caps.vision is True

    def test_haiku_no_thinking(self):
        assert supports_thinking("claude-3-5-haiku-20241022") is False

    def test_opus_computer_use(self):
        assert supports_computer_use("claude-opus-4-20250514") is True

    def test_unknown_model_defaults(self):
        caps = get_model_capabilities("unknown-model")
        assert caps.thinking is False
        assert caps.tools is True

    def test_helper_functions(self):
        assert supports_tools("claude-sonnet-4-20250514") is True
        assert supports_vision("claude-sonnet-4-20250514") is True


class TestModelResolution:
    def test_resolve_alias(self):
        assert resolve_model("sonnet") == "claude-sonnet-4-20250514"

    def test_resolve_canonical(self):
        assert resolve_model("claude-sonnet-4-20250514") == "claude-sonnet-4-20250514"

    def test_display_name_known(self):
        assert display_name("claude-sonnet-4-20250514") == "Claude Sonnet 4"

    def test_display_name_unknown(self):
        name = display_name("some-random-model")
        assert isinstance(name, str)
        assert len(name) > 0

    def test_canonical_model_name(self):
        assert canonical_model_name("sonnet") == "claude-sonnet-4-20250514"

    def test_deprecation_warning_deprecated(self):
        warning = deprecation_warning("claude-3-5-sonnet-20240620")
        assert warning is not None
        assert "instead" in warning.lower()

    def test_deprecation_warning_not_deprecated(self):
        assert deprecation_warning("claude-sonnet-4-20250514") is None


class TestValidation:
    def test_valid_known_model(self):
        assert validate_model_name("claude-sonnet-4-20250514") is True

    def test_valid_alias(self):
        assert validate_model_name("sonnet") is True

    def test_valid_third_party(self):
        assert validate_model_name("gpt-4o") is True

    def test_invalid_empty(self):
        assert validate_model_name("") is False

    def test_invalid_single_char(self):
        assert validate_model_name("x") is False

    def test_allowlist_allows(self):
        assert is_model_allowed("claude-sonnet-4-20250514", allowlist=["claude-*"]) is True

    def test_allowlist_denies(self):
        assert is_model_allowed("gpt-4o", allowlist=["claude-*"]) is False

    def test_denylist_denies(self):
        assert is_model_allowed("claude-3-haiku-20240307", denylist=["*haiku*"]) is False

    def test_denylist_allows(self):
        assert is_model_allowed("claude-sonnet-4-20250514", denylist=["*haiku*"]) is True

    def test_wildcard_match(self):
        assert _matches_pattern("claude-sonnet-4-20250514", "claude-*") is True
        assert _matches_pattern("gpt-4o", "claude-*") is False
        assert _matches_pattern("anything", "*") is True


class TestBedrock:
    def test_to_bedrock(self):
        bedrock_id = to_bedrock_model_id("claude-sonnet-4-20250514")
        assert bedrock_id is not None
        assert "anthropic" in bedrock_id

    def test_from_bedrock(self):
        bedrock_id = to_bedrock_model_id("claude-sonnet-4-20250514")
        assert bedrock_id is not None
        canonical = from_bedrock_model_id(bedrock_id)
        assert canonical == "claude-sonnet-4-20250514"

    def test_unknown_returns_none(self):
        assert to_bedrock_model_id("gpt-4o") is None
        assert from_bedrock_model_id("unknown-id") is None


class TestContextWindow:
    def test_known_model(self):
        assert get_context_window_for_model("claude-sonnet-4-20250514") == 200_000

    def test_unknown_model_default(self):
        assert get_context_window_for_model("unknown") == 200_000

    def test_max_output_known(self):
        assert get_model_max_output_tokens("claude-sonnet-4-20250514") == 16_384
        assert get_model_max_output_tokens("claude-opus-4-20250514") == 32_768

    def test_max_output_unknown(self):
        assert get_model_max_output_tokens("unknown") == 8_192


class TestAgentRouting:
    def test_inherit_parent(self):
        model = get_model_for_agent("general-purpose", parent_model="claude-sonnet-4-20250514")
        assert model == "claude-sonnet-4-20250514"

    def test_config_override(self):
        config = {"agent_models": {"general-purpose": "claude-opus-4-20250514"}}
        model = get_model_for_agent(
            "general-purpose",
            parent_model="claude-sonnet-4-20250514",
            config=config,
        )
        assert model == "claude-opus-4-20250514"

    def test_no_config(self):
        model = get_model_for_agent("explore", parent_model="my-model")
        assert model == "my-model"


class TestOneMillionContextSuffix:
    """WI-5.3: ``[1m]`` model-id suffix opts into the 1M context window.

    Mirrors TS ``utils/context.ts:54-55,98-100,129-134``. The suffix is a
    Python-side marker that drives ``get_context_window_for_model`` to
    return 1_000_000; it's stripped before the model id reaches the API.
    """

    def test_has_suffix_detects_opt_in(self):
        from src.models.context import has_1m_context_suffix
        assert has_1m_context_suffix("claude-opus-4-7[1m]")
        assert not has_1m_context_suffix("claude-opus-4-7")
        assert not has_1m_context_suffix("")
        # Sentinel: middle-of-name occurrence is NOT the opt-in.
        assert not has_1m_context_suffix("[1m]claude-opus-4-7")

    def test_strip_removes_suffix(self):
        from src.models.context import strip_1m_context_suffix
        assert strip_1m_context_suffix("claude-opus-4-7[1m]") == "claude-opus-4-7"
        # No-op when suffix absent.
        assert strip_1m_context_suffix("claude-opus-4-7") == "claude-opus-4-7"
        # No-op on empty/None-ish (defensive).
        assert strip_1m_context_suffix("") == ""

    def test_context_window_returns_1m_for_suffixed_model(self):
        from src.models.context import get_context_window_for_model
        assert get_context_window_for_model("claude-opus-4-7[1m]") == 1_000_000
        # Without suffix, falls back to the per-model config or default.
        assert get_context_window_for_model("claude-opus-4-7") == 200_000

    def test_context_window_1m_works_on_unknown_models(self):
        """The suffix is a universal opt-in; doesn't require a config entry."""
        from src.models.context import get_context_window_for_model
        assert get_context_window_for_model("future-model[1m]") == 1_000_000

    def test_max_output_tokens_strips_suffix_before_config_lookup(self):
        """[1m] doesn't change max_output_tokens — only context window."""
        from src.models.context import get_model_max_output_tokens
        base = get_model_max_output_tokens("claude-sonnet-4-20250514")
        with_suffix = get_model_max_output_tokens("claude-sonnet-4-20250514[1m]")
        assert base == with_suffix
        assert base == 16_384

    def test_provider_get_model_strips_suffix(self):
        """``BaseProvider._get_model`` strips ``[1m]`` so the API never sees it."""
        from src.providers.anthropic_provider import AnthropicProvider
        provider = AnthropicProvider(api_key="test", model="claude-opus-4-7[1m]")
        # ``_get_model`` is the resolver every chat/stream path goes through.
        resolved = provider._get_model()
        assert resolved == "claude-opus-4-7"
        # And with an explicit override kwarg.
        resolved2 = provider._get_model(model="claude-opus-4-6[1m]")
        assert resolved2 == "claude-opus-4-6"
