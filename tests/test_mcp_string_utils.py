from __future__ import annotations

import pytest
from src.services.mcp.mcp_string_utils import (
    McpInfoParsed,
    build_mcp_tool_name,
    extract_mcp_tool_display_name,
    get_mcp_display_name,
    get_mcp_prefix,
    mcp_info_from_string,
)


class TestMcpInfoFromString:
    def test_valid_tool_name(self) -> None:
        result = mcp_info_from_string("mcp__myserver__mytool")
        assert result is not None
        assert result.server_name == "myserver"
        assert result.tool_name == "mytool"

    def test_server_only(self) -> None:
        result = mcp_info_from_string("mcp__myserver")
        assert result is not None
        assert result.server_name == "myserver"
        assert result.tool_name is None

    def test_tool_with_double_underscores(self) -> None:
        result = mcp_info_from_string("mcp__myserver__my__tool")
        assert result is not None
        assert result.server_name == "myserver"
        assert result.tool_name == "my__tool"

    def test_not_mcp_prefix(self) -> None:
        result = mcp_info_from_string("notmcp__myserver__tool")
        assert result is None

    def test_single_part(self) -> None:
        result = mcp_info_from_string("something")
        assert result is None

    def test_empty_string(self) -> None:
        result = mcp_info_from_string("")
        assert result is None


class TestGetMcpPrefix:
    def test_simple(self) -> None:
        assert get_mcp_prefix("myserver") == "mcp__myserver__"

    def test_with_special_chars(self) -> None:
        assert get_mcp_prefix("my server") == "mcp__my_server__"


class TestBuildMcpToolName:
    def test_basic(self) -> None:
        assert build_mcp_tool_name("server", "tool") == "mcp__server__tool"

    def test_special_chars(self) -> None:
        name = build_mcp_tool_name("my server", "my tool")
        assert name == "mcp__my_server__my_tool"


class TestGetMcpDisplayName:
    def test_strips_prefix(self) -> None:
        result = get_mcp_display_name("mcp__server__tool", "server")
        assert result == "tool"

    def test_no_prefix_match(self) -> None:
        result = get_mcp_display_name("other_tool", "server")
        assert result == "other_tool"


class TestExtractMcpToolDisplayName:
    def test_with_mcp_suffix(self) -> None:
        result = extract_mcp_tool_display_name("Server - Tool (MCP)")
        assert result == "Tool"

    def test_without_suffix(self) -> None:
        result = extract_mcp_tool_display_name("Server - Tool")
        assert result == "Tool"

    def test_plain_name(self) -> None:
        result = extract_mcp_tool_display_name("Tool")
        assert result == "Tool"
