from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable

from .errors import McpAuthError, McpSessionExpiredError, McpToolCallError
from .transport import (
    HttpTransport,
    JsonRpcMessage,
    McpTransport,
    SseTransport,
    StdioTransport,
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

DEFAULT_MCP_TOOL_TIMEOUT_MS = 100_000_000
MAX_MCP_DESCRIPTION_LENGTH = 2048
DEFAULT_CONNECTION_TIMEOUT_MS = 30_000


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

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def connect(
        self,
        name: str,
        config: ScopedMcpServerConfig,
    ) -> MCPServerConnection:
        connect_start = time.monotonic()
        try:
            transport = self._create_transport(config.config)
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
                    "capabilities": {"roots": {}},
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
            logger.debug(
                "MCP %r connection failed after %dms: %s", name, elapsed, e
            )
            return FailedMCPServer(
                name=name,
                error=str(e),
                config=config,
            )

    def _create_transport(self, config: McpServerConfig) -> McpTransport:
        if isinstance(config, McpStdioServerConfig):
            return StdioTransport(
                command=config.command,
                args=config.args,
                env=config.env,
            )
        elif isinstance(config, McpSSEServerConfig):
            return SseTransport(
                url=config.url,
                headers=config.headers,
            )
        elif isinstance(config, McpHTTPServerConfig):
            return HttpTransport(
                url=config.url,
                headers=config.headers,
            )
        elif isinstance(config, McpWebSocketServerConfig):
            raise NotImplementedError("WebSocket transport not yet implemented")
        else:
            raise ValueError(f"Unsupported server config type: {type(config).__name__}")

    async def _receive_loop(self) -> None:
        if self._transport is None:
            return
        try:
            while self._transport.is_connected:
                msg = await self._transport.receive()
                if msg is None:
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
        except Exception as e:
            logger.debug("MCP receive loop error: %s", e)
            for future in self._pending_requests.values():
                if not future.done():
                    future.set_exception(e)
            self._pending_requests.clear()

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


async def connect_to_server(
    name: str,
    config: ScopedMcpServerConfig,
) -> tuple[McpClient, MCPServerConnection]:
    cache_key = f"{name}-{id(config)}"
    if cache_key in _connection_cache:
        client, conn = _connection_cache[cache_key]
        if isinstance(conn, ConnectedMCPServer) and client._transport and client._transport.is_connected:
            return client, conn

    client = McpClient()
    connection = await client.connect(name, config)
    if isinstance(connection, ConnectedMCPServer):
        _connection_cache[cache_key] = (client, connection)
    return client, connection


def clear_connection_cache(name: str | None = None) -> None:
    global _connection_cache
    if name is None:
        _connection_cache.clear()
    else:
        keys_to_remove = [k for k in _connection_cache if k.startswith(f"{name}-")]
        for k in keys_to_remove:
            del _connection_cache[k]
