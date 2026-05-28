"""Tests for ``src.transports.transport_utils``.

Strategy
--------

Construct real ``WebSocketTransport`` / ``HybridTransport`` /
``SSETransport`` instances and assert the factory picks the right one
for each env-var configuration. No real network I/O: the real classes
don't connect at construction, only on ``await connect()``.

The Protocol-runtime-check test constructs the real classes (not
stubs) so future signature drift surfaces here.
"""

from __future__ import annotations

import pytest

from src.transports.hybrid_transport import HybridTransport
from src.transports.sse_transport import SSETransport
from src.transports.transport_utils import (
    Transport,
    get_transport_for_url,
    is_env_truthy,
)
from src.transports.websocket_transport import WebSocketTransport


# ---------------------------------------------------------------------------
# is_env_truthy


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, False),
        ("", False),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("off", False),
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("On", True),
    ],
)
def test_is_env_truthy(value, expected):
    assert is_env_truthy(value) is expected


# ---------------------------------------------------------------------------
# Selection branches


def test_ws_url_returns_websocket_transport(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_USE_CCR_V2", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2", raising=False)
    t = get_transport_for_url("ws://example.com/session")
    assert isinstance(t, WebSocketTransport)


def test_wss_url_returns_websocket_transport(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_USE_CCR_V2", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2", raising=False)
    t = get_transport_for_url("wss://example.com/session")
    assert isinstance(t, WebSocketTransport)
    # Not a HybridTransport (HybridTransport subclasses WebSocketTransport,
    # so we also assert it's not the subclass).
    assert not isinstance(t, HybridTransport)


def test_post_for_ingress_env_returns_hybrid_transport(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_USE_CCR_V2", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2", "1")
    t = get_transport_for_url("wss://example.com/session")
    assert isinstance(t, HybridTransport)


def test_ccr_v2_env_returns_sse_transport(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_USE_CCR_V2", "1")
    monkeypatch.delenv("CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2", raising=False)
    t = get_transport_for_url("wss://example.com/x")
    assert isinstance(t, SSETransport)
    # SSE URL was rewritten to https and got the events-stream path.
    assert t._url == "https://example.com/x/worker/events/stream"


def test_ccr_v2_rewrites_ws_scheme_to_http(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_USE_CCR_V2", "1")
    t = get_transport_for_url("ws://example.com/x")
    assert isinstance(t, SSETransport)
    assert t._url == "http://example.com/x/worker/events/stream"


def test_ccr_v2_strips_trailing_slash_before_appending_path(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_USE_CCR_V2", "1")
    t = get_transport_for_url("wss://example.com/sessions/abc/")
    assert isinstance(t, SSETransport)
    # Trailing slash stripped, path appended exactly once.
    assert t._url == "https://example.com/sessions/abc/worker/events/stream"


def test_unsupported_scheme_raises_value_error(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_USE_CCR_V2", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2", raising=False)
    with pytest.raises(ValueError, match="Unsupported protocol"):
        get_transport_for_url("http://example.com/session")


def test_ccr_v2_truthy_with_http_url_still_returns_sse(monkeypatch):
    # The CCR v2 branch runs *before* the ws/wss check, so it accepts any
    # URL (the scheme just isn't rewritten if it's already http/https).
    monkeypatch.setenv("CLAUDE_CODE_USE_CCR_V2", "1")
    t = get_transport_for_url("https://example.com/x")
    assert isinstance(t, SSETransport)
    assert t._url == "https://example.com/x/worker/events/stream"


# ---------------------------------------------------------------------------
# Callback wiring


def test_refresh_headers_passed_through_for_ws(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_USE_CCR_V2", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2", raising=False)

    def refresh():
        return {"Authorization": "Bearer fresh"}

    t = get_transport_for_url("ws://example.com/x", refresh_headers=refresh)
    assert isinstance(t, WebSocketTransport)
    assert t._refresh_headers is refresh


def test_refresh_headers_passed_through_for_hybrid(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_USE_CCR_V2", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2", "1")

    def refresh():
        return {"Authorization": "Bearer fresh"}

    t = get_transport_for_url("wss://example.com/x", refresh_headers=refresh)
    assert isinstance(t, HybridTransport)
    assert t._refresh_headers is refresh


def test_refresh_headers_translated_to_get_auth_headers_for_sse(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_USE_CCR_V2", "1")

    def refresh():
        return {"Authorization": "Bearer fresh"}

    t = get_transport_for_url("wss://example.com/x", refresh_headers=refresh)
    assert isinstance(t, SSETransport)
    # SSETransport stores the callable as _get_auth_headers (the
    # factory translates refresh_headers → get_auth_headers).
    assert t._get_auth_headers is refresh


def test_headers_and_session_id_passed_through(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_USE_CCR_V2", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2", raising=False)
    t = get_transport_for_url(
        "wss://example.com/x",
        headers={"X-Custom": "yes"},
        session_id="abc-123",
    )
    assert isinstance(t, WebSocketTransport)
    assert t._headers.get("X-Custom") == "yes"
    assert t._session_id == "abc-123"


def test_httpx_url_input_accepted(monkeypatch):
    import httpx

    monkeypatch.delenv("CLAUDE_CODE_USE_CCR_V2", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2", raising=False)
    t = get_transport_for_url(httpx.URL("ws://example.com/x"))
    assert isinstance(t, WebSocketTransport)


# ---------------------------------------------------------------------------
# Protocol shape


def test_transport_protocol_runtime_check_for_websocket():
    t = WebSocketTransport("ws://example.com/x")
    assert isinstance(t, Transport)


def test_transport_protocol_runtime_check_for_hybrid():
    t = HybridTransport("ws://example.com/x")
    assert isinstance(t, Transport)


def test_transport_protocol_runtime_check_for_sse():
    t = SSETransport("https://example.com/x/stream")
    assert isinstance(t, Transport)


def test_transport_protocol_rejects_object_missing_methods():
    class NotATransport:
        pass

    assert not isinstance(NotATransport(), Transport)
