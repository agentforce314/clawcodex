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
    def test_with_code_32001(self) -> None:
        err = Exception('"code":-32001')
        assert is_mcp_session_expired_error(err) is True

    def test_with_code_32001_spaced(self) -> None:
        err = Exception('"code": -32001')
        assert is_mcp_session_expired_error(err) is True

    def test_without_code(self) -> None:
        err = Exception("some other error")
        assert is_mcp_session_expired_error(err) is False

    def test_empty_error(self) -> None:
        err = Exception()
        assert is_mcp_session_expired_error(err) is False
