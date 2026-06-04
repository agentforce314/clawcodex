from __future__ import annotations

import pytest

from clawcodex_ext.cli.model_cmd.errors import ProviderMismatchError, UnknownModelError
from clawcodex_ext.cli.model_cmd.registry import (
    ModelRegistry,
    register_discovery_hook,
    _DISCOVERY_HOOKS,
)
from clawcodex_ext.cli.provider_cmd.errors import UnknownProviderError


def test_model_registry_lists_and_validates_known_providers() -> None:
    registry = ModelRegistry()

    assert "anthropic" in registry.provider_names()
    assert registry.validate_provider("glm") == "glm"
    assert registry.provider_default_model("glm") == "zai/glm-5"
    assert "zai/glm-4" in registry.available_models("glm")


def test_model_registry_rejects_unknown_provider() -> None:
    with pytest.raises(UnknownProviderError):
        ModelRegistry().validate_provider("missing")


def test_model_registry_validates_model_provider_pair() -> None:
    registry = ModelRegistry()

    assert registry.validate_model("zai/glm-4", "glm") == "zai/glm-4"
    with pytest.raises(ProviderMismatchError):
        registry.validate_model("zai/glm-4", "anthropic")
    with pytest.raises(UnknownModelError):
        registry.validate_model("not-a-real-model", "glm")


def test_model_registry_infers_provider_for_unique_model() -> None:
    assert ModelRegistry().infer_provider_for_model("zai/glm-4") == "glm"


def test_dynamic_discovery_hook_adds_models() -> None:
    """Global register_discovery_hook affects ModelRegistry() default."""
    # Ensure global hooks dict is clean for this test
    _DISCOVERY_HOOKS.clear()
    registry = ModelRegistry(provider_info={"openai-codex": {"available_models": ["gpt-5.3-codex"], "default_model": "gpt-5.3-codex", "label": "test", "default_base_url": ""}}, discovery_hooks=None)

    assert "gpt-9999" not in registry.available_models("openai-codex")

    register_discovery_hook("openai-codex", lambda: ["gpt-9999"])

    # A new ModelRegistry with default hooks reads the global registry
    registry2 = ModelRegistry(provider_info={"openai-codex": {"available_models": ["gpt-5.3-codex"], "default_model": "gpt-5.3-codex", "label": "test", "default_base_url": ""}}, discovery_hooks=None)
    assert "gpt-9999" in registry2.available_models("openai-codex")

    # Existing instances that share the global _DISCOVERY_HOOKS see the update too
    assert "gpt-9999" in registry.available_models("openai-codex")


def test_dynamic_discovery_hook_isolation() -> None:
    """Custom discovery_hooks on ModelRegistry isolates test from globals."""
    hooks: dict[str, list[Callable[[], list[str]]]] = {}
    registry = ModelRegistry(discovery_hooks=hooks)

    assert "gpt-7777" not in registry.available_models("openai-codex")

    hooks.setdefault("openai-codex", []).append(lambda: ["gpt-7777"])

    assert "gpt-7777" in registry.available_models("openai-codex")
    # gpt-5.5 should also be there (from static list)
    assert "gpt-5.3-codex" in registry.available_models("openai-codex")


def test_dynamic_discovery_hook_failure_silent() -> None:
    """A hook that raises is silently swallowed."""
    hooks: dict[str, list[Callable[[], list[str]]]] = {}
    registry = ModelRegistry(discovery_hooks=hooks)

    def _broken_hook() -> list[str]:
        raise RuntimeError("API unavailable")

    hooks.setdefault("openai-codex", []).append(_broken_hook)

    # Should return static list without raising
    models = registry.available_models("openai-codex")
    assert "gpt-5.3-codex" in models


def test_dynamic_discovery_hook_no_duplicates() -> None:
    """Discovered models that are already in the static list are not duplicated."""
    hooks: dict[str, list[Callable[[], list[str]]]] = {}
    registry = ModelRegistry(discovery_hooks=hooks)

    # Return a model that's already in the static list
    hooks.setdefault("openai-codex", []).append(lambda: ["gpt-5.3-codex"])

    models = registry.available_models("openai-codex")
    assert models.count("gpt-5.3-codex") == 1


def test_dynamic_discovery_hook_validate_model() -> None:
    """validate_model respects dynamically discovered models."""
    hooks: dict[str, list[Callable[[], list[str]]]] = {}
    registry = ModelRegistry(discovery_hooks=hooks)

    with pytest.raises(UnknownModelError):
        registry.validate_model("gpt-9999", "openai-codex")

    hooks.setdefault("openai-codex", []).append(lambda: ["gpt-9999"])

    assert registry.validate_model("gpt-9999", "openai-codex") == "gpt-9999"


def test_dynamic_discovery_hook_infer_provider() -> None:
    """infer_provider_for_model matches dynamically discovered models."""
    hooks: dict[str, list[Callable[[], list[str]]]] = {}
    registry = ModelRegistry(discovery_hooks=hooks)

    hooks.setdefault("anthropic", []).append(lambda: ["claude-4-21"])
    hooks.setdefault("openai", []).append(lambda: ["gpt-5.9"])

    assert registry.infer_provider_for_model("claude-4-21") == "anthropic"
    assert registry.infer_provider_for_model("gpt-5.9") == "openai"


# ---- register_discovery_hook ----
# Import here to avoid name collision with hook installation
Callable = __import__("collections.abc", fromlist=["Callable"]).Callable
