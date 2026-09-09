"""Nano mode on the web/desktop gateway path (``clawcodex serve`` / ``web``).

Covers the whole flag chain — ``clawcodex web`` argv → ``clawcodex serve``
argparse → process-global + ``AgentServerConfig`` → per-session spawn — plus
the wire surfaces the browser client reads: ``/api/status`` (welcome screen,
before any session) and ``session.info`` (init and every republish).

The runtime itself (registry, prompt, provider switch) is pinned by
tests/nano/test_nano_tui.py on the same ``_AgentSession``; these tests pin the
transport in front of it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from src.nano.state import is_nano_mode, reset_nano_mode, set_nano_mode
from src.server.desktop_serve import DesktopServeState, build_app

from tests.nano.test_nano_tui import _SessionHarness
from tests.server.test_desktop_gateway import (
    TOKEN,
    FakeAgent,
    FakeManager,
    _connect,
    _drain_for_response,
    _rpc,
)

# Nano/eco process-globals are reset around every test by
# tests/nano/conftest.py's autouse fixture.
pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Flag plumbing: web argv → serve argparse → config + process-global
# ---------------------------------------------------------------------------


def test_web_serve_argv_forwards_nano():
    import argparse

    from src.entrypoints import web_cli

    def _args(**overrides):
        base = dict(
            host="127.0.0.1", port=8081, token=None, workspace=None,
            provider=None, model=None, effort=None, permission_mode=None,
            nano=False, dangerously_skip_permissions=False, allow_remote=False,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    assert "--nano" in web_cli._serve_argv(_args(nano=True))
    assert "--nano" not in web_cli._serve_argv(_args())


def test_serve_cli_parses_nano_into_config(monkeypatch):
    """--nano reaches AgentServerConfig AND flips the process-global before
    the server starts — sessions spawn lazily, so /api/status must already
    tell the truth with no session in existence."""
    import src.entrypoints.serve_cli as serve_mod
    from src.bootstrap.state import reset_state_for_tests

    captured: dict = {}

    async def _fake_serve(args, workspace, token, agent_config, on_ready=None):
        captured["config"] = agent_config
        captured["nano_global"] = is_nano_mode()
        return 0

    monkeypatch.setattr(serve_mod, "_serve", _fake_serve)

    try:
        assert serve_mod.run_serve_subcommand(["--port", "0", "--nano"]) == 0
        assert captured["config"].nano is True
        assert captured["nano_global"] is True

        reset_nano_mode()
        captured.clear()
        assert serve_mod.run_serve_subcommand(["--port", "0"]) == 0
        assert captured["config"].nano is False
        assert captured["nano_global"] is False
    finally:
        # run_serve_subcommand marks the process interactive; leave the suite
        # as it found it.
        reset_state_for_tests()


def test_spawn_for_override_keeps_nano(tmp_path: Path):
    """A session created with the composer's own provider/model (spawn_for's
    dataclasses.replace) must still be nano — otherwise picking a model on the
    welcome screen would silently resurrect the maximal surface."""
    from src.server.agent_server import AgentServerConfig

    async def _noop_spawn(session_id, cwd, resume):  # pragma: no cover - never called
        raise AssertionError("base spawn must not be used when overriding")

    state = DesktopServeState(
        token=TOKEN,
        workspace=str(tmp_path),
        manager=FakeManager(),
        spawn_agent=_noop_spawn,
        protocol_version="0.1.0",
        agent_config=AgentServerConfig(nano=True),
    )

    seen: dict = {}

    def _capture_make_spawn(config):
        seen["config"] = config
        return _noop_spawn

    with patch("src.server.agent_server.make_spawn_agent", _capture_make_spawn):
        state.spawn_for("deepseek", "deepseek-v4-flash", "high")

    assert seen["config"].nano is True
    assert seen["config"].provider_name == "deepseek"


# ---------------------------------------------------------------------------
# /api/status — the welcome screen's pre-session nano fact
# ---------------------------------------------------------------------------


def _bare_state(tmp_path: Path) -> DesktopServeState:
    async def spawn(session_id, cwd, resume):  # pragma: no cover - not spawned here
        raise AssertionError("status tests never spawn a session")

    return DesktopServeState(
        token=TOKEN,
        workspace=str(tmp_path),
        manager=FakeManager(),
        spawn_agent=spawn,
        protocol_version="0.1.0",
    )


def test_api_status_reports_nano(tmp_path: Path):
    set_nano_mode(True)
    with TestClient(build_app(_bare_state(tmp_path))) as client:
        reply = client.get("/api/status", headers={"X-ClawCodex-Session-Token": TOKEN})
    assert reply.status_code == 200
    assert reply.json()["nano"] is True


def test_api_status_nano_false_by_default(tmp_path: Path):
    with TestClient(build_app(_bare_state(tmp_path))) as client:
        reply = client.get("/api/status", headers={"X-ClawCodex-Session-Token": TOKEN})
    assert reply.status_code == 200
    assert reply.json()["nano"] is False


# ---------------------------------------------------------------------------
# session.info mapping — init frame and the get_settings republish
# ---------------------------------------------------------------------------


def test_init_session_info_maps_nano_strictly():
    """Strict ``is True`` and always stamped: an older agent's init frame
    (no field) must read as not-nano, never as unknown."""
    from src.server.desktop_gateway_methods import _init_session_info

    assert _init_session_info({"model": "m", "nano": True})["nano"] is True
    assert _init_session_info({"model": "m", "nano": False})["nano"] is False
    assert _init_session_info({"model": "m"})["nano"] is False
    # Truthy garbage is not a nano session.
    assert _init_session_info({"model": "m", "nano": "yes"})["nano"] is False


class NanoFakeAgent(FakeAgent):
    """The scripted gateway agent, reporting a nano session like the real
    agent-server does: init frame + get_settings both carry the flag."""

    async def messages_from_agent(self):
        yield {
            "type": "system",
            "subtype": "init",
            "cwd": "/tmp/w",
            "permissionMode": "bypassPermissions",
            "model": "fake",
            "nano": True,
        }
        while True:
            yield await self.queue.get()

    async def send_to_agent(self, frame: dict) -> None:
        request = frame.get("request") or {}
        if frame.get("type") == "control_request" and request.get("subtype") == "get_settings":
            self.inbound.append(frame)
            await self.queue.put({
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": frame.get("request_id"),
                    "response": {
                        "model": self.model,
                        "provider": self.provider,
                        "permission_mode": self.permission_mode,
                        "nano": True,
                    },
                },
            })
            return
        await super().send_to_agent(frame)


def _nano_fake_state(tmp_path: Path) -> tuple[DesktopServeState, list[NanoFakeAgent]]:
    agents: list[NanoFakeAgent] = []

    async def spawn(session_id, cwd, resume):
        agent = NanoFakeAgent()
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


def test_session_info_carries_nano_through_the_gateway(tmp_path: Path):
    """session.create's info and the model-switch republish both stamp nano,
    so the browser's chip survives every full session.info replace."""
    state, _ = _nano_fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()  # gateway.ready
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {"cwd": "/tmp"})
        result = _drain_for_response(ws, 1, events)["result"]
        assert result["info"]["nano"] is True
        sid = result["session_id"]

        events.clear()
        _rpc(ws, 2, "config.set", {
            "session_id": sid, "key": "model",
            "value": "new-model --provider newprov --session",
        })
        assert _drain_for_response(ws, 2, events)["result"]["ok"] is True
        infos = [e for e in events if e["type"] == "session.info"]
        assert infos, "no session.info published after the model switch"
        assert infos[-1]["payload"]["nano"] is True


def test_session_info_nano_false_for_a_default_agent(tmp_path: Path):
    """The stock FakeAgent reports no nano anywhere — the gateway must say
    False on both surfaces, not omit the field."""
    from tests.server.test_desktop_gateway import _fake_state

    state, _ = _fake_state(tmp_path)
    with TestClient(build_app(state)) as client, _connect(client) as ws:
        ws.receive_json()
        events: list[dict] = []
        _rpc(ws, 1, "session.create", {"cwd": "/tmp"})
        result = _drain_for_response(ws, 1, events)["result"]
        assert result["info"]["nano"] is False

        events.clear()
        _rpc(ws, 2, "config.set", {
            "session_id": result["session_id"], "key": "model",
            "value": "new-model --provider newprov --session",
        })
        assert _drain_for_response(ws, 2, events)["result"]["ok"] is True
        infos = [e for e in events if e["type"] == "session.info"]
        assert infos and infos[-1]["payload"]["nano"] is False


# ---------------------------------------------------------------------------
# get_settings on the real _AgentSession (the source publish_session_info reads)
# ---------------------------------------------------------------------------


class TestGetSettingsCarriesNano(_SessionHarness):
    def _get_settings(self, sess) -> dict:
        asyncio.run(sess._handle_control_request({
            "type": "control_request",
            "request_id": "req-settings",
            "request": {"subtype": "get_settings"},
        }))
        frames = [
            call.args[1]
            for call in sess.loop.call_soon_threadsafe.call_args_list
            if len(call.args) == 2 and isinstance(call.args[1], dict)
        ]
        replies = [
            f["response"]["response"] for f in frames
            if f.get("type") == "control_response"
            and (f.get("response") or {}).get("request_id") == "req-settings"
        ]
        self.assertTrue(replies, "get_settings produced no reply")
        return replies[-1]

    def test_nano_session_reports_nano(self) -> None:
        sess = self._build(nano=True)
        self.assertIs(self._get_settings(sess)["nano"], True)

    def test_default_session_reports_nano_false(self) -> None:
        sess = self._build()
        self.assertIs(self._get_settings(sess)["nano"], False)
