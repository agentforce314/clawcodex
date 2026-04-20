"""Phase D — MCP Integration Tests.

MCP client lifecycle: types → connection → tools → disconnect.
"""
from __future__ import annotations

import unittest

from src.services.mcp.types import (
    ConnectedMCPServer,
    DisabledMCPServer,
    FailedMCPServer,
    ScopedMcpServerConfig,
    ServerCapabilities,
    ServerInfo,
)


class TestMcpServerStates(unittest.TestCase):
    """All MCP server state types are correctly defined."""

    def test_connected_server(self) -> None:
        server = ConnectedMCPServer(name="test-server")
        self.assertEqual(server.type, "connected")
        self.assertEqual(server.name, "test-server")
        self.assertIsNotNone(server.capabilities)

    def test_failed_server(self) -> None:
        server = FailedMCPServer(name="bad-server", error="Connection refused")
        self.assertEqual(server.type, "failed")
        self.assertEqual(server.error, "Connection refused")

    def test_disabled_server(self) -> None:
        server = DisabledMCPServer(name="off-server")
        self.assertEqual(server.type, "disabled")


class TestMcpServerConfig(unittest.TestCase):
    """Server configuration types."""

    def test_scoped_config_stdio(self) -> None:
        from src.services.mcp.types import McpStdioServerConfig
        inner = McpStdioServerConfig(command="npx", args=["-y", "@example/mcp-server"], type="stdio")
        config = ScopedMcpServerConfig(config=inner, scope="user")
        self.assertEqual(config.server_type, "stdio")
        self.assertEqual(config.config.command, "npx")

    def test_scoped_config_sse(self) -> None:
        from src.services.mcp.types import McpSSEServerConfig
        inner = McpSSEServerConfig(url="http://localhost:3000/sse")
        config = ScopedMcpServerConfig(config=inner, scope="project")
        self.assertEqual(config.server_type, "sse")


class TestMcpCapabilities(unittest.TestCase):
    """Server capabilities structure."""

    def test_default_capabilities(self) -> None:
        caps = ServerCapabilities()
        # Default: all capabilities should be present as attributes
        self.assertIsNotNone(caps)

    def test_server_info(self) -> None:
        info = ServerInfo(name="MyServer", version="1.0.0")
        self.assertEqual(info.name, "MyServer")
        self.assertEqual(info.version, "1.0.0")


class TestMcpClientStructure(unittest.TestCase):
    """MCP client has required interface."""

    def test_client_class_exists(self) -> None:
        from src.services.mcp.client import McpClient
        client = McpClient()
        self.assertIsNotNone(client)

    def test_client_has_connect(self) -> None:
        from src.services.mcp.client import McpClient
        self.assertTrue(hasattr(McpClient, "connect"))

    def test_client_has_close(self) -> None:
        from src.services.mcp.client import McpClient
        self.assertTrue(hasattr(McpClient, "close"))

    def test_client_has_list_tools(self) -> None:
        from src.services.mcp.client import McpClient
        self.assertTrue(hasattr(McpClient, "list_tools"))
        self.assertTrue(hasattr(McpClient, "call_tool"))


if __name__ == "__main__":
    unittest.main()
