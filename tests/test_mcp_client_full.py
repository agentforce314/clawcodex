import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.mcp.client import McpClient, MAX_RECONNECT_ATTEMPTS
from src.services.mcp.types import (
    ConnectedMCPServer,
    FailedMCPServer,
    McpStdioServerConfig,
    ScopedMcpServerConfig,
    ServerCapabilities,
)


class TestMcpClientReconnection:
    def test_initial_state(self):
        client = McpClient()
        assert client.is_connected is False
        assert client._reconnect_attempt == 0

    def test_resource_cache(self):
        client = McpClient()
        assert client._resource_cache == {}
        client.clear_resource_cache()
        assert client._resource_cache == {}


class TestMcpClientProperties:
    def test_capabilities(self):
        client = McpClient()
        caps = client.capabilities
        assert isinstance(caps, ServerCapabilities)
        assert caps.tools is False

    def test_server_info(self):
        client = McpClient()
        assert client.server_info is None

    def test_instructions(self):
        client = McpClient()
        assert client.instructions is None


class TestMcpClientConstants:
    def test_max_reconnect_attempts(self):
        assert MAX_RECONNECT_ATTEMPTS == 5


class TestMcpClientClose:
    @pytest.mark.asyncio
    async def test_close_no_transport(self):
        client = McpClient()
        await client.close()
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_close_sets_disconnected(self):
        client = McpClient()
        client._connected = True
        await client.close()
        assert client.is_connected is False
