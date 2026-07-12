from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.auth import anthropic_subscription as auth
from src.providers.anthropic_provider import AnthropicProvider


def _credentials(expires_at: float | None = None) -> auth.SubscriptionCredentials:
    return auth.SubscriptionCredentials("access", "refresh", expires_at or time.time() + 3600)


def test_credentials_are_private_and_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    saved = _credentials()
    auth.save_credentials(saved)
    assert auth.load_credentials() == saved
    assert auth.credentials_path().stat().st_mode & 0o777 == 0o600


def test_refresh_rotates_and_persists_tokens(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    auth.save_credentials(_credentials(time.time() - 1))
    with patch.object(auth, "_post_json", return_value={
        "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 7200,
    }) as post:
        result = auth.get_valid_credentials()
    assert result and result.access_token == "new-access"
    stored = auth.load_credentials()
    assert stored and stored.refresh_token == "new-refresh"
    assert post.call_args.args[0] == auth.TOKEN_URL


def test_complete_login_accepts_claude_copy_paste_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    with patch.object(auth, "_post_json", return_value={
        "access_token": "a", "refresh_token": "r", "expires_in": 3600,
    }) as post:
        auth.complete_login("authorization#returned-state", "verifier")
    payload = post.call_args.args[1]
    assert payload["code"] == "authorization"
    assert payload["state"] == "returned-state"
    assert payload["code_verifier"] == "verifier"


def test_provider_uses_bearer_oauth_and_adapts_tools(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    with patch.object(auth, "get_valid_credentials", return_value=_credentials()), \
         patch("src.providers.anthropic_provider.anthropic.Anthropic") as client_cls:
        response = MagicMock()
        response.content = []
        response.model = "claude-sonnet-4-6"
        response.stop_reason = "end_turn"
        response.usage = None
        client_cls.return_value.messages.create.return_value = response
        provider = AnthropicProvider(api_key="")
        result = provider.chat(
            [{"role": "user", "content": "hi"}],
            tools=[{"name": "Bash", "description": "run", "input_schema": {}}],
            system="ClawCodex system",
        )

    kwargs = client_cls.call_args.kwargs
    assert kwargs["auth_token"] == "access"
    assert "oauth-2025-04-20" in kwargs["default_headers"]["anthropic-beta"]
    request = client_cls.return_value.messages.create.call_args.kwargs
    assert request["tools"][0]["name"] == "mcp_Bash"
    assert "ClawCodex" not in str(request["system"])
    assert result.usage["billing_mode"] == "subscription"


def test_subscription_usage_is_not_priced_as_api_billing() -> None:
    from src.cost_tracker import record_api_usage
    with patch("src.cost_tracker.add_to_total_cost_state"), \
         patch("src.cost_tracker.get_model_usage", return_value={}):
        cost = record_api_usage("claude-sonnet-4-6", {
            "input_tokens": 1000, "output_tokens": 1000,
            "billing_mode": "subscription",
        })
    assert cost == 0.0
