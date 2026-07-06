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
    # real contract is input_schema (McpToolSchema / _wrap), NOT camelCase
    return SimpleNamespace(name=name, description="d",
                           input_schema={"type": "object", "properties": {}})


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
    """_do_mcp_auth is now ASYNC (B1): it awaits rt.submit(trigger_oauth_async)
    so the main loop stays responsive during the 300s browser wait, then
    registers tools + wires handlers (M1) + rebuilds on the main loop."""

    def _run_auth(self, result_dict, *, pending=None):
        import asyncio
        import concurrent.futures

        from src.server.agent_server import _AgentSession

        registered, rebuilt, wired, replies = [], [], [], []

        def _submit(coro):
            f = concurrent.futures.Future()
            f.set_result(result_dict)  # the fake resolves immediately
            return f

        rt = SimpleNamespace(
            submit=_submit,
            trigger_oauth_async=lambda name: None,  # coro arg; fake submit ignores it
            pending_auth=lambda: (pending or []),
        )
        reg = SimpleNamespace(register=lambda t: registered.append(getattr(t, "name", t)))
        stub = SimpleNamespace(
            _mcp_runtime=rt, tool_registry=reg,
            _reply=lambda rid, p: replies.append(p),
            _rebuild_base_prompt_for_mcp=lambda: rebuilt.append(True),
            _wire_mcp_client_handlers=lambda rt_, cl, nm: wired.append(nm),
        )
        asyncio.run(_AgentSession._do_mcp_auth(stub, "r1", "gh"))
        return registered, rebuilt, wired, replies

    def test_is_coroutine(self):
        # B1: must be awaitable (the dispatch awaits it) so the main loop yields
        import inspect

        from src.server.agent_server import _AgentSession
        assert inspect.iscoroutinefunction(_AgentSession._do_mcp_auth)

    def test_success_registers_wires_rebuilds(self):
        newtool = _tool("mcp__gh__do_thing")
        registered, rebuilt, wired, replies = self._run_auth(
            {"ok": True, "tools": [newtool], "client": object()})
        assert registered == ["mcp__gh__do_thing"]  # new tool registered live
        assert wired == ["gh"]                        # M1: handlers wired on the client
        assert rebuilt == [True]                      # prompt rebuilt (instructions surface)
        assert replies[0]["ok"] is True

    def test_failure_no_register_no_wire(self):
        registered, rebuilt, wired, replies = self._run_auth(
            {"ok": False, "error": "nope"}, pending=["gh"])
        assert registered == [] and wired == [] and rebuilt == []
        assert replies[0]["ok"] is False and replies[0]["pending_auth"] == ["gh"]


class TestWireMcpClientHandlers:
    """M1: a late-authed client gets elicitation + (capability-gated)
    list_changed handlers, matching the boot path."""

    def test_wires_elicitation_and_gated_list_changed(self, monkeypatch):
        from src.server import agent_server as mod

        set_elic, set_notif = [], []
        client = SimpleNamespace(
            set_elicitation_handler=lambda h: set_elic.append(h),
            set_notification_handler=lambda h: set_notif.append(h),
            capabilities=SimpleNamespace(tools_list_changed=True),
        )
        monkeypatch.setattr(mod, "_make_elicitation_handler", lambda sess: "elic")
        monkeypatch.setattr(mod, "_make_mcp_notification_handler",
                            lambda rt, sess, name: "notif")
        stub = SimpleNamespace()
        mod._AgentSession._wire_mcp_client_handlers(stub, object(), client, "gh")
        assert set_elic == ["elic"]           # elicitation always wired
        assert set_notif == ["notif"]         # list_changed wired (capability advertised)

    def test_list_changed_skipped_without_capability(self, monkeypatch):
        from src.server import agent_server as mod

        set_notif = []
        client = SimpleNamespace(
            set_elicitation_handler=lambda h: None,
            set_notification_handler=lambda h: set_notif.append(h),
            capabilities=SimpleNamespace(tools_list_changed=False),
        )
        monkeypatch.setattr(mod, "_make_elicitation_handler", lambda sess: "elic")
        monkeypatch.setattr(mod, "_make_mcp_notification_handler",
                            lambda rt, sess, name: "notif")
        mod._AgentSession._wire_mcp_client_handlers(SimpleNamespace(), object(), client, "gh")
        assert set_notif == []  # not advertised → not wired


class TestConcurrentTriggerLock:
    """m1 (coupled to the B1 async fix): concurrent /mcp auth triggers for the
    same server must not double-register tools / duplicate server_infos. The
    per-server asyncio.Lock serializes them."""

    def test_concurrent_triggers_register_once(self, monkeypatch):
        import asyncio

        from src.server.mcp_runtime import McpRuntime

        connected = SimpleNamespace(name="gh", type="connected", instructions="i")

        class _FakeClient:
            def set_auth_provider(self, p): pass
            async def connect(self, name, scoped): return connected
            async def list_tools(self): return [_tool("do_thing")]
            async def close(self): pass

        monkeypatch.setattr("src.services.mcp.client.McpClient", _FakeClient)

        rt = McpRuntime()
        rt._auth_provider = _FakeAuthProvider(succeed=True)
        rt.needs_auth = [{"name": "gh", "auth_url": "x", "scoped": _scoped()}]
        # _wrap captures self._loop for later dispatch; give it a throwaway loop
        rt._loop = asyncio.new_event_loop()

        async def go():
            return await asyncio.gather(
                rt.trigger_oauth_async("gh", open_browser=False),
                rt.trigger_oauth_async("gh", open_browser=False),
            )

        try:
            r1, r2 = asyncio.run(go())
        finally:
            rt._loop.close()
        # exactly one promotion despite two concurrent triggers
        assert len(rt.tools) == 1, f"double-registered: {len(rt.tools)}"
        assert len(rt.server_infos) == 1
        assert rt.pending_auth() == []
        assert (r1["ok"], r2["ok"]) == (True, True)
