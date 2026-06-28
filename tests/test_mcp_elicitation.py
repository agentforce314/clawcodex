"""MCP elicitation handling (server→client requests) in McpClient."""
import asyncio

from src.services.mcp.client import McpClient
from src.services.mcp.transport import JsonRpcMessage, McpTransport


class _FakeTransport(McpTransport):
    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.sent = []
        self._open = True

    async def start(self): ...

    async def send(self, message):
        self.sent.append(message)

    async def receive(self):
        if self._incoming:
            return self._incoming.pop(0)
        # one short tick, then signal close so the loop exits
        await asyncio.sleep(0.02)
        self._open = False
        return None

    async def close(self):
        self._open = False

    @property
    def is_connected(self):
        return self._open


def _run(client, transport):
    async def go():
        client._transport = transport
        await client._receive_loop()
        # let any out-of-band handler task finish + send its reply
        await asyncio.sleep(0.05)
    asyncio.run(go())


def test_elicitation_default_declines():
    req = JsonRpcMessage(method="elicitation/create", id=7, params={"message": "Name?"})
    tx = _FakeTransport([req])
    c = McpClient()
    _run(c, tx)
    replies = [m for m in tx.sent if m.id == 7]
    assert replies, "no reply sent to elicitation/create"
    assert replies[0].result == {"action": "decline"}


def test_elicitation_uses_handler():
    captured = {}

    async def handler(params):
        captured.update(params)
        return {"action": "accept", "content": {"name": "Ada"}}

    req = JsonRpcMessage(method="elicitation/create", id=9, params={"message": "Name?"})
    tx = _FakeTransport([req])
    c = McpClient()
    c.set_elicitation_handler(handler)
    _run(c, tx)
    replies = [m for m in tx.sent if m.id == 9]
    assert replies and replies[0].result == {"action": "accept", "content": {"name": "Ada"}}
    assert captured.get("message") == "Name?"


def test_unknown_request_method_errors():
    req = JsonRpcMessage(method="sampling/createMessage", id=11, params={})
    tx = _FakeTransport([req])
    c = McpClient()
    _run(c, tx)
    replies = [m for m in tx.sent if m.id == 11]
    assert replies and replies[0].error and replies[0].error["code"] == -32601
