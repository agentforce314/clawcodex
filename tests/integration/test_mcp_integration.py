from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from src.services.mcp.client import McpClient, clear_connection_cache
from src.services.mcp.config import parse_mcp_config
from src.services.mcp.mcp_string_utils import build_mcp_tool_name
from src.services.mcp.tool_wrapper import wrap_mcp_tool
from src.services.mcp.transport import StdioTransport, JsonRpcMessage
from src.services.mcp.types import (
    ConnectedMCPServer,
    McpStdioServerConfig,
    McpToolSchema,
    ScopedMcpServerConfig,
)

MOCK_MCP_SERVER_SCRIPT = textwrap.dedent("""\
    import json
    import sys

    def read_message():
        headers = {}
        while True:
            line = sys.stdin.readline()
            if not line or line.strip() == '':
                break
            if ':' in line:
                key, value = line.split(':', 1)
                headers[key.strip().lower()] = value.strip()
        content_length = int(headers.get('content-length', 0))
        if content_length == 0:
            return None
        body = sys.stdin.read(content_length)
        return json.loads(body)

    def send_message(msg):
        body = json.dumps(msg)
        header = f"Content-Length: {len(body.encode('utf-8'))}\\r\\n\\r\\n"
        sys.stdout.write(header)
        sys.stdout.write(body)
        sys.stdout.flush()

    TOOLS = [
        {
            "name": "echo",
            "description": "Echoes back the input",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to echo"}
                },
                "required": ["message"]
            },
            "annotations": {"readOnlyHint": True}
        },
        {
            "name": "add",
            "description": "Adds two numbers",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"}
                },
                "required": ["a", "b"]
            }
        }
    ]

    while True:
        msg = read_message()
        if msg is None:
            break

        method = msg.get('method', '')
        msg_id = msg.get('id')

        if method == 'initialize':
            send_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "test-server", "version": "1.0.0"}
                }
            })
        elif method == 'notifications/initialized':
            pass
        elif method == 'tools/list':
            send_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS}
            })
        elif method == 'tools/call':
            tool_name = msg['params']['name']
            args = msg['params'].get('arguments', {})
            if tool_name == 'echo':
                send_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": args.get('message', '')}],
                        "isError": False
                    }
                })
            elif tool_name == 'add':
                result = args.get('a', 0) + args.get('b', 0)
                send_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": str(result)}],
                        "isError": False
                    }
                })
            else:
                send_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                        "isError": True
                    }
                })
        else:
            if msg_id is not None:
                send_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                })
""")


@pytest.fixture
def mock_server_script(tmp_path: Path) -> str:
    script = tmp_path / "mock_mcp_server.py"
    script.write_text(MOCK_MCP_SERVER_SCRIPT)
    return str(script)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_connection_cache()
    yield  # type: ignore[misc]
    clear_connection_cache()


class TestMcpClientIntegration:
    @pytest.mark.asyncio
    async def test_connect_to_mock_server(self, mock_server_script: str) -> None:
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(
                command=sys.executable,
                args=[mock_server_script],
            ),
            scope="project",
        )
        client = McpClient()
        connection = await client.connect("test-server", config)
        assert isinstance(connection, ConnectedMCPServer)
        assert connection.name == "test-server"
        assert connection.capabilities.tools is True
        assert connection.server_info is not None
        assert connection.server_info.name == "test-server"
        await client.close()

    @pytest.mark.asyncio
    async def test_list_tools(self, mock_server_script: str) -> None:
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(
                command=sys.executable,
                args=[mock_server_script],
            ),
            scope="project",
        )
        client = McpClient()
        connection = await client.connect("test-server", config)
        assert isinstance(connection, ConnectedMCPServer)

        tools = await client.list_tools()
        assert len(tools) == 2
        tool_names = {t.name for t in tools}
        assert "echo" in tool_names
        assert "add" in tool_names

        echo_tool = next(t for t in tools if t.name == "echo")
        assert echo_tool.description == "Echoes back the input"
        assert echo_tool.annotations is not None
        assert echo_tool.annotations.get("readOnlyHint") is True

        await client.close()

    @pytest.mark.asyncio
    async def test_call_echo_tool(self, mock_server_script: str) -> None:
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(
                command=sys.executable,
                args=[mock_server_script],
            ),
            scope="project",
        )
        client = McpClient()
        await client.connect("test-server", config)

        result = await client.call_tool("echo", {"message": "Hello, MCP!"})
        assert len(result.content) == 1
        assert result.content[0]["text"] == "Hello, MCP!"
        assert result.is_error is False

        await client.close()

    @pytest.mark.asyncio
    async def test_call_add_tool(self, mock_server_script: str) -> None:
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(
                command=sys.executable,
                args=[mock_server_script],
            ),
            scope="project",
        )
        client = McpClient()
        await client.connect("test-server", config)

        result = await client.call_tool("add", {"a": 3, "b": 4})
        assert result.content[0]["text"] == "7"

        await client.close()

    @pytest.mark.asyncio
    async def test_tool_naming(self, mock_server_script: str) -> None:
        expected = build_mcp_tool_name("test-server", "echo")
        assert expected == "mcp__test-server__echo"

    @pytest.mark.asyncio
    async def test_wrap_and_call_tool(self, mock_server_script: str) -> None:
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(
                command=sys.executable,
                args=[mock_server_script],
            ),
            scope="project",
        )
        client = McpClient()
        connection = await client.connect("test-server", config)
        assert isinstance(connection, ConnectedMCPServer)

        tools = await client.list_tools()
        echo_tool = next(t for t in tools if t.name == "echo")

        wrapped = wrap_mcp_tool("test-server", echo_tool, client)
        assert wrapped.name == "mcp__test-server__echo"
        assert wrapped.is_mcp is True
        assert wrapped.mcp_info is not None
        assert wrapped.mcp_info.server_name == "test-server"
        assert wrapped.mcp_info.tool_name == "echo"
        assert wrapped.is_read_only({}) is True
        assert wrapped.is_concurrency_safe({}) is True

        await client.close()


class TestMcpConfigIntegration:
    def test_parse_config_and_validate(self) -> None:
        config_data = {
            "mcpServers": {
                "local-server": {
                    "command": "python",
                    "args": ["-m", "my_server"],
                    "env": {"API_KEY": "test"},
                },
                "remote-server": {
                    "type": "http",
                    "url": "https://example.com/mcp",
                },
            }
        }
        result = parse_mcp_config(config_data, expand_vars=False)
        assert result.config is not None
        assert len(result.config) == 2
        assert "local-server" in result.config
        assert "remote-server" in result.config

    def test_parse_config_from_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".mcp.json"
        config_data = {
            "mcpServers": {
                "file-server": {
                    "command": "node",
                    "args": ["server.js"],
                }
            }
        }
        config_file.write_text(json.dumps(config_data))

        from src.services.mcp.config import parse_mcp_config_from_file_path
        result = parse_mcp_config_from_file_path(str(config_file), expand_vars=False)
        assert result.config is not None
        assert "file-server" in result.config


class TestFullMcpPipeline:
    @pytest.mark.asyncio
    async def test_end_to_end_flow(self, mock_server_script: str) -> None:
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(
                command=sys.executable,
                args=[mock_server_script],
            ),
            scope="project",
        )
        client = McpClient()
        connection = await client.connect("e2e-server", config)

        assert isinstance(connection, ConnectedMCPServer)
        assert connection.capabilities.tools is True

        tools = await client.list_tools()
        assert len(tools) == 2

        from src.services.mcp.tool_wrapper import wrap_mcp_tools_for_server
        wrapped_tools = wrap_mcp_tools_for_server(connection, tools, client)
        assert len(wrapped_tools) == 2

        tool_names = {t.name for t in wrapped_tools}
        assert "mcp__e2e-server__echo" in tool_names
        assert "mcp__e2e-server__add" in tool_names

        for tool in wrapped_tools:
            assert tool.is_mcp is True
            assert tool.mcp_info is not None

        result = await client.call_tool("echo", {"message": "integration test"})
        assert result.content[0]["text"] == "integration test"

        result = await client.call_tool("add", {"a": 10, "b": 20})
        assert result.content[0]["text"] == "30"

        await client.close()
