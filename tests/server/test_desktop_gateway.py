"""Tests for the desktop gateway (``/api/ws`` JSON-RPC surface of serve).

Two tiers:

- **Fake-agent tier** — a scripted agent handle exercises the JSON-RPC pump,
  frame translation, and the approval round-trip deterministically.
- **Real-spawn tier** — ``make_spawn_agent`` with the provider/tool stack
  stubbed (same patch set as ``test_agent_server_e2e``) drives a real turn
  end-to-end through the Starlette app: create → submit → streamed events →
  ``message.complete``; and the permission control-plane: ``can_use_tool`` →
  ``approval.request`` event → ``approval.respond`` → tool runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from src.providers.base import ChatResponse
from src.server.agent_server import AgentServerConfig, make_spawn_agent
from src.server.desktop_serve import DesktopServeState, build_app
from src.server.session_manager import SessionManager

pytestmark = pytest.mark.integration

TOKEN = "gw-test-token"


# ─── helpers ─────────────────────────────────────────────────────────────────


def _connect(client: TestClient):
    return client.websocket_connect(f"/api/ws?token={TOKEN}")


def _drain_for_response(ws, request_id, collected_events, limit=200):
    """Read frames until the reply for ``request_id`` arrives."""
    for _ in range(limit):
        frame = ws.receive_json()
        if frame.get("id") == request_id:
            return frame
        if frame.get("method") == "event":
            collected_events.append(frame["params"])
    raise AssertionError(f"no response for {request_id} within {limit} frames")


def _drain_for_event(ws, type_, collected_events, limit=200):
    for _ in range(limit):
        frame = ws.receive_json()
        if frame.get("method") == "event":
            collected_events.append(frame["params"])
            if frame["params"].get("type") == type_:
                return frame["params"]
    raise AssertionError(f"no {type_} event within {limit} frames")


def _rpc(ws, rid, method, params):
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))


# ─── fake-agent tier ─────────────────────────────────────────────────────────


class FakeAgent:
    """Scripted agent handle: records inbound frames, emits queued outbound."""

    def __init__(self) -> None:
        self.inbound: list[dict] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.shutdown_called = False

    async def send_to_agent(self, frame: dict) -> None:
        self.inbound.append(frame)
        if frame.get("type") == "user":
            # One scripted streamed turn per user message.
            await self.queue.put(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "hel"},
                    },
                }
            )
            await self.queue.put(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "lo"},
                    },
                }
            )
            await self.queue.put(
                {
                    "type": "result",
                    "subtype": "success",
                    "num_turns": 1,
                    "result": "hello",
                    "is_error": False,
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                }
            )

    async def messages_from_agent(self):
        yield {
            "type": "system",
            "subtype": "init",
            "cwd": "/tmp/w",
            "permissionMode": "default",
            "model": "fake",
        }
        while True:
            yield await self.queue.get()

    async def shutdown(self) -> None:
        self.shutdown_called = True


class FakeManager:
    def __init__(self) -> None:
        self.created: list[str] = []
        self._n = 0

    def create_session(self, cwd: str):
        self._n += 1
        session_id = f"fake-{self._n}"
        self.created.append(session_id)
        return SimpleNamespace(id=session_id, cwd=cwd)

    def mark_running(self, session_id: str) -> None:
        pass


def _fake_state(tmp_path: Path) -> tuple[DesktopServeState, list[FakeAgent]]:
    agents: list[FakeAgent] = []

    async def spawn(session_id, cwd, resume):
        agent = FakeAgent()
        agents.append(agent)
        return agent

    state = DesktopServeState(
        token=TOKEN,
        workspace=str(tmp_path),
        manager=FakeManager(),
        spawn_agent=spawn,
        protocol_version="0.1.0",
    )
    return state, agents


def test_gateway_ready_is_first_event(tmp_path: Path) -> None:
    state, _ = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        frame = ws.receive_json()
        assert frame["method"] == "event"
        assert frame["params"]["type"] == "gateway.ready"


def test_create_submit_stream_complete(tmp_path: Path) -> None:
    state, agents = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()  # gateway.ready

        events: list[dict] = []
        _rpc(ws, 1, "session.create", {})
        created = _drain_for_response(ws, 1, events)
        session_id = created["result"]["session_id"]
        assert session_id == "fake-1"
        assert created["result"]["info"]["approval_mode"] == "default"

        _rpc(ws, 2, "prompt.submit", {"session_id": session_id, "text": "hi"})
        _drain_for_response(ws, 2, events)
        complete = _drain_for_event(ws, "message.complete", events)

        assert agents[0].inbound[-1] == {
            "type": "user",
            "message": {"role": "user", "content": "hi"},
        }
        types = [e["type"] for e in events] + ["message.complete"]
        assert "message.start" in types
        deltas = [e for e in events if e["type"] == "message.delta"]
        assert "".join(d["payload"]["text"] for d in deltas) == "hello"
        assert complete["payload"]["text"] == "hello"
        assert complete["payload"]["status"] == "ok"
        assert complete["payload"]["usage"] == {
            "calls": 1, "input": 3, "output": 2, "total": 5,
        }
        assert complete["session_id"] == session_id


def test_interrupt_sends_control(tmp_path: Path) -> None:
    state, agents = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {})
        session_id = _drain_for_response(ws, 1, events)["result"]["session_id"]
        _rpc(ws, 2, "session.interrupt", {"session_id": session_id})
        _drain_for_response(ws, 2, events)

        control = [f for f in agents[0].inbound if f.get("type") == "control_request"]
        assert control and control[-1]["request"]["subtype"] == "interrupt"


def test_unknown_method_errors_without_dropping_socket(tmp_path: Path) -> None:
    state, _ = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        _rpc(ws, 7, "voice.start", {})
        frame = ws.receive_json()
        assert frame["id"] == 7
        assert "method not found" in frame["error"]["message"]
        # Socket still serves after the error.
        _rpc(ws, 8, "setup.status", {})
        assert _drain_for_response(ws, 8, [])["result"] == {"provider_configured": True}


def test_approval_roundtrip_fake(tmp_path: Path) -> None:
    state, agents = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {})
        session_id = _drain_for_response(ws, 1, events)["result"]["session_id"]
        agent = agents[0]

        # Agent asks for permission.
        agent.queue.put_nowait(
            {
                "type": "control_request",
                "request_id": "ask-1",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "Bash",
                    "input": {"command": "rm -rf /tmp/x"},
                    "suggestions": [{"destination": "session", "rule": "Bash(rm:*)"}],
                    "session_label": "allow rm during this session",
                },
            }
        )
        ask = _drain_for_event(ws, "approval.request", events)
        assert ask["payload"]["command"] == "rm -rf /tmp/x"
        assert ask["session_id"] == session_id

        _rpc(ws, 2, "approval.respond", {"session_id": session_id, "choice": "once"})
        assert _drain_for_response(ws, 2, events)["result"] == {"resolved": True}

        def reply_frames():
            return [f for f in agent.inbound if f.get("type") == "control_response"]

        for _ in range(100):
            if reply_frames():
                break
        reply = reply_frames()[-1]["response"]
        assert reply["request_id"] == "ask-1"
        assert reply["response"]["behavior"] == "allow"
        assert reply["response"]["updatedInput"] == {"command": "rm -rf /tmp/x"}


# ─── real-spawn tier (provider/tool stack stubbed, agent real) ───────────────


class _TextProvider:
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

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


def _patches(provider_cls, registry):
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


def _real_state(tmp_path: Path) -> DesktopServeState:
    manager = SessionManager(workspace=str(tmp_path), index_path=tmp_path / "idx.json")
    spawn = make_spawn_agent(AgentServerConfig(single_session=False))
    return DesktopServeState(
        token=TOKEN,
        workspace=str(tmp_path),
        manager=manager,
        spawn_agent=spawn,
        protocol_version="0.1.0",
    )


def test_real_agent_turn_streams_to_gateway(tmp_path: Path) -> None:
    from src.tool_system.registry import ToolRegistry

    with contextlib.ExitStack() as stack:
        for p in _patches(_TextProvider, ToolRegistry([])):
            stack.enter_context(p)

        state = _real_state(tmp_path)
        with TestClient(build_app(state)) as client, _connect(client) as ws:
            ws.receive_json()  # gateway.ready
            events: list[dict] = []
            _rpc(ws, 1, "session.create", {"cwd": str(tmp_path)})
            created = _drain_for_response(ws, 1, events)
            session_id = created["result"]["session_id"]
            assert session_id

            _rpc(ws, 2, "prompt.submit", {"session_id": session_id, "text": "hello?"})
            _drain_for_response(ws, 2, events)
            complete = _drain_for_event(ws, "message.complete", events)

            assert complete["payload"]["status"] == "ok"
            assert "hi back" in complete["payload"]["text"]
            types = [e["type"] for e in events]
            assert "message.start" in types


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


def test_real_agent_permission_roundtrip_runs_tool(tmp_path: Path) -> None:
    """can_use_tool → approval.request event → approval.respond 'once' → the
    tool actually runs and the turn finishes with tool.start/complete events."""
    from src.permissions.types import PermissionPassthroughResult
    from src.tool_system.build_tool import build_tool
    from src.tool_system.protocol import ToolResult
    from src.tool_system.registry import ToolRegistry

    ran: list = []
    ask_tool = build_tool(
        name="DoThing",
        description="does a thing (asks first)",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        call=lambda ti, c: ran.append(dict(ti)) or ToolResult(name="DoThing", output={"ok": True}),
        check_permissions=lambda ti, c: PermissionPassthroughResult(),
    )

    with contextlib.ExitStack() as stack:
        for p in _patches(_ToolThenTextProvider, ToolRegistry([ask_tool])):
            stack.enter_context(p)

        state = _real_state(tmp_path)
        with TestClient(build_app(state)) as client, _connect(client) as ws:
            ws.receive_json()  # gateway.ready
            events: list[dict] = []
            _rpc(ws, 1, "session.create", {"cwd": str(tmp_path)})
            session_id = _drain_for_response(ws, 1, events)["result"]["session_id"]

            _rpc(ws, 2, "prompt.submit", {"session_id": session_id, "text": "go"})
            _drain_for_response(ws, 2, events)

            ask = _drain_for_event(ws, "approval.request", events)
            assert ask["payload"]["tool_name"] == "DoThing"

            _rpc(ws, 3, "approval.respond", {"session_id": session_id, "choice": "once"})
            assert _drain_for_response(ws, 3, events)["result"] == {"resolved": True}

            complete = _drain_for_event(ws, "message.complete", events)
            assert complete["payload"]["status"] == "ok"
            assert "all done" in complete["payload"]["text"]
            assert ran == [{"x": "1"}], "tool must run exactly once after approval"

            types = [e["type"] for e in events]
            assert "tool.start" in types
            assert "tool.complete" in types
