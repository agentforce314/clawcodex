from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

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


def test_chat_uses_codex_responses_api(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class FakeChatCompletions:
        def create(self, **kwargs):
            raise AssertionError("chat completions must not be used for openai-codex")

    class FakeResponses:
        def create(self, **kwargs):
            requests.append(kwargs)
            return iter([
                SimpleNamespace(type="response.output_text.delta", delta="codex "),
                SimpleNamespace(type="response.output_text.delta", delta="reply"),
                SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(
                        output=[
                            SimpleNamespace(
                                type="message",
                                content=[SimpleNamespace(type="output_text", text="codex reply")],
                            )
                        ],
                        usage=SimpleNamespace(input_tokens=5, output_tokens=3, total_tokens=8),
                        status="completed",
                        model="gpt-5.3-codex",
                    ),
                ),
            ])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeChatCompletions())
            self.responses = FakeResponses()

    monkeypatch.setattr("src.providers.openai_codex_provider.OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        "src.providers.openai_codex_provider.resolve_codex_runtime_credentials",
        lambda *args, **kwargs: FakeCredentials(api_key="access-token"),
    )

    response = OpenAICodexProvider(model="gpt-5.3-codex").chat([
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hello"},
    ])

    assert response.content == "codex reply"
    assert response.model == "gpt-5.3-codex"
    assert response.usage == {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8}
    assert requests == [
        {
            "model": "gpt-5.3-codex",
            "input": [{"role": "user", "content": "hello"}],
            "store": False,
            "stream": True,
            "instructions": "You are helpful.",
        }
    ]


def test_chat_filters_internal_runtime_kwargs(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class FakeResponses:
        def create(self, **kwargs):
            requests.append(kwargs)
            return iter([
                SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(
                        output=[
                            SimpleNamespace(
                                type="message",
                                content=[SimpleNamespace(type="output_text", text="ok")],
                            )
                        ],
                        usage=None,
                        status="completed",
                        model="gpt-5.3-codex",
                    ),
                )
            ])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()

    monkeypatch.setattr("src.providers.openai_codex_provider.OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        "src.providers.openai_codex_provider.resolve_codex_runtime_credentials",
        lambda *args, **kwargs: FakeCredentials(api_key="access-token"),
    )

    OpenAICodexProvider(model="gpt-5.3-codex").chat(
        [{"role": "user", "content": "hello"}],
        abort_signal=object(),
        temperature=0,
    )

    assert "abort_signal" not in requests[0]
    assert requests[0]["stream"] is True
    assert requests[0]["temperature"] == 0


def test_chat_parses_codex_responses_function_calls(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class FakeResponses:
        def create(self, **kwargs):
            requests.append(kwargs)
            return iter([
                SimpleNamespace(
                    type="response.output_item.done",
                    item=SimpleNamespace(
                        type="function_call",
                        id="fc_1",
                        call_id="call_1",
                        name="Bash",
                        arguments='{"command":"pwd"}',
                    ),
                ),
                SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(
                        output=[
                            SimpleNamespace(
                                type="function_call",
                                id="fc_1",
                                call_id="call_1",
                                name="Bash",
                                arguments='{"command":"pwd"}',
                            )
                        ],
                        usage=None,
                        status="requires_action",
                        model="gpt-5.3-codex",
                    ),
                ),
            ])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()

    monkeypatch.setattr("src.providers.openai_codex_provider.OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        "src.providers.openai_codex_provider.resolve_codex_runtime_credentials",
        lambda *args, **kwargs: FakeCredentials(api_key="access-token"),
    )

    response = OpenAICodexProvider(model="gpt-5.3-codex").chat(
        [{"role": "user", "content": "run pwd"}],
        tools=[
            {
                "name": "Bash",
                "description": "Run shell commands",
                "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            }
        ],
    )

    assert response.content == ""
    assert response.tool_uses == [{"id": "call_1", "name": "Bash", "input": {"command": "pwd"}}]
    assert requests[0]["tools"] == [
        {
            "type": "function",
            "name": "Bash",
            "description": "Run shell commands",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            "strict": False,
        }
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
