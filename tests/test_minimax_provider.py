"""Request and usage handling for the MiniMax provider."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.providers.minimax_provider import MinimaxProvider


@pytest.mark.parametrize(
    ("base_url", "expected_url"),
    [
        (
            "https://api.minimax.io/anthropic",
            "https://api.minimax.io/anthropic/v1/messages",
        ),
        (
            "https://api.minimaxi.com/anthropic",
            "https://api.minimaxi.com/anthropic/v1/messages",
        ),
    ],
)
def test_priority_request_capture_and_usage(
    base_url: str,
    expected_url: str,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "MiniMax-M3",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 600_001,
                    "output_tokens": 10,
                    "cache_creation_input_tokens": 11,
                    "cache_read_input_tokens": 12,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        provider = MinimaxProvider(api_key="test", base_url=base_url)
        provider._client_kwargs["http_client"] = http_client
        response = provider.chat(
            [{"role": "user", "content": "hello"}],
            service_tier="priority",
        )
        provider.client.close()

    assert captured["url"] == expected_url
    assert captured["body"]["service_tier"] == "priority"
    assert response.usage == {
        "input_tokens": 600_001,
        "output_tokens": 10,
        "cache_creation_input_tokens": 11,
        "cache_read_input_tokens": 12,
        "service_tier": "priority",
    }
