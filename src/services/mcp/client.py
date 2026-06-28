from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable

from .errors import (
    McpAuthError,
    McpSessionExpiredError,
    McpToolCallError,
    is_mcp_session_expired_error,
)
from .transport import (
    HttpTransport,
    JsonRpcMessage,
    McpTransport,
    SseTransport,
    StdioTransport,
    WebSocketTransport,
)
from .types import (
    ConnectedMCPServer,
    DisabledMCPServer,
    FailedMCPServer,
    MCPServerConnection,
    McpHTTPServerConfig,
    McpSSEServerConfig,
    McpServerConfig,
    McpStdioServerConfig,
    McpToolResult,
    McpToolSchema,
    McpWebSocketServerConfig,
    NeedsAuthMCPServer,
    ScopedMcpServerConfig,
    ServerCapabilities,
    ServerInfo,
)

logger = logging.getLogger(__name__)

# Tool-call timeout: 5 minutes default, mirrors TS canonical
# (typescript/src/services/mcp/client.ts:DEFAULT_MCP_TOOL_TIMEOUT_MS = 300_000).
# Operators raise via the MCP_TOOL_TIMEOUT env override when a long-running
# tool (e.g. a deep agentic search) needs a longer cap. The chapter
# (§"Timeout Architecture") notes ~27.8h as the upper-bound budget for
# legitimately long operations; that's the cap, not the default.
DEFAULT_MCP_TOOL_TIMEOUT_MS = 300_000  # 5 min
MAX_MCP_DESCRIPTION_LENGTH = 2048
DEFAULT_CONNECTION_TIMEOUT_MS = 30_000


def _is_remote_config(config: Any) -> bool:
    """Return True for HTTP/SSE/WS configs — the ones that can require OAuth.

    Used to gate auth-provider lookups so stdio / SDK configs never pay
    the OAuth-cache lookup cost.
    """
    return isinstance(
        config, (McpHTTPServerConfig, McpSSEServerConfig, McpWebSocketServerConfig)
    )


def _unwrap_exception_group_message(exc: BaseException) -> str:
    """Extract the most actionable error string from a (possibly nested)
    ``BaseExceptionGroup``.

    The SDK's anyio task groups wrap real connection errors (e.g.
    ``ConnectionRefusedError``) in ``BaseExceptionGroup``, whose ``str()``
    is the opaque ``"unhandled errors in a TaskGroup (1 sub-exception)"``.
    Walk the group tree and return the leaf exception's message — that's
    what the user actually needs to debug an unreachable server.
    """
    try:
        eg_cls = BaseExceptionGroup  # 3.11+ builtin  # type: ignore[name-defined]
    except NameError:  # pragma: no cover - Python < 3.11
        return str(exc) or type(exc).__name__
    if isinstance(exc, eg_cls) and exc.exceptions:
        return _unwrap_exception_group_message(exc.exceptions[0])
    return str(exc) or type(exc).__name__


def _get_connection_timeout_ms() -> int:
    try:
        return int(os.environ.get("MCP_TIMEOUT", "")) or DEFAULT_CONNECTION_TIMEOUT_MS
    except (ValueError, TypeError):
        return DEFAULT_CONNECTION_TIMEOUT_MS


def _get_tool_timeout_ms() -> int:
    try:
        return int(os.environ.get("MCP_TOOL_TIMEOUT", "")) or DEFAULT_MCP_TOOL_TIMEOUT_MS
    except (ValueError, TypeError):
        return DEFAULT_MCP_TOOL_TIMEOUT_MS


MAX_RECONNECT_ATTEMPTS = 5
INITIAL_RECONNECT_DELAY_MS = 1000
MAX_RECONNECT_DELAY_MS = 30000


class McpClient:
    def __init__(self) -> None:
        self._transport: McpTransport | None = None
        self._request_id = 0
        self._pending_requests: dict[int | str, asyncio.Future[Any]] = {}
        self._receive_task: asyncio.Task[None] | None = None
        self._capabilities = ServerCapabilities()
        self._server_info: ServerInfo | None = None
        self._instructions: str | None = None
        self._name: str | None = None
        self._config: ScopedMcpServerConfig | None = None
        self._reconnect_attempt = 0
        self._connected = False
        self._resource_cache: dict[str, list[dict[str, Any]]] = {}
        self._on_disconnect: Callable[[], None] | None = None
        # Phase 6a WI-6.1: serialize concurrent session-expiry recovery.
        # When N parallel call_tool invocations all hit the same expired
        # session, we want exactly one reconnect, not N. The lock + epoch
        # counter ("session generation") implement double-checked recovery:
        # only the first coroutine to take the lock reconnects; the others
        # observe the bumped generation and skip the reconnect step.
        self._recovery_lock: asyncio.Lock | None = None
        self._session_generation = 0
        # Phase 4 WI-4.5: optional OAuth provider. Injected by callers
        # that want OAuth-protected MCP servers to work end-to-end;
        # legacy callers (stdio / open HTTP) pass None.
        self._auth_provider: Any = None
        # MCP elicitation (server→client input requests, §6): optional async
        # handler that presents the request to the user and returns the result
        # ({"action": "accept"|"decline"|"cancel", "content": {...}}). Default
        # (None) declines — a valid response, so elicitation-capable servers no
        # longer hang on an ignored request.
        self._elicitation_handler: Any = None

    def set_elicitation_handler(self, handler: Any) -> None:
        """Inject the async elicitation handler (params dict -> result dict).

        Wired in by the runtime to bridge an MCP server's input request to the
        TUI. When unset, elicitation requests are declined.
        """
        self._elicitation_handler = handler

    def set_auth_provider(self, provider: Any) -> None:
        """Inject the McpAuthProvider used for HTTP/SSE/WS auth flows.

        Wired in by the runtime / connection_manager layer at startup.
        Stored as ``Any`` to keep the client.py ↔ auth_provider.py
        import edge optional — callers that don't use OAuth never
        import the provider module.
        """
        self._auth_provider = provider

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def connect(
        self,
        name: str,
        config: ScopedMcpServerConfig,
    ) -> MCPServerConnection:
        connect_start = time.monotonic()
        # Phase 4 WI-4.8: respect the 15-min needs-auth cache. If a prior
        # attempt determined this server needs OAuth and the operator
        # hasn't completed the flow yet, fast-fail to NeedsAuthMCPServer
        # without retrying the OAuth discovery / browser open on every
        # call.
        if self._auth_provider is not None and _is_remote_config(config.config):
            cached = self._auth_provider.get_needs_auth_state(name)
            if cached is not None:
                return NeedsAuthMCPServer(
                    name=name,
                    config=config,
                    auth_url=cached.auth_url,
                    auth_method="oauth",
                    requires_user_action=True,
                    error=cached.reason,
                )

        try:
            transport = self._create_transport(config.config, server_name=name)
            self._transport = transport

            timeout_ms = _get_connection_timeout_ms()
            try:
                await asyncio.wait_for(
                    transport.start(),
                    timeout=timeout_ms / 1000.0,
                )
            except asyncio.TimeoutError:
                await transport.close()
                elapsed = int((time.monotonic() - connect_start) * 1000)
                logger.debug(
                    "MCP server %r connection timed out after %dms", name, elapsed
                )
                return FailedMCPServer(
                    name=name,
                    error=f"Connection timed out after {timeout_ms}ms",
                    config=config,
                )

            self._receive_task = asyncio.get_event_loop().create_task(
                self._receive_loop()
            )

            init_result = await self._send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"roots": {}, "elicitation": {}},
                    "clientInfo": {
                        "name": "claude-code",
                        "version": "1.0.0",
                    },
                },
            )

            if init_result and isinstance(init_result, dict):
                caps = init_result.get("capabilities", {})
                self._capabilities = ServerCapabilities(
                    tools=bool(caps.get("tools")),
                    prompts=bool(caps.get("prompts")),
                    resources=bool(caps.get("resources")),
                )
                server_info_raw = init_result.get("serverInfo")
                if server_info_raw and isinstance(server_info_raw, dict):
                    self._server_info = ServerInfo(
                        name=server_info_raw.get("name", ""),
                        version=server_info_raw.get("version", ""),
                    )
                raw_instructions = init_result.get("instructions")
                if raw_instructions and isinstance(raw_instructions, str):
                    if len(raw_instructions) > MAX_MCP_DESCRIPTION_LENGTH:
                        self._instructions = (
                            raw_instructions[:MAX_MCP_DESCRIPTION_LENGTH] + "... [truncated]"
                        )
                    else:
                        self._instructions = raw_instructions

            await self._send_notification("notifications/initialized", {})

            elapsed = int((time.monotonic() - connect_start) * 1000)
            server_type = getattr(config.config, "type", "stdio") or "stdio"
            logger.debug(
                "MCP %r connected (transport: %s) in %dms",
                name, server_type, elapsed,
            )

            self._name = name
            self._config = config
            self._connected = True
            self._reconnect_attempt = 0

            return ConnectedMCPServer(
                name=name,
                capabilities=self._capabilities,
                server_info=self._server_info,
                instructions=self._instructions,
                config=config,
            )

        except Exception as e:
            if self._transport:
                await self._transport.close()
            elapsed = int((time.monotonic() - connect_start) * 1000)
            error_msg = _unwrap_exception_group_message(e)
            logger.debug(
                "MCP %r connection failed after %dms: %s", name, elapsed, e
            )

            # Phase 4 WI-4.5 + Phase 6b WI-6.2: if the failure looks like
            # an OAuth-required signal (401 / WWW-Authenticate / "Unauthorized"),
            # surface NeedsAuthMCPServer instead of a generic Failed.
            if self._auth_provider is not None and _is_remote_config(config.config):
                from .auth_provider import is_oauth_required_error

                if is_oauth_required_error(e):
                    cached = self._auth_provider.get_needs_auth_state(name)
                    auth_url = cached.auth_url if cached else None
                    if cached is None:
                        self._auth_provider.mark_needs_auth(
                            name, reason=error_msg
                        )
                    return NeedsAuthMCPServer(
                        name=name,
                        config=config,
                        auth_url=auth_url,
                        auth_method="oauth",
                        requires_user_action=True,
                        error=error_msg,
                    )

            return FailedMCPServer(
                name=name,
                error=error_msg,
                config=config,
            )

    def _create_transport(
        self, config: McpServerConfig, *, server_name: str | None = None
    ) -> McpTransport:
        if isinstance(config, McpStdioServerConfig):
            return StdioTransport(
                command=config.command,
                args=config.args,
                env=config.env,
            )
        # Phase 4 WI-4.5: merge auth headers into transport-level headers
        # for remote configs so the SDK transport authenticates requests.
        if isinstance(config, (McpSSEServerConfig, McpHTTPServerConfig, McpWebSocketServerConfig)):
            headers = dict(config.headers or {})
            if (
                self._auth_provider is not None
                and server_name is not None
                and "Authorization" not in headers
            ):
                auth_headers = self._auth_provider.get_auth_headers(server_name)
                if auth_headers:
                    headers.update(auth_headers)
            if isinstance(config, McpSSEServerConfig):
                return SseTransport(url=config.url, headers=headers or None)
            if isinstance(config, McpHTTPServerConfig):
                return HttpTransport(url=config.url, headers=headers or None)
            if isinstance(config, McpWebSocketServerConfig):
                return WebSocketTransport(url=config.url, headers=headers or None)
        else:
            raise ValueError(f"Unsupported server config type: {type(config).__name__}")

    async def _receive_loop(self) -> None:
        if self._transport is None:
            return
        try:
            while self._transport.is_connected:
                msg = await self._transport.receive()
                if msg is None:
                    # Transport closed cleanly. Reject any in-flight futures
                    # so concurrent callers fail fast instead of waiting out
                    # the full tool-call timeout (5 min default). Without
                    # this, a `receive() → None` after a peer-side close left
                    # every pending request silently hung.
                    closed_exc = ConnectionError("MCP transport closed")
                    for future in self._pending_requests.values():
                        if not future.done():
                            future.set_exception(closed_exc)
                    self._pending_requests.clear()
                    break
                if msg.id is not None and msg.id in self._pending_requests:
                    future = self._pending_requests.pop(msg.id)
                    if msg.error:
                        future.set_exception(
                            McpToolCallError(
                                json.dumps(msg.error),
                                msg.error.get("message", "MCP error"),
                            )
                        )
                    else:
                        future.set_result(msg.result)
                elif msg.method is not None and msg.id is not None:
                    # Incoming server→client REQUEST (e.g. elicitation/create).
                    # Handle out-of-band so the loop keeps draining, then reply.
                    asyncio.get_event_loop().create_task(
                        self._handle_incoming_request(msg)
                    )
        except Exception as e:
            logger.debug("MCP receive loop error: %s", e)
            for future in self._pending_requests.values():
                if not future.done():
                    future.set_exception(e)
            self._pending_requests.clear()

    async def _handle_incoming_request(self, msg: JsonRpcMessage) -> None:
        """Reply to a server→client request (elicitation/create, etc.)."""
        try:
            if msg.method == "elicitation/create":
                result = await self._run_elicitation(msg.params or {})
                await self._send_response(msg.id, result=result)
            else:
                await self._send_response(
                    msg.id,
                    error={"code": -32601, "message": f"Method not found: {msg.method}"},
                )
        except Exception as e:  # never let a handler crash the receive loop
            try:
                await self._send_response(
                    msg.id, error={"code": -32603, "message": str(e)}
                )
            except Exception:
                pass

    async def _run_elicitation(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run the injected elicitation handler, or decline if none is set."""
        handler = self._elicitation_handler
        if handler is None:
            return {"action": "decline"}
        try:
            res = await handler(params)
            return res if isinstance(res, dict) and res.get("action") else {"action": "decline"}
        except Exception:
            return {"action": "decline"}

    async def _send_response(
        self,
        request_id: int | str | None,
        result: Any = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        if self._transport is None or request_id is None:
            return
        await self._transport.send(
            JsonRpcMessage(id=request_id, result=result, error=error)
        )

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if self._transport is None:
            raise RuntimeError("Transport not connected")
        request_id = self._next_id()
        msg = JsonRpcMessage(
            method=method,
            params=params,
            id=request_id,
        )
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending_requests[request_id] = future
        await self._transport.send(msg)
        timeout_s = _get_tool_timeout_ms() / 1000.0
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise

    async def _send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        if self._transport is None:
            raise RuntimeError("Transport not connected")
        msg = JsonRpcMessage(method=method, params=params)
        await self._transport.send(msg)

    async def list_tools(self) -> list[McpToolSchema]:
        if not self._capabilities.tools:
            return []
        result = await self._send_request("tools/list", {})
        if not result or not isinstance(result, dict):
            return []
        tools_raw = result.get("tools", [])
        tools: list[McpToolSchema] = []
        for t in tools_raw:
            if not isinstance(t, dict):
                continue
            tools.append(
                McpToolSchema(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                    annotations=t.get("annotations"),
                    meta=t.get("_meta"),
                )
            )
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> McpToolResult:
        params: dict[str, Any] = {
            "name": tool_name,
            "arguments": arguments or {},
        }
        if meta:
            params["_meta"] = meta

        # Phase 6a WI-6.1 (gap #8): on Streamable-HTTP session expiry,
        # the chapter §"Session Expiry Detection" specifies clear-cache +
        # retry-once. Mirrors typescript/src/services/mcp/client.ts: the
        # cache is cleared on detection so the next request reconnects
        # against a fresh session rather than reusing the expired one.
        try:
            result = await self._send_request("tools/call", params)
        except McpToolCallError as err:
            if not is_mcp_session_expired_error(err):
                # Regular tool error (invalid params, server-rejected, etc.) —
                # propagate untouched. No reconnect, no retry.
                raise
            await self._recover_from_session_expiry(err, tool_name=tool_name)
            # Retry once after the recovery routine returned (it either
            # reconnected, or another concurrent caller did, or recovery
            # failed and re-raised). A second session-expired here means
            # the server is unstable / the retry hit a fresh session that
            # already expired — propagate so we don't loop indefinitely.
            result = await self._send_request("tools/call", params)
        if not result or not isinstance(result, dict):
            return McpToolResult()

        is_error = result.get("isError", False)
        content = result.get("content", [])
        result_meta = result.get("_meta")
        structured = result.get("structuredContent")

        if is_error:
            error_text = ""
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    error_text += item.get("text", "")
            raise McpToolCallError(
                error_text or "MCP tool returned an error",
                "MCP tool error",
                {"_meta": result_meta} if result_meta else None,
            )

        return McpToolResult(
            content=content,
            is_error=False,
            meta=result_meta,
            structured_content=structured,
        )

    async def _recover_from_session_expiry(
        self,
        original_error: Exception,
        *,
        tool_name: str | None = None,
    ) -> None:
        """Serialize concurrent session-expiry recovery via lock + epoch.

        When N parallel callers all observe the same expired session, we
        want exactly one reconnect, not N. The lock implements double-
        checked recovery: only the first coroutine to take the lock
        reconnects; the others observe the bumped ``_session_generation``
        and return immediately, letting their retry path proceed against
        the freshly-reconnected transport.

        On reconnect failure, all waiters re-raise ``original_error`` so
        the caller sees the session-expiry signal in context (rather than
        a misleading reconnect-related error).

        Phase 6a WI-6.1 (gap #8). Scope: invoked from ``call_tool`` only;
        ``list_tools`` / ``list_resources`` / ``list_prompts`` /
        ``initialize`` retain their previous propagate-on-error behavior.
        Lifting recovery into ``_send_request`` to cover every JSON-RPC
        method is tracked as a follow-up; the chapter §"Session Expiry
        Detection" describes recovery as a tool-call concern.
        """
        # Lazy-init the lock so __init__ doesn't require a running loop.
        if self._recovery_lock is None:
            self._recovery_lock = asyncio.Lock()
        gen_at_entry = self._session_generation
        async with self._recovery_lock:
            if self._session_generation != gen_at_entry:
                # Another concurrent caller already reconnected for this
                # session generation; nothing to do.
                logger.info(
                    "MCP %r tool %r: session expired (gen %d); piggybacking "
                    "on concurrent reconnect (now gen %d)",
                    self._name, tool_name, gen_at_entry, self._session_generation,
                )
                return
            logger.info(
                "MCP %r tool %r: session expired (gen %d); clearing cache + reconnecting",
                self._name, tool_name, gen_at_entry,
            )
            if self._name is not None:
                clear_connection_cache(self._name)
            reconnected = await self.reconnect()
            if not isinstance(reconnected, ConnectedMCPServer):
                # Reconnect failed; bump the generation anyway so other
                # waiters don't repeatedly try, and surface the original
                # session-expired error to all callers.
                self._session_generation += 1
                raise original_error
            self._session_generation += 1

    async def list_resources(self) -> list[dict[str, Any]]:
        if not self._capabilities.resources:
            return []
        result = await self._send_request("resources/list", {})
        if not result or not isinstance(result, dict):
            return []
        return result.get("resources", [])

    async def list_prompts(self) -> list[dict[str, Any]]:
        if not self._capabilities.prompts:
            return []
        result = await self._send_request("prompts/list", {})
        if not result or not isinstance(result, dict):
            return []
        return result.get("prompts", [])

    async def close(self) -> None:
        self._connected = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._transport:
            await self._transport.close()

    @property
    def is_connected(self) -> bool:
        if not self._connected:
            return False
        if self._transport and not self._transport.is_connected:
            self._connected = False
            return False
        return True

    async def reconnect(self) -> MCPServerConnection:
        if self._name is None or self._config is None:
            return FailedMCPServer(
                name=self._name or "unknown",
                error="No connection info for reconnection",
            )

        max_attempts = MAX_RECONNECT_ATTEMPTS
        delay_ms = INITIAL_RECONNECT_DELAY_MS

        for attempt in range(1, max_attempts + 1):
            self._reconnect_attempt = attempt
            logger.debug(
                "MCP %r reconnect attempt %d/%d",
                self._name, attempt, max_attempts,
            )

            await self.close()
            self._pending_requests.clear()
            self._transport = None
            self._receive_task = None

            conn = await self.connect(self._name, self._config)
            if isinstance(conn, ConnectedMCPServer):
                logger.debug("MCP %r reconnected on attempt %d", self._name, attempt)
                return conn

            if attempt < max_attempts:
                await asyncio.sleep(delay_ms / 1000.0)
                delay_ms = min(delay_ms * 2, MAX_RECONNECT_DELAY_MS)

        return FailedMCPServer(
            name=self._name,
            error=f"Failed to reconnect after {max_attempts} attempts",
            config=self._config,
        )

    async def list_resources_paginated(
        self,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        if not self._capabilities.resources:
            return []

        if "resources" in self._resource_cache:
            return self._resource_cache["resources"]

        all_resources: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor

            result = await self._send_request("resources/list", params)
            if not result or not isinstance(result, dict):
                break

            resources = result.get("resources", [])
            all_resources.extend(resources)

            next_cursor = result.get("nextCursor")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        self._resource_cache["resources"] = all_resources
        return all_resources

    def clear_resource_cache(self) -> None:
        self._resource_cache.clear()

    async def call_tool_with_retry(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        *,
        max_retries: int = 1,
    ) -> McpToolResult:
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await self.call_tool(tool_name, arguments, meta)
            except Exception as e:
                last_error = e
                if attempt < max_retries and not self.is_connected:
                    await self.reconnect()
                elif attempt < max_retries:
                    await asyncio.sleep(0.5)

        raise last_error or RuntimeError("call_tool_with_retry failed")

    @property
    def capabilities(self) -> ServerCapabilities:
        return self._capabilities

    @property
    def server_info(self) -> ServerInfo | None:
        return self._server_info

    @property
    def instructions(self) -> str | None:
        return self._instructions


_connection_cache: dict[str, tuple[McpClient, MCPServerConnection]] = {}

# Cache key separator: only needs to be unambiguous for the prefix-match in
# ``clear_connection_cache`` (we never parse the key back). Any single char
# that is unlikely to appear at the start of a server name works.
_CACHE_KEY_SEP = "|"


def _cache_key_for(name: str, config: ScopedMcpServerConfig) -> str:
    """Compose a content-based cache key for a (name, config) pair.

    Mirrors TS' connection-cache keying (typescript/src/services/mcp/
    client.ts:600-606 uses ``${name}-${jsonStringify(serverRef)}`` — keying
    on the **full** scoped config so that env vars / headers / scope all
    participate). Two configs with the same ``command``/``args`` but
    different ``env`` (e.g. different API keys) MUST produce distinct cache
    keys; otherwise the second registration would silently reuse the first
    server's authenticated connection — a credential-leak class bug.

    NOTE: ``get_mcp_server_signature(...)`` is intentionally narrow — it
    encodes only ``[command, args]`` for stdio or ``url`` for remote, so it
    cannot be reused as a cache key without env/header collisions. Its
    actual call sites (``config.py:dedup_plugin_mcp_servers``) are about
    de-duplicating plugin servers that share a launch surface, not about
    keying live connections.
    """
    from dataclasses import asdict, is_dataclass

    inner = config.config
    if is_dataclass(inner):
        payload = {
            "scope": config.scope,
            "plugin_source": config.plugin_source,
            "type": type(inner).__name__,
            "config": asdict(inner),
        }
    else:  # pragma: no cover - defensive; all current configs are dataclasses
        payload = {"scope": config.scope, "type": type(inner).__name__, "id": id(inner)}
    return f"{name}{_CACHE_KEY_SEP}{json.dumps(payload, sort_keys=True, default=str)}"


async def connect_to_server(
    name: str,
    config: ScopedMcpServerConfig,
    *,
    auth_provider: Any | None = None,
) -> tuple[McpClient, MCPServerConnection]:
    """Connect to (or return cached client for) an MCP server.

    ``auth_provider`` MUST be bound BEFORE ``client.connect`` so the
    NeedsAuth fast-path + auth-header injection take effect on the very
    first connect. Threading the provider after ``connect`` (as a prior
    iteration did) caused first-connect 401s to surface as FailedMCPServer
    instead of NeedsAuthMCPServer, and tool calls thereafter ran without
    credentials. This signature mirrors TS' ``connectToServer(name, config, authProvider)``.
    """
    cache_key = _cache_key_for(name, config)
    if cache_key in _connection_cache:
        client, conn = _connection_cache[cache_key]
        if isinstance(conn, ConnectedMCPServer) and client._transport and client._transport.is_connected:
            return client, conn

    client = McpClient()
    if auth_provider is not None:
        client.set_auth_provider(auth_provider)
    connection = await client.connect(name, config)
    if isinstance(connection, ConnectedMCPServer):
        _connection_cache[cache_key] = (client, connection)
    return client, connection


def clear_connection_cache(name: str | None = None) -> None:
    global _connection_cache
    if name is None:
        _connection_cache.clear()
    else:
        prefix = f"{name}{_CACHE_KEY_SEP}"
        keys_to_remove = [k for k in _connection_cache if k.startswith(prefix)]
        for k in keys_to_remove:
            del _connection_cache[k]
