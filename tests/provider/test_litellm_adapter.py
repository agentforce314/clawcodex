"""Tests for the decoupled LiteLLM provider extension."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from extensions.providers_ext import LiteLLMProvider as ExtensionLiteLLMProvider
from extensions.providers_ext import create_litellm_provider as extension_create_litellm_provider
from extensions.providers_ext import is_litellm_available as extension_is_litellm_available
from src.providers._litellm_adapter import (
    LiteLLMProvider,
    create_litellm_provider,
    is_litellm_available,
)


class FakeLiteLLM:
    model_list = ["openai/gpt-4o", "anthropic/claude-sonnet-4-6"]

    def __init__(self, response):
        self.response = response
        self.calls = []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _message_response(**overrides):
    response = {
        "model": "openai/gpt-4o",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "choices": [
            {
                "message": {
                    "content": "Hello!",
                    "reasoning_content": "Thinking...",
                },
                "finish_reason": "stop",
            }
        ],
    }
    response.update(overrides)
    return response


class TestLiteLLMAvailable:
    def test_litellm_is_available(self):
        assert is_litellm_available() is True


class TestExtensionImportPath:
    def test_extension_exports_match_compatibility_shim(self):
        assert ExtensionLiteLLMProvider is LiteLLMProvider
        assert extension_create_litellm_provider is create_litellm_provider
        assert extension_is_litellm_available is is_litellm_available


class TestLiteLLMProvider:
    def test_provider_initialization(self):
        provider = LiteLLMProvider(
            api_key="test-key",
            base_url="https://api.example.com",
            model="gpt-4o",
            provider_name="openai",
        )
        assert provider.api_key == "test-key"
        assert provider.base_url == "https://api.example.com"
        assert provider.model == "gpt-4o"
        assert provider.provider_name == "openai"

    def test_provider_default_values(self):
        provider = LiteLLMProvider(api_key="test-key")
        assert provider.api_key == "test-key"
        assert provider.base_url is None
        assert provider.model is None
        assert provider.provider_name == "openai"

    def test_get_model_from_kwargs(self):
        provider = LiteLLMProvider(api_key="test-key", model="gpt-4o")
        model = provider._get_model(model="gpt-4o-mini")
        assert model == "gpt-4o-mini"

    def test_get_model_from_self(self):
        provider = LiteLLMProvider(api_key="test-key", model="gpt-4o")
        model = provider._get_model()
        assert model == "gpt-4o"

    def test_litellm_model_adds_provider_prefix(self):
        provider = LiteLLMProvider(api_key="test-key", model="gpt-4o", provider_name="openai")
        assert provider._get_litellm_model() == "openai/gpt-4o"

    def test_litellm_model_keeps_existing_provider_prefix(self):
        provider = LiteLLMProvider(
            api_key="test-key",
            model="anthropic/claude-sonnet-4-6",
            provider_name="anthropic",
        )
        assert provider._get_litellm_model() == "anthropic/claude-sonnet-4-6"


class TestCreateLiteLLMProvider:
    def test_create_provider_anthropic(self):
        provider = create_litellm_provider(
            provider_name="anthropic",
            api_key="sk-ant-key",
            model="claude-sonnet-4-6",
        )
        assert provider.provider_name == "anthropic"
        assert provider.model == "claude-sonnet-4-6"

    def test_create_provider_openai(self):
        provider = create_litellm_provider(
            provider_name="openai",
            api_key="sk-key",
            model="gpt-4o",
        )
        assert provider.provider_name == "openai"
        assert provider.model == "gpt-4o"

    def test_create_provider_with_base_url(self):
        provider = create_litellm_provider(
            provider_name="openai",
            api_key="sk-key",
            base_url="https://custom.endpoint.com/v1",
            model="gpt-4o",
        )
        assert provider.base_url == "https://custom.endpoint.com/v1"


class TestPrepareMessages:
    def test_prepare_dict_messages(self):
        provider = LiteLLMProvider(api_key="test")
        messages = [{"role": "user", "content": "hello"}]
        prepared = provider._prepare_messages(messages)
        assert prepared == messages

    def test_prepare_chat_message_objects(self):
        from src.providers.base import ChatMessage

        provider = LiteLLMProvider(api_key="test")
        messages = [ChatMessage(role="user", content="hello")]
        prepared = provider._prepare_messages(messages)
        assert prepared == [{"role": "user", "content": "hello"}]

    def test_prepare_anthropic_tool_use_for_litellm(self):
        provider = LiteLLMProvider(api_key="test")
        prepared = provider._prepare_messages([
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call_1", "name": "Read", "input": {"file_path": "README.md"}}
                ],
            }
        ])
        assert prepared[0]["tool_calls"][0]["function"]["name"] == "Read"


class TestChatCompletion:
    def test_chat_passes_external_api_parameters(self, monkeypatch):
        fake_litellm = FakeLiteLLM(_message_response())
        monkeypatch.setattr("extensions.providers_ext.litellm_provider._load_litellm", lambda: fake_litellm)

        provider = LiteLLMProvider(
            api_key="sk-key",
            base_url="https://litellm.example.com/v1",
            model="gpt-4o",
            provider_name="openai",
        )
        response = provider.chat([{"role": "user", "content": "Hi"}], temperature=0)

        assert response.content == "Hello!"
        assert response.reasoning_content == "Thinking..."
        assert response.usage["total_tokens"] == 15
        assert fake_litellm.calls == [
            {
                "model": "openai/gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
                "temperature": 0,
                "api_key": "sk-key",
                "base_url": "https://litellm.example.com/v1",
            }
        ]

    def test_chat_converts_tools_and_tool_calls(self, monkeypatch):
        fake_litellm = FakeLiteLLM(_message_response(
            choices=[
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {"name": "Read", "arguments": '{"file_path":"README.md"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        ))
        monkeypatch.setattr("extensions.providers_ext.litellm_provider._load_litellm", lambda: fake_litellm)

        provider = LiteLLMProvider(api_key="sk-key", model="gpt-4o", provider_name="openai")
        response = provider.chat(
            [{"role": "user", "content": "Read README"}],
            tools=[{"name": "Read", "description": "", "input_schema": {"type": "object"}}],
        )

        assert fake_litellm.calls[0]["tools"][0]["function"]["name"] == "Read"
        assert response.finish_reason == "tool_calls"
        assert response.tool_uses == [
            {"id": "call_1", "name": "Read", "input": {"file_path": "README.md"}}
        ]

    def test_chat_accepts_object_response(self, monkeypatch):
        response = SimpleNamespace(
            model="openai/gpt-4o",
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="object", reasoning_content=None, tool_calls=None),
                    finish_reason="stop",
                )
            ],
        )
        fake_litellm = FakeLiteLLM(response)
        monkeypatch.setattr("extensions.providers_ext.litellm_provider._load_litellm", lambda: fake_litellm)

        provider = LiteLLMProvider(api_key="sk-key", model="gpt-4o")
        result = provider.chat([{"role": "user", "content": "Hi"}])

        assert result.content == "object"
        assert result.usage == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}


class TestStreaming:
    def test_chat_stream_yields_text_chunks(self, monkeypatch):
        fake_litellm = FakeLiteLLM([
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
        ])
        monkeypatch.setattr("extensions.providers_ext.litellm_provider._load_litellm", lambda: fake_litellm)

        provider = LiteLLMProvider(api_key="sk-key", model="gpt-4o")
        assert list(provider.chat_stream([{"role": "user", "content": "Hi"}])) == ["Hel", "lo"]
        assert fake_litellm.calls[0]["stream"] is True

    def test_chat_stream_response_rebuilds_content_reasoning_tool_calls_and_usage(self, monkeypatch):
        tool_call_delta = SimpleNamespace(
            index=0,
            id="call_1",
            function=SimpleNamespace(name="Read", arguments='{"file_path":"README.md"}'),
        )
        fake_litellm = FakeLiteLLM([
            SimpleNamespace(
                model="openai/gpt-4o",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(content="Hel", reasoning_content="Think", tool_calls=[]),
                    )
                ],
            ),
            SimpleNamespace(
                model="openai/gpt-4o",
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        delta=SimpleNamespace(content="lo", reasoning_content=None, tool_calls=[tool_call_delta]),
                    )
                ],
            ),
        ])
        monkeypatch.setattr("extensions.providers_ext.litellm_provider._load_litellm", lambda: fake_litellm)

        provider = LiteLLMProvider(api_key="sk-key", model="gpt-4o")
        text_chunks: list[str] = []
        thinking_chunks: list[str] = []
        response = provider.chat_stream_response(
            [{"role": "user", "content": "Hi"}],
            on_text_chunk=text_chunks.append,
            on_thinking_chunk=thinking_chunks.append,
        )

        assert text_chunks == ["Hel", "lo"]
        assert thinking_chunks == ["Think"]
        assert response.content == "Hello"
        assert response.reasoning_content == "Think"
        assert response.finish_reason == "tool_calls"
        assert response.usage["total_tokens"] == 15
        assert response.tool_uses == [
            {"id": "call_1", "name": "Read", "input": {"file_path": "README.md"}}
        ]
        assert fake_litellm.calls[0]["stream"] is True
        assert fake_litellm.calls[0]["stream_options"] == {"include_usage": True}


class TestGetAvailableModels:
    def test_get_available_models(self):
        provider = LiteLLMProvider(api_key="test")
        models = provider.get_available_models()
        assert isinstance(models, list)
        assert len(models) > 0


class TestBackwardCompatibility:
    def test_returns_litellm_provider(self):
        provider = create_litellm_provider(
            provider_name="openai",
            api_key="test",
        )
        assert isinstance(provider, LiteLLMProvider)

    def test_inherits_from_base_provider(self):
        from src.providers.base import BaseProvider

        provider = LiteLLMProvider(api_key="test")
        assert isinstance(provider, BaseProvider)

    def test_missing_litellm_dependency_raises_clear_error(self, monkeypatch):
        monkeypatch.setattr("extensions.providers_ext.litellm_provider.is_litellm_available", lambda: False)
        provider = LiteLLMProvider(api_key="test", model="gpt-4o")

        with pytest.raises(RuntimeError, match="LiteLLM is not installed"):
            provider.chat([{"role": "user", "content": "Hi"}])
