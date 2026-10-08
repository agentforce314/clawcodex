"""Search and fetch anonymously through ClawCodex's configured HTTP MCP client."""

from __future__ import annotations

import asyncio
import re
import uuid

from src.services.mcp.client import McpClient
from src.services.mcp.config import get_all_mcp_configs
from src.services.mcp.types import ConnectedMCPServer, McpToolResult


def print_result(name: str, result: McpToolResult) -> str:
    """Print text content, failing visibly on a server-reported tool error."""
    if result.is_error:
        raise RuntimeError(f"{name} failed: {result.content}")
    text = "\n".join(
        block.get("text", "") for block in result.content if block.get("type") == "text"
    )
    if not text.strip():
        raise RuntimeError(f"{name} returned no text content")
    print(f"\n{name}:\n{text}")
    return text


def select_url(search_text: str) -> str:
    """Select the first HTTP(S) URL in the returned search text."""
    match = re.search(r'https?://[^\s"<>]+', search_text)
    if match is None:
        raise RuntimeError("web_search returned no HTTP(S) URL to fetch")
    return match.group(0).rstrip("),]")


async def main() -> None:
    """Load the opt-in server and run one search and one fetch without an LLM."""
    configs, errors = get_all_mcp_configs()
    if "parallel-search" not in configs:
        raise RuntimeError(
            "Configure parallel-search first (see demos/parallel_search/README.md). "
            f"Configuration notices: {errors}"
        )
    client = McpClient()
    try:
        connection = await client.connect("parallel-search", configs["parallel-search"])
        if not isinstance(connection, ConnectedMCPServer):
            raise RuntimeError(f"MCP connection failed: {connection}")
        tools = {tool.name for tool in await client.list_tools()}
        if not {"web_search", "web_fetch"}.issubset(tools):
            raise RuntimeError(
                f"Expected web_search and web_fetch, got {sorted(tools)}"
            )
        session_id = str(uuid.uuid4())
        search_text = print_result(
            "web_search",
            await client.call_tool(
                "web_search",
                {
                    "objective": "Find the official Python asyncio documentation",
                    "search_queries": ["Python asyncio official documentation"],
                    "session_id": session_id,
                },
            ),
        )
        print_result(
            "web_fetch",
            await client.call_tool(
                "web_fetch",
                {
                    "urls": [select_url(search_text)],
                    "objective": "Explain what asyncio is used for",
                    "session_id": session_id,
                },
            ),
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
