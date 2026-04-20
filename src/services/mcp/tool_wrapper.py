from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src.permissions.types import PermissionPassthroughResult, PermissionResult
from src.tool_system.build_tool import McpInfo, Tool, build_tool
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolResult

from .client import McpClient
from .mcp_string_utils import build_mcp_tool_name
from .types import ConnectedMCPServer, McpToolSchema

logger = logging.getLogger(__name__)

MAX_MCP_DESCRIPTION_LENGTH = 2048


def wrap_mcp_tool(
    server_name: str,
    mcp_tool: McpToolSchema,
    client: McpClient,
) -> Tool:
    fully_qualified_name = build_mcp_tool_name(server_name, mcp_tool.name)
    annotations = mcp_tool.annotations or {}

    read_only = annotations.get("readOnlyHint", False)
    destructive = annotations.get("destructiveHint", False)
    open_world = annotations.get("openWorldHint", False)

    raw_desc = mcp_tool.description or ""
    truncated_desc = (
        raw_desc[:MAX_MCP_DESCRIPTION_LENGTH] + "... [truncated]"
        if len(raw_desc) > MAX_MCP_DESCRIPTION_LENGTH
        else raw_desc
    )

    search_hint = None
    if mcp_tool.meta and isinstance(mcp_tool.meta.get("anthropic/searchHint"), str):
        hint = mcp_tool.meta["anthropic/searchHint"]
        import re
        cleaned = re.sub(r"\s+", " ", hint).strip()
        search_hint = cleaned or None

    always_load_val = bool(
        mcp_tool.meta and mcp_tool.meta.get("anthropic/alwaysLoad") is True
    )

    def _call(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    _async_call(args, ctx),
                )
                return future.result()
        else:
            return loop.run_until_complete(_async_call(args, ctx))

    async def _async_call(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            result = await client.call_tool(mcp_tool.name, args)
            text_parts: list[str] = []
            for item in result.content:
                if isinstance(item, dict):
                    item_type = item.get("type", "")
                    if item_type == "text":
                        text_parts.append(item.get("text", ""))
                    elif item_type == "image":
                        text_parts.append("[image content]")
                    elif item_type == "resource":
                        text_parts.append(
                            json.dumps(item.get("resource", {}))
                        )
                    else:
                        text_parts.append(json.dumps(item))
                else:
                    text_parts.append(str(item))

            output = "\n".join(text_parts)
            return ToolResult(
                name=fully_qualified_name,
                output=output,
                is_error=False,
            )
        except Exception as e:
            return ToolResult(
                name=fully_qualified_name,
                output=str(e),
                is_error=True,
            )

    def _check_permissions(
        _input: dict[str, Any], _ctx: ToolContext
    ) -> PermissionResult:
        return PermissionPassthroughResult()

    input_schema = mcp_tool.input_schema or {"type": "object", "properties": {}}

    return build_tool(
        name=fully_qualified_name,
        input_schema=input_schema,
        call=_call,
        prompt=truncated_desc,
        description=truncated_desc,
        is_mcp=True,
        mcp_info=McpInfo(server_name=server_name, tool_name=mcp_tool.name),
        is_concurrency_safe=lambda _input: read_only,
        is_read_only=lambda _input: read_only,
        is_destructive=lambda _input: destructive,
        is_open_world=lambda _input: open_world,
        check_permissions=_check_permissions,
        search_hint=search_hint,
        always_load=always_load_val,
        input_json_schema=input_schema,
    )


def wrap_mcp_tools_for_server(
    server: ConnectedMCPServer,
    tools: list[McpToolSchema],
    client: McpClient,
) -> list[Tool]:
    wrapped: list[Tool] = []
    for mcp_tool in tools:
        try:
            tool = wrap_mcp_tool(server.name, mcp_tool, client)
            wrapped.append(tool)
        except Exception as e:
            logger.warning(
                "Failed to wrap MCP tool %s from server %s: %s",
                mcp_tool.name, server.name, e,
            )
    return wrapped
