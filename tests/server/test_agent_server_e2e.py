"""End-to-end tests for the agent-server (src/server/agent_server.py).

These drive the REAL Direct Connect client (src/server/direct_connect_manager.py)
against the REAL DirectConnectServer + REAL make_spawn_agent over the actual
WebSocket protocol — only the model *provider* is stubbed, so no network is
touched. They exercise the load-bearing Phase-0/1/2 paths:

  * a full turn streams an assistant message + a result (data plane)
  * a tool permission round-trip: server asks `can_use_tool`, client allows,
    the tool actually runs (control plane)
  * system/init carries the protocol_version + tool schemas
  * control ops (set_permission_mode / get_settings) round-trip at the handle
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.providers.base import ChatResponse
from src.server.agent_server import PROTOCOL_VERSION, AgentServerConfig, make_spawn_agent
from src.server.direct_connect_manager import (
    DirectConnectCallbacks,
    DirectConnectSessionManager,
)
from src.server.direct_connect_session import create_direct_connect_session
from src.server.server import DirectConnectServer
from src.server.session_manager import SessionManager
from src.server.types import ServerConfig
from src.tool_system.build_tool import build_tool
from src.tool_system.protocol import ToolResult


pytestmark = pytest.mark.integration


# ─── fake providers (model stub; no network) ─────────────────────────────────


class _TextProvider:
    """One turn: plain text, no tools."""

    def __init__(self, api_key=None, base_url=None, model=None):
        self.model = model or "fake"

    def chat(self, messages, tools=None, **kw):
        return ChatResponse(
            content="hi back",
            model=self.model,
            usage={"input_tokens": 3, "output_tokens": 2},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):  # force fallback to chat()
        raise NotImplementedError


class _ToolThenTextProvider:
    """Turn 1: call the ask-tool. Turn 2: final text."""

    def __init__(self, api_key=None, base_url=None, model=None):
        self.model = model or "fake"
        self._turn = 0

    def chat(self, messages, tools=None, **kw):
        self._turn += 1
        if self._turn == 1:
            return ChatResponse(
                content="running the tool",
                model=self.model,
                usage={"input_tokens": 4, "output_tokens": 3},
                finish_reason="tool_use",
                tool_uses=[{"id": "t1", "name": "DoThing", "input": {"x": "1"}}],
            )
        return ChatResponse(
            content="all done",
            model=self.model,
            usage={"input_tokens": 6, "output_tokens": 4},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


class _ThinkingProvider:
    """One streaming turn that emits a live thinking delta then text."""

    def __init__(self, api_key=None, base_url=None, model=None):
        self.model = model or "fake"

    def chat(self, messages, tools=None, **kw):
        return ChatResponse(
            content="answer", model=self.model,
            usage={"input_tokens": 3, "output_tokens": 2},
            finish_reason="stop", tool_uses=None,
        )

    def chat_stream_response(
        self, messages, tools=None, on_text_chunk=None, abort_signal=None,
        on_thinking_chunk=None, **kw,
    ):
        if on_thinking_chunk is not None:
            on_thinking_chunk("let me think ")
        if on_text_chunk is not None:
            on_text_chunk("answer")
        return ChatResponse(
            content="answer", model=self.model,
            usage={"input_tokens": 3, "output_tokens": 2},
            finish_reason="stop", tool_uses=None,
        )


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _patches(provider_cls, registry):
    """Patch every construction hook `_build_runtime` reaches into."""
    return [
        patch("src.config.get_default_provider", lambda: "anthropic"),
        patch(
            "src.config.get_provider_config",
            lambda n: {"api_key": "x", "default_model": "fake", "base_url": None},
        ),
        patch("src.providers.get_provider_class", lambda n: provider_cls),
        patch("src.providers.provider_requires_api_key", lambda n: False),
        patch("src.providers.resolve_api_key", lambda n, c: "x"),
        patch(
            "src.tool_system.defaults.build_default_registry",
            lambda provider=None: registry,
        ),
        patch(
            "src.query.agent_loop_compat.build_effective_system_prompt",
            lambda *a, **k: "You are a test assistant.",
        ),
        patch(
            "src.outputStyles.resolve_output_style",
            lambda *a, **k: SimpleNamespace(prompt=""),
        ),
    ]


@contextlib.asynccontextmanager
async def _running_server(tmp_path, provider_cls, registry, agent_config=None):
    config = ServerConfig(host="127.0.0.1", port=_free_port(), workspace=str(tmp_path))
    manager = SessionManager(workspace=str(tmp_path), index_path=tmp_path / "idx.json")
    spawn = make_spawn_agent(agent_config or AgentServerConfig())
    server = DirectConnectServer(config=config, manager=manager, spawn_agent=spawn)
    await server.start()
    serve_task = asyncio.get_running_loop().create_task(server.serve_forever())
    with contextlib.ExitStack() as stack:
        for p in _patches(provider_cls, registry):
            stack.enter_context(p)
        try:
            yield config
        finally:
            await server.stop()
            serve_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await serve_task


async def _wait_for(predicate, timeout=10.0, interval=0.05):
    for _ in range(int(timeout / interval)):
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


def _assistant_text(msg: dict) -> str:
    """Extract plain text from an assistant SDK message.

    The agent emits Anthropic-shaped content — a list of content blocks —
    not a bare string, so tests must flatten it.
    """
    content = msg.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


# ─── data plane ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_streams_assistant_and_result(tmp_path):
    from src.tool_system.registry import ToolRegistry

    async with _running_server(tmp_path, _TextProvider, ToolRegistry([])) as config:
        cfg, _ = await create_direct_connect_session(
            server_url=f"http://127.0.0.1:{config.port}", cwd=str(tmp_path)
        )
        received: list[dict] = []
        callbacks = DirectConnectCallbacks(
            on_message=lambda m: received.append(m),
            on_permission_request=lambda req, rid: None,
        )
        client = DirectConnectSessionManager(cfg, callbacks)
        await client.connect()
        try:
            # system/init should arrive first, carrying the protocol version.
            assert await _wait_for(
                lambda: any(m.get("subtype") == "init" for m in received)
            ), "no system/init received"
            init = next(m for m in received if m.get("subtype") == "init")
            assert init["protocol_version"] == PROTOCOL_VERSION
            assert init["model"] == "fake"

            await client.send_message("hello")

            assert await _wait_for(
                lambda: any(m.get("type") == "result" for m in received)
            ), "no result received"
            assistant = [m for m in received if m.get("type") == "assistant"]
            assert assistant, "no assistant message"
            assert _assistant_text(assistant[0]) == "hi back"
            result = next(m for m in received if m.get("type") == "result")
            assert result["subtype"] == "success"
        finally:
            await client.disconnect()


@pytest.mark.asyncio
async def test_turn_streams_thinking_delta(tmp_path):
    """A provider that emits reasoning deltas → the server ships thinking_delta."""
    from src.tool_system.registry import ToolRegistry

    async with _running_server(tmp_path, _ThinkingProvider, ToolRegistry([])) as config:
        cfg, _ = await create_direct_connect_session(
            server_url=f"http://127.0.0.1:{config.port}", cwd=str(tmp_path)
        )
        received: list[dict] = []
        callbacks = DirectConnectCallbacks(
            on_message=lambda m: received.append(m),
            on_permission_request=lambda req, rid: None,
        )
        client = DirectConnectSessionManager(cfg, callbacks)
        await client.connect()
        try:
            await client.send_message("hello")
            assert await _wait_for(
                lambda: any(m.get("type") == "result" for m in received)
            ), "no result received"
            thinking = [
                m for m in received
                if m.get("type") == "stream_event"
                and m.get("event", {}).get("delta", {}).get("type") == "thinking_delta"
            ]
            assert thinking, "no thinking_delta stream_event emitted"
            assert thinking[0]["event"]["delta"]["thinking"] == "let me think "
        finally:
            await client.disconnect()


# ─── control plane: permission round-trip ────────────────────────────────────


@pytest.mark.asyncio
async def test_permission_round_trip_allow_runs_tool(tmp_path):
    from src.permissions.types import PermissionPassthroughResult
    from src.tool_system.registry import ToolRegistry

    ran: list[dict] = []

    def _call(tool_input, context):
        ran.append(dict(tool_input))
        return ToolResult(name="DoThing", output={"ok": True})

    tool = build_tool(
        name="DoThing",
        description="does a thing (asks first)",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        call=_call,
        check_permissions=lambda tool_input, context: PermissionPassthroughResult(),
    )
    registry = ToolRegistry([tool])

    async with _running_server(tmp_path, _ToolThenTextProvider, registry) as config:
        cfg, _ = await create_direct_connect_session(
            server_url=f"http://127.0.0.1:{config.port}", cwd=str(tmp_path)
        )
        received: list[dict] = []
        perms: list[tuple[dict, str]] = []

        async def on_perm(req, request_id):
            perms.append((req, request_id))
            await client.respond_to_permission_request(
                request_id,
                SimpleNamespace(behavior="allow", updated_input={}, message=""),
            )

        callbacks = DirectConnectCallbacks(
            on_message=lambda m: received.append(m),
            on_permission_request=on_perm,
        )
        client = DirectConnectSessionManager(cfg, callbacks)
        await client.connect()
        try:
            await _wait_for(lambda: any(m.get("subtype") == "init" for m in received))
            await client.send_message("do the thing")

            assert await _wait_for(
                lambda: any(
                    m.get("type") == "result" and m.get("subtype") == "success"
                    for m in received
                )
            ), "turn did not complete successfully"

            # The server asked for permission, the client allowed, the tool ran.
            assert perms, "server never asked can_use_tool"
            assert perms[0][0]["subtype"] == "can_use_tool"
            assert perms[0][0]["tool_name"] == "DoThing"
            assert ran == [{"x": "1"}], "tool body did not execute after allow"
            assistant = [m for m in received if m.get("type") == "assistant"]
            assert any(_assistant_text(a) == "all done" for a in assistant)
        finally:
            await client.disconnect()


@pytest.mark.asyncio
async def test_permission_deny_blocks_tool(tmp_path):
    from src.permissions.types import PermissionPassthroughResult
    from src.tool_system.registry import ToolRegistry

    ran: list[dict] = []

    tool = build_tool(
        name="DoThing",
        description="does a thing (asks first)",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        call=lambda tool_input, context: ran.append(dict(tool_input))
        or ToolResult(name="DoThing", output={"ok": True}),
        check_permissions=lambda tool_input, context: PermissionPassthroughResult(),
    )

    async with _running_server(
        tmp_path, _ToolThenTextProvider, ToolRegistry([tool])
    ) as config:
        cfg, _ = await create_direct_connect_session(
            server_url=f"http://127.0.0.1:{config.port}", cwd=str(tmp_path)
        )
        received: list[dict] = []

        async def on_perm(req, request_id):
            await client.respond_to_permission_request(
                request_id,
                SimpleNamespace(behavior="deny", updated_input={}, message="nope"),
            )

        client = DirectConnectSessionManager(
            cfg,
            DirectConnectCallbacks(
                on_message=lambda m: received.append(m), on_permission_request=on_perm
            ),
        )
        await client.connect()
        try:
            await _wait_for(lambda: any(m.get("subtype") == "init" for m in received))
            await client.send_message("do the thing")
            assert await _wait_for(
                lambda: any(m.get("type") == "result" for m in received)
            )
            assert ran == [], "tool ran despite a deny"
        finally:
            await client.disconnect()


def _ask_tool(ran: list):
    from src.permissions.types import PermissionPassthroughResult

    return build_tool(
        name="DoThing",
        description="does a thing (asks first)",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        call=lambda ti, c: ran.append(dict(ti)) or ToolResult(name="DoThing", output={"ok": True}),
        check_permissions=lambda ti, c: PermissionPassthroughResult(),
    )


async def _drive_until_permission_ask(handle, gen):
    """Send a prompt and drain until the server emits a can_use_tool request."""
    await asyncio.wait_for(gen.__anext__(), timeout=5)  # init
    await handle.send_to_agent(
        {"type": "user", "message": {"role": "user", "content": "go"}}
    )
    for _ in range(40):
        msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
        if msg.get("type") == "control_request" and msg["request"].get("subtype") == "can_use_tool":
            return
    raise AssertionError("server never asked can_use_tool")


@pytest.mark.asyncio
async def test_interrupt_during_permission_releases_fast(tmp_path):
    """Proposal §7's highest-risk case: ESC during a permission prompt must both
    deny the pending ask AND abort the turn — fast, not at permission_timeout_s."""
    from src.tool_system.registry import ToolRegistry

    ran: list = []
    # A long timeout: if the interrupt did NOT release the wait, the turn would
    # only end at ~30s. We assert it ends in well under that.
    cfg = AgentServerConfig(permission_mode="default", permission_timeout_s=30.0)
    with contextlib.ExitStack() as stack:
        for p in _patches(_ToolThenTextProvider, ToolRegistry([_ask_tool(ran)])):
            stack.enter_context(p)
        handle = await make_spawn_agent(cfg)("ds", str(tmp_path), None)
        gen = handle.messages_from_agent()
        try:
            await _drive_until_permission_ask(handle, gen)
            t0 = time.monotonic()
            await handle.send_to_agent(
                {"type": "control_request", "request": {"subtype": "interrupt"}}
            )
            result = None
            for _ in range(40):
                msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
                if msg.get("type") == "result":
                    result = msg
                    break
            elapsed = time.monotonic() - t0
            assert result is not None, "no result after interrupt"
            assert result["subtype"] in ("cancelled", "error")
            assert elapsed < 5.0, f"interrupt released only after {elapsed:.1f}s (timeout was 30s)"
            assert ran == [], "tool ran despite interrupt during the permission prompt"
        finally:
            await handle.shutdown()
            with contextlib.suppress(Exception):
                await gen.aclose()


@pytest.mark.asyncio
async def test_permission_timeout_default_denies(tmp_path):
    """A client that never answers must not wedge a tool: the server default-denies
    after permission_timeout_s and the tool stays unrun."""
    from src.tool_system.registry import ToolRegistry

    ran: list = []
    cfg = AgentServerConfig(permission_mode="default", permission_timeout_s=1.0)
    with contextlib.ExitStack() as stack:
        for p in _patches(_ToolThenTextProvider, ToolRegistry([_ask_tool(ran)])):
            stack.enter_context(p)
        handle = await make_spawn_agent(cfg)("ds", str(tmp_path), None)
        gen = handle.messages_from_agent()
        try:
            await _drive_until_permission_ask(handle, gen)
            # Deliberately never respond — wait for the default-deny to drive a result.
            result = None
            for _ in range(60):
                msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
                if msg.get("type") == "result":
                    result = msg
                    break
            assert result is not None, "no result after permission timeout"
            assert ran == [], "tool ran despite no permission answer (timeout should deny)"
        finally:
            await handle.shutdown()
            with contextlib.suppress(Exception):
                await gen.aclose()


# ─── control ops at the handle level (client filters control_response) ───────


@pytest.mark.asyncio
async def test_control_ops_set_mode_and_get_settings(tmp_path):
    from src.tool_system.registry import ToolRegistry

    with contextlib.ExitStack() as stack:
        for p in _patches(_TextProvider, ToolRegistry([])):
            stack.enter_context(p)
        spawn = make_spawn_agent(AgentServerConfig(permission_mode="default"))
        handle = await spawn("ds_test", str(tmp_path), None)
        gen = handle.messages_from_agent()
        try:
            # Drain the initial system/init.
            init = await asyncio.wait_for(gen.__anext__(), timeout=5)
            assert init["subtype"] == "init"
            assert init["permission_mode"] == "default"

            await handle.send_to_agent(
                {
                    "type": "control_request",
                    "request_id": "c1",
                    "request": {
                        "subtype": "set_permission_mode",
                        "mode": "acceptEdits",
                    },
                }
            )
            await handle.send_to_agent(
                {
                    "type": "control_request",
                    "request_id": "c2",
                    "request": {"subtype": "get_settings"},
                }
            )

            # Collect control_responses until we see the get_settings reply.
            settings = None
            for _ in range(10):
                msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
                if (
                    msg.get("type") == "control_response"
                    and msg["response"].get("request_id") == "c2"
                ):
                    settings = msg["response"]["response"]
                    break
            assert settings is not None, "no get_settings reply"
            assert settings["permission_mode"] == "acceptEdits"
            assert settings["model"] == "fake"
        finally:
            await handle.shutdown()
            with contextlib.suppress(Exception):
                await gen.aclose()


@pytest.mark.asyncio
async def test_control_set_output_style(tmp_path):
    """set_output_style validates the style and accepts a valid one."""
    from src.tool_system.registry import ToolRegistry

    with contextlib.ExitStack() as stack:
        for p in _patches(_TextProvider, ToolRegistry([])):
            stack.enter_context(p)
        spawn = make_spawn_agent(AgentServerConfig(permission_mode="default"))
        handle = await spawn("ds_test", str(tmp_path), None)
        gen = handle.messages_from_agent()
        try:
            init = await asyncio.wait_for(gen.__anext__(), timeout=5)
            assert init["subtype"] == "init"

            async def _reply_for(rid, req):
                await handle.send_to_agent({"type": "control_request", "request_id": rid, "request": req})
                for _ in range(12):
                    msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
                    if msg.get("type") == "control_response" and msg["response"].get("request_id") == rid:
                        return msg["response"]["response"]
                raise AssertionError(f"no reply for {rid}")

            ok = await _reply_for("s1", {"subtype": "set_output_style", "style": "concise"})
            assert ok["ok"] is True and ok["style"] == "concise"

            bad = await _reply_for("s2", {"subtype": "set_output_style", "style": "bogus"})
            assert bad["ok"] is False and "valid" in bad["error"]
        finally:
            await handle.shutdown()
            with contextlib.suppress(Exception):
                await gen.aclose()


@pytest.mark.asyncio
async def test_control_knowledge(tmp_path):
    """knowledge control returns stats and toggles enable/disable."""
    from src.tool_system.registry import ToolRegistry

    with contextlib.ExitStack() as stack:
        for p in _patches(_TextProvider, ToolRegistry([])):
            stack.enter_context(p)
        spawn = make_spawn_agent(AgentServerConfig(permission_mode="default"))
        handle = await spawn("ds_test", str(tmp_path), None)
        gen = handle.messages_from_agent()
        try:
            init = await asyncio.wait_for(gen.__anext__(), timeout=5)
            assert init["subtype"] == "init"

            async def _reply_for(rid, req):
                await handle.send_to_agent({"type": "control_request", "request_id": rid, "request": req})
                for _ in range(12):
                    msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
                    if msg.get("type") == "control_response" and msg["response"].get("request_id") == rid:
                        return msg["response"]["response"]
                raise AssertionError(f"no reply for {rid}")

            st = await _reply_for("k1", {"subtype": "knowledge", "action": "status"})
            assert st["ok"] is True and "total" in st["stats"] and st["enabled"] is True

            off = await _reply_for("k2", {"subtype": "knowledge", "action": "disable"})
            assert off["enabled"] is False

            lst = await _reply_for("k3", {"subtype": "knowledge", "action": "list"})
            assert lst["ok"] is True and isinstance(lst["entities"], list)
        finally:
            await handle.shutdown()
            with contextlib.suppress(Exception):
                await gen.aclose()


@pytest.mark.asyncio
async def test_control_wiki(tmp_path):
    """wiki control inits the structure and reports status."""
    from src.tool_system.registry import ToolRegistry

    with contextlib.ExitStack() as stack:
        for p in _patches(_TextProvider, ToolRegistry([])):
            stack.enter_context(p)
        spawn = make_spawn_agent(AgentServerConfig(permission_mode="default"))
        handle = await spawn("ds_test", str(tmp_path), None)
        gen = handle.messages_from_agent()
        try:
            init = await asyncio.wait_for(gen.__anext__(), timeout=5)
            assert init["subtype"] == "init"

            async def _reply_for(rid, req):
                await handle.send_to_agent({"type": "control_request", "request_id": rid, "request": req})
                for _ in range(12):
                    msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
                    if msg.get("type") == "control_response" and msg["response"].get("request_id") == rid:
                        return msg["response"]["response"]
                raise AssertionError(f"no reply for {rid}")

            before = await _reply_for("w0", {"subtype": "wiki", "action": "status"})
            assert before["initialized"] is False

            ini = await _reply_for("w1", {"subtype": "wiki", "action": "init"})
            assert ini["ok"] is True and len(ini["created_files"]) >= 4

            after = await _reply_for("w2", {"subtype": "wiki", "action": "status"})
            assert after["initialized"] is True and after["page_count"] == 1
        finally:
            await handle.shutdown()
            with contextlib.suppress(Exception):
                await gen.aclose()


@pytest.mark.asyncio
async def test_control_background_task(tmp_path):
    """bg_run starts a detached command; bg_list reports it completing."""
    from src.tool_system.registry import ToolRegistry

    with contextlib.ExitStack() as stack:
        for p in _patches(_TextProvider, ToolRegistry([])):
            stack.enter_context(p)
        spawn = make_spawn_agent(AgentServerConfig(permission_mode="default"))
        handle = await spawn("ds_test", str(tmp_path), None)
        gen = handle.messages_from_agent()
        try:
            init = await asyncio.wait_for(gen.__anext__(), timeout=5)
            assert init["subtype"] == "init"

            async def _reply_for(rid, req):
                await handle.send_to_agent({"type": "control_request", "request_id": rid, "request": req})
                for _ in range(12):
                    msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
                    if msg.get("type") == "control_response" and msg["response"].get("request_id") == rid:
                        return msg["response"]["response"]
                raise AssertionError(f"no reply for {rid}")

            run = await _reply_for("b1", {"subtype": "bg_run", "command": "echo hi"})
            assert run["ok"] is True and run["id"]
            tid = run["id"]

            # poll until the task completes
            done = None
            for i in range(20):
                lst = await _reply_for(f"bl{i}", {"subtype": "bg_list"})
                match = [t for t in lst["tasks"] if t["id"] == tid]
                if match and match[0]["status"] != "running":
                    done = match[0]
                    break
                await asyncio.sleep(0.1)
            assert done is not None and done["status"] == "done"
            assert "hi" in done["output"]

            # bg_agent builds a detached `clawcodex -p <prompt>` task.
            ag = await _reply_for("a1", {"subtype": "bg_agent", "command": "hello"})
            assert ag["ok"] is True and ag["id"]
            assert ag["command"] == "clawcodex -p hello"
            await _reply_for("ak", {"subtype": "bg_kill", "id": ag["id"]})  # clean up
        finally:
            await handle.shutdown()
            with contextlib.suppress(Exception):
                await gen.aclose()


@pytest.mark.asyncio
async def test_control_insights(tmp_path):
    """insights guards an empty session, then returns a model-based narrative."""
    from src.tool_system.registry import ToolRegistry

    with contextlib.ExitStack() as stack:
        for p in _patches(_TextProvider, ToolRegistry([])):
            stack.enter_context(p)
        spawn = make_spawn_agent(AgentServerConfig(permission_mode="default"))
        handle = await spawn("ds_test", str(tmp_path), None)
        gen = handle.messages_from_agent()
        try:
            init = await asyncio.wait_for(gen.__anext__(), timeout=5)
            assert init["subtype"] == "init"

            async def _reply_for(rid, req):
                await handle.send_to_agent({"type": "control_request", "request_id": rid, "request": req})
                for _ in range(30):
                    msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
                    if msg.get("type") == "control_response" and msg["response"].get("request_id") == rid:
                        return msg["response"]["response"]
                raise AssertionError(f"no reply for {rid}")

            empty = await _reply_for("i0", {"subtype": "insights"})
            assert empty["ok"] is False  # no conversation yet

            # run a turn so there is conversation to analyze
            await handle.send_to_agent({"type": "user", "message": {"role": "user", "content": "hello"}})
            for _ in range(30):
                msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
                if msg.get("type") == "result":
                    break

            ins = await _reply_for("i1", {"subtype": "insights"})
            assert ins["ok"] is True and ins["insights"]  # _TextProvider returns a narrative
        finally:
            await handle.shutdown()
            with contextlib.suppress(Exception):
                await gen.aclose()


@pytest.mark.asyncio
async def test_control_plan(tmp_path):
    """plan set/view/clear round-trips and injects into the system prompt."""
    from src.tool_system.registry import ToolRegistry

    with contextlib.ExitStack() as stack:
        for p in _patches(_TextProvider, ToolRegistry([])):
            stack.enter_context(p)
        spawn = make_spawn_agent(AgentServerConfig(permission_mode="default"))
        handle = await spawn("ds_test", str(tmp_path), None)
        gen = handle.messages_from_agent()
        try:
            init = await asyncio.wait_for(gen.__anext__(), timeout=5)
            assert init["subtype"] == "init"

            async def _reply_for(rid, req):
                await handle.send_to_agent({"type": "control_request", "request_id": rid, "request": req})
                for _ in range(12):
                    msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
                    if msg.get("type") == "control_response" and msg["response"].get("request_id") == rid:
                        return msg["response"]["response"]
                raise AssertionError(f"no reply for {rid}")

            empty = await _reply_for("p0", {"subtype": "plan", "action": "view"})
            assert empty["plan"] == ""
            setr = await _reply_for("p1", {"subtype": "plan", "action": "set", "text": "ship v2"})
            assert setr["ok"] is True and "ship v2" in setr["plan"]
            view = await _reply_for("p2", {"subtype": "plan", "action": "view"})
            assert "ship v2" in view["plan"]
            cleared = await _reply_for("p3", {"subtype": "plan", "action": "clear"})
            assert cleared["plan"] == ""
        finally:
            await handle.shutdown()
            with contextlib.suppress(Exception):
                await gen.aclose()


@pytest.mark.asyncio
async def test_interrupt_trips_abort(tmp_path):
    """An interrupt control_request must trip the in-flight turn's abort."""
    from src.permissions.types import PermissionPassthroughResult
    from src.tool_system.registry import ToolRegistry

    started = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _call(tool_input, context):
        # Signal that the turn is mid-tool, then spin until aborted.
        loop.call_soon_threadsafe(started.set)
        for _ in range(200):
            if context.abort_controller.signal.aborted:
                from src.utils.abort_controller import AbortError

                raise AbortError("aborted")
            import time

            time.sleep(0.02)
        return ToolResult(name="Spin", output={"ok": True})

    tool = build_tool(
        name="Spin",
        description="spins until aborted",
        input_schema={"type": "object", "properties": {}},
        call=_call,
        # Passthrough + bypassPermissions mode → runs without asking.
        check_permissions=lambda ti, c: PermissionPassthroughResult(),
    )

    cfg = AgentServerConfig(permission_mode="bypassPermissions")
    with contextlib.ExitStack() as stack:
        for p in _patches(_SpinProvider, ToolRegistry([tool])):
            stack.enter_context(p)
        handle = await spawn_for(stack, cfg, tmp_path)
        gen = handle.messages_from_agent()
        try:
            await asyncio.wait_for(gen.__anext__(), timeout=5)  # init
            await handle.send_to_agent(
                {"type": "user", "message": {"role": "user", "content": "spin"}}
            )
            assert await _wait_for(lambda: started.is_set(), timeout=5)
            await handle.send_to_agent(
                {"type": "control_request", "request": {"subtype": "interrupt"}}
            )
            # Drain until a result arrives; it must be cancelled or error, not hang.
            result = None
            for _ in range(40):
                msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
                if msg.get("type") == "result":
                    result = msg
                    break
            assert result is not None
            assert result["subtype"] in ("cancelled", "error")
        finally:
            await handle.shutdown()
            with contextlib.suppress(Exception):
                await gen.aclose()


class _SpinProvider:
    def __init__(self, api_key=None, base_url=None, model=None):
        self.model = model or "fake"
        self._turn = 0

    def chat(self, messages, tools=None, **kw):
        self._turn += 1
        if self._turn == 1:
            return ChatResponse(
                content="spinning",
                model=self.model,
                usage={"input_tokens": 1, "output_tokens": 1},
                finish_reason="tool_use",
                tool_uses=[{"id": "s1", "name": "Spin", "input": {}}],
            )
        return ChatResponse(
            content="done",
            model=self.model,
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


async def spawn_for(stack, agent_config, tmp_path):
    spawn = make_spawn_agent(agent_config)
    return await spawn("ds_test", str(tmp_path), None)
