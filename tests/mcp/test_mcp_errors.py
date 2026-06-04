from __future__ import annotations

import pytest
from src.services.mcp.errors import (
    McpAuthError,
    McpSessionExpiredError,
    McpToolCallError,
    is_mcp_session_expired_error,
)


class TestMcpAuthError:
    def test_init(self) -> None:
        err = McpAuthError("test-server", "auth failed")
        assert err.server_name == "test-server"
        assert str(err) == "auth failed"

    def test_inherits_exception(self) -> None:
        err = McpAuthError("s", "m")
        assert isinstance(err, Exception)


class TestMcpSessionExpiredError:
    def test_init(self) -> None:
        err = McpSessionExpiredError("my-server")
        assert err.server_name == "my-server"
        assert "my-server" in str(err)
        assert "session expired" in str(err)


class TestMcpToolCallError:
    def test_init_basic(self) -> None:
        err = McpToolCallError("tool failed")
        assert str(err) == "tool failed"
        assert err.telemetry_message == "tool failed"
        assert err.mcp_meta is None

    def test_init_with_meta(self) -> None:
        meta = {"_meta": {"some": "data"}}
        err = McpToolCallError("fail", "tele-msg", meta)
        assert err.telemetry_message == "tele-msg"
        assert err.mcp_meta == meta


class TestIsMcpSessionExpiredError:
    """Per MCP Streamable-HTTP spec, session-expiry requires BOTH HTTP 404
    AND JSON-RPC -32001 — matches TS isMcpSessionExpiredError behavior."""

    @staticmethod
    def _make_error(message: str, status_code: int | None = None) -> Exception:
        err = Exception(message)
        if status_code is not None:
            err.status_code = status_code  # type: ignore[attr-defined]
        return err

    def test_404_plus_neg32001_returns_true(self) -> None:
        err = self._make_error('{"code":-32001,"message":"session expired"}', status_code=404)
        assert is_mcp_session_expired_error(err) is True

    def test_404_plus_neg32001_spaced_returns_true(self) -> None:
        err = self._make_error('{"code": -32001, "message": "session expired"}', status_code=404)
        assert is_mcp_session_expired_error(err) is True

    def test_500_with_neg32001_returns_false(self) -> None:
        """Wrong HTTP status: spec requires 404 specifically."""
        err = self._make_error('{"code":-32001}', status_code=500)
        assert is_mcp_session_expired_error(err) is False

    def test_404_without_neg32001_returns_false(self) -> None:
        """Wrong JSON-RPC code: spec requires -32001 specifically."""
        err = self._make_error('{"code":-32600,"message":"invalid request"}', status_code=404)
        assert is_mcp_session_expired_error(err) is False

    def test_no_status_code_with_neg32001_returns_true(self) -> None:
        """Dual-mode: when no HTTP status is exposed (the SDK-wrapped
        ``McpToolCallError`` shape), the JSON-RPC code -32001 alone is
        sufficient signal — the HTTP status was erased by the transport
        layer."""
        err = Exception('{"code":-32001,"message":"session expired"}')
        assert is_mcp_session_expired_error(err) is True

    def test_no_status_code_with_sdk_32600_returns_true(self) -> None:
        """Dual-mode: the mcp PyPI SDK uses ``code=32600`` (positive!) on a
        Streamable-HTTP 404. Detect it too."""
        err = Exception('{"code":32600,"message":"Session terminated"}')
        assert is_mcp_session_expired_error(err) is True

    def test_session_terminated_with_unrecognized_code_does_NOT_match(self) -> None:
        """Regression: an unrecognized code paired with the literal
        'Session terminated' string must NOT trigger session-expiry.
        A server that emits e.g. -32602 (Invalid Params) and happens to
        use 'Session terminated' as the message text would otherwise be
        misclassified, causing spurious reconnect+retry. The regex must
        require the code field to be one of the recognized session-expiry
        codes (-32001 or 32600)."""
        err = Exception('{"code":-99999,"message":"Session terminated"}')
        assert is_mcp_session_expired_error(err) is False

    def test_session_terminated_text_alone_does_not_match(self) -> None:
        """Regression: free-form 'Session terminated' text without a JSON-RPC
        envelope (e.g. a tool returning the phrase as content) must NOT be
        misclassified. The regex requires a code field nearby."""
        err = Exception("My remote chat: Session terminated by user")
        assert is_mcp_session_expired_error(err) is False

    def test_neg32600_invalid_request_does_NOT_match_pos32600(self) -> None:
        """Regression for substring ambiguity: ``-32600`` (JSON-RPC Invalid
        Request) must NOT match the SDK's ``32600`` session-terminated code.
        Without a regex with negative-lookbehind, ``"code":32600`` substring-
        matches ``"code":-32600`` and every malformed-request error falsely
        triggers reconnect."""
        err = Exception('{"code":-32600,"message":"Invalid Request"}')
        assert is_mcp_session_expired_error(err) is False

    def test_neg320013_does_NOT_match_neg32001(self) -> None:
        """Regression: ``-320013`` (longer suffix) must NOT match ``-32001``.
        Without a digit-boundary, plain substring matching would falsely
        accept this."""
        err = Exception('{"code":-320013,"message":"some error"}')
        assert is_mcp_session_expired_error(err) is False

    def test_falls_back_to_code_attribute(self) -> None:
        """Some libs put HTTP status on `.code` instead of `.status_code`."""
        err = Exception('"code":-32001')
        err.code = 404  # type: ignore[attr-defined]
        assert is_mcp_session_expired_error(err) is True

    def test_httpx_shape_status_on_response(self) -> None:
        """httpx errors put status on ``.response.status_code``, not on the
        exception itself. Once Phase 2 lands HttpTransport, this is the
        dominant shape the function will see."""

        class _FakeResponse:
            status_code = 404

        class _HttpxLikeError(Exception):
            def __init__(self, message: str) -> None:
                super().__init__(message)
                self.response = _FakeResponse()

        err = _HttpxLikeError('{"code":-32001,"message":"session expired"}')
        assert is_mcp_session_expired_error(err) is True

    def test_httpx_shape_with_wrong_status(self) -> None:
        """Same shape as above but HTTP 500 — must return False."""

        class _FakeResponse:
            status_code = 500

        class _HttpxLikeError(Exception):
            def __init__(self, message: str) -> None:
                super().__init__(message)
                self.response = _FakeResponse()

        err = _HttpxLikeError('"code":-32001')
        assert is_mcp_session_expired_error(err) is False

    def test_http_status_enum_coerced_to_int(self) -> None:
        """Some clients (httpx with newer Python) expose HTTPStatus enum."""
        from http import HTTPStatus

        err = Exception('"code":-32001')
        err.status_code = HTTPStatus.NOT_FOUND  # type: ignore[attr-defined]
        assert is_mcp_session_expired_error(err) is True

    def test_empty_error_returns_false(self) -> None:
        err = Exception()
        assert is_mcp_session_expired_error(err) is False
