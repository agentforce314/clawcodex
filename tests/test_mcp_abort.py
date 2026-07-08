"""#277 — MCP tool calls honor abort_controller (ESC-cancel).

A pending ``tools/call`` must unblock the moment the abort signal trips
(not at the multi-minute MCP request timeout), send the MCP
``notifications/cancelled`` notification so a compliant server stops the
work, and surface as ``AbortError`` so the dispatch layer renders the
user-cancel message instead of a generic tool error.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from src.services.mcp.client import McpClient
from src.services.mcp.transport import JsonRpcMessage
from src.utils.abort_controller import AbortController, AbortError


class _HangingTransport:
    """Records sends; never delivers a response (a hung MCP server)."""

    def __init__(self):
        self.sent: list[JsonRpcMessage] = []
        self._closed = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        return not self._closed.is_set()

    async def send(self, message: JsonRpcMessage) -> None:
        self.sent.append(message)

    async def receive(self) -> JsonRpcMessage | None:
        await self._closed.wait()
        return None

    async def close(self) -> None:
        self._closed.set()


def _make_client() -> tuple[McpClient, _HangingTransport]:
    client = McpClient()
    transport = _HangingTransport()
    client._transport = transport
    return client, transport


def _abort_after(controller: AbortController, delay_s: float) -> None:
    t = threading.Timer(delay_s, lambda: controller.abort("user_interrupt"))
    t.daemon = True
    t.start()


class TestMcpAbort:
    @pytest.mark.asyncio
    async def test_abort_unblocks_pending_call_fast(self):
        client, transport = _make_client()
        controller = AbortController()
        _abort_after(controller, 0.1)

        start = time.monotonic()
        with pytest.raises(AbortError):
            await client.call_tool(
                "slow_tool", {}, abort_signal=controller.signal
            )
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"abort took {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_abort_sends_cancellation_notification(self):
        client, transport = _make_client()
        controller = AbortController()
        _abort_after(controller, 0.05)

        with pytest.raises(AbortError):
            await client.call_tool("slow_tool", {}, abort_signal=controller.signal)

        call_msg = transport.sent[0]
        assert call_msg.method == "tools/call"
        cancels = [m for m in transport.sent if m.method == "notifications/cancelled"]
        assert len(cancels) == 1
        assert cancels[0].params["requestId"] == call_msg.id
        assert cancels[0].params["reason"] == "user_interrupt"
        assert cancels[0].id is None  # notification, not a request

    @pytest.mark.asyncio
    async def test_abort_cleans_pending_request(self):
        client, transport = _make_client()
        controller = AbortController()
        _abort_after(controller, 0.05)

        with pytest.raises(AbortError):
            await client.call_tool("slow_tool", {}, abort_signal=controller.signal)
        assert client._pending_requests == {}

    @pytest.mark.asyncio
    async def test_pre_aborted_signal_raises_before_sending(self):
        client, transport = _make_client()
        controller = AbortController()
        controller.abort("user_interrupt")

        with pytest.raises(AbortError):
            await client.call_tool("slow_tool", {}, abort_signal=controller.signal)
        assert transport.sent == []

    @pytest.mark.asyncio
    async def test_listener_removed_after_normal_completion(self):
        client, transport = _make_client()
        controller = AbortController()

        async def _respond():
            while not transport.sent:
                await asyncio.sleep(0.01)
            req = transport.sent[0]
            future = client._pending_requests[req.id]
            future.set_result({"content": [{"type": "text", "text": "ok"}]})

        responder = asyncio.create_task(_respond())
        result = await client.call_tool("tool", {}, abort_signal=controller.signal)
        await responder

        assert result.content[0]["text"] == "ok"
        assert controller.signal._listeners == []

    @pytest.mark.asyncio
    async def test_send_failure_cleans_listener_and_pending(self):
        """transport.send raising must not leak the abort listener or the
        pending future (the finally covers the send, not just the wait)."""
        client, transport = _make_client()

        async def _broken_send(message):
            raise ConnectionError("broken pipe")

        transport.send = _broken_send  # type: ignore[method-assign]
        controller = AbortController()

        with pytest.raises(ConnectionError):
            await client.call_tool("tool", {}, abort_signal=controller.signal)
        assert controller.signal._listeners == []
        assert client._pending_requests == {}

    @pytest.mark.asyncio
    async def test_without_signal_behavior_unchanged(self):
        client, transport = _make_client()

        async def _respond():
            while not transport.sent:
                await asyncio.sleep(0.01)
            req = transport.sent[0]
            client._pending_requests[req.id].set_result(
                {"content": [{"type": "text", "text": "plain"}]}
            )

        responder = asyncio.create_task(_respond())
        result = await client.call_tool("tool", {})
        await responder
        assert result.content[0]["text"] == "plain"


class TestReceiveLoopCancelledRace:
    @pytest.mark.asyncio
    async def test_late_response_for_cancelled_future_does_not_kill_loop(self):
        """A response racing the abort cleanup must not raise
        InvalidStateError inside the receive loop."""
        client = McpClient()

        class _ScriptedTransport(_HangingTransport):
            def __init__(self):
                super().__init__()
                self.inbox: asyncio.Queue[JsonRpcMessage | None] = asyncio.Queue()

            async def receive(self) -> JsonRpcMessage | None:
                return await self.inbox.get()

        transport = _ScriptedTransport()
        client._transport = transport
        receive_task = asyncio.create_task(client._receive_loop())

        # A cancelled future still registered in pending (the race window).
        loop = asyncio.get_event_loop()
        cancelled_future: asyncio.Future[Any] = loop.create_future()
        cancelled_future.cancel()
        client._pending_requests[1] = cancelled_future

        # A live request that must still resolve afterwards.
        live_future: asyncio.Future[Any] = loop.create_future()
        client._pending_requests[2] = live_future

        await transport.inbox.put(JsonRpcMessage(id=1, result={"late": True}))
        await transport.inbox.put(JsonRpcMessage(id=2, result={"ok": True}))

        assert await asyncio.wait_for(live_future, timeout=2) == {"ok": True}
        await transport.inbox.put(None)
        await asyncio.wait_for(receive_task, timeout=2)


class TestToolWrapperAbortPropagation:
    @pytest.mark.asyncio
    async def test_wrapper_reraises_abort_error(self, tmp_path):
        """AbortError must escape the wrapper's except-Exception so the
        dispatch layer renders the user-cancel message."""
        from src.permissions.types import ToolPermissionContext
        from src.services.mcp.tool_wrapper import wrap_mcp_tool
        from src.services.mcp.types import McpToolSchema
        from src.tool_system.context import ToolContext

        class _AbortingClient:
            async def call_tool(self, *args, **kwargs):
                raise AbortError("user_interrupt")

        tool = wrap_mcp_tool(
            "srv",
            McpToolSchema(name="t", description="", input_schema={"type": "object"}),
            _AbortingClient(),  # type: ignore[arg-type]
        )
        ctx = ToolContext(
            workspace_root=tmp_path,
            permission_context=ToolPermissionContext(mode="bypassPermissions"),
        )
        with pytest.raises(AbortError):
            tool.call({}, ctx)
