from __future__ import annotations

import pytest

from clawcodex_ext.cli.model_cmd.errors import ProviderMismatchError, UnknownModelError
from clawcodex_ext.cli.model_cmd.registry import ModelRegistry
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
