from __future__ import annotations

from dataclasses import dataclass

from src.auth.codex_oauth import CODEX_BASE_URL
from src.providers.codex_models import CODEX_FALLBACK_MODELS
from src.providers.openai_codex_provider import OpenAICodexProvider


@dataclass
class FakeCredentials:
    api_key: str
    base_url: str = CODEX_BASE_URL
    provider: str = "openai-codex"
    source: str = "test"
    auth_mode: str = "chatgpt"
    last_refresh: float | None = None


def test_client_resolves_oauth_token_before_creation(monkeypatch) -> None:
    created: list[dict[str, object]] = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr("src.providers.openai_codex_provider.OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        "src.providers.openai_codex_provider.resolve_codex_runtime_credentials",
        lambda *args, **kwargs: FakeCredentials(api_key="oauth-access"),
    )

    provider = OpenAICodexProvider(api_key="stale", model="gpt-5.3-codex")
    client = provider.client

    assert isinstance(client, FakeOpenAI)
    assert provider.api_key == "oauth-access"
    assert created == [{"api_key": "oauth-access", "base_url": CODEX_BASE_URL}]


def test_client_is_recreated_when_access_token_changes(monkeypatch) -> None:
    created: list[dict[str, object]] = []
    credentials = [
        FakeCredentials(api_key="first"),
        FakeCredentials(api_key="first"),
        FakeCredentials(api_key="second"),
        FakeCredentials(api_key="second"),
    ]

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created.append(kwargs)

    def fake_resolve(*args, **kwargs):
        return credentials.pop(0) if credentials else FakeCredentials(api_key="second")

    monkeypatch.setattr("src.providers.openai_codex_provider.OpenAI", FakeOpenAI)
    monkeypatch.setattr("src.providers.openai_codex_provider.resolve_codex_runtime_credentials", fake_resolve)

    provider = OpenAICodexProvider()

    first_client = provider.client
    second_client = provider.client

    assert first_client is not second_client
    assert created == [
        {"api_key": "first", "base_url": CODEX_BASE_URL},
        {"api_key": "second", "base_url": CODEX_BASE_URL},
    ]


def test_get_available_models_uses_codex_model_discovery(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        "src.providers.openai_codex_provider.resolve_codex_runtime_credentials",
        lambda *args, **kwargs: FakeCredentials(api_key="access-token"),
    )
    monkeypatch.setattr(
        "src.providers.openai_codex_provider.get_codex_model_ids",
        lambda access_token: calls.append(access_token) or ["codex-model"],
    )

    assert OpenAICodexProvider().get_available_models() == ["codex-model"]
    assert calls == ["access-token"]


def test_get_available_models_falls_back_when_not_authenticated(monkeypatch) -> None:
    def fake_resolve(*args, **kwargs):
        raise RuntimeError("not authenticated")

    monkeypatch.setattr("src.providers.openai_codex_provider.resolve_codex_runtime_credentials", fake_resolve)

    assert OpenAICodexProvider().get_available_models() == CODEX_FALLBACK_MODELS
