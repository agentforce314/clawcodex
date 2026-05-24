"""Tests for LiteLLM adapter (Task #5)."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from src.providers._litellm_adapter import (
    LiteLLMProvider,
    create_litellm_provider,
    is_litellm_available,
)


class TestLiteLLMAvailable:
    def test_litellm_is_available(self):
        assert is_litellm_available() is True


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
        assert provider.provider_name == "openai"  # default

    def test_get_model_from_kwargs(self):
        provider = LiteLLMProvider(api_key="test-key", model="gpt-4o")
        model = provider._get_model(model="gpt-4o-mini")
        assert model == "gpt-4o-mini"

    def test_get_model_from_self(self):
        provider = LiteLLMProvider(api_key="test-key", model="gpt-4o")
        model = provider._get_model()
        assert model == "gpt-4o"


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


class TestGetAvailableModels:
    def test_get_available_models(self):
        provider = LiteLLMProvider(api_key="test")
        models = provider.get_available_models()
        # LiteLLM should return a non-empty list
        assert isinstance(models, list)
        assert len(models) > 0


class TestBackwardCompatibility:
    def test_returns_litellm_provider(self):
        """Ensure factory returns LiteLLMProvider instance."""
        provider = create_litellm_provider(
            provider_name="openai",
            api_key="test",
        )
        assert isinstance(provider, LiteLLMProvider)

    def test_inherits_from_base_provider(self):
        """Ensure LiteLLMProvider inherits from BaseProvider."""
        from src.providers.base import BaseProvider
        provider = LiteLLMProvider(api_key="test")
        assert isinstance(provider, BaseProvider)