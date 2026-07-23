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


def test_oauth_endpoints_are_current_platform_domain() -> None:
    # The login flow migrated to platform.claude.com; the old
    # console.anthropic.com token endpoint is Cloudflare-blocked/404 and made
    # login fail with "error code: 1010". Guard against a regression to the
    # stale hosts. (Mirrors typescript/src/constants/oauth.ts PROD_OAUTH_CONFIG.)
    # Subscriber authorize entrypoint (claude.com/cai → 307 → claude.ai
    # consent); console platform.claude.com/oauth/authorize is the --console
    # path and must NOT be used for a subscription login.
    assert auth.AUTHORIZE_URL == "https://claude.com/cai/oauth/authorize"
    assert auth.TOKEN_URL == "https://platform.claude.com/v1/oauth/token"
    assert auth.REDIRECT_URI == "https://platform.claude.com/oauth/code/callback"
    assert "console.anthropic.com" not in auth.TOKEN_URL
    assert "console.anthropic.com" not in auth.REDIRECT_URI
    # The old code pointed the token exchange at console.anthropic.com — the
    # host that 1010'd. Ensure neither login host regressed to it.
    assert "console.anthropic.com" not in auth.AUTHORIZE_URL
    # The full subscriber scope set (ALL_OAUTH_SCOPES), not the old 3-scope subset.
    for scope in ("user:inference", "user:sessions:claude_code", "user:mcp_servers"):
        assert scope in auth.SCOPES


def test_post_json_sends_user_agent_to_evade_cloudflare(monkeypatch) -> None:
    # Without a real User-Agent, urllib sends Python-urllib/x.y and Cloudflare
    # 1010-blocks the token endpoint. The request MUST carry a genuine UA.
    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"access_token":"a","refresh_token":"r","expires_in":3600}'

    def _fake_urlopen(request, timeout=30):
        captured["ua"] = request.get_header("User-agent")
        return _Resp()

    monkeypatch.setattr(auth.urllib.request, "urlopen", _fake_urlopen)
    auth._post_json(auth.TOKEN_URL, {"grant_type": "refresh_token"})
    assert captured["ua"] == auth.OAUTH_USER_AGENT
    assert captured["ua"] and "urllib" not in captured["ua"].lower()


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


def test_subscription_adapts_deferred_tool_references(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    with patch.object(auth, "get_valid_credentials", return_value=_credentials()), \
         patch("src.providers.anthropic_provider.anthropic.Anthropic"):
        provider = AnthropicProvider(api_key="")

    messages = [{
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": "search-1",
            "content": [{"type": "tool_reference", "tool_name": "Grep"}],
        }],
    }]
    prepared, tools, _ = provider._prepare_subscription_request(
        messages,
        [{"name": "Grep", "description": "search", "input_schema": {}}],
        None,
    )

    assert prepared[0]["content"][0]["content"][0]["tool_name"] == "mcp_Grep"
    assert tools and tools[0]["name"] == "mcp_Grep"
    # Caller-owned history remains canonical for discovery on future turns.
    assert messages[0]["content"][0]["content"][0]["tool_name"] == "Grep"


def test_identity_rewrite_preserves_paths_env_vars_and_domains(monkeypatch) -> None:
    """The OAuth identity disguise must not corrupt machine-readable tokens.

    Regression for the `.Claude Code` misdirection: rewriting `clawcodex`
    inside paths turned the #706/#707 prompt lines (and the auto-memory
    section) into literal nonexistent locations — `~/.Claude Code/sessions`
    — which the model then searched verbatim.
    """
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    with patch.object(auth, "get_valid_credentials", return_value=_credentials()), \
         patch("src.providers.anthropic_provider.anthropic.Anthropic"):
        provider = AnthropicProvider(api_key="")

    system_blocks = [
        {"type": "text", "text": (
            "- clawcodex data directory: /Users/u/.clawcodex — sessions live in "
            "the sessions/ subdirectory, or under $CLAWCODEX_CONFIG_DIR."
        )},
        {"type": "text", "text": (
            "You have a persistent, file-based memory system at "
            "`/Users/u/.clawcodex/projects/-Users-u-proj/memory/`.\n"
            'Grep with pattern="x" path="/Users/u/.clawcodex/sessions/" glob="*.json"\n'
            "Managed policy lives in /etc/clawcodex; see clawcodex_dirs.py and "
            "https://clawcodex.app/install.sh. Run clawcodex to start. clawcodex, "
            "the tool, saves hooks in .clawcodex/settings.json."
        )},
    ]
    _, _, system = provider._prepare_subscription_request([], None, system_blocks)

    assert system[0]["text"].startswith("You are Claude Code")
    text = "\n".join(b["text"] for b in system[1:])
    # Machine-readable tokens survive verbatim.
    assert "/Users/u/.clawcodex — sessions" in text
    assert "/Users/u/.clawcodex/projects/-Users-u-proj/memory/" in text
    assert '/Users/u/.clawcodex/sessions/' in text
    assert "$CLAWCODEX_CONFIG_DIR" in text
    assert "/etc/clawcodex" in text
    assert "clawcodex_dirs.py" in text
    assert "clawcodex.app" in text
    assert ".clawcodex/settings.json" in text
    # Nothing is rewritten INTO a bogus path.
    assert ".Claude Code" not in text
    # Standalone brand mentions are still disguised.
    assert "Claude Code data directory:" in text
    assert "Run Claude Code to start. Claude Code, the tool," in text


def test_subscription_usage_is_not_priced_as_api_billing() -> None:
    from src.cost_tracker import record_api_usage
    with patch("src.cost_tracker.add_to_total_cost_state"), \
         patch("src.cost_tracker.get_model_usage", return_value={}):
        cost = record_api_usage("claude-sonnet-4-6", {
            "input_tokens": 1000, "output_tokens": 1000,
            "billing_mode": "subscription",
        })
    assert cost == 0.0
