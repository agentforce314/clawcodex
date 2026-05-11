"""Runtime MCP connection manager.

Phase 9 WI-9.1 (gap #22). Mirrors the runtime portion of TS'
``services/mcp/useManageMCPConnections.ts`` (the parts that don't
require a React render tree). Provides:

* ``reconnect_mcp_server(name)`` — clear cache + reconnect; updates
  client/tool tables.
* ``toggle_mcp_server(name)`` — flip enabled bit; reconnect if newly
  enabled, drop client if newly disabled.
* ``inject_dynamic_config(name, config)`` — SDK-time server injection
  (mirrors ``add_dynamic_mcp_config`` but with side-effect connect).
* ``trigger_oauth(name)`` — initiate the OAuth flow for a server in
  the ``needs-auth`` state via the bound auth provider.

The manager keeps an internal ``state`` map of ``MCPServerConnection``
objects so consumers (UI / agent loop / coordinator) can subscribe via
``snapshot()`` or query a single server via ``get_state(name)``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.tool_system.build_tool import Tool

from .client import McpClient, clear_connection_cache, connect_to_server
from .config import (
    add_dynamic_mcp_config,
    get_mcp_config_by_name,
    is_mcp_server_disabled,
    remove_dynamic_mcp_config,
    set_mcp_server_enabled,
)
from .tool_wrapper import wrap_mcp_tools_for_server
from .types import (
    ConnectedMCPServer,
    DisabledMCPServer,
    FailedMCPServer,
    MCPServerConnection,
    McpServerConfig,
    NeedsAuthMCPServer,
    PendingMCPServer,
    ScopedMcpServerConfig,
)

logger = logging.getLogger(__name__)


class MCPConnectionManager:
    """Runtime lifecycle owner for active MCP server connections.

    Not thread-safe; intended to live on the asyncio event loop and be
    called from a single task at a time. A per-server ``asyncio.Lock``
    serializes concurrent reconnect/toggle attempts for the same server.
    """

    def __init__(self, auth_provider: Any | None = None) -> None:
        self._auth_provider = auth_provider
        self._state: dict[str, MCPServerConnection] = {}
        self._clients: dict[str, McpClient] = {}
        self._tools: dict[str, list[Tool]] = {}
        self._server_locks: dict[str, asyncio.Lock] = {}

    # --- Read surface ----------------------------------------------------

    def snapshot(self) -> dict[str, MCPServerConnection]:
        """Return a defensive copy of the current state map."""
        return dict(self._state)

    def get_state(self, name: str) -> MCPServerConnection | None:
        return self._state.get(name)

    def get_tools(self, name: str) -> list[Tool]:
        """Return tools currently wrapped for the named server (or empty
        if the server is not Connected)."""
        return list(self._tools.get(name, ()))

    def all_tools(self) -> list[Tool]:
        """Return all tools across all currently-connected servers."""
        flat: list[Tool] = []
        for tools in self._tools.values():
            flat.extend(tools)
        return flat

    # --- Write surface ---------------------------------------------------

    async def reconnect_mcp_server(self, name: str) -> MCPServerConnection:
        """Force a fresh connection for the named server.

        Steps:
          1. Clear the (name, content-signature) cache entry so the next
             ``connect_to_server`` builds a fresh transport.
          2. Drop the existing client (if any).
          3. Publish ``PendingMCPServer`` state so observers can render
             an in-flight indicator while the connect runs (Critic FU#4).
          4. ``connect_to_server`` for the named server's config.
          5. If Connected, wrap and store tools.
        """
        config = get_mcp_config_by_name(name)
        if config is None:
            return FailedMCPServer(name=name, error=f"No config for {name!r}")

        async with self._lock_for(name):
            await self._drop_client(name)
            clear_connection_cache(name)
            # Publish in-flight state so observers can render a spinner /
            # placeholder while the connect attempt is running. The state
            # gets overwritten with the terminal connection state below.
            self._state[name] = PendingMCPServer(
                name=name,
                config=config,
                reconnect_attempt=1,
                max_reconnect_attempts=1,
            )
            # try/finally so a hard exception (CancelledError, etc.) in
            # connect_to_server doesn't leave stale PendingMCPServer in
            # self._state. On exception, replace with a FailedMCPServer
            # carrying the exception text so observers see a terminal
            # state and the next reconnect attempt isn't fooled by
            # leftover Pending. Critic FU#4b.
            try:
                # Bind auth_provider BEFORE connect so the NeedsAuth fast-path
                # + auth-header injection take effect on the very first attempt.
                client, conn = await connect_to_server(
                    name, config, auth_provider=self._auth_provider
                )
            except BaseException as exc:
                self._state[name] = FailedMCPServer(
                    name=name,
                    config=config,
                    error=f"{type(exc).__name__}: {exc}",
                )
                self._tools.pop(name, None)
                raise
            self._state[name] = conn
            if isinstance(conn, ConnectedMCPServer):
                self._clients[name] = client
                tools_raw = await client.list_tools()
                self._tools[name] = wrap_mcp_tools_for_server(conn, tools_raw, client)
            else:
                self._tools.pop(name, None)
            return conn

    async def toggle_mcp_server(self, name: str) -> MCPServerConnection:
        """Flip the enabled/disabled bit and reconcile the state.

        The entire toggle (flip + reconcile) runs under a single lock
        critical section. An earlier two-step design released the lock
        between the flip and the reconnect call, leaving a window where
        a concurrent toggle could observe the just-flipped state and
        re-flip it, producing inconsistent UI state. We do the inline
        connect here rather than calling self.reconnect_mcp_server (which
        re-acquires) to keep the toggle atomic.
        """
        async with self._lock_for(name):
            if is_mcp_server_disabled(name):
                set_mcp_server_enabled(name, True)
                # Inline reconnect — see reconnect_mcp_server for the
                # canonical flow; we duplicate here to keep the toggle
                # atomic under one lock acquisition.
                config = get_mcp_config_by_name(name)
                if config is None:
                    failed = FailedMCPServer(
                        name=name, error=f"No config for {name!r}"
                    )
                    self._state[name] = failed
                    self._tools.pop(name, None)
                    return failed
                await self._drop_client(name)
                clear_connection_cache(name)
                # Publish in-flight state for the toggle-induced reconnect
                # (Critic FU#4). Overwritten below with the terminal state.
                self._state[name] = PendingMCPServer(
                    name=name,
                    config=config,
                    reconnect_attempt=1,
                    max_reconnect_attempts=1,
                )
                # try/finally guard mirrors reconnect_mcp_server: avoid
                # stale PendingMCPServer if connect_to_server raises.
                try:
                    client, conn = await connect_to_server(
                        name, config, auth_provider=self._auth_provider
                    )
                except BaseException as exc:
                    self._state[name] = FailedMCPServer(
                        name=name,
                        config=config,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    self._tools.pop(name, None)
                    raise
                self._state[name] = conn
                if isinstance(conn, ConnectedMCPServer):
                    self._clients[name] = client
                    tools_raw = await client.list_tools()
                    self._tools[name] = wrap_mcp_tools_for_server(
                        conn, tools_raw, client
                    )
                else:
                    self._tools.pop(name, None)
                return conn
            set_mcp_server_enabled(name, False)
            await self._drop_client(name)
            clear_connection_cache(name)
            disabled = DisabledMCPServer(name=name)
            self._state[name] = disabled
            self._tools.pop(name, None)
            return disabled

    async def inject_dynamic_config(
        self, name: str, config: McpServerConfig, *, auto_connect: bool = True
    ) -> MCPServerConnection | None:
        """Register a runtime / SDK-injected server. If ``auto_connect``
        is True, immediately attempt to connect; otherwise just register
        and let a later ``reconnect_mcp_server(name)`` drive the connect.

        Returns the connection state if auto_connect=True, else None.
        """
        add_dynamic_mcp_config(name, config)
        if not auto_connect:
            return None
        return await self.reconnect_mcp_server(name)

    async def remove_dynamic(self, name: str) -> bool:
        """Counterpart to ``inject_dynamic_config``: drop the SDK-injected
        registration and tear down the connection. Returns True if a
        registration existed and was removed."""
        async with self._lock_for(name):
            removed = remove_dynamic_mcp_config(name)
            await self._drop_client(name)
            clear_connection_cache(name)
            self._state.pop(name, None)
            self._tools.pop(name, None)
        return removed

    async def trigger_oauth(
        self, name: str, *, open_browser: bool = True
    ) -> MCPServerConnection:
        """Initiate the OAuth flow for a server currently in needs-auth.

        Returns the resulting ``MCPServerConnection`` — Connected on
        success, NeedsAuthMCPServer (still) or FailedMCPServer on
        failure.
        """
        if self._auth_provider is None:
            return FailedMCPServer(
                name=name, error="No auth provider configured"
            )
        config = get_mcp_config_by_name(name)
        if config is None:
            return FailedMCPServer(name=name, error=f"No config for {name!r}")
        inner = config.config
        server_url = getattr(inner, "url", None)
        if not server_url:
            return FailedMCPServer(
                name=name, error="OAuth flow requires an HTTP/SSE/WS server URL"
            )
        async with self._lock_for(name):
            result = await self._auth_provider.acquire_token(
                server_name=name,
                server_url=server_url,
                auth_server_metadata_url=getattr(inner, "auth_server_metadata_url", None),
                # Pass the scope so auth_discovery can reject
                # authServerMetadataUrl overrides from repo-write scopes
                # (project / local). Critic FU#3.
                config_scope=config.scope,
                open_browser=open_browser,
            )
            if not result.success:
                return NeedsAuthMCPServer(
                    name=name,
                    config=config,
                    auth_url=None,
                    auth_method="oauth",
                    requires_user_action=True,
                    error=result.error,
                )
        # Connect happens outside the OAuth lock; reconnect_mcp_server
        # re-acquires it.
        return await self.reconnect_mcp_server(name)

    async def close_all(self) -> None:
        """Tear down every active client. Idempotent."""
        names = list(self._clients.keys())
        for name in names:
            async with self._lock_for(name):
                await self._drop_client(name)

    async def bootstrap_all_servers(
        self, *, batch_size: int = 5
    ) -> dict[str, MCPServerConnection]:
        """Connect to every enabled MCP server in the merged config.

        Critic FU#5: canonical runtime mount point. Replaces the older
        callback-based ``prefetch_all_mcp_resources`` for callers that
        want the manager surface (reconnect, toggle, trigger_oauth,
        snapshot subscription).

        Steps:
          1. Read merged configs via ``get_all_mcp_configs`` (warms with
             whatever claudeai snapshot is currently cached).
          2. For each enabled server, kick off ``reconnect_mcp_server``,
             concurrently up to ``batch_size``. Each call already takes
             the per-server lock so this is safe.
          3. Return a defensive copy of the resulting state map.

        Disabled servers get a ``DisabledMCPServer`` entry without a
        connect attempt. Failed servers retain a ``FailedMCPServer``.
        """
        from .config import get_all_mcp_configs  # local import: avoid cycle
        configs, _errors = get_all_mcp_configs()

        sem = asyncio.Semaphore(max(1, batch_size))

        async def _bootstrap_one(name: str) -> None:
            if is_mcp_server_disabled(name):
                cfg = configs.get(name)
                self._state[name] = DisabledMCPServer(name=name, config=cfg)
                return
            async with sem:
                await self.reconnect_mcp_server(name)

        await asyncio.gather(
            *(_bootstrap_one(name) for name in configs),
            return_exceptions=True,
        )
        return self.snapshot()

    # --- Internals -------------------------------------------------------

    def _lock_for(self, name: str) -> asyncio.Lock:
        lock = self._server_locks.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self._server_locks[name] = lock
        return lock

    async def _drop_client(self, name: str) -> None:
        client = self._clients.pop(name, None)
        if client is None:
            return
        try:
            await client.close()
        except Exception as exc:  # pragma: no cover - shutdown variance
            logger.debug(
                "MCP connection_manager: client.close raised for %r: %s",
                name, exc,
            )


async def bootstrap_mcp_runtime(
    *,
    auth_provider: Any | None = None,
    prefetch_claudeai: bool = True,
    batch_size: int = 5,
) -> MCPConnectionManager:
    """Build, warm, and return an ``MCPConnectionManager``.

    Critic FU#5 canonical runtime mount. Callers in the agent boot path
    should prefer this over the legacy callback-based
    ``prefetch_all_mcp_resources`` — it returns a manager handle so the
    rest of the runtime can drive reconnect / toggle / trigger_oauth /
    inject_dynamic_config without rebuilding state.

    Steps:
      1. (Optional) warm the claudeai snapshot via
         ``fetch_claudeai_mcp_configs_if_eligible`` so the subsequent
         ``get_all_mcp_configs`` merge picks up web-configured servers.
      2. Construct an ``MCPConnectionManager`` bound to the provided
         auth provider.
      3. Call ``bootstrap_all_servers`` to connect each enabled server.

    The returned manager owns its clients; callers must invoke
    ``manager.close_all()`` on shutdown.
    """
    if prefetch_claudeai:
        try:
            from .claudeai import fetch_claudeai_mcp_configs_if_eligible  # local: cycle
            await fetch_claudeai_mcp_configs_if_eligible(
                auth_provider=auth_provider
            )
        except Exception as exc:  # pragma: no cover - non-fatal
            logger.warning(
                "MCP bootstrap: claudeai prefetch failed (%s); continuing "
                "without web-configured connectors.", exc,
            )
    manager = MCPConnectionManager(auth_provider=auth_provider)
    await manager.bootstrap_all_servers(batch_size=batch_size)
    return manager
