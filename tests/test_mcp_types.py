from __future__ import annotations

import pytest
from src.services.mcp.types import (
    ConnectedMCPServer,
    DisabledMCPServer,
    FailedMCPServer,
    McpHTTPServerConfig,
    McpSSEServerConfig,
    McpStdioServerConfig,
    McpToolSchema,
    NeedsAuthMCPServer,
    PendingMCPServer,
    ScopedMcpServerConfig,
    ServerCapabilities,
    ServerInfo,
    parse_server_config,
)


class TestParseServerConfig:
    def test_stdio_config(self) -> None:
        data = {"command": "python", "args": ["-m", "server"]}
        config = parse_server_config(data)
        assert isinstance(config, McpStdioServerConfig)
        assert config.command == "python"
        assert config.args == ["-m", "server"]

    def test_stdio_with_type(self) -> None:
        data = {"type": "stdio", "command": "node", "args": ["server.js"]}
        config = parse_server_config(data)
        assert isinstance(config, McpStdioServerConfig)
        assert config.command == "node"

    def test_http_config(self) -> None:
        data = {"type": "http", "url": "https://example.com"}
        config = parse_server_config(data)
        assert isinstance(config, McpHTTPServerConfig)
        assert config.url == "https://example.com"

    def test_sse_config(self) -> None:
        data = {"type": "sse", "url": "https://example.com/sse"}
        config = parse_server_config(data)
        assert isinstance(config, McpSSEServerConfig)

    def test_invalid_config(self) -> None:
        data = {"type": "unknown_type"}
        config = parse_server_config(data)
        assert config is None

    def test_stdio_no_command(self) -> None:
        data = {"type": "stdio"}
        config = parse_server_config(data)
        assert config is None


class TestScopedMcpServerConfig:
    def test_server_type_stdio(self) -> None:
        scoped = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="echo", type="stdio"),
            scope="project",
        )
        assert scoped.server_type == "stdio"

    def test_server_type_stdio_default(self) -> None:
        scoped = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="echo"),
            scope="project",
        )
        assert scoped.server_type is None

    def test_server_type_none(self) -> None:
        scoped = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="echo"),
            scope="user",
        )
        assert scoped.server_type is None or scoped.server_type == "stdio"


class TestMCPServerConnection:
    def test_connected(self) -> None:
        c = ConnectedMCPServer(name="test")
        assert c.type == "connected"

    def test_failed(self) -> None:
        f = FailedMCPServer(name="test", error="connection refused")
        assert f.type == "failed"
        assert f.error == "connection refused"

    def test_needs_auth(self) -> None:
        n = NeedsAuthMCPServer(name="test")
        assert n.type == "needs-auth"

    def test_pending(self) -> None:
        p = PendingMCPServer(name="test")
        assert p.type == "pending"

    def test_disabled(self) -> None:
        d = DisabledMCPServer(name="test")
        assert d.type == "disabled"


class TestMcpToolSchema:
    def test_basic(self) -> None:
        tool = McpToolSchema(
            name="my_tool",
            description="does stuff",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        assert tool.name == "my_tool"
        assert tool.description == "does stuff"

    def test_with_annotations(self) -> None:
        tool = McpToolSchema(
            name="tool",
            annotations={"readOnlyHint": True, "destructiveHint": False},
        )
        assert tool.annotations is not None
        assert tool.annotations["readOnlyHint"] is True
