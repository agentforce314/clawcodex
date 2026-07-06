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
# OAuth needs a longer budget: the user opens a browser, authenticates, and the
# local callback completes before the token exchange returns.
_OAUTH_TIMEOUT_S = 300.0


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
        # ConnectedMCPServer objects (name + server-authored ``instructions``
        # from the InitializeResult handshake). The connect() return used to
        # be DISCARDED here, so instructions never reached the system prompt
        # (the UTILS-1 inert-wiring gap) — retained so the prompt build can
        # render the "# MCP Server Instructions" section (C2).
        self.server_infos: list[Any] = []
        # Servers that returned needs-auth (OAuth) from connect() — retained
        # (name, auth_url, scoped-config) so the runtime can surface a
        # "run /mcp auth <server>" prompt and later trigger the flow (C4).
        # Without an injected auth_provider the live path could never even
        # detect needs-auth; now it can, and the server stays reachable for
        # the auth trigger instead of silently failing to connect.
        self.needs_auth: list[dict[str, Any]] = []
        self._auth_provider: Any = None

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

        # One shared auth provider for the whole runtime — so an OAuth server's
        # connect() can detect + cache needs-auth (client.py consults
        # self._auth_provider), and the /mcp auth trigger can later run the flow
        # against the same token store (C4).
        try:
            from src.services.mcp.auth_provider import McpAuthProvider

            self._auth_provider = McpAuthProvider()
        except Exception:  # noqa: BLE001 — no auth provider ⇒ no OAuth, still works
            logger.debug("[mcp] auth provider unavailable", exc_info=True)
            self._auth_provider = None

        for name, scoped in enabled.items():
            try:
                client = McpClient()
                if self._auth_provider is not None:
                    client.set_auth_provider(self._auth_provider)
                connected = self._run(client.connect(name, scoped), _CONNECT_TIMEOUT_S)
                self.clients[name] = client
                # An OAuth server returns needs-auth instead of connected:
                # retain it (with its auth_url) + surface a prompt, and do NOT
                # list_tools (there are none until the user authenticates).
                if getattr(connected, "type", "") == "needs-auth":
                    self.needs_auth.append({
                        "name": name,
                        "auth_url": getattr(connected, "auth_url", None),
                        "scoped": scoped,
                    })
                    logger.info(
                        "[mcp] %s needs authentication — run `/mcp auth %s`",
                        name, name,
                    )
                    continue
                mcp_tools = self._run(client.list_tools(), _CONNECT_TIMEOUT_S)
                self.servers[name] = [t.name for t in mcp_tools]
                # Keep the server info (name + instructions) for the prompt —
                # only truly-connected servers carry instructions.
                if getattr(connected, "type", "") == "connected":
                    self.server_infos.append(connected)
                for mt in mcp_tools:
                    self.tools.append(self._wrap(name, mt, client))
                logger.info("[mcp] connected %s (%d tools)", name, len(mcp_tools))
            except Exception:  # noqa: BLE001 — one bad server must not sink the rest
                logger.exception("[mcp] connect failed: %s", name)

        # Keep the runtime alive if ANY server connected OR needs auth — a
        # needs-auth-only runtime must survive so `/mcp auth` can trigger the
        # flow (previously a tool-less runtime was torn down immediately).
        if not self.tools and not self.needs_auth:
            self.shutdown()
            return False
        return True

    def pending_auth(self) -> list[str]:
        """Names of servers awaiting authentication (for the /mcp UI)."""
        return [e["name"] for e in self.needs_auth]

    def trigger_oauth(self, name: str, *, open_browser: bool = True) -> dict[str, Any]:
        """Run the OAuth flow for a needs-auth server, then reconnect + register
        its tools (C4). Returns ``{ok, error?, tools?}``. Runs on the runtime's
        dedicated loop so the reconnected client's streams stay loop-bound."""
        if self._loop is None or self._auth_provider is None:
            return {"ok": False, "error": "MCP runtime has no auth provider"}
        entry = next((e for e in self.needs_auth if e["name"] == name), None)
        if entry is None:
            return {"ok": False, "error": f"{name!r} is not awaiting authentication"}
        scoped = entry["scoped"]
        inner = getattr(scoped, "config", None)
        server_url = getattr(inner, "url", None)
        if not server_url:
            return {"ok": False, "error": "OAuth requires an HTTP/SSE/WS server URL"}
        try:
            result = self._run(
                self._auth_provider.acquire_token(
                    server_name=name,
                    server_url=server_url,
                    auth_server_metadata_url=getattr(inner, "auth_server_metadata_url", None),
                    config_scope=getattr(scoped, "scope", None),
                    open_browser=open_browser,
                ),
                _OAUTH_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[mcp] OAuth flow failed for %s", name)
            return {"ok": False, "error": f"OAuth flow failed: {exc}"}
        if not getattr(result, "success", False):
            return {"ok": False, "error": getattr(result, "error", None) or "authentication failed"}

        # Authenticated — reconnect with the now-cached token + register tools.
        try:
            from src.services.mcp.client import McpClient

            client = McpClient()
            client.set_auth_provider(self._auth_provider)
            connected = self._run(client.connect(name, scoped), _CONNECT_TIMEOUT_S)
            if getattr(connected, "type", "") != "connected":
                return {"ok": False, "error": "reconnect did not complete after auth"}
            mcp_tools = self._run(client.list_tools(), _CONNECT_TIMEOUT_S)
            # promote: drop the old client, register the connected one
            old = self.clients.get(name)
            if old is not None and old is not client:
                try:
                    self._run(old.close(), 5.0)
                except Exception:  # noqa: BLE001
                    pass
            self.clients[name] = client
            self.servers[name] = [t.name for t in mcp_tools]
            self.server_infos.append(connected)
            new_tools = [self._wrap(name, mt, client) for mt in mcp_tools]
            self.tools.extend(new_tools)
            self.needs_auth = [e for e in self.needs_auth if e["name"] != name]
            logger.info("[mcp] %s authenticated (%d tools)", name, len(new_tools))
            return {"ok": True, "tools": new_tools}
        except Exception as exc:  # noqa: BLE001
            logger.exception("[mcp] reconnect after auth failed for %s", name)
            return {"ok": False, "error": f"reconnect failed: {exc}"}

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

    def _apply_refreshed_tools(
        self, name: str, new_raw: list, client: Any
    ) -> tuple[list[str], list]:
        """ch15 round-4 — swap in a server's freshly-fetched tools.

        Pure w.r.t. the event loop (no I/O) so it's unit-testable: given the
        new raw MCP tool list, rebuild the wrapped tools, replace this
        server's slice of self.tools/self.servers, and return
        ``(removed_full_names, new_wrapped_tools)`` so the caller can swap
        them in the live agent registry. Returns the FULL
        ``mcp__server__tool`` names removed (what registry.unregister keys on).
        """
        from src.services.mcp.mcp_string_utils import build_mcp_tool_name

        # R5 (ch15 m2) — derive the removed set from THIS server's KNOWN tool
        # names, not a ``startswith("mcp__{name}__")`` prefix. A sibling
        # server whose name normalizes to share the prefix (e.g. "foo, bar"
        # → mcp__foo__bar__…, which startswith mcp__foo__) would be wrongly
        # captured and nuked when refreshing "foo".
        removed_full = [
            build_mcp_tool_name(name, t) for t in self.servers.get(name, [])
        ]
        # R5 (ch15 m1) — build the new wrapped tools BEFORE mutating
        # self.tools, so a _wrap failure leaves the current tool list intact.
        # (Was remove-then-build: a mid-build failure truncated self.tools AND
        # was swallowed on the discarded future, leaving the server's tools
        # gone with no refresh.)
        new_tools = [self._wrap(name, mt, client) for mt in new_raw]
        removed_set = set(removed_full)
        self.tools = [
            t for t in self.tools if getattr(t, "name", "") not in removed_set
        ]
        self.tools.extend(new_tools)
        self.servers[name] = [t.name for t in new_raw]
        return removed_full, new_tools

    async def _refresh_server_tools_async(self, name: str, on_change: Any) -> None:
        """Re-fetch a server's tools (on the connection loop) and hand the
        diff to ``on_change(removed_full_names, new_tools)``. Guarded."""
        client = self.clients.get(name)
        if client is None:
            return
        try:
            new_raw = await client.list_tools()
        except Exception:  # noqa: BLE001
            logger.debug("[mcp] refresh list_tools failed: %s", name, exc_info=True)
            return
        removed_full, new_tools = self._apply_refreshed_tools(name, new_raw, client)
        try:
            on_change(removed_full, new_tools)
        except Exception:  # noqa: BLE001
            logger.debug("[mcp] refresh on_change failed: %s", name, exc_info=True)
        logger.info("[mcp] %s tools refreshed (list_changed): %d tool(s)",
                    name, len(new_tools))

    def schedule_tool_refresh(self, name: str, on_change: Any) -> None:
        """ch15 round-4 — schedule a tools/list_changed refresh on the
        connection loop. Safe to call from the loop thread (the notification
        dispatch path) — it schedules without blocking, so no self-deadlock."""
        loop = self._loop
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._refresh_server_tools_async(name, on_change), loop
            )
        except Exception:  # noqa: BLE001
            logger.debug("[mcp] schedule refresh failed: %s", name, exc_info=True)

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
        self.servers = {}
        self.server_infos = []  # consistency: a re-start() would double-append otherwise
