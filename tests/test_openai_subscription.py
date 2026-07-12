from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from src.auth import openai_subscription as auth
from src.providers.openai_provider import OpenAIProvider
from src.providers.openai_responses import (
    RESPONSES_ITEM_BLOCK_TYPE,
    build_usage_dict,
    convert_messages_to_responses_input,
    convert_tools_to_responses_format,
    strip_responses_item_blocks,
)


def _credentials(expires_at: float | None = None) -> auth.SubscriptionCredentials:
    return auth.SubscriptionCredentials(
        "access", "refresh", expires_at or time.time() + 3600, "acct-123", "idtok"
    )


def _fake_jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return f"h.{payload.decode()}.s"


# --- credential store ---------------------------------------------------


def test_credentials_are_private_and_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    saved = _credentials()
    auth.save_credentials(saved)
    assert auth.load_credentials() == saved
    assert auth.credentials_path().stat().st_mode & 0o777 == 0o600


def test_refresh_posts_form_and_rotates_tokens(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    auth.save_credentials(_credentials(time.time() - 1))
    with patch.object(auth, "_post_form", return_value={
        "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 7200,
    }) as post:
        result = auth.get_valid_credentials()
    assert result and result.access_token == "new-access"
    stored = auth.load_credentials()
    assert stored and stored.refresh_token == "new-refresh"
    # Account id survives a refresh response that has no id_token.
    assert stored.account_id == "acct-123"
    assert post.call_args.args[0] == auth.TOKEN_URL
    assert post.call_args.args[1]["grant_type"] == "refresh_token"


def test_refresh_keeps_old_refresh_token_when_absent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    auth.save_credentials(_credentials(time.time() - 1))
    with patch.object(auth, "_post_form", return_value={
        "access_token": "new-access", "expires_in": 3600,
    }):
        result = auth.get_valid_credentials()
    assert result and result.refresh_token == "refresh"


# --- login flows ---------------------------------------------------------


def test_begin_login_builds_pkce_authorize_url() -> None:
    url, verifier, state = auth.begin_login()
    parsed = urllib.parse.urlparse(url)
    params = dict(urllib.parse.parse_qsl(parsed.query))
    assert url.startswith(auth.AUTHORIZE_URL)
    assert params["client_id"] == auth.CLIENT_ID
    assert params["redirect_uri"] == auth.REDIRECT_URI
    assert params["state"] == state
    assert params["codex_cli_simplified_flow"] == "true"
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert params["code_challenge"] == expected_challenge


def test_complete_login_exchanges_code_and_extracts_account(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    id_token = _fake_jwt({
        "https://api.openai.com/auth": {"chatgpt_account_id": "acct-jwt"},
    })
    with patch.object(auth, "_post_form", return_value={
        "access_token": "a", "refresh_token": "r", "expires_in": 3600,
        "id_token": id_token,
    }) as post:
        credentials = auth.complete_login("auth-code", "verifier")
    payload = post.call_args.args[1]
    assert payload["code"] == "auth-code"
    assert payload["code_verifier"] == "verifier"
    assert payload["grant_type"] == "authorization_code"
    assert credentials.account_id == "acct-jwt"
    assert auth.load_credentials() == credentials


def test_extract_account_id_claim_precedence() -> None:
    direct = _fake_jwt({"chatgpt_account_id": "direct"})
    nested = _fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "nested"}})
    orgs = _fake_jwt({"organizations": [{"id": "org-1"}]})
    assert auth.extract_account_id(direct) == "direct"
    assert auth.extract_account_id(nested) == "nested"
    assert auth.extract_account_id(orgs) == "org-1"
    # id_token wins over access_token; fall back when id_token has none.
    assert auth.extract_account_id(_fake_jwt({}), nested) == "nested"


def test_import_codex_cli_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    access = _fake_jwt({"exp": time.time() + 1000})
    (codex_home / "auth.json").write_text(json.dumps({
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": "id", "access_token": access,
            "refresh_token": "ref", "account_id": "acct-codex",
        },
    }))
    assert auth.has_codex_cli_credentials()
    imported = auth.import_codex_cli_credentials()
    assert imported.account_id == "acct-codex"
    assert imported.refresh_token == "ref"
    assert not imported.needs_refresh
    assert auth.load_credentials() == imported


# --- Responses wire format ------------------------------------------------


def test_converter_maps_system_tools_and_history() -> None:
    messages = [
        {"role": "system", "content": "SYSTEM PROMPT"},
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "calling"},
                {"type": "tool_use", "id": "call_1", "name": "Bash", "input": {"c": 1}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "ok"},
                {"type": "text", "text": "continue"},
            ],
        },
    ]
    items, instructions = convert_messages_to_responses_input(messages)
    assert instructions == "SYSTEM PROMPT"
    assert items[0] == {
        "role": "user", "content": [{"type": "input_text", "text": "hello"}],
    }
    assert items[1]["content"] == [{"type": "output_text", "text": "calling"}]
    assert items[2] == {
        "type": "function_call", "call_id": "call_1", "name": "Bash",
        "arguments": json.dumps({"c": 1}),
    }
    # Tool output precedes the remaining user content.
    assert items[3] == {
        "type": "function_call_output", "call_id": "call_1", "output": "ok",
    }
    assert items[4]["content"] == [{"type": "input_text", "text": "continue"}]


def test_converter_drops_orphan_tool_results() -> None:
    messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "never-issued", "content": "x"},
        ]},
    ]
    items, _ = convert_messages_to_responses_input(messages)
    assert items == []


def test_converter_replays_passthrough_items_and_skips_projections() -> None:
    reasoning_item = {
        "type": "reasoning", "encrypted_content": "gAAA", "summary": [],
    }
    fcall_item = {
        "type": "function_call", "call_id": "call_9", "name": "Bash",
        "arguments": "{}",
    }
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "projected text"},
                {"type": "tool_use", "id": "call_9", "name": "Bash", "input": {}},
                {"type": RESPONSES_ITEM_BLOCK_TYPE, "item": reasoning_item},
                {"type": RESPONSES_ITEM_BLOCK_TYPE, "item": fcall_item},
            ],
        },
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_9", "content": "done"},
        ]},
    ]
    items, _ = convert_messages_to_responses_input(messages)
    # Raw items only — the projected text/tool_use blocks must not double-send.
    assert items[0] == reasoning_item
    assert items[1] == fcall_item
    assert items[2]["type"] == "function_call_output"
    assert len(items) == 3


def test_converter_translates_images_and_multimodal_tool_results() -> None:
    image = {"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": "AAA",
    }}
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "c1", "name": "Read", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": [image]},
            image,
        ]},
    ]
    items, _ = convert_messages_to_responses_input(messages)
    output_item = items[1]
    assert output_item["type"] == "function_call_output"
    assert "delivered in the following message" in output_item["output"]
    follow_up = items[2]
    assert follow_up["role"] == "user"
    assert follow_up["content"][1]["type"] == "input_image"
    assert follow_up["content"][1]["image_url"] == "data:image/png;base64,AAA"
    direct = items[3]
    assert direct["content"][0]["type"] == "input_image"


def test_tools_convert_to_flat_function_format() -> None:
    tools = [
        {"name": "Bash", "description": "run", "input_schema": {"type": "object"}},
        {"name": "Broken", "description": "x", "input_schema": {"type": None}},
    ]
    converted = convert_tools_to_responses_format(tools)
    assert converted == [{
        "type": "function", "name": "Bash", "description": "run",
        "parameters": {"type": "object", "properties": {}}, "strict": False,
    }]


def test_strip_responses_item_blocks_for_foreign_providers() -> None:
    messages = [
        {"role": "assistant", "content": [
            {"type": "text", "text": "keep"},
            {"type": RESPONSES_ITEM_BLOCK_TYPE, "item": {"type": "reasoning"}},
        ]},
        {"role": "assistant", "content": [
            {"type": RESPONSES_ITEM_BLOCK_TYPE, "item": {"type": "reasoning"}},
        ]},
        {"role": "user", "content": "untouched"},
    ]
    stripped = strip_responses_item_blocks(messages)
    assert stripped[0]["content"] == [{"type": "text", "text": "keep"}]
    # A passthrough-only assistant message is dropped entirely.
    assert len(stripped) == 2
    assert stripped[1]["content"] == "untouched"


def test_chat_completions_converter_strips_passthrough_blocks() -> None:
    from src.providers.openai_compatible import _convert_anthropic_messages_to_openai

    messages = [
        {"role": "assistant", "content": [
            {"type": "text", "text": "hi"},
            {"type": RESPONSES_ITEM_BLOCK_TYPE, "item": {"type": "reasoning"}},
        ]},
    ]
    converted = _convert_anthropic_messages_to_openai(messages)
    assert json.dumps(converted).find(RESPONSES_ITEM_BLOCK_TYPE) == -1


def test_usage_dict_marks_subscription_billing() -> None:
    usage = build_usage_dict({
        "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
        "input_tokens_details": {"cached_tokens": 4},
    })
    assert usage == {
        "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
        "billing_mode": "subscription", "cache_read_input_tokens": 4,
    }


# --- provider ---------------------------------------------------------------


class _FakeStreamResponse:
    """Stand-in for a streamed httpx response emitting canned SSE lines."""

    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code
        self.closed = False

    def iter_lines(self):
        yield from self._lines

    def read(self) -> bytes:
        return b"error-body"

    def close(self) -> None:
        self.closed = True


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event)


def _subscription_provider(monkeypatch) -> OpenAIProvider:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Construction checks credential PRESENCE only (load_credentials);
    # request-time freshness goes through get_valid_credentials.
    with patch(
        "src.auth.openai_subscription.load_credentials",
        return_value=_credentials(),
    ):
        provider = OpenAIProvider(api_key="")
    assert provider._subscription_active
    return provider


def test_provider_streams_responses_and_builds_chat_response(monkeypatch) -> None:
    provider = _subscription_provider(monkeypatch)

    reasoning_item = {
        "id": "rs_1", "type": "reasoning", "encrypted_content": "enc",
        "summary": [],
    }
    fcall_item = {
        "id": "fc_1", "type": "function_call", "status": "completed",
        "call_id": "call_7", "name": "Bash", "arguments": '{"cmd":"ls"}',
    }
    lines = [
        _sse({"type": "response.created", "response": {"id": "resp_1"}}),
        _sse({"type": "response.output_text.delta", "delta": "hel"}),
        _sse({"type": "response.reasoning_summary_text.delta", "delta": "think"}),
        _sse({"type": "response.output_text.delta", "delta": "lo"}),
        _sse({"type": "response.output_item.done", "item": reasoning_item}),
        _sse({"type": "response.output_item.done", "item": fcall_item}),
        _sse({"type": "response.completed", "response": {
            "model": "gpt-5.4",
            "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
        }}),
    ]
    fake_response = _FakeStreamResponse(lines)
    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def build_request(self, method, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            return "request"

        def send(self, request, stream=False):
            return fake_response

        def close(self):
            pass

    text_chunks: list[str] = []
    thinking_chunks: list[str] = []
    with patch(
        "src.auth.openai_subscription.get_valid_credentials",
        return_value=_credentials(),
    ), patch("httpx.Client", _FakeClient):
        result = provider.chat_stream_response(
            [
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "hi"},
            ],
            tools=[{"name": "Bash", "description": "run",
                    "input_schema": {"type": "object", "properties": {}}}],
            on_text_chunk=text_chunks.append,
            on_thinking_chunk=thinking_chunks.append,
            system="EXTRA",
        )

    assert captured["url"] == auth.CODEX_API_ENDPOINT
    assert captured["headers"]["Authorization"] == "Bearer access"
    assert captured["headers"]["chatgpt-account-id"] == "acct-123"
    assert captured["headers"]["originator"] == auth.ORIGINATOR
    body = captured["body"]
    assert body["store"] is False and body["stream"] is True
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["instructions"] == "EXTRA\n\nSYS"
    assert body["tools"][0]["name"] == "Bash"
    assert "max_tokens" not in body and "max_output_tokens" not in body

    assert result.content == "hello"
    assert text_chunks == ["hel", "lo"]
    assert thinking_chunks == ["think"]
    assert result.reasoning_content == "think"
    assert result.tool_uses == [{"id": "call_7", "name": "Bash", "input": {"cmd": "ls"}}]
    assert result.finish_reason == "tool_calls"
    assert result.usage["billing_mode"] == "subscription"
    assert result.usage["input_tokens"] == 7
    # Raw items round-trip with server ids stripped, ready for replay.
    assert result.raw_content_blocks == [
        {"type": RESPONSES_ITEM_BLOCK_TYPE,
         "item": {"type": "reasoning", "encrypted_content": "enc", "summary": []}},
        {"type": RESPONSES_ITEM_BLOCK_TYPE,
         "item": {"type": "function_call", "call_id": "call_7", "name": "Bash",
                  "arguments": '{"cmd":"ls"}'}},
    ]


def test_chat_stream_yields_text_deltas(monkeypatch) -> None:
    provider = _subscription_provider(monkeypatch)
    lines = [
        _sse({"type": "response.output_text.delta", "delta": "hel"}),
        _sse({"type": "response.output_text.delta", "delta": "lo"}),
        _sse({"type": "response.completed", "response": {
            "model": "gpt-5.4", "usage": {"input_tokens": 1, "output_tokens": 1},
        }}),
    ]

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def build_request(self, method, url, headers=None, json=None):
            return "request"

        def send(self, request, stream=False):
            return _FakeStreamResponse(lines)

        def close(self):
            pass

    with patch(
        "src.auth.openai_subscription.get_valid_credentials",
        return_value=_credentials(),
    ), patch("httpx.Client", _FakeClient):
        assert list(provider.chat_stream([{"role": "user", "content": "hi"}])) == [
            "hel", "lo",
        ]


def test_provider_refreshes_once_on_401(monkeypatch) -> None:
    provider = _subscription_provider(monkeypatch)

    ok_lines = [
        _sse({"type": "response.output_text.delta", "delta": "ok"}),
        _sse({"type": "response.completed", "response": {
            "model": "gpt-5.4", "usage": {"input_tokens": 1, "output_tokens": 1},
        }}),
    ]
    responses = [
        _FakeStreamResponse([], status_code=401),
        _FakeStreamResponse(ok_lines),
    ]
    sent_headers: list[dict] = []

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def build_request(self, method, url, headers=None, json=None):
            sent_headers.append(headers)
            return "request"

        def send(self, request, stream=False):
            return responses.pop(0)

        def close(self):
            pass

    refreshed = auth.SubscriptionCredentials(
        "fresh-access", "fresh-refresh", time.time() + 3600, "acct-123", ""
    )
    with patch(
        "src.auth.openai_subscription.get_valid_credentials",
        return_value=_credentials(),
    ), patch(
        "src.auth.openai_subscription.force_refresh", return_value=refreshed,
    ) as force, patch("httpx.Client", _FakeClient):
        result = provider.chat_stream_response([{"role": "user", "content": "hi"}])

    assert force.call_count == 1
    assert sent_headers[0]["Authorization"] == "Bearer access"
    assert sent_headers[1]["Authorization"] == "Bearer fresh-access"
    assert result.content == "ok"


def test_provider_surfaces_backend_failure_events(monkeypatch) -> None:
    provider = _subscription_provider(monkeypatch)
    lines = [
        _sse({"type": "response.failed", "response": {
            "error": {"message": "usage limit reached"},
        }}),
    ]

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def build_request(self, method, url, headers=None, json=None):
            return "request"

        def send(self, request, stream=False):
            return _FakeStreamResponse(lines)

        def close(self):
            pass

    with patch(
        "src.auth.openai_subscription.get_valid_credentials",
        return_value=_credentials(),
    ), patch("httpx.Client", _FakeClient):
        try:
            provider.chat_stream_response([{"role": "user", "content": "hi"}])
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "usage limit reached" in str(exc)


def test_abort_mid_stream_raises_and_closes_response(monkeypatch) -> None:
    """ESC during generation: AbortError surfaces promptly and the guard's
    close-on-abort listener actually closes the underlying httpx response
    (via the _HttpxStreamHolder.response slot)."""
    import threading

    from src.utils.abort_controller import AbortController, AbortError

    provider = _subscription_provider(monkeypatch)
    controller = AbortController()
    started = threading.Event()

    class _HangingResponse(_FakeStreamResponse):
        def iter_lines(self):
            yield _sse({"type": "response.output_text.delta", "delta": "par"})
            started.set()
            # Block like a stalled socket until the abort closes us.
            while not self.closed:
                time.sleep(0.02)
            raise RuntimeError("connection closed")

    fake_response = _HangingResponse([])

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def build_request(self, method, url, headers=None, json=None):
            return "request"

        def send(self, request, stream=False):
            return fake_response

        def close(self):
            pass

    def _abort_after_first_chunk():
        started.wait(timeout=5)
        controller.abort("user_interrupt")

    aborter = threading.Thread(target=_abort_after_first_chunk, daemon=True)
    aborter.start()
    with patch(
        "src.auth.openai_subscription.get_valid_credentials",
        return_value=_credentials(),
    ), patch("httpx.Client", _FakeClient):
        try:
            provider.chat_stream_response(
                [{"role": "user", "content": "hi"}],
                abort_signal=controller.signal,
            )
            raise AssertionError("expected AbortError")
        except AbortError:
            pass
    aborter.join(timeout=5)
    assert fake_response.closed, "abort must close the underlying response"


def test_effort_setting_reaches_reasoning_body(monkeypatch) -> None:
    """/effort (injected as extra_body.reasoning_effort by the agent-server
    wrapper) wins over the default; xhigh clamps to high."""
    provider = _subscription_provider(monkeypatch)
    body = provider._subscription_request_body(
        [{"role": "user", "content": "hi"}], None,
        extra_body={"reasoning_effort": "low"},
    )
    assert body["reasoning"]["effort"] == "low"
    body = provider._subscription_request_body(
        [{"role": "user", "content": "hi"}], None,
        extra_body={"reasoning_effort": "xhigh"},
    )
    assert body["reasoning"]["effort"] == "high"
    body = provider._subscription_request_body(
        [{"role": "user", "content": "hi"}], None,
    )
    assert body["reasoning"]["effort"] == "medium"


def test_provider_lists_subscription_models(monkeypatch) -> None:
    provider = _subscription_provider(monkeypatch)
    models = provider.get_available_models()
    assert "gpt-5.5" in models and "gpt-5.3-codex-spark" in models


def test_api_key_wins_over_subscription(monkeypatch) -> None:
    with patch(
        "src.auth.openai_subscription.load_credentials",
        return_value=_credentials(),
    ):
        provider = OpenAIProvider(api_key="sk-live")
    assert not provider._subscription_active


def test_provider_validation_accepts_subscription(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    auth.save_credentials(_credentials())
    from src.providers import provider_has_credentials

    assert provider_has_credentials("openai", "")
    auth.remove_credentials()
    assert not provider_has_credentials("openai", "")
    # The shared gate helper covers the Claude subscription too (the #697
    # flow was blocked by the agent-server's inline api-key checks before
    # this helper existed — see agent_server.py session init / set_provider).
    from src.auth import anthropic_subscription as anth

    anth.save_credentials(
        anth.SubscriptionCredentials("a", "r", time.time() + 3600)
    )
    assert provider_has_credentials("anthropic", "")
    anth.remove_credentials()
    assert not provider_has_credentials("anthropic", "")
