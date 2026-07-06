"""Chapter C4 — make OAuth MCP servers reachable.

The live McpRuntime path created McpClient() with NO auth_provider, so an
OAuth server's needs-auth could never be detected — connect() failed silently
and the machinery (trigger_oauth in the parallel, non-live MCPConnectionManager)
was unreachable. These execute McpRuntime.start() + trigger_oauth against fakes
and pin: needs-auth retained (not dropped), the runtime kept alive for the
trigger, the OAuth flow → reconnect → tools promoted, and the _do_mcp_auth
wiring (register tools + rebuild prompt).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _scoped(url="https://mcp.example.com", enabled=True):
    return SimpleNamespace(
        config=SimpleNamespace(url=url, enabled=enabled, auth_server_metadata_url=None),
        scope="user",
    )


def _tool(name="do_thing"):
    return SimpleNamespace(name=name, description="d",
                           inputSchema={"type": "object", "properties": {}})


class _FakeAuthProvider:
    def __init__(self, *, succeed=True):
        self._succeed = succeed
        self.acquire_calls = []

    async def acquire_token(self, *, server_name, server_url, **kw):
        self.acquire_calls.append(server_name)
        if self._succeed:
            return SimpleNamespace(success=True, token="tok", error=None)
        return SimpleNamespace(success=False, token=None, error="user cancelled")


class TestNeedsAuthRetention:
    def _runtime(self, monkeypatch, connect_type, *, auth=None):
        from src.server import mcp_runtime as mod

        info = SimpleNamespace(name="gh", type=connect_type,
                              auth_url="https://auth.example/x", instructions="use me")

        class _FakeClient:
            def set_auth_provider(self, p): self._p = p
            async def connect(self, name, scoped): return info
            async def list_tools(self): return [_tool()]
            async def close(self): pass

        monkeypatch.setattr("src.services.mcp.config.get_all_mcp_configs",
                            lambda: {"gh": _scoped()})
        monkeypatch.setattr("src.services.mcp.client.McpClient", _FakeClient)
        if auth is not None:
            monkeypatch.setattr(mod, "McpAuthProvider", lambda: auth, raising=False)
            monkeypatch.setattr("src.services.mcp.auth_provider.McpAuthProvider",
                                lambda: auth)
        rt = mod.McpRuntime()
        return rt

    def test_needs_auth_retained_not_dropped(self, monkeypatch):
        rt = self._runtime(monkeypatch, "needs-auth", auth=_FakeAuthProvider())
        try:
            assert rt.start() is True  # kept alive despite 0 tools
            assert rt.pending_auth() == ["gh"]
            assert rt.tools == []  # no list_tools for a needs-auth server
        finally:
            rt.shutdown()

    def test_connected_still_works(self, monkeypatch):
        rt = self._runtime(monkeypatch, "connected", auth=_FakeAuthProvider())
        try:
            assert rt.start() is True
            assert rt.pending_auth() == []
            assert len(rt.tools) == 1
            assert [s.name for s in rt.server_infos] == ["gh"]
        finally:
            rt.shutdown()


class TestTriggerOAuth:
    def _runtime_needing_auth(self, monkeypatch, auth):
        from src.server import mcp_runtime as mod

        needs = SimpleNamespace(name="gh", type="needs-auth",
                               auth_url="https://auth/x", instructions="use me")
        connected = SimpleNamespace(name="gh", type="connected", instructions="use me")
        state = {"authed": False}

        class _FakeClient:
            def set_auth_provider(self, p): self._p = p
            async def connect(self, name, scoped):
                return connected if state["authed"] else needs
            async def list_tools(self): return [_tool("do_thing")]
            async def close(self): pass

        monkeypatch.setattr("src.services.mcp.config.get_all_mcp_configs",
                            lambda: {"gh": _scoped()})
        monkeypatch.setattr("src.services.mcp.client.McpClient", _FakeClient)
        monkeypatch.setattr("src.services.mcp.auth_provider.McpAuthProvider",
                            lambda: auth)
        rt = mod.McpRuntime()
        rt.start()
        return rt, state

    def test_trigger_success_reconnects_and_registers(self, monkeypatch):
        auth = _FakeAuthProvider(succeed=True)
        rt, state = self._runtime_needing_auth(monkeypatch, auth)
        try:
            assert rt.pending_auth() == ["gh"]
            state["authed"] = True  # after auth, connect() returns connected
            result = rt.trigger_oauth("gh", open_browser=False)
            assert result["ok"] is True
            assert auth.acquire_calls == ["gh"]
            assert rt.pending_auth() == []  # promoted out of needs_auth
            assert len(rt.tools) == 1
            assert [s.name for s in rt.server_infos] == ["gh"]  # instructions now live
        finally:
            rt.shutdown()

    def test_trigger_auth_failure_stays_pending(self, monkeypatch):
        auth = _FakeAuthProvider(succeed=False)
        rt, state = self._runtime_needing_auth(monkeypatch, auth)
        try:
            result = rt.trigger_oauth("gh", open_browser=False)
            assert result["ok"] is False and "cancel" in result["error"].lower()
            assert rt.pending_auth() == ["gh"]  # still awaiting
            assert rt.tools == []
        finally:
            rt.shutdown()

    def test_trigger_unknown_server(self, monkeypatch):
        auth = _FakeAuthProvider()
        rt, _ = self._runtime_needing_auth(monkeypatch, auth)
        try:
            result = rt.trigger_oauth("nonexistent", open_browser=False)
            assert result["ok"] is False and "awaiting" in result["error"].lower()
        finally:
            rt.shutdown()


class TestDoMcpAuthWiring:
    def test_do_mcp_auth_registers_tools_and_rebuilds(self, monkeypatch):
        # drive _AgentSession._do_mcp_auth with a fake runtime + registry
        from src.server.agent_server import _AgentSession

        registered = []
        rebuilt = []
        newtool = _tool("mcp__gh__do_thing")

        rt = SimpleNamespace(
            trigger_oauth=lambda name: {"ok": True, "tools": [newtool]},
            pending_auth=lambda: [],
        )
        reg = SimpleNamespace(register=lambda t: registered.append(t.name))
        replies = []
        stub = SimpleNamespace(
            _mcp_runtime=rt, tool_registry=reg,
            _reply=lambda rid, payload: replies.append(payload),
            _rebuild_base_prompt_for_mcp=lambda: rebuilt.append(True),
        )
        _AgentSession._do_mcp_auth(stub, "r1", "gh")
        assert registered == ["mcp__gh__do_thing"]  # new tool registered live
        assert rebuilt == [True]                     # prompt rebuilt (instructions surface)
        assert replies[0]["ok"] is True

    def test_do_mcp_auth_failure_no_register(self, monkeypatch):
        from src.server.agent_server import _AgentSession

        registered = []
        rt = SimpleNamespace(
            trigger_oauth=lambda name: {"ok": False, "error": "nope"},
            pending_auth=lambda: ["gh"],
        )
        reg = SimpleNamespace(register=lambda t: registered.append(t))
        replies = []
        stub = SimpleNamespace(
            _mcp_runtime=rt, tool_registry=reg,
            _reply=lambda rid, payload: replies.append(payload),
            _rebuild_base_prompt_for_mcp=lambda: None,
        )
        _AgentSession._do_mcp_auth(stub, "r1", "gh")
        assert registered == []  # nothing registered on failure
        assert replies[0]["ok"] is False and replies[0]["pending_auth"] == ["gh"]
