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


class TestHttpTransport:
    def test_init(self) -> None:
        transport = HttpTransport(url="https://example.com")
        assert transport.is_connected is False

    @pytest.mark.asyncio
    async def test_start_close(self) -> None:
        transport = HttpTransport(url="https://example.com")
        await transport.start()
        assert transport.is_connected is True
        await transport.close()
        assert transport.is_connected is False

    @pytest.mark.asyncio
    async def test_send_not_implemented(self) -> None:
        transport = HttpTransport(url="https://example.com")
        await transport.start()
        with pytest.raises(NotImplementedError):
            await transport.send(JsonRpcMessage(method="test"))
        await transport.close()


class TestSseTransport:
    def test_init(self) -> None:
        transport = SseTransport(url="https://example.com/sse")
        assert transport.is_connected is False

    @pytest.mark.asyncio
    async def test_send_not_implemented(self) -> None:
        transport = SseTransport(url="https://example.com")
        await transport.start()
        with pytest.raises(NotImplementedError):
            await transport.send(JsonRpcMessage(method="test"))
        await transport.close()
