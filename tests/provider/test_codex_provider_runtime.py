from __future__ import annotations

import pytest

from src.auth.codex_oauth import CODEX_BASE_URL, CodexAuthError
from src.providers import AVAILABLE_PROVIDERS, PROVIDER_INFO, get_provider_class
from src.providers.openai_codex_provider import OpenAICodexProvider
from src.providers.runtime import build_provider_from_config


class FakeProvider:
    def __init__(self, api_key: str, base_url: str | None = None, model: str | None = None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model


class FakeCredentials:
    api_key = "oauth-access"
    base_url = CODEX_BASE_URL


def test_openai_codex_is_registered_as_first_class_provider() -> None:
    assert PROVIDER_INFO["openai-codex"]["label"] == "OpenAI Codex (ChatGPT OAuth)"
    assert PROVIDER_INFO["openai-codex"]["default_base_url"] == CODEX_BASE_URL
    assert PROVIDER_INFO["openai-codex"]["default_model"] == "gpt-5.3-codex"
    assert AVAILABLE_PROVIDERS["openai-codex"] == "OpenAI Codex (ChatGPT OAuth)"
    assert get_provider_class("openai-codex") is OpenAICodexProvider


def test_build_provider_from_config_allows_openai_codex_without_config_api_key(monkeypatch) -> None:
    created: list[dict[str, object]] = []

    def fake_create_provider(provider_name, **kwargs):
        created.append({"provider_name": provider_name, **kwargs})
        return FakeProvider(**kwargs)

    monkeypatch.setattr(
        "src.providers.runtime.get_provider_config",
        lambda provider_name: {
            "api_key": "",
            "base_url": "https://configured.example/codex",
            "default_model": "configured-model",
        },
    )
    monkeypatch.setattr(
        "src.providers.runtime.resolve_codex_runtime_credentials",
        lambda: FakeCredentials(),
    )
    monkeypatch.setattr("src.providers.runtime.create_provider", fake_create_provider)

    provider = build_provider_from_config("openai-codex")

    assert isinstance(provider, FakeProvider)
    assert provider.api_key == "oauth-access"
    assert provider.base_url == "https://configured.example/codex"
    assert provider.model == "configured-model"
    assert created == [
        {
            "provider_name": "openai-codex",
            "api_key": "oauth-access",
            "base_url": "https://configured.example/codex",
            "model": "configured-model",
        }
    ]


def test_build_provider_from_config_uses_codex_base_url_when_config_base_url_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.providers.runtime.get_provider_config",
        lambda provider_name: {"api_key": "", "default_model": "configured-model"},
    )
    monkeypatch.setattr(
        "src.providers.runtime.resolve_codex_runtime_credentials",
        lambda: FakeCredentials(),
    )
    monkeypatch.setattr(
        "src.providers.runtime.create_provider",
        lambda provider_name, **kwargs: FakeProvider(**kwargs),
    )

    provider = build_provider_from_config("openai-codex", model="override-model")

    assert provider.api_key == "oauth-access"
    assert provider.base_url == CODEX_BASE_URL
    assert provider.model == "override-model"


def test_build_provider_from_config_reports_codex_login_guidance(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.providers.runtime.get_provider_config",
        lambda provider_name: {"api_key": "", "default_model": "gpt-5.3-codex"},
    )

    def fake_resolve():
        raise CodexAuthError("missing", code="codex_auth_missing", relogin_required=True)

    monkeypatch.setattr("src.providers.runtime.resolve_codex_runtime_credentials", fake_resolve)

    with pytest.raises(RuntimeError) as exc_info:
        build_provider_from_config("openai-codex")

    assert "Run `clawcodex login` and select openai-codex" in str(exc_info.value)


def test_build_provider_from_config_still_requires_api_key_for_api_key_providers(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.providers.runtime.get_provider_config",
        lambda provider_name: {"api_key": "", "base_url": "https://api.example.com", "default_model": "model"},
    )

    with pytest.raises(RuntimeError) as exc_info:
        build_provider_from_config("openai")

    assert "API key for provider 'openai' is not configured" in str(exc_info.value)


def test_build_provider_from_config_constructs_api_key_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.providers.runtime.get_provider_config",
        lambda provider_name: {
            "api_key": "api-key",
            "base_url": "https://api.example.com",
            "default_model": "default-model",
        },
    )
    monkeypatch.setattr(
        "src.providers.runtime.create_provider",
        lambda provider_name, **kwargs: FakeProvider(**kwargs),
    )

    provider = build_provider_from_config("openai", model="override-model")

    assert provider.api_key == "api-key"
    assert provider.base_url == "https://api.example.com"
    assert provider.model == "override-model"
