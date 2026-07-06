"""Chapter C3 — MCP elicitation hooks (the 3-event UNIT).

Port of executeElicitationHooks / executeElicitationResultHooks /
executeNotificationHooks + the elicitationHandler.ts wrapping. The critical
correctness property (why the 3 must ship together): the ElicitationResult
hook can OVERRIDE the user's response — shipping only the notification would
silently drop that. These drive the executors with real hook commands (echo
JSON), matched on server name, and pin the override/short-circuit/block flow.
"""
from __future__ import annotations

import asyncio
import json
import shlex

import pytest

from src.hooks.hook_executor import (
    _parse_elicitation_hook_output,
    execute_elicitation_hooks,
    execute_elicitation_result_hooks,
    execute_notification_hooks,
)
from src.hooks.hook_types import HookConfig, HookSource


def _hook(payload_json: str, matcher: str | None = None) -> HookConfig:
    return HookConfig(type="command", command=f"echo {shlex.quote(payload_json)}",
                      source=HookSource.USER_SETTINGS, matcher=matcher)


def _exit2_hook(matcher: str | None = None) -> HookConfig:
    # exit code 2 = blocking
    return HookConfig(type="command", command="exit 2",
                      source=HookSource.USER_SETTINGS, matcher=matcher)


class _Ctx:
    """A minimal tool_use_context whose hook snapshot is these configs."""
    def __init__(self, hooks_by_event):
        self._hooks = hooks_by_event
        self.abort_controller = None


@pytest.fixture(autouse=True)
def _snapshot(monkeypatch):
    # route _get_hooks_from_snapshot to the ctx's dict, and disable the trust gate
    monkeypatch.setattr(
        "src.hooks.hook_executor._get_hooks_from_snapshot",
        lambda ctx: getattr(ctx, "_hooks", {}),
    )
    monkeypatch.setattr(
        "src.hooks.hook_executor.should_skip_hook_due_to_trust", lambda ctx: False
    )


def _elic(action, content=None):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "Elicitation",
                       "action": action, **({"content": content} if content is not None else {})}})


def _result(action, content=None):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "ElicitationResult",
                       "action": action, **({"content": content} if content is not None else {})}})


class TestParse:
    def test_exit2_blocks(self):
        from src.hooks.hook_executor import HookResult
        r = HookResult(exit_code=2, stdout="nope", command="c")
        resp, block = _parse_elicitation_hook_output(r, "Elicitation")
        assert resp is None and block and "nope" in block["blockingError"]

    def test_response_parsed(self):
        from src.hooks.hook_executor import HookResult
        r = HookResult(exit_code=0, stdout=_elic("accept", {"x": 1}), command="c")
        resp, block = _parse_elicitation_hook_output(r, "Elicitation")
        assert resp == {"action": "accept", "content": {"x": 1}} and block is None

    def test_decline_action_also_blocks(self):
        from src.hooks.hook_executor import HookResult
        r = HookResult(exit_code=0, stdout=_elic("decline"), command="c")
        resp, block = _parse_elicitation_hook_output(r, "Elicitation")
        assert resp["action"] == "decline" and block is not None

    def test_wrong_event_ignored(self):
        from src.hooks.hook_executor import HookResult
        r = HookResult(exit_code=0, stdout=_result("accept"), command="c")
        resp, block = _parse_elicitation_hook_output(r, "Elicitation")  # expected Elicitation
        assert resp is None and block is None


class TestElicitationHook:
    def test_response_short_circuits(self):
        ctx = _Ctx({"Elicitation": [_hook(_elic("accept", {"y": 2}))]})
        resp, block = asyncio.run(
            execute_elicitation_hooks("srv", "msg", ctx))
        assert resp == {"action": "accept", "content": {"y": 2}} and block is None

    def test_block(self):
        ctx = _Ctx({"Elicitation": [_exit2_hook()]})
        resp, block = asyncio.run(execute_elicitation_hooks("srv", "msg", ctx))
        assert block is not None

    def test_matcher_scopes_by_server_name(self):
        ctx = _Ctx({"Elicitation": [_hook(_elic("accept"), matcher="other-server")]})
        resp, block = asyncio.run(execute_elicitation_hooks("srv", "msg", ctx))
        assert resp is None and block is None  # matcher didn't match srv

    def test_no_hooks_returns_none(self):
        resp, block = asyncio.run(execute_elicitation_hooks("srv", "msg", _Ctx({})))
        assert resp is None and block is None


class TestElicitationResultHook:
    def test_override(self):
        ctx = _Ctx({"ElicitationResult": [_hook(_result("accept", {"overridden": True}))]})
        resp, block = asyncio.run(
            execute_elicitation_result_hooks("srv", "cancel", None, ctx))
        assert resp == {"action": "accept", "content": {"overridden": True}}

    def test_block(self):
        ctx = _Ctx({"ElicitationResult": [_exit2_hook()]})
        resp, block = asyncio.run(
            execute_elicitation_result_hooks("srv", "accept", {"x": 1}, ctx))
        assert block is not None


class TestNotificationHook:
    def test_fires_matching_notification_type(self, tmp_path):
        marker = tmp_path / "fired"
        cfg = HookConfig(type="command", command=f"touch {shlex.quote(str(marker))}",
                         source=HookSource.USER_SETTINGS, matcher="elicitation_response")
        ctx = _Ctx({"Notification": [cfg]})
        asyncio.run(execute_notification_hooks("m", "elicitation_response", ctx))
        assert marker.exists()

    def test_type_matcher_scopes(self, tmp_path):
        marker = tmp_path / "fired"
        cfg = HookConfig(type="command", command=f"touch {shlex.quote(str(marker))}",
                         source=HookSource.USER_SETTINGS, matcher="some_other_type")
        ctx = _Ctx({"Notification": [cfg]})
        asyncio.run(execute_notification_hooks("m", "elicitation_response", ctx))
        assert not marker.exists()  # notification_type didn't match


class TestElicitHandlerIntegration:
    """Through the REAL _elicit handler (the live entry point) — a fake sess
    that simulates the user round-trip so we prove the hooks flow end-to-end,
    not just the executors in isolation."""

    def _sess(self, hooks, *, user_reply=None):
        import threading
        import types

        from src.server.agent_server import _Pending

        emitted = []

        sess = types.SimpleNamespace()
        sess.tool_context = _Ctx(hooks)
        sess._lock = threading.Lock()
        sess._pending = {}
        sess.config = types.SimpleNamespace(permission_timeout_s=5.0)

        def _emit(msg):
            emitted.append(msg)
            # simulate the TUI answering: set the matching pending's reply+event
            rid = msg.get("request_id")
            p = sess._pending.get(rid)
            if p is not None and user_reply is not None:
                p.reply = user_reply
                p.event.set()

        sess._emit = _emit
        sess._emitted = emitted
        return sess

    def test_before_hook_short_circuits_without_prompting(self, monkeypatch):
        from src.server.agent_server import _make_elicitation_handler

        # a before-hook that answers AND a result-hook that WOULD override:
        # TS returns the before-hook response DIRECTLY (elicitationHandler.ts:
        # 96-107), so the result-hook must NOT run on the short-circuit.
        sess = self._sess({
            "Elicitation": [_hook(_elic("accept", {"pre": True}))],
            "ElicitationResult": [_hook(_result("accept", {"should_not_apply": True}))],
        })
        handler = _make_elicitation_handler(sess)
        res = asyncio.run(handler({"serverName": "srv", "message": "m"}))
        assert res == {"action": "accept", "content": {"pre": True}}  # NOT overridden
        assert sess._emitted == []  # the user was NEVER prompted

    def test_result_hook_overrides_user_reply(self, monkeypatch):
        from src.server.agent_server import _make_elicitation_handler

        # user says accept {u:1}; the ElicitationResult hook overrides to
        # accept {overridden:1} — the override must win.
        sess = self._sess(
            {"ElicitationResult": [_hook(_result("accept", {"overridden": 1}))]},
            user_reply={"action": "accept", "content": {"u": 1}},
        )
        handler = _make_elicitation_handler(sess)
        res = asyncio.run(handler({"serverName": "srv", "message": "m"}))
        assert sess._emitted, "the user SHOULD have been prompted"
        assert res == {"action": "accept", "content": {"overridden": 1}}

    def test_no_hooks_passes_user_reply_through(self):
        from src.server.agent_server import _make_elicitation_handler

        sess = self._sess({}, user_reply={"action": "accept", "content": {"u": 9}})
        handler = _make_elicitation_handler(sess)
        res = asyncio.run(handler({"serverName": "srv", "message": "m"}))
        assert res == {"action": "accept", "content": {"u": 9}}
