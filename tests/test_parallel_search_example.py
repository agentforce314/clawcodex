"""Exercise the opt-in example with the real loader and a scripted MCP client."""

from pathlib import Path

import pytest

from demos.parallel_search import __main__ as example
from src.services.mcp.config import get_all_mcp_configs
from src.services.mcp.types import (
    ConnectedMCPServer,
    McpToolResult,
    McpToolSchema,
    ServerCapabilities,
    ServerInfo,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_error", [False, True])
async def test_example_uses_loaded_server_and_closes(
    monkeypatch, tmp_path, capsys, tool_error
) -> None:
    """Load the shipped config, reuse the session, and close even on tool errors."""
    demo = Path(__file__).resolve().parents[1] / "demos" / "parallel_search"
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(demo))
    monkeypatch.setenv("CLAWCODEX_MANAGED_CONFIG_DIR", str(tmp_path / "managed"))
    monkeypatch.chdir(tmp_path)
    configs, errors = get_all_mcp_configs()
    assert not errors
    assert set(configs) == {"parallel-search"}
    config = configs["parallel-search"]
    assert config.scope == "user"
    assert config.config.type == "http"
    assert config.config.url == "https://search.parallel.ai/mcp"
    assert config.config.headers == {
        "User-Agent": "clawcodex-parallel-search-example/1.0 "
        "(https://github.com/agentforce314/clawcodex)"
    }

    calls = []
    closed = []

    class Client:
        async def connect(self, name, selected):
            assert name == "parallel-search"
            assert selected == config
            return ConnectedMCPServer(
                name=name,
                config=selected,
                capabilities=ServerCapabilities(tools=True),
                server_info=ServerInfo(name="parallel", version="test"),
            )

        async def list_tools(self):
            return [
                McpToolSchema(name="web_search", input_schema={}),
                McpToolSchema(name="web_fetch", input_schema={}),
            ]

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return McpToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "Python asyncio documentation https://example.org/result-selected",
                    }
                ],
                is_error=tool_error,
            )

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(example, "McpClient", Client)
    if tool_error:
        with pytest.raises(RuntimeError, match="web_search failed"):
            await example.main()
        assert len(calls) == 1
    else:
        await example.main()
        assert [name for name, _ in calls] == ["web_search", "web_fetch"]
        assert calls[0][1]["search_queries"] == [
            "Python asyncio official documentation"
        ]
        assert calls[1][1]["urls"] == ["https://example.org/result-selected"]
        assert calls[0][1]["session_id"] == calls[1][1]["session_id"]
        assert "Python asyncio documentation" in capsys.readouterr().out
    assert closed == [True]


def test_example_rejects_empty_content() -> None:
    """An empty response must not look like a successful search."""
    with pytest.raises(RuntimeError, match="returned no text content"):
        example.print_result("web_search", McpToolResult())


def test_example_rejects_search_without_url() -> None:
    """Never fall back to a hardcoded page when search has no result URL."""
    with pytest.raises(RuntimeError, match="no HTTP"):
        example.select_url("No search results")
