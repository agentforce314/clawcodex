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


def _last_control(agent: "FakeAgent", subtype: str) -> dict:
    """The most recent ``subtype`` control the gateway sent this agent (a
    switch is followed by the ``get_settings`` of the session.info republish,
    so the LAST inbound frame is rarely the one under test)."""
    frames = [
        f["request"] for f in agent.inbound
        if f.get("type") == "control_request" and (f.get("request") or {}).get("subtype") == subtype
    ]
    assert frames, f"no {subtype} control reached the agent"
    return frames[-1]


# ─── fake-agent tier ─────────────────────────────────────────────────────────


class FakeAgent:
    """Scripted agent handle: records inbound frames, emits queued outbound."""

    def __init__(self) -> None:
        self.inbound: list[dict] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.shutdown_called = False
        self.model = "fake"
        self.provider = "fakeprov"
        self.permission_mode = "bypassPermissions"
        self.recap = True
        self.effort: str | None = None
        # What the real agent answers on an own-user transport: the pick was
        # saved as the default for new sessions. Tests flip it to model a
        # session that may not write the host's settings.
        self.persist_preferences = True
        # Leave a user message unanswered (the turn stays open until the test
        # pushes its own ``result`` frame).
        self.hold_turns = False
        # What ``get_activity`` reports beyond the gateway's own view: a goal
        # continuation, a loop, a background shell.
        self.busy = False
        # A shutdown that takes a while (SessionEnd hooks, a worker join).
        self.shutdown_delay_s = 0.0

    async def send_to_agent(self, frame: dict) -> None:
        self.inbound.append(frame)
        if frame.get("type") == "control_request":
            request = frame.get("request") or {}
            subtype = request.get("subtype")
            reply: dict | None = None
            if subtype == "resume":
                reply = {"ok": True}
            elif subtype == "get_activity":
                reply = {"ok": True, "busy": self.busy}
            elif subtype == "set_model":
                # Record the switch so get_settings reports the new state.
                self.model = request.get("model") or self.model
                self.provider = request.get("provider") or self.provider
                reply = {
                    "ok": True, "model": self.model, "provider": self.provider,
                    "persisted": self.persist_preferences and request.get("persist") is not False,
                }
            elif subtype == "set_effort":
                level = request.get("effort")
                if level in ("auto", "unset"):
                    self.effort = None
                    reply = {
                        "ok": True, "effort": "default", "ultracode": False,
                        "persisted": self.persist_preferences and request.get("persist") is not False,
                    }
                elif level in ("low", "medium", "high", "xhigh", "max"):
                    self.effort = level
                    reply = {
                        "ok": True, "effort": level, "ultracode": False,
                        "persisted": self.persist_preferences and request.get("persist") is not False,
                    }
                else:
                    reply = {"ok": False, "error": f"invalid effort '{level}'"}
            elif subtype == "set_permission_mode":
                self.permission_mode = request.get("mode") or self.permission_mode
                reply = {"ok": True, "mode": self.permission_mode, "persisted": True}
            elif subtype == "list_model_providers":
                reply = {
                    "ok": True,
                    "model": self.model,
                    "provider": self.provider,
                    "providers": [{
                        "authenticated": True,
                        "auth_type": "api_key",
                        "is_current": True,
                        "models": [self.model],
                        "name": "Fake",
                        "slug": self.provider,
                    }],
                }
            elif subtype == "set_recap":
                value = request.get("value")
                if value in ("on", "off"):
                    self.recap = value == "on"
                    reply = {"ok": True, "value": value}
                else:
                    reply = {"ok": False, "error": "usage: /recap [on|off|status]"}
            elif subtype == "get_settings":
                reply = {
                    "model": self.model,
                    "provider": self.provider,
                    "permission_mode": self.permission_mode,
                    "recap": self.recap,
                    "reasoning_effort": self.effort,
                }
            if reply is not None:
                await self.queue.put(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": frame.get("request_id"),
                            "response": reply,
                        },
                    }
                )
            return
        if frame.get("type") == "user":
            if self.hold_turns:
                return
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
            "permissionMode": "bypassPermissions",
            "model": "fake",
        }
        while True:
            yield await self.queue.get()

    async def shutdown(self) -> None:
        if self.shutdown_delay_s:
            await asyncio.sleep(self.shutdown_delay_s)
        self.shutdown_called = True


class FakeManager:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.cwds: list[str] = []
        self._n = 0

    def create_session(self, cwd: str):
        self._n += 1
        session_id = f"fake-{self._n}"
        self.created.append(session_id)
        self.cwds.append(cwd)
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
        # Full Access maps to the desktop vocabulary: manual|smart|off.
        assert created["result"]["info"]["approval_mode"] == "off"

        _rpc(ws, 2, "prompt.submit", {"session_id": session_id, "text": "hi"})
        _drain_for_response(ws, 2, events)
        complete = _drain_for_event(ws, "message.complete", events)

        # The prompt reached the agent. (Not necessarily the LAST frame: turn
        # end schedules a get_settings refresh for the session.info republish.)
        assert {
            "type": "user",
            "message": {"role": "user", "content": "hi"},
        } in agents[0].inbound
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


def test_serve_defaults_to_full_access_like_the_cli() -> None:
    """The desktop is an interactive surface, so serve resolves permissions
    through the SAME resolver as `clawcodex` / the TUI launcher — Full Access
    by default. Running in "default" mode made every Write/Bash raise an
    approval the user never answered, and the tools timed out."""
    from src.entrypoints.serve_cli import _build_parser
    from src.permissions.modes import resolve_interactive_permission_state

    # No --permission-mode → None, so the resolver's floor applies (pinning it
    # to "default" here would defeat the implicit Full Access floor).
    assert _build_parser().parse_args([]).permission_mode is None

    mode, _available, selectable = resolve_interactive_permission_state(
        permission_mode_cli=None,
        dangerously_skip_permissions=False,
        allow_dangerously_skip_permissions=False,
        cwd=None,
    )
    assert mode == "bypassPermissions"
    assert selectable is True


def test_approval_mode_vocabulary_mapping() -> None:
    """The renderer only knows manual|smart|off and coerces anything else to
    "manual" — so a Full Access session rendered as "ask every time"."""
    from src.server.desktop_gateway_methods import approval_mode_for, permission_mode_for

    assert approval_mode_for("bypassPermissions") == "off"
    assert approval_mode_for("auto") == "smart"
    assert approval_mode_for("default") == "manual"
    assert approval_mode_for("acceptEdits") == "manual"
    assert approval_mode_for("plan") == "manual"
    assert approval_mode_for(None) is None

    assert permission_mode_for("off") == "bypassPermissions"
    assert permission_mode_for("smart") == "auto"
    assert permission_mode_for("manual") == "default"
    assert permission_mode_for("nonsense") is None


def test_approvals_mode_get_and_set(tmp_path: Path) -> None:
    """The Safety panel round-trips a single `approvals.mode` key; without a
    handler config.get returned the settings blob (no `value`) and the panel
    always showed "manual"."""
    state, agents = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {})
        sid = _drain_for_response(ws, 1, events)["result"]["session_id"]

        # Reads the live mode in the desktop's vocabulary.
        _rpc(ws, 2, "config.get", {"session_id": sid, "key": "approvals.mode"})
        assert _drain_for_response(ws, 2, events)["result"] == {"value": "off"}

        # Writing maps back to a permission mode and persists it.
        _rpc(ws, 3, "config.set", {"session_id": sid, "key": "approvals.mode",
                                   "value": "manual"})
        result = _drain_for_response(ws, 3, events)["result"]
        assert result["ok"] is True and result["value"] == "manual"
        assert agents[0].permission_mode == "default"

        _rpc(ws, 4, "config.get", {"session_id": sid, "key": "approvals.mode"})
        assert _drain_for_response(ws, 4, events)["result"] == {"value": "manual"}

        # An unknown mode is refused rather than silently mapped.
        _rpc(ws, 5, "config.set", {"session_id": sid, "key": "approvals.mode",
                                   "value": "bogus"})
        assert _drain_for_response(ws, 5, events)["result"]["ok"] is False


def test_a_starting_session_does_not_break_the_catalog_calls(tmp_path: Path) -> None:
    """A session is registered BEFORE its agent is spawned, and spawning takes
    seconds. Any call that reaches for "some session" in that window used to
    crash on ``'NoneType' object has no attribute 'send_to_agent'`` — the
    browser swallowed the error and the model picker sat empty ("No configured
    providers yet") with a nameless model chip.

    Dispatch is serial per socket, so this is reached from a SECOND window
    while the first one starts a session — reproduced here by registering the
    half-built session directly, which is exactly the state the traceback
    proved exists.
    """
    from src.server.desktop_gateway_methods import DesktopSession

    state, _ = _fake_state(tmp_path)
    starting = DesktopSession("starting-1", state)
    assert starting.agent is None and starting.ready is False
    state.sessions["starting-1"] = starting

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []

        # Each answers from config rather than failing the whole call.
        _rpc(ws, 1, "model.options", {})
        assert "error" not in _drain_for_response(ws, 1, events)

        _rpc(ws, 2, "provider.list", {})
        assert "error" not in _drain_for_response(ws, 2, events)

        _rpc(ws, 3, "commands.catalog", {})
        assert "error" not in _drain_for_response(ws, 3, events)

        # Naming it explicitly is answered the same way — with the no-session
        # fallback, never with another session's settings.
        _rpc(ws, 4, "settings.general", {"session_id": "starting-1"})
        assert _drain_for_response(ws, 4, events)["result"]["output_style"] == ""

        # But a call that can only mean THAT session says so by name instead of
        # dying somewhere deeper.
        _rpc(ws, 5, "prompt.submit", {"session_id": "starting-1", "text": "hi"})
        assert "still starting" in _drain_for_response(ws, 5, events)["error"]["message"]

        # A ready session is still preferred over the one that is starting.
        _rpc(ws, 6, "session.create", {})
        sid = _drain_for_response(ws, 6, events)["result"]["session_id"]
        _rpc(ws, 7, "settings.general", {})
        assert _drain_for_response(ws, 7, events)["result"]["available_output_styles"] == []
        assert state.sessions[sid].ready is True


def test_a_cancelled_create_leaves_no_half_built_session(tmp_path: Path) -> None:
    """A browser navigating away mid-spawn cancels the create. CancelledError
    is not an Exception, so the cleanup used to be skipped and the agent-less
    session stayed in the registry for the life of the process — poisoning
    every later sessionless call from every window."""
    from src.server.desktop_gateway_methods import GatewayConnection

    state, _ = _fake_state(tmp_path)

    async def cancelled_spawn(session_id, cwd, resume):
        raise asyncio.CancelledError

    state.spawn_agent = cancelled_spawn
    conn = GatewayConnection(object(), state)  # type: ignore[arg-type]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(conn._create(str(tmp_path), None, {}))

    assert state.sessions == {}


def test_default_provider_set_and_listed(tmp_path: Path, monkeypatch) -> None:
    """Settings → Providers writes `default_provider` to config.json and
    `provider.list` reports it — without needing any session, because changing
    the default is exactly what someone does when the current default cannot
    even start one. New sessions follow it: _build_runtime resolves
    `cfg.provider_name or get_default_provider()` at spawn time."""
    import src.config as config_mod

    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    monkeypatch.setattr("src.config.get_global_config_path", lambda: config_path)
    config_mod._get_default_manager().invalidate()
    try:
        state, _ = _fake_state(tmp_path)
        with TestClient(build_app(state)) as client, _connect(client) as ws:
            ws.receive_json()
            events: list[dict] = []

            _rpc(ws, 1, "provider.set_default", {"slug": "deepseek"})
            result = _drain_for_response(ws, 1, events)["result"]
            assert result["ok"] is True and result["default"] == "deepseek"
            saved = json.loads(config_path.read_text())
            assert saved["default_provider"] == "deepseek"

            # The reader of the same key: the settings page's list reply.
            _rpc(ws, 2, "provider.list", {})
            assert _drain_for_response(ws, 2, events)["result"]["default"] == "deepseek"

            # An alias lands as its canonical id, matching the catalog's slugs.
            _rpc(ws, 3, "provider.set_default", {"slug": "kimi"})
            assert _drain_for_response(ws, 3, events)["result"]["default"] == "moonshot"

            # An unknown provider is refused — every later session would fail
            # to spawn on it.
            _rpc(ws, 4, "provider.set_default", {"slug": "bogus"})
            result = _drain_for_response(ws, 4, events)["result"]
            assert result["ok"] is False and "bogus" in result["error"]
            assert json.loads(config_path.read_text())["default_provider"] == "moonshot"
    finally:
        config_mod._get_default_manager().invalidate()


def test_model_options_without_a_session_describes_the_next_one(
    tmp_path: Path, monkeypatch
) -> None:
    """A window with no session of its own asks what the NEXT session will run
    on — never what some other window is running.

    Reported as a composer showing "New sessions start on deepseek." beside an
    `openai:gpt-5.6-luna` chip: the sessionless call borrowed a session that
    was still live on the old provider, so the notice and the chip described
    different sessions.
    """
    import src.config as config_mod

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "default_provider": "deepseek",
        "providers": {"deepseek": {"api_key": "k", "default_model": "deepseek-v4-pro"}},
    }))
    monkeypatch.setattr("src.config.get_global_config_path", lambda: config_path)
    config_mod._get_default_manager().invalidate()
    try:
        state, agents = _fake_state(tmp_path)
        with TestClient(build_app(state)) as client, _connect(client) as ws:
            ws.receive_json()
            events: list[dict] = []

            _rpc(ws, 1, "session.create", {})
            sid = _drain_for_response(ws, 1, events)["result"]["session_id"]
            assert agents[0].provider == "fakeprov"

            # No session named: the config default, not the live session's.
            _rpc(ws, 2, "model.options", {})
            result = _drain_for_response(ws, 2, events)["result"]
            assert result["provider"] == "deepseek"
            assert result["model"] == "deepseek-v4-pro"

            # Naming one still reports what THAT session is really running.
            _rpc(ws, 3, "model.options", {"session_id": sid})
            result = _drain_for_response(ws, 3, events)["result"]
            assert result["provider"] == "fakeprov"
    finally:
        config_mod._get_default_manager().invalidate()


def test_recap_get_and_set(tmp_path: Path) -> None:
    """The settings page round-trips the recap toggle: `settings.general`
    reports the flag only when the agent did (so "off" and "unknown" stay
    distinguishable), and `settings.set_recap` flips it."""
    state, agents = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {})
        sid = _drain_for_response(ws, 1, events)["result"]["session_id"]

        _rpc(ws, 2, "settings.general", {"session_id": sid})
        assert _drain_for_response(ws, 2, events)["result"]["recap"] is True

        _rpc(ws, 3, "settings.set_recap", {"session_id": sid, "value": "off"})
        result = _drain_for_response(ws, 3, events)["result"]
        assert result["ok"] is True and result["value"] == "off"
        assert agents[0].recap is False

        _rpc(ws, 4, "settings.general", {"session_id": sid})
        assert _drain_for_response(ws, 4, events)["result"]["recap"] is False

        # The agent's usage refusal passes through rather than becoming a
        # generic failure.
        _rpc(ws, 5, "settings.set_recap", {"session_id": sid, "value": "bogus"})
        result = _drain_for_response(ws, 5, events)["result"]
        assert result["ok"] is False and "recap" in result["error"]


def test_init_session_info_carries_provider() -> None:
    """The picker only prefers the session's selection when BOTH model and
    provider are set; without provider it fell back to the catalog while the
    composer chip kept the session's model — the two disagreed."""
    from src.server.desktop_gateway_methods import _init_session_info

    info = _init_session_info({
        "cwd": "/w", "model": "m1", "provider": "p1",
        "permissionMode": "bypassPermissions", "session_id": "s1",
    })
    assert info["model"] == "m1"
    assert info["provider"] == "p1"


def test_model_switch_publishes_session_info(tmp_path: Path) -> None:
    """The composer chip reads the SESSION's model (not the draft), so a switch
    that doesn't republish session.info leaves it showing the spawn-time model
    forever — the reported bug."""
    state, agents = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {"cwd": "/tmp"})
        sid = _drain_for_response(ws, 1, events)["result"]["session_id"]

        events.clear()
        _rpc(ws, 2, "config.set", {
            "session_id": sid, "key": "model",
            "value": "new-model --provider newprov --session",
        })
        result = _drain_for_response(ws, 2, events)["result"]
        assert result["ok"] is True
        # ``--session`` is the caller's "this session only": forwarded to the
        # agent as persist=False, which it echoes back as not saved.
        assert result["persisted"] is False
        assert _last_control(agents[0], "set_model") == {
            "subtype": "set_model", "model": "new-model", "provider": "newprov",
            "persist": False,
        }

        # A session.info carrying the NEW model+provider must have been pushed.
        infos = [e for e in events if e["type"] == "session.info"]
        assert infos, "no session.info published after the model switch"
        latest = infos[-1]["payload"]
        assert latest["model"] == "new-model"
        assert latest["provider"] == "newprov"


def test_turn_end_republishes_session_info_without_deadlock(tmp_path: Path) -> None:
    """Turn end refreshes the info line. The refresh issues a control query
    whose response is routed by the pump, so it must be SCHEDULED — awaiting it
    inside the pump would deadlock until the control timeout."""
    state, agents = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {"cwd": "/tmp"})
        sid = _drain_for_response(ws, 1, events)["result"]["session_id"]

        events.clear()
        _rpc(ws, 2, "prompt.submit", {"session_id": sid, "text": "hi"})
        _drain_for_response(ws, 2, events)
        # message.complete proves the pump kept draining (no deadlock)…
        _drain_for_event(ws, "message.complete", events)
        # …and the scheduled refresh lands with model/provider stamped.
        info = _drain_for_event(ws, "session.info", events)
        assert info["payload"]["model"] == "fake"
        assert info["payload"]["provider"] == "fakeprov"


def test_session_create_honors_provider_override(tmp_path: Path) -> None:
    """A composer selection (provider/model) must reach the spawn, so a session
    can use a working provider even when the config default is broken."""
    import dataclasses

    from src.server.agent_server import AgentServerConfig

    spawned_with: list = []

    def make_spawn(config):
        async def spawn(session_id, cwd, resume):
            spawned_with.append((config.provider_name, config.model, config.effort))
            agent = FakeAgent()
            return agent
        return spawn

    base = AgentServerConfig(provider_name="anthropic", model="claude-sonnet-4-6")
    state, _ = _fake_state(tmp_path)
    state.agent_config = base
    state.spawn_agent = make_spawn(base)
    # Real make_spawn_agent is heavy; substitute ours for the override path.
    import src.server.desktop_serve as serve_mod
    import src.server.agent_server as agent_mod
    orig = agent_mod.make_spawn_agent
    agent_mod.make_spawn_agent = make_spawn
    try:
        with TestClient(build_app(state)) as client, _connect(client) as ws:
            ws.receive_json()
            events: list[dict] = []
            _rpc(ws, 1, "session.create",
                 {"cwd": "/tmp", "source": "desktop", "provider": "deepseek",
                  "model": "deepseek-v4-flash", "reasoning_effort": "high"})
            _drain_for_response(ws, 1, events)
    finally:
        agent_mod.make_spawn_agent = orig

    assert spawned_with[-1] == ("deepseek", "deepseek-v4-flash", "high")


def test_session_create_without_override_uses_base_spawn(tmp_path: Path) -> None:
    state, agents = _fake_state(tmp_path)
    # No agent_config → spawn_for returns the shared spawn_agent unchanged.
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {"cwd": "/tmp", "source": "desktop"})
        _drain_for_response(ws, 1, events)
    assert len(agents) == 1  # the base spawn was used


def test_resume_hydrates_saved_transcript(tmp_path: Path) -> None:
    state, agents = _fake_state(tmp_path)
    sessions_dir = tmp_path / "saved"
    sessions_dir.mkdir()
    state.sessions_dir = sessions_dir
    (sessions_dir / "old-chat.json").write_text(
        json.dumps(
            {
                "session_id": "old-chat",
                "preview": "hello?",
                "message_count": 2,
                "conversation": {
                    "messages": [
                        {"role": "user", "content": "hello?"},
                        {"role": "assistant",
                         "content": [{"type": "text", "text": "hi back"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.resume", {"session_id": "old-chat"})
        result = _drain_for_response(ws, 1, events)["result"]

        assert result["resumed"] == "old-chat"
        assert result["stored_session_id"] == "old-chat"
        assert result["session_id"] == "fake-1"  # fresh runtime session
        assert result["message_count"] == 2
        assert [m["role"] for m in result["messages"]] == ["user", "assistant"]
        # The agent got the resume control with the stored id.
        resumes = [
            f for f in agents[0].inbound
            if f.get("type") == "control_request"
            and (f.get("request") or {}).get("subtype") == "resume"
        ]
        assert resumes and resumes[0]["request"]["session_id"] == "old-chat"


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


def test_subagent_transcript_reads_the_run_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    (transcripts / "a7.jsonl").write_text(
        json.dumps({"role": "assistant", "content": [{"type": "text", "text": "Done."}]}) + "\n",
        encoding="utf-8",
    )
    state, _ = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()  # gateway.ready
        events: list[dict] = []
        _rpc(ws, 1, "subagent.transcript", {"agent_id": "a7"})
        reply = _drain_for_response(ws, 1, events)["result"]
        assert reply["found"] is True
        assert reply["agent_id"] == "a7"
        assert reply["message_count"] == 1
        assert reply["messages"][0]["role"] == "assistant"

        # A miss and a path-shaped id both answer "not found", never an error.
        for rid, agent_id in ((2, "nope"), (3, "../a7")):
            _rpc(ws, rid, "subagent.transcript", {"agent_id": agent_id})
            reply = _drain_for_response(ws, rid, events)["result"]
            assert reply == {"agent_id": agent_id, "found": False, "messages": [], "message_count": 0}


def test_model_switch_is_saved_as_the_default_unless_scoped(tmp_path: Path) -> None:
    """A picker selection (no scope flag) is the user's default for new
    sessions: the gateway sends no ``persist`` (the agent's default is to
    save) and echoes the agent's ``persisted`` verdict, which the web and
    desktop clients word their confirmation on."""
    state, agents = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {"cwd": "/tmp"})
        sid = _drain_for_response(ws, 1, events)["result"]["session_id"]

        _rpc(ws, 2, "config.set", {
            "session_id": sid, "key": "model", "value": "new-model --provider fakeprov",
        })
        result = _drain_for_response(ws, 2, events)["result"]
        assert result["ok"] is True
        assert result["value"] == "new-model"
        assert result["persisted"] is True
        assert "persist" not in _last_control(agents[0], "set_model")

        # The legacy ``--global`` spelling means the same as no flag.
        _rpc(ws, 3, "config.set", {
            "session_id": sid, "key": "model", "value": "other-model --global",
        })
        result = _drain_for_response(ws, 3, events)["result"]
        assert result["value"] == "other-model" and result["persisted"] is True

        # A transport that may not write the host's settings says so, and the
        # gateway passes that through rather than inventing a verdict.
        agents[0].persist_preferences = False
        _rpc(ws, 4, "config.set", {
            "session_id": sid, "key": "model", "value": "third-model",
        })
        assert _drain_for_response(ws, 4, events)["result"]["persisted"] is False


def test_effort_change_round_trips_and_reports_persisted(tmp_path: Path) -> None:
    """``config.set{effort}`` used to be fire-and-forget (``{ok: true}`` before
    the agent had even looked at the value). The chips need the level the
    agent actually took and whether it became the default for new sessions,
    and the session.info republish must carry the new level."""
    state, agents = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {"cwd": "/tmp"})
        sid = _drain_for_response(ws, 1, events)["result"]["session_id"]

        events.clear()
        _rpc(ws, 2, "config.set", {"session_id": sid, "key": "effort", "value": "high"})
        result = _drain_for_response(ws, 2, events)["result"]
        assert result == {"ok": True, "value": "high", "persisted": True, "ultracode": False}
        infos = [e for e in events if e["type"] == "session.info"]
        assert infos and infos[-1]["payload"]["reasoning_effort"] == "high"

        # ``auto`` clears the level; the agent spells that "default", the
        # chips spell it "auto", and the gateway translates.
        _rpc(ws, 3, "config.set", {"session_id": sid, "key": "reasoning", "value": "auto"})
        result = _drain_for_response(ws, 3, events)["result"]
        assert result["ok"] is True and result["value"] == "auto"
        assert result["persisted"] is True

        # An explicit persist=False is the session-only scope.
        _rpc(ws, 4, "config.set", {
            "session_id": sid, "key": "effort", "value": "low", "persist": False,
        })
        result = _drain_for_response(ws, 4, events)["result"]
        assert result["value"] == "low" and result["persisted"] is False
        assert _last_control(agents[0], "set_effort") == {
            "subtype": "set_effort", "effort": "low", "persist": False,
        }

        # A rejected level is an error, not a silent ok.
        _rpc(ws, 5, "config.set", {"session_id": sid, "key": "effort", "value": "bogus"})
        result = _drain_for_response(ws, 5, events)["result"]
        assert result["ok"] is False and "invalid effort" in result["error"]


# ─── opening a saved session: cold history, runtime reuse ────────────────────


def _write_saved(sessions_dir: Path, session_id: str, messages: list, **extra) -> None:
    sessions_dir.mkdir(exist_ok=True)
    payload = {
        "session_id": session_id,
        "preview": "hello?",
        "message_count": len(messages),
        "cwd": "/tmp/where",
        "model": "m-stored",
        "provider": "p-stored",
        "conversation": {"messages": messages},
        **extra,
    }
    (sessions_dir / f"{session_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_session_history_reads_the_saved_transcript_without_a_runtime(tmp_path: Path) -> None:
    """The sidebar click renders from this — no spawn, no control round-trip."""
    state, agents = _fake_state(tmp_path)
    state.sessions_dir = tmp_path / "saved"
    _write_saved(
        state.sessions_dir, "old-chat",
        [{"role": "user", "content": "hello?"},
         {"role": "assistant", "content": [{"type": "text", "text": "hi back"}]}],
        name="My chat",
    )

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.history", {"session_id": "old-chat"})
        result = _drain_for_response(ws, 1, events)["result"]

    assert result["found"] is True
    assert result["stored_session_id"] == "old-chat"
    assert result["title"] == "My chat"
    assert [m["role"] for m in result["messages"]] == ["user", "assistant"]
    assert result["info"] == {"cwd": "/tmp/where", "model": "m-stored", "provider": "p-stored"}
    assert agents == [] and state.manager.created == []


def test_session_history_of_an_unknown_row_is_an_error(tmp_path: Path) -> None:
    state, _agents = _fake_state(tmp_path)
    state.sessions_dir = tmp_path / "saved"
    state.sessions_dir.mkdir()

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        _rpc(ws, 1, "session.history", {"session_id": "nope"})
        reply = _drain_for_response(ws, 1, [])

    assert "unknown session" in reply["error"]["message"]


def test_resuming_the_same_row_twice_reuses_its_runtime(tmp_path: Path) -> None:
    """Every click used to spawn a fresh runtime and leave the last one alive.

    ``state.sessions`` is keyed by runtime id, and a resumed row's runtime has
    a different id from the row, so the "already live" check never matched a
    row. The second resume must come back with the same runtime, spawn-free.
    """
    state, agents = _fake_state(tmp_path)
    state.sessions_dir = tmp_path / "saved"
    _write_saved(state.sessions_dir, "old-chat", [{"role": "user", "content": "hello?"}])

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.resume", {"session_id": "old-chat"})
        first = _drain_for_response(ws, 1, events)["result"]
        _rpc(ws, 2, "session.resume", {"session_id": "old-chat", "omit_messages": True})
        second = _drain_for_response(ws, 2, events)["result"]

    assert first["session_id"] == "fake-1"
    assert second["session_id"] == "fake-1"
    assert second["stored_session_id"] == "old-chat"
    assert second.get("messages_omitted") is True
    assert len(agents) == 1 and state.manager.created == ["fake-1"]
    assert state.sessions["fake-1"].stored_id == "old-chat"


def test_a_live_replay_answers_history_from_its_own_record(tmp_path: Path) -> None:
    """Turns run after a resume are saved under the RUNTIME's id; the row the
    user clicks still names the original file. Opening the row again must
    show the conversation as it is now, not as the row's file left it."""
    state, _agents = _fake_state(tmp_path)
    state.sessions_dir = tmp_path / "saved"
    _write_saved(state.sessions_dir, "old-chat", [{"role": "user", "content": "first"}])

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.resume", {"session_id": "old-chat", "omit_messages": True})
        runtime = _drain_for_response(ws, 1, events)["result"]["session_id"]
        # The runtime saved its own, longer record after a turn.
        _write_saved(
            state.sessions_dir, runtime,
            [{"role": "user", "content": "first"}, {"role": "user", "content": "second"}],
        )
        _rpc(ws, 2, "session.history", {"session_id": "old-chat"})
        history = _drain_for_response(ws, 2, events)["result"]
        _rpc(ws, 3, "session.resume", {"session_id": "old-chat"})
        resumed = _drain_for_response(ws, 3, events)["result"]

    assert history["live_session_id"] == runtime
    assert [m["content"] for m in history["messages"]] == ["first", "second"]
    assert [m["content"] for m in resumed["messages"]] == ["first", "second"]


def test_two_concurrent_resumes_of_one_row_share_a_spawn(tmp_path: Path) -> None:
    state, agents = _fake_state(tmp_path)
    state.sessions_dir = tmp_path / "saved"
    _write_saved(state.sessions_dir, "old-chat", [{"role": "user", "content": "hello?"}])
    gate = asyncio.Event()
    base_spawn = state.spawn_agent

    async def slow_spawn(session_id, cwd, resume):
        await gate.wait()
        return await base_spawn(session_id, cwd, resume)

    state.spawn_agent = slow_spawn

    async def run() -> tuple[dict, dict]:
        from src.server.desktop_gateway_methods import GatewayConnection

        class _Socket:
            async def send_json(self, obj):  # pragma: no cover - no pushes read
                pass

        conn = GatewayConnection(websocket=_Socket(), state=state)  # type: ignore[arg-type]
        first = asyncio.create_task(conn.session_resume({"session_id": "old-chat"}))
        await asyncio.sleep(0)
        second = asyncio.create_task(conn.session_resume({"session_id": "old-chat"}))
        await asyncio.sleep(0)
        gate.set()
        return await first, await second

    a, b = asyncio.run(run())
    assert a["session_id"] == b["session_id"] == "fake-1"
    assert len(agents) == 1


# ─── session.create: a new folder, a worktree ────────────────────────────────


def test_session_create_can_make_the_workspace_folder(tmp_path: Path) -> None:
    state, _agents = _fake_state(tmp_path)
    target = tmp_path / "fresh" / "project"

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        # A plain cwd passes through untouched, as it always has (the desktop
        # client sends paths the gateway is not the judge of): nothing made.
        _rpc(ws, 1, "session.create", {"cwd": str(target)})
        plain = _drain_for_response(ws, 1, [])["result"]
        assert not target.exists()
        _rpc(ws, 2, "session.create", {"cwd": str(target), "create_dir": True})
        created = _drain_for_response(ws, 2, [])["result"]
        _rpc(ws, 3, "session.create", {"cwd": "relative/path", "create_dir": True})
        relative = _drain_for_response(ws, 3, [])

    assert plain["session_id"] == "fake-1"
    assert target.is_dir()
    assert created["session_id"] == "fake-2"
    assert state.manager.cwds == [str(target), str(target)]
    assert "must be absolute" in relative["error"]["message"]


def test_session_create_can_isolate_the_session_in_a_worktree(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"],
           "HOME": str(tmp_path)}
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "root"]):
        subprocess.run(["git", *args], cwd=repo, check=True, env=env)
    state, _agents = _fake_state(tmp_path)

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        _rpc(ws, 1, "session.create", {"cwd": str(repo), "worktree": True})
        created = _drain_for_response(ws, 1, [])["result"]
        _rpc(ws, 2, "session.create", {"cwd": str(tmp_path), "worktree": True})
        refused = _drain_for_response(ws, 2, [])

    worktree = created["worktree"]
    assert Path(worktree["path"]).is_dir()
    assert Path(worktree["path"]).parent == repo / ".clawcodex" / "worktrees"
    assert worktree["repo_root"] == str(repo.resolve())
    assert state.manager.cwds == [worktree["path"]]
    assert "git repository" in refused["error"]["message"]


def test_session_close_if_idle_refuses_a_busy_runtime(tmp_path: Path) -> None:
    state, _agents = _fake_state(tmp_path)

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {})
        sid = _drain_for_response(ws, 1, events)["result"]["session_id"]
        _agents[0].hold_turns = True
        _rpc(ws, 2, "prompt.submit", {"session_id": sid, "text": "hi"})
        _drain_for_response(ws, 2, events)
        # Mid-turn: the conditional close is refused, the runtime stays.
        _rpc(ws, 3, "session.close", {"session_id": sid, "if_idle": True})
        refused = _drain_for_response(ws, 3, events)["result"]
        assert refused == {"ok": True, "closed": False, "reason": "busy"}
        assert sid in state.sessions
        # The turn ends: now it is idle and goes.
        _agents[0].queue.put_nowait({"type": "result", "subtype": "success",
                                     "result": "done", "permission_mode": "default"})
        _drain_for_event(ws, "message.complete", events)
        _rpc(ws, 4, "session.close", {"session_id": sid, "if_idle": True})
        closed = _drain_for_response(ws, 4, events)["result"]
        assert closed == {"ok": True, "closed": True}
        assert sid not in state.sessions


def test_session_close_if_idle_trusts_the_agent_about_its_own_work(tmp_path: Path) -> None:
    """A /goal continuation or a /loop is invisible to the gateway: the agent
    says busy through ``get_activity``, and the conditional close is refused."""
    state, agents = _fake_state(tmp_path)

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {})
        sid = _drain_for_response(ws, 1, events)["result"]["session_id"]
        agents[0].busy = True
        _rpc(ws, 2, "session.close", {"session_id": sid, "if_idle": True})
        assert _drain_for_response(ws, 2, events)["result"] == {"ok": True, "closed": False, "reason": "busy"}
        agents[0].busy = False
        _rpc(ws, 3, "session.close", {"session_id": sid, "if_idle": True})
        assert _drain_for_response(ws, 3, events)["result"] == {"ok": True, "closed": True}


def test_session_close_if_idle_refuses_while_an_approval_is_pending(tmp_path: Path) -> None:
    state, agents = _fake_state(tmp_path)

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {})
        sid = _drain_for_response(ws, 1, events)["result"]["session_id"]
        agents[0].queue.put_nowait({
            "type": "control_request", "request_id": "ask-1",
            "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls"}},
        })
        _drain_for_event(ws, "approval.request", events)
        _rpc(ws, 2, "session.close", {"session_id": sid, "if_idle": True})
        assert _drain_for_response(ws, 2, events)["result"]["closed"] is False
        _rpc(ws, 3, "approval.respond", {"session_id": sid, "choice": "once"})
        _drain_for_response(ws, 3, events)
        _rpc(ws, 4, "session.close", {"session_id": sid, "if_idle": True})
        assert _drain_for_response(ws, 4, events)["result"]["closed"] is True


def test_session_close_answers_before_a_slow_teardown_and_tells_every_window(tmp_path: Path) -> None:
    """The reply — and the next call on the socket, the open of the session
    being moved to — must not wait on SessionEnd hooks and the worker join."""
    import time

    state, agents = _fake_state(tmp_path)
    state.sessions_dir = tmp_path / "saved"
    _write_saved(state.sessions_dir, "next-row", [{"role": "user", "content": "hi"}])

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {})
        sid = _drain_for_response(ws, 1, events)["result"]["session_id"]
        agents[0].shutdown_delay_s = 2.0
        started = time.monotonic()
        _rpc(ws, 2, "session.close", {"session_id": sid})
        _rpc(ws, 3, "session.history", {"session_id": "next-row"})
        closed = _drain_for_response(ws, 2, events)["result"]
        history = _drain_for_response(ws, 3, events)["result"]
        elapsed = time.monotonic() - started

    assert closed == {"ok": True, "closed": True}
    assert history["message_count"] == 1
    assert elapsed < 1.5, f"the close's teardown held the socket for {elapsed:.1f}s"
    assert any(e.get("type") == "session.closed" and e.get("session_id") == sid for e in events)
    assert sid not in state.sessions


def test_a_second_window_resuming_the_same_row_gets_the_turn_events(tmp_path: Path) -> None:
    """The reuse path must subscribe the resuming socket: before, a second
    window handed the first window's runtime saw none of its own turn."""
    state, agents = _fake_state(tmp_path)
    state.sessions_dir = tmp_path / "saved"
    _write_saved(state.sessions_dir, "shared-row", [{"role": "user", "content": "hi"}])

    with TestClient(build_app(state)) as client, _connect(client) as first, _connect(client) as second:
        first.receive_json()
        second.receive_json()
        events_a: list[dict] = []
        events_b: list[dict] = []
        _rpc(first, 1, "session.resume", {"session_id": "shared-row", "omit_messages": True})
        runtime = _drain_for_response(first, 1, events_a)["result"]["session_id"]
        _rpc(second, 1, "session.resume", {"session_id": "shared-row", "omit_messages": True})
        assert _drain_for_response(second, 1, events_b)["result"]["session_id"] == runtime
        assert len(agents) == 1
        _rpc(second, 2, "prompt.submit", {"session_id": runtime, "text": "from b"})
        _drain_for_response(second, 2, events_b)
        complete = _drain_for_event(second, "message.complete", events_b)
        assert complete["session_id"] == runtime


def test_resume_reports_a_runtime_mid_turn_as_running(tmp_path: Path) -> None:
    state, agents = _fake_state(tmp_path)
    state.sessions_dir = tmp_path / "saved"
    _write_saved(state.sessions_dir, "busy-row", [{"role": "user", "content": "hi"}])

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.resume", {"session_id": "busy-row", "omit_messages": True})
        runtime = _drain_for_response(ws, 1, events)["result"]["session_id"]
        agents[0].hold_turns = True
        _rpc(ws, 2, "prompt.submit", {"session_id": runtime, "text": "go"})
        _drain_for_response(ws, 2, events)
        _rpc(ws, 3, "session.resume", {"session_id": "busy-row", "omit_messages": True})
        again = _drain_for_response(ws, 3, events)["result"]
        _rpc(ws, 4, "session.history", {"session_id": "busy-row"})
        history = _drain_for_response(ws, 4, events)["result"]

    assert again["session_id"] == runtime
    assert again["info"]["running"] is True
    assert history["info"]["running"] is True


def test_session_history_of_a_live_runtime_that_never_saved_is_empty_not_an_error(tmp_path: Path) -> None:
    state, _agents = _fake_state(tmp_path)
    state.sessions_dir = tmp_path / "saved"
    state.sessions_dir.mkdir()

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {})
        sid = _drain_for_response(ws, 1, events)["result"]["session_id"]
        _rpc(ws, 2, "session.history", {"session_id": sid})
        history = _drain_for_response(ws, 2, events)["result"]

    assert history["found"] is False
    assert history["messages"] == [] and history["live_session_id"] == sid
    assert history["info"]["running"] is False


def test_a_refused_replay_is_not_reused(tmp_path: Path) -> None:
    """A runtime whose ``resume`` control the agent refused holds no
    conversation; the next click must spawn again rather than adopt it."""
    state, agents = _fake_state(tmp_path)
    state.sessions_dir = tmp_path / "saved"
    _write_saved(state.sessions_dir, "row", [{"role": "user", "content": "hi"}])

    class Refusing(FakeAgent):
        async def send_to_agent(self, frame: dict) -> None:
            request = frame.get("request") or {}
            if frame.get("type") == "control_request" and request.get("subtype") == "resume":
                self.inbound.append(frame)
                await self.queue.put({
                    "type": "control_response",
                    "response": {"request_id": frame["request_id"], "response": {"ok": False, "error": "nope"}},
                })
                return
            await super().send_to_agent(frame)

    async def spawn(session_id, cwd, resume):
        agent = Refusing()
        agents.append(agent)
        return agent

    state.spawn_agent = spawn

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.resume", {"session_id": "row", "omit_messages": True})
        first = _drain_for_response(ws, 1, events)["result"]["session_id"]
        _rpc(ws, 2, "session.resume", {"session_id": "row", "omit_messages": True})
        second = _drain_for_response(ws, 2, events)["result"]["session_id"]

    assert first != second and len(agents) == 2


def test_prepare_workspace_expands_home_and_refuses_a_file(tmp_path: Path, monkeypatch) -> None:
    from src.server.desktop_gateway_methods import _prepare_workspace

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "proj").mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")

    assert _prepare_workspace("~/proj", False) == str(tmp_path / "proj")
    assert _prepare_workspace("~/fresh/deep", True) == str(tmp_path / "fresh" / "deep")
    assert (tmp_path / "fresh" / "deep").is_dir()
    with pytest.raises(ValueError, match="not a directory"):
        _prepare_workspace(str(tmp_path / "notes.txt"), True)


def test_worktree_on_a_new_folder_is_refused_before_anything_is_created(tmp_path: Path) -> None:
    state, _agents = _fake_state(tmp_path)
    target = tmp_path / "brand-new"

    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        _rpc(ws, 1, "session.create", {"cwd": str(target), "create_dir": True, "worktree": True})
        refused = _drain_for_response(ws, 1, [])

    assert "existing git repository" in refused["error"]["message"]
    assert not target.exists()
