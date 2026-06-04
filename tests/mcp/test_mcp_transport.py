from __future__ import annotations

import asyncio
import json
import pytest
from src.services.mcp.transport import (
    JsonRpcMessage,
    StdioTransport,
    HttpTransport,
    SseTransport,
)


class TestJsonRpcMessage:
    def test_to_dict_request(self) -> None:
        msg = JsonRpcMessage(method="initialize", params={"foo": "bar"}, id=1)
        d = msg.to_dict()
        assert d["jsonrpc"] == "2.0"
        assert d["method"] == "initialize"
        assert d["params"] == {"foo": "bar"}
        assert d["id"] == 1
        assert "result" not in d
        assert "error" not in d

    def test_to_dict_response(self) -> None:
        msg = JsonRpcMessage(result={"ok": True}, id=1)
        d = msg.to_dict()
        assert d["result"] == {"ok": True}
        assert d["id"] == 1
        assert "method" not in d

    def test_to_dict_notification(self) -> None:
        msg = JsonRpcMessage(method="notify")
        d = msg.to_dict()
        assert d["method"] == "notify"
        assert "id" not in d

    def test_from_dict(self) -> None:
        data = {"jsonrpc": "2.0", "method": "test", "params": {"x": 1}, "id": 42}
        msg = JsonRpcMessage.from_dict(data)
        assert msg.method == "test"
        assert msg.params == {"x": 1}
        assert msg.id == 42

    def test_round_trip(self) -> None:
        original = JsonRpcMessage(method="tools/list", params={}, id=5)
        restored = JsonRpcMessage.from_dict(original.to_dict())
        assert restored.method == original.method
        assert restored.id == original.id


class TestStdioTransport:
    def test_init(self) -> None:
        transport = StdioTransport(command="echo", args=["hello"])
        assert transport._command == "echo"
        assert transport._args == ["hello"]
        assert transport.is_connected is False

    @pytest.mark.asyncio
    async def test_start_and_close_echo(self) -> None:
        transport = StdioTransport(command="echo", args=["test"])
        await transport.start()
        assert transport.is_connected is True
        await transport.close()
        assert transport.is_connected is False

    @pytest.mark.asyncio
    async def test_send_receive_with_cat(self) -> None:
        transport = StdioTransport(command="cat")
        await transport.start()
        assert transport.is_connected

        msg = JsonRpcMessage(method="test", params={"x": 1}, id=1)
        await transport.send(msg)

        response = await asyncio.wait_for(transport.receive(), timeout=5.0)
        assert response is not None
        assert response.method == "test"
        assert response.id == 1

        await transport.close()

    @pytest.mark.asyncio
    async def test_receives_multiple_messages_back_to_back(self) -> None:
        """Regression guard: confirms message boundaries are preserved across
        sequential sends. A buffering-bug regression (e.g. switching readline
        for read(N)) would surface here as merged or dropped messages."""
        transport = StdioTransport(command="cat")
        await transport.start()

        await transport.send(JsonRpcMessage(method="m1", id=1))
        await transport.send(JsonRpcMessage(method="m2", id=2))
        await transport.send(JsonRpcMessage(method="m3", id=3))

        r1 = await asyncio.wait_for(transport.receive(), timeout=5.0)
        r2 = await asyncio.wait_for(transport.receive(), timeout=5.0)
        r3 = await asyncio.wait_for(transport.receive(), timeout=5.0)

        assert r1 is not None and r2 is not None and r3 is not None
        assert (r1.method, r1.id) == ("m1", 1)
        assert (r2.method, r2.id) == ("m2", 2)
        assert (r3.method, r3.id) == ("m3", 3)

        await transport.close()

    @pytest.mark.asyncio
    async def test_receive_strips_crlf_line_endings(self) -> None:
        """Regression guard: a Node-on-Windows / `print()`-on-Windows server may
        emit CRLF terminators. The receiver must strip both."""
        # Use printf to write a CRLF-terminated JSON line to stdout.
        crlf_payload = '{"jsonrpc":"2.0","method":"crlf","id":1}\r\n'
        transport = StdioTransport(
            command="printf",
            args=["%s", crlf_payload],
        )
        await transport.start()
        response = await asyncio.wait_for(transport.receive(), timeout=5.0)
        assert response is not None
        assert response.method == "crlf"
        assert response.id == 1
        await transport.close()


class TestHttpTransport:
    """HttpTransport now wraps ``mcp.client.streamable_http.streamable_http_client``
    (Path A, ch15-mcp Phase 2). It speaks Streamable HTTP per the MCP spec. End-to-
    end behavior is exercised in ``tests/integration/test_real_mcp_server.py``;
    these unit tests cover instantiation + lifecycle wiring.
    """

    def test_init(self) -> None:
        transport = HttpTransport(url="https://example.com")
        assert transport.is_connected is False

    @pytest.mark.asyncio
    async def test_start_allocates_streams_does_not_validate_connectivity(self) -> None:
        """``HttpTransport.start()`` enters the SDK's async context manager,
        which allocates the read/write streams + spawns task-group workers
        but does **not** make a real HTTP request. So ``start()`` succeeds
        even against an unreachable URL; connectivity surfaces on the first
        ``send()`` (typically the ``initialize`` request from
        ``McpClient.connect()``). This test documents that contract — it is
        NOT a connectivity check, just a lifecycle smoke-test."""
        transport = HttpTransport(url="https://example.com")
        await transport.start()
        assert transport.is_connected is True
        await transport.close()
        assert transport.is_connected is False


class TestSseTransport:
    """SseTransport now wraps ``mcp.client.sse.sse_client`` (Path A)."""

    def test_init(self) -> None:
        transport = SseTransport(url="https://example.com/sse")
        assert transport.is_connected is False


class TestWebSocketTransport:
    """WebSocketTransport wraps ``mcp.client.websocket.websocket_client``."""

    def test_init(self) -> None:
        from src.services.mcp.transport import WebSocketTransport

        transport = WebSocketTransport(url="ws://example.com/mcp")
        assert transport.is_connected is False
