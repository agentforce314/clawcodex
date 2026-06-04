"""Real-MCP-server smoke test (WI-0.3 acceptance).

Verifies that the Python MCP stdio transport speaks the canonical
newline-delimited framing by connecting to the official
``@modelcontextprotocol/server-everything`` test server via ``npx``.
If this passes, we know the framing fix in WI-0.1 is spec-compliant.

Automatically skipped when Node/npx is not available, so CI without
Node won't fail; environments with Node will run the test (which
downloads the npm package on first invocation — slow).
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from src.services.mcp.client import McpClient
from src.services.mcp.types import (
    ConnectedMCPServer,
    McpStdioServerConfig,
    ScopedMcpServerConfig,
)


pytestmark = pytest.mark.skipif(
    shutil.which("npx") is None,
    reason="`npx` (Node) required for the real-MCP-server smoke test",
)


@pytest.mark.asyncio
async def test_connects_to_official_everything_server() -> None:
    """Smoke-test the stdio framing fix against an official MCP server.

    Uses ``@modelcontextprotocol/server-everything``, which speaks the
    canonical newline-delimited JSON-RPC framing. If the Python stdio
    transport's framing was wrong (LSP-style Content-Length, as it was
    prior to WI-0.1), this test would hang waiting for an init response.
    """
    config = ScopedMcpServerConfig(
        config=McpStdioServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-everything"],
        ),
        scope="project",
    )
    client = McpClient()
    try:
        connection = await asyncio.wait_for(
            client.connect("everything", config),
            timeout=120.0,  # Generous: first run downloads ~MB of npm package.
        )
        assert isinstance(connection, ConnectedMCPServer), (
            f"Expected ConnectedMCPServer, got {type(connection).__name__}: "
            f"{getattr(connection, 'error', '<no error>')}"
        )
        assert connection.capabilities.tools is True

        tools = await client.list_tools()
        assert len(tools) > 0, "Expected at least one tool from server-everything"
        # The "everything" server publishes a known ``echo`` tool across
        # versions (some versions also have ``add``/``get-sum``; ``echo``
        # is the most stable lowest-common-denominator).
        assert any(t.name == "echo" for t in tools), (
            f"Expected `echo` tool; got {[t.name for t in tools]}"
        )
    finally:
        await client.close()
