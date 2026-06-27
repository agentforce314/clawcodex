"""McpRuntime: connect a stdio MCP server on a dedicated loop and call a tool
through the loop-correct sync wrapper (the path the agent-server uses)."""
from __future__ import annotations

import sys
from pathlib import Path

from tests.integration.test_mcp_integration import MOCK_MCP_SERVER_SCRIPT
from src.services.mcp.types import McpStdioServerConfig, ScopedMcpServerConfig
from src.server.mcp_runtime import McpRuntime
from src.tool_system.context import ToolContext


def _configure(monkeypatch, tmp_path: Path) -> None:
    script = tmp_path / "mock_mcp_server.py"
    script.write_text(MOCK_MCP_SERVER_SCRIPT)
    cfg = {
        "test-server": ScopedMcpServerConfig(
            config=McpStdioServerConfig(command=sys.executable, args=[str(script)]),
            scope="project",
        )
    }
    import src.services.mcp.config as mcpconfig

    monkeypatch.setattr(mcpconfig, "get_all_mcp_configs", lambda: cfg)


def test_runtime_connects_lists_and_calls(monkeypatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path)
    rt = McpRuntime()
    try:
        assert rt.start() is True
        assert set(rt.servers.get("test-server", [])) == {"echo", "add"}
        names = {t.name for t in rt.tools}
        assert "mcp__test-server__echo" in names
        assert "mcp__test-server__add" in names

        # Call through the loop-correct sync wrapper (connection lives on the
        # runtime's bg loop; this dispatches back to it via run_coroutine_threadsafe).
        echo = next(t for t in rt.tools if t.name == "mcp__test-server__echo")
        res = echo.call({"message": "hi mcp"}, ToolContext(workspace_root="."))
        assert res.is_error is False
        assert "hi mcp" in res.output

        add = next(t for t in rt.tools if t.name == "mcp__test-server__add")
        res2 = add.call({"a": 3, "b": 4}, ToolContext(workspace_root="."))
        assert res2.is_error is False
        assert "7" in res2.output
    finally:
        rt.shutdown()


def test_mcp_tools_register_into_default_registry(monkeypatch, tmp_path: Path) -> None:
    """The agent-server wiring: MCP tools register into a build_default_registry
    and are callable by name (what the model's tool dispatch does)."""
    _configure(monkeypatch, tmp_path)
    from src.tool_system.build_tool import find_tool_by_name
    from src.tool_system.defaults import build_default_registry

    class _StubProvider:
        model = "stub"

    registry = build_default_registry(provider=_StubProvider())
    rt = McpRuntime()
    try:
        assert rt.start() is True
        for mtool in rt.tools:
            registry.register(mtool)
        tools = list(registry.list_tools())
        tool = find_tool_by_name(tools, "mcp__test-server__echo")
        assert tool is not None
        res = tool.call({"message": "via registry"}, ToolContext(workspace_root="."))
        assert res.is_error is False
        assert "via registry" in res.output
    finally:
        rt.shutdown()


def test_runtime_no_servers_is_noop(monkeypatch) -> None:
    import src.services.mcp.config as mcpconfig

    monkeypatch.setattr(mcpconfig, "get_all_mcp_configs", lambda: {})
    rt = McpRuntime()
    assert rt.start() is False
    assert rt.tools == []
    rt.shutdown()  # idempotent / safe when never started
