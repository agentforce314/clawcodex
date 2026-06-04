from __future__ import annotations

import pytest

from clawcodex_ext.cli.model_cmd.store import ModelStore
from clawcodex_ext.cli.provider_cmd.errors import UnsupportedScopeError as ProviderUnsupportedScopeError
from clawcodex_ext.cli.model_cmd.errors import UnsupportedScopeError as ModelUnsupportedScopeError


def test_model_store_sets_default_provider(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("src.config.set_default_provider", calls.append)

    ModelStore().set_default_provider("glm")

    assert calls == ["glm"]


def test_model_store_rejects_project_provider_scope() -> None:
    with pytest.raises(ProviderUnsupportedScopeError):
        ModelStore().set_default_provider("glm", scope="project")


def test_model_store_sets_default_model_without_losing_existing_config(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "src.config.get_provider_config",
        lambda provider: {"api_key": "secret", "base_url": "https://custom.example"},
    )

    def fake_set_api_key(provider: str, **kwargs) -> None:
        calls.append({"provider": provider, **kwargs})

    monkeypatch.setattr("src.config.set_api_key", fake_set_api_key)

    ModelStore().set_default_model("glm", "zai/glm-4")

    assert calls == [
        {
            "provider": "glm",
            "api_key": "secret",
            "base_url": "https://custom.example",
            "default_model": "zai/glm-4",
        }
    ]


def test_model_store_rejects_project_model_scope() -> None:
    with pytest.raises(ModelUnsupportedScopeError):
        ModelStore().set_default_model("glm", "zai/glm-4", scope="project")
