"""Chapter C2 — MCP server instructions LIVE wiring (completes UTILS-1).

PR #650 ported the rendering but the utils-critic caught it inert: McpRuntime
discarded connect()'s instructions and every live prompt-build site passed
mcp_servers=None. These pin the full live path: retention in McpRuntime, the
REQUEST-scoped uncached section through build_effective_system_prompt (the
LIVE entry point — the UTILS-1 lesson), the disabled-server filter, and the
session/request cache split.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.context_system.prompt_assembly import (
    _build_mcp_instructions_section,
    _build_mcp_section,
)
from src.query.agent_loop_compat import build_effective_system_prompt
from src.tool_system.context import ToolContext, ToolUseOptions


def _tc():
    tc = ToolContext(workspace_root=Path("/tmp"))
    tc.options = ToolUseOptions(tools=[])
    return tc


def _srv(name, instructions=None):
    return SimpleNamespace(name=name, instructions=instructions, type="connected")


def _texts(blocks):
    return "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))


class TestLiveEntryPoint:
    """Through build_effective_system_prompt — the path the agent-server uses."""

    def test_instructions_reach_the_live_build(self):
        blocks = build_effective_system_prompt(
            "", _tc(), mcp_servers=[_srv("github", "Auth via /mcp first."), _srv("fs")]
        )
        joined = _texts(blocks)
        assert "# MCP Server Instructions" in joined
        assert "## github\nAuth via /mcp first." in joined
        assert "## fs" not in joined  # no instructions → no block

    def test_instructions_block_is_request_scoped(self):
        blocks = build_effective_system_prompt(
            "", _tc(), mcp_servers=[_srv("github", "Auth first.")]
        )
        instr = [b for b in blocks if isinstance(b, dict)
                 and "MCP Server Instructions" in b.get("text", "")]
        assert instr and all(b.get("_cache_scope") == "request" for b in instr)

    def test_name_list_stays_session_scoped_without_instructions(self):
        blocks = build_effective_system_prompt(
            "", _tc(), mcp_servers=[_srv("github", "Auth first.")]
        )
        names = [b for b in blocks if isinstance(b, dict)
                 and b.get("text", "").startswith("# MCP Servers")]
        assert names and all(b.get("_cache_scope") == "session" for b in names)
        # the split: the session block must NOT embed the instructions
        assert all("MCP Server Instructions" not in b["text"] for b in names)

    def test_none_path_unchanged(self):
        joined = _texts(build_effective_system_prompt("", _tc(), mcp_servers=None))
        assert "# MCP Server Instructions" not in joined


class TestSectionBuilder:
    def test_never_prompt_cached(self):
        # two consecutive builds with DIFFERENT instructions must both render
        # fresh (no _prompt_cache stale-serve — the utils-critic's M1).
        s1 = _build_mcp_instructions_section([_srv("a", "one")])
        s2 = _build_mcp_instructions_section([_srv("a", "two")])
        assert "one" in s1.content and "two" in s2.content

    def test_no_instructions_no_section(self):
        assert _build_mcp_instructions_section([_srv("a"), _srv("b", "  ")]) is None
        assert _build_mcp_instructions_section(None) is None

    def test_name_section_no_longer_renders_instructions(self):
        sec = _build_mcp_section([_srv("a", "SECRET-GUIDANCE")], use_cache=False)
        assert "SECRET-GUIDANCE" not in sec.content  # moved to the request section


class TestRuntimeRetention:
    def test_start_retains_connected_server_infos(self, monkeypatch):
        """Execute McpRuntime.start() for real against fakes: the connect()
        return is RETAINED (previously discarded — the inert-wiring gap)."""
        from src.server import mcp_runtime as mod

        info = SimpleNamespace(name="srv", type="connected",
                               instructions="use wisely")
        fake_tool = SimpleNamespace(
            name="do_thing", description="d",
            inputSchema={"type": "object", "properties": {}},
        )

        class _FakeClient:
            async def connect(self, name, scoped):
                return info

            async def list_tools(self):
                return [fake_tool]

        scoped = SimpleNamespace(config=SimpleNamespace(enabled=True))
        monkeypatch.setattr(
            "src.services.mcp.config.get_all_mcp_configs", lambda: {"srv": scoped}
        )
        monkeypatch.setattr("src.services.mcp.client.McpClient", _FakeClient)

        rt = mod.McpRuntime()
        try:
            assert rt.start() is True
            assert rt.server_infos == [info]
        finally:
            rt.shutdown()

    def test_non_connected_returns_not_retained(self, monkeypatch):
        from src.server import mcp_runtime as mod

        needs_auth = SimpleNamespace(name="srv", type="needs-auth")
        fake_tool = SimpleNamespace(
            name="t", description="d",
            inputSchema={"type": "object", "properties": {}},
        )

        class _FakeClient:
            async def connect(self, name, scoped):
                return needs_auth

            async def list_tools(self):
                return [fake_tool]

        scoped = SimpleNamespace(config=SimpleNamespace(enabled=True))
        monkeypatch.setattr(
            "src.services.mcp.config.get_all_mcp_configs", lambda: {"srv": scoped}
        )
        monkeypatch.setattr("src.services.mcp.client.McpClient", _FakeClient)

        rt = mod.McpRuntime()
        try:
            rt.start()
            assert rt.server_infos == []  # only truly-connected retained
        finally:
            rt.shutdown()


class TestSessionHelper:
    def _sess(self, runtime, registry):
        from src.server.agent_server import _AgentSession

        stub = SimpleNamespace(_mcp_runtime=runtime, tool_registry=registry)
        return _AgentSession._mcp_server_infos(stub)

    def test_no_runtime_none(self):
        assert self._sess(None, None) is None

    def test_disabled_server_filtered(self):
        rt = SimpleNamespace(server_infos=[_srv("a", "ia"), _srv("b", "ib")])
        reg = SimpleNamespace(disabled_servers={"a"})
        live = self._sess(rt, reg)
        assert [s.name for s in live] == ["b"]

    def test_all_disabled_none(self):
        rt = SimpleNamespace(server_infos=[_srv("a", "ia")])
        reg = SimpleNamespace(disabled_servers={"a"})
        assert self._sess(rt, reg) is None
