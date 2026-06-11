"""Every OpenAI-SDK-based provider must get a bounded read timeout.

Regression: the read timeout that stops a stalled stream from blocking the event
loop was added only to OpenAIProvider, so openrouter/deepseek/glm inherited the
SDK's 600s default and froze workflow agents. It is now applied centrally in the
base ``OpenAICompatibleProvider.client`` property.
"""

from __future__ import annotations

import pytest

from src.providers.deepseek_provider import DeepSeekProvider
from src.providers.openai_provider import OpenAIProvider
from src.providers.openrouter_provider import OpenRouterProvider

_OPENAI_SDK_PROVIDERS = (OpenAIProvider, OpenRouterProvider, DeepSeekProvider)


@pytest.mark.parametrize("cls", _OPENAI_SDK_PROVIDERS)
def test_provider_has_bounded_read_timeout(cls):
    p = cls(api_key="x", base_url="https://example.com/v1", model="m")
    # default 120s — far below the openai SDK's 600s default
    assert p.client.timeout.read <= 120.0
    assert p.client.max_retries == 1


def test_read_timeout_is_env_tunable(monkeypatch):
    monkeypatch.setenv("CLAWCODEX_LLM_READ_TIMEOUT", "45")
    p = OpenRouterProvider(api_key="x", base_url="https://x/v1", model="m")
    assert p.client.timeout.read == 45.0
