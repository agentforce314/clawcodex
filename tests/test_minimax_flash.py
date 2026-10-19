"""Capture real Anthropic SDK requests without external inference or credentials."""

from __future__ import annotations

import copy
import json

import httpx
import pytest

from src.models.configs import get_model_config
from src.providers.minimax_provider import MinimaxProvider
from src.providers.openrouter_provider import OpenRouterProvider
from src.services.pricing import compute_cost, get_pricing

FLASH = "MiniMax-M3.1-Flash"
CASES = [
    (
        FLASH,
        {"output_config": {"effort": effort}},
        "max" if effort == "none" else effort,
    )
    for effort in ("low", "medium", "high", "xhigh", "max", "none")
] + [
    (FLASH, {}, "max"),
    (
        FLASH,
        {
            "thinking": {"type": "disabled"},
            "service_tier": "priority",
            "reasoning_split": False,
        },
        "max",
    ),
    (
        FLASH,
        {
            "output_config": {"effort": "low"},
            "extra_body": {"metadata": {"user_id": "test"}},
        },
        "low",
    ),
    (
        FLASH,
        {
            "output_config": {"effort": "low"},
            "extra_body": {
                "output_config": {"effort": "xhigh"},
                "thinking": {"type": "disabled"},
                "service_tier": "priority",
                "reasoning_split": False,
            },
        },
        "xhigh",
    ),
    (
        FLASH,
        {
            "output_config": {"effort": "medium"},
            "extra_body": {
                "output_config": {
                    "format": {"type": "json_schema", "schema": {"type": "object"}}
                }
            },
        },
        "medium",
    ),
    ("MiniMax-M3", {}, None),
    (
        "MiniMax-M3",
        {
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "output_config": {"effort": "low"},
            "service_tier": "priority",
        },
        None,
    ),
    (
        "MiniMax-M3",
        {"extra_body": {"thinking": {"type": "disabled"}, "service_tier": "priority"}},
        None,
    ),
    ("MiniMax-M2.7", {"thinking": {"type": "enabled", "budget_tokens": 1024}}, None),
    (
        "private-custom-model",
        {"extra_body": {"thinking": {"type": "disabled"}, "reasoning_split": False}},
        None,
    ),
]


@pytest.mark.parametrize("region", ["api.minimax.io", "api.minimaxi.com"])
@pytest.mark.parametrize("method", ["chat", "chat_stream", "chat_stream_response"])
@pytest.mark.parametrize("model,options,effort", CASES)
def test_sdk_request_capture(region, method, model, options, effort):
    original = copy.deepcopy(options)
    captured = []

    def handler(request):
        body = json.loads(request.content)
        captured.append(body)
        assert str(request.url) == f"https://{region}/anthropic/v1/messages"
        message = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1},
        }
        if not body.get("stream"):
            return httpx.Response(200, json=message)
        start = dict(message, content=[], stop_reason=None)
        events = [
            ("message_start", {"message": start}),
            (
                "content_block_start",
                {"index": 0, "content_block": {"type": "text", "text": ""}},
            ),
            (
                "content_block_delta",
                {"index": 0, "delta": {"type": "text_delta", "text": "ok"}},
            ),
            ("content_block_stop", {"index": 0}),
            (
                "message_delta",
                {
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
            ),
            ("message_stop", {}),
        ]
        data = "".join(
            f"event: {event}\ndata: {json.dumps(dict(payload, type=event))}\n\n"
            for event, payload in events
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=data
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = MinimaxProvider(
            api_key="test", base_url=f"https://{region}/anthropic", model=model
        )
        provider._client_kwargs["http_client"] = client
        response = getattr(provider, method)(
            [{"role": "user", "content": "hello"}], **options
        )
        if method == "chat_stream":
            assert "".join(response) == "ok"
        else:
            assert response.content == "ok"
            tier = "priority" if model == "MiniMax-M3" and options else "standard"
            assert response.usage["service_tier"] == tier
        provider.client.close()
    assert options == original
    assert len(captured) == 1
    body = captured[0]
    assert body["model"] == model
    if model == FLASH:
        assert body["thinking"] == {"type": "adaptive"}
        assert body["output_config"]["effort"] == effort
        assert "service_tier" not in body
        assert "reasoning_split" not in body
        if "format" in options.get("extra_body", {}).get("output_config", {}):
            assert body["output_config"]["format"]["type"] == "json_schema"
    elif model == "MiniMax-M3":
        if not options:
            assert "thinking" not in body  # Anthropic defaults to disabled.
        elif "extra_body" in options:
            assert body["thinking"] == {"type": "disabled"}
        else:
            assert body["thinking"] == {"type": "adaptive"}
            assert body["output_config"] == {"effort": "low"}  # M3 ignores effort.
    else:
        expected = options.get("extra_body", options)
        for key, value in expected.items():
            assert body[key] == value


def test_flash_limits_and_cost_paths():
    from src.services.cost_tracker import CostTracker

    for model in (FLASH, "MiniMax-M3"):
        config = get_model_config(model)
        assert config.context_window == 1_000_000
        assert config.max_output_tokens == 524_288
    assert get_model_config("MiniMax-M2.7").context_window == 204_800
    card = get_pricing(FLASH, input_tokens=900_000, service_tier="priority")
    assert card == {
        "input": 0.15 / 1e6,
        "output": 0.60 / 1e6,
        "cache_read": 0.015 / 1e6,
    }
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000,
    }
    assert compute_cost(FLASH, usage) == pytest.approx(0.915)
    assert CostTracker().record_usage(FLASH, usage) == pytest.approx(0.915)
    assert get_pricing("MiniMax-M3.1-private") is None


@pytest.mark.parametrize(
    "host", ["openrouter.ai", "custom.example", "openrouter.ai.example"]
)
def test_openrouter_discovery_preserves_custom_hosts_and_nontext(monkeypatch, host):
    models = [
        "minimax/minimax-01",
        "minimax/minimax-m1",
        "minimax/minimax-m2.7:free",
        "minimax/minimax-m3",
        "minimax/minimax-m3.1-flash",
        "minimax/speech-02-hd",
        "minimax/image-01",
        "minimax/abab-embedding",
        "other/model",
    ]
    monkeypatch.setattr(
        "src.providers.model_discovery.discovered_models", lambda *a, **k: list(models)
    )
    provider = OpenRouterProvider(
        api_key="test", base_url=f"https://{host}/api/v1", model=models[0]
    )
    assert provider.get_available_models() == (
        models[3:] if host == "openrouter.ai" else models
    )
    assert provider.model == models[0]


def test_openrouter_does_not_invent_current_routes(monkeypatch):
    monkeypatch.setattr(
        "src.providers.model_discovery.discovered_models",
        lambda *a, **k: ["other/model"],
    )
    assert OpenRouterProvider(api_key="test").get_available_models() == ["other/model"]
