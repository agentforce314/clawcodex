"""Run MCP server connections on a dedicated background asyncio loop and expose
their tools as loop-correct **sync** Tool objects for the agent-server.

Why a dedicated loop: an ``McpClient``'s anyio stdio streams are bound to the
event loop on which ``connect()`` ran. The agent-server runs each turn on a
*fresh* ``asyncio.run`` loop, and ``tool_system.tool_wrapper`` dispatches calls
on yet another worker loop — either of which deadlocks the streams (verified:
the call hangs). So we keep one long-lived loop in a daemon thread, connect all
servers there, and make every tool call hop back to that loop via
``run_coroutine_threadsafe``. This is additive and self-contained — it does not
touch the shared ``tool_wrapper``/``client`` infrastructure the REPL + MCP test
suite depend on.

Usage (guarded — no configured servers ⇒ ``start()`` returns False, no-op):

    rt = McpRuntime()
    if rt.start():
        for t in rt.tools: registry.register(t)
        tool_context.mcp_clients = rt.clients
    ...
    rt.shutdown()
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_CALL_TIMEOUT_S = 60.0
_CONNECT_TIMEOUT_S = 30.0


def _render_content(content: Any) -> str:
    """Flatten an MCP result's content blocks to the str ToolResult.output."""
    if isinstance(content, str):
        return content
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        else:
            parts.append(str(block))
    return "\n".join(parts)


class McpRuntime:
    """Owns the background loop, the connected clients, and the wrapped tools."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self.clients: dict[str, Any] = {}
        self.servers: dict[str, list[str]] = {}  # server name -> tool names
        self.tools: list[Any] = []  # wrapped sync Tool objects (mcp__server__tool)

    def _run(self, coro: Any, timeout: float) -> Any:
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def start(self) -> bool:
        """Connect all enabled, configured MCP servers. Returns True if at least
        one tool was registered. Fully guarded: any failure leaves the runtime
        empty and never raises."""
        try:
            from src.services.mcp.config import get_all_mcp_configs
        except Exception:  # noqa: BLE001
            logger.debug("[mcp] config module unavailable", exc_info=True)
            return False
        try:
            configs = get_all_mcp_configs()
        except Exception:  # noqa: BLE001
            logger.debug("[mcp] reading configs failed", exc_info=True)
            return False
        enabled = {
            name: scoped
            for name, scoped in (configs or {}).items()
            if getattr(getattr(scoped, "config", None), "enabled", True)
        }
        if not enabled:
            return False

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="mcp-loop"
        )
        self._thread.start()

        from src.services.mcp.client import McpClient

        for name, scoped in enabled.items():
            try:
                client = McpClient()
                self._run(client.connect(name, scoped), _CONNECT_TIMEOUT_S)
                mcp_tools = self._run(client.list_tools(), _CONNECT_TIMEOUT_S)
                self.clients[name] = client
                self.servers[name] = [t.name for t in mcp_tools]
                for mt in mcp_tools:
                    self.tools.append(self._wrap(name, mt, client))
                logger.info("[mcp] connected %s (%d tools)", name, len(mcp_tools))
            except Exception:  # noqa: BLE001 — one bad server must not sink the rest
                logger.exception("[mcp] connect failed: %s", name)

        if not self.tools:
            self.shutdown()
            return False
        return True

    def _wrap(self, server: str, mcp_tool: Any, client: Any) -> Any:
        """Build a loop-correct sync Tool that dispatches to the connection loop."""
        from src.services.mcp.mcp_string_utils import build_mcp_tool_name
        from src.tool_system.build_tool import build_tool
        from src.tool_system.protocol import ToolResult

        full = build_mcp_tool_name(server, mcp_tool.name)
        schema = getattr(mcp_tool, "input_schema", None) or {"type": "object", "properties": {}}
        loop = self._loop

        def _call(args: dict[str, Any], ctx: Any) -> ToolResult:
            try:
                fut = asyncio.run_coroutine_threadsafe(client.call_tool(mcp_tool.name, args), loop)
                res = fut.result(_CALL_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(name=full, output=f"MCP call failed: {exc}", is_error=True)
            return ToolResult(
                name=full,
                output=_render_content(getattr(res, "content", None)),
                is_error=bool(getattr(res, "is_error", False)),
            )

        return build_tool(
            name=full,
            input_schema=schema,
            call=_call,
            description=getattr(mcp_tool, "description", None) or f"MCP tool {full}",
            is_mcp=True,
        )

    def shutdown(self) -> None:
        """Disconnect clients and stop the background loop. Idempotent."""
        loop = self._loop
        if loop is None:
            return
        for name, client in list(self.clients.items()):
            try:
                self._run(client.close(), 5.0)
            except Exception:  # noqa: BLE001
                logger.debug("[mcp] close failed: %s", name, exc_info=True)
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:  # noqa: BLE001
            pass
        self._loop = None
        self.clients = {}
        self.tools = []
