from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.services.mcp.tool_wrapper import wrap_mcp_tool, wrap_mcp_tools_for_server
from src.services.mcp.types import ConnectedMCPServer, McpToolSchema, ServerCapabilities
from src.services.mcp.client import McpClient


class TestWrapMcpTool:
    def test_basic_wrapping(self) -> None:
        client = MagicMock(spec=McpClient)
        mcp_tool = McpToolSchema(
            name="my_tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        tool = wrap_mcp_tool("test-server", mcp_tool, client)
        assert tool.name == "mcp__test-server__my_tool"
        assert tool.is_mcp is True
        assert tool.mcp_info is not None
        assert tool.mcp_info.server_name == "test-server"
        assert tool.mcp_info.tool_name == "my_tool"

    def test_description_truncation(self) -> None:
        client = MagicMock(spec=McpClient)
        long_desc = "x" * 3000
        mcp_tool = McpToolSchema(
            name="tool",
            description=long_desc,
        )
        tool = wrap_mcp_tool("server", mcp_tool, client)
        desc = tool.description({})
        assert len(desc) < 3000
        assert "truncated" in desc

    def test_read_only_hint(self) -> None:
        client = MagicMock(spec=McpClient)
        mcp_tool = McpToolSchema(
            name="read_tool",
            annotations={"readOnlyHint": True},
        )
        tool = wrap_mcp_tool("server", mcp_tool, client)
        assert tool.is_read_only({}) is True
        assert tool.is_concurrency_safe({}) is True

    def test_destructive_hint(self) -> None:
        client = MagicMock(spec=McpClient)
        mcp_tool = McpToolSchema(
            name="destroy_tool",
            annotations={"destructiveHint": True},
        )
        tool = wrap_mcp_tool("server", mcp_tool, client)
        assert tool.is_destructive({}) is True

    def test_open_world_hint(self) -> None:
        client = MagicMock(spec=McpClient)
        mcp_tool = McpToolSchema(
            name="web_tool",
            annotations={"openWorldHint": True},
        )
        tool = wrap_mcp_tool("server", mcp_tool, client)
        assert tool.is_open_world is not None
        assert tool.is_open_world({}) is True

    def test_search_hint(self) -> None:
        client = MagicMock(spec=McpClient)
        mcp_tool = McpToolSchema(
            name="tool",
            meta={"anthropic/searchHint": "search for  files"},
        )
        tool = wrap_mcp_tool("server", mcp_tool, client)
        assert tool.search_hint is not None
        assert "  " not in tool.search_hint

    def test_always_load(self) -> None:
        client = MagicMock(spec=McpClient)
        mcp_tool = McpToolSchema(
            name="tool",
            meta={"anthropic/alwaysLoad": True},
        )
        tool = wrap_mcp_tool("server", mcp_tool, client)
        assert tool.always_load is True

    def test_name_with_special_chars(self) -> None:
        client = MagicMock(spec=McpClient)
        mcp_tool = McpToolSchema(name="my tool")
        tool = wrap_mcp_tool("my server", mcp_tool, client)
        assert tool.name == "mcp__my_server__my_tool"


class TestWrapMcpToolsForServer:
    def test_wraps_all_tools(self) -> None:
        client = MagicMock(spec=McpClient)
        server = ConnectedMCPServer(
            name="test-server",
            capabilities=ServerCapabilities(tools=True),
        )
        tools = [
            McpToolSchema(name="tool1", description="first"),
            McpToolSchema(name="tool2", description="second"),
        ]
        wrapped = wrap_mcp_tools_for_server(server, tools, client)
        assert len(wrapped) == 2
        assert wrapped[0].name == "mcp__test-server__tool1"
        assert wrapped[1].name == "mcp__test-server__tool2"

    def test_handles_errors_gracefully(self) -> None:
        client = MagicMock(spec=McpClient)
        server = ConnectedMCPServer(name="server")
        tools = [McpToolSchema(name="good"), McpToolSchema(name="good2")]
        wrapped = wrap_mcp_tools_for_server(server, tools, client)
        assert len(wrapped) >= 1
