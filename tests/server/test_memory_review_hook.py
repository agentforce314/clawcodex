"""Agent-server self-improvement wiring: the post-turn
``_maybe_spawn_memory_review`` hook — interval cadence, resume hydration,
organic-write postponement, single-fork guard, outcome gating, settings
gates, and the ``review_summary``/``memory_manage`` surfaces."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.server.agent_server import AgentServerConfig, _AgentSession
from src.types.content_blocks import ToolResultBlock, ToolUseBlock
from src.types.messages import create_message


class _Settings:
    memory_store_enabled = True
    memory_review_interval = 2
    memory_notifications = "on"


def _make_session() -> tuple[_AgentSession, list[dict]]:
    emitted: list[dict] = []
    sess = _AgentSession(
        session_id="mem-sess", cwd="/tmp",
        config=AgentServerConfig(single_session=False),
        loop=MagicMock(), out_queue=MagicMock(),
    )
    sess._emit = lambda env: emitted.append(env)  # type: ignore[method-assign]
    sess.session = MagicMock()
    sess.session.conversation.messages = []
    sess.provider = MagicMock()
    sess.tool_context = SimpleNamespace(workspace_root="/tmp", cwd="/tmp")
    registry = MagicMock()
    registry.get.return_value = object()  # Memory tool present
    sess.tool_registry = registry
    return sess, emitted


def _turn(sess: _AgentSession, response: str = "done") -> int:
    """Append one user+assistant round; returns the pre-turn length."""
    msgs = sess.session.conversation.messages
    pre = len(msgs)
    msgs.append(create_message("user", f"prompt {pre}"))
    msgs.append(create_message("assistant", response))
    return pre


_OK = {"subtype": "success", "response_text": "done"}


class TestNudgeCadence(unittest.TestCase):
    _UNSET = object()

    def _hook(self, sess: _AgentSession, pre: int, outcome=_UNSET) -> None:
        if outcome is self._UNSET:
            outcome = dict(_OK)
        with patch("src.settings.settings.get_settings", return_value=_Settings()):
            sess._maybe_spawn_memory_review(outcome, pre)

    def test_fires_every_interval_turns(self) -> None:
        sess, _ = _make_session()
        spawned: list[threading.Thread] = []
        with patch.object(threading.Thread, "start", lambda self: spawned.append(self)):
            self._hook(sess, _turn(sess))
            self.assertEqual(len(spawned), 0)  # turn 1 of 2
            self._hook(sess, _turn(sess))
            self.assertEqual(len(spawned), 1)  # interval reached
            self.assertEqual(spawned[0].name, "bg-review")
            self.assertTrue(spawned[0].daemon)
            self.assertEqual(sess._turns_since_memory, 0)  # reset after fire

    def test_hydrates_from_stats_turns(self) -> None:
        sess, _ = _make_session()
        # A resumed session with 1 prior completed turn (interval 2): the
        # first NEW turn completes the cadence. ``_stats_turns`` is the
        # resume-seeded odometer and includes THIS turn at hook time.
        sess.session.conversation.messages = [
            create_message("user", "old prompt"),
            create_message("assistant", "old answer"),
        ]
        sess._stats_turns = 2  # 1 restored + the just-completed turn
        spawned: list[threading.Thread] = []
        with patch.object(threading.Thread, "start", lambda self: spawned.append(self)):
            self._hook(sess, _turn(sess))
        self.assertEqual(len(spawned), 1)

    def test_organic_memory_call_postpones(self) -> None:
        sess, _ = _make_session()
        spawned: list[threading.Thread] = []
        with patch.object(threading.Thread, "start", lambda self: spawned.append(self)):
            self._hook(sess, _turn(sess))  # 1/2
            # Turn 2 uses the Memory tool in the foreground.
            msgs = sess.session.conversation.messages
            pre = len(msgs)
            msgs.append(create_message("user", "remember this"))
            msgs.append(create_message("assistant", [
                ToolUseBlock(id="m1", name="Memory", input={"action": "add"}),
            ]))
            msgs.append(create_message("user", [
                ToolResultBlock(tool_use_id="m1", content=json.dumps({"success": True})),
            ]))
            msgs.append(create_message("assistant", "saved"))
            self._hook(sess, pre)
            self.assertEqual(len(spawned), 0)  # postponed
            self.assertEqual(sess._turns_since_memory, 0)  # counter reset

    def test_skips_non_success_outcomes(self) -> None:
        sess, _ = _make_session()
        spawned: list[threading.Thread] = []
        with patch.object(threading.Thread, "start", lambda self: spawned.append(self)):
            self._hook(sess, _turn(sess), {"subtype": "cancelled", "response_text": ""})
            self._hook(sess, _turn(sess), {"subtype": "error", "response_text": ""})
            self._hook(sess, _turn(sess), {"subtype": "success", "response_text": "  "})
            self._hook(sess, _turn(sess), None)
        self.assertEqual(len(spawned), 0)
        self.assertEqual(sess._turns_since_memory, 0)

    def test_single_fork_at_a_time(self) -> None:
        sess, _ = _make_session()
        live = threading.Thread(target=time.sleep, args=(5,), daemon=True)
        live.start_called = False  # type: ignore[attr-defined]
        sess._memory_review_thread = live
        live_event = threading.Event()

        def _fake_target() -> None:
            live_event.wait(5)

        alive = threading.Thread(target=_fake_target, daemon=True)
        alive.start()
        sess._memory_review_thread = alive
        try:
            spawned: list[threading.Thread] = []
            with patch.object(threading.Thread, "start", lambda self: spawned.append(self)):
                self._hook(sess, _turn(sess))
                self._hook(sess, _turn(sess))  # interval reached, but fork alive
            self.assertEqual(len(spawned), 0)
        finally:
            live_event.set()

    def test_disabled_by_settings(self) -> None:
        sess, _ = _make_session()

        class _Off(_Settings):
            memory_store_enabled = False

        spawned: list[threading.Thread] = []
        with patch.object(threading.Thread, "start", lambda self: spawned.append(self)):
            with patch("src.settings.settings.get_settings", return_value=_Off()):
                sess._maybe_spawn_memory_review(dict(_OK), _turn(sess))
                sess._maybe_spawn_memory_review(dict(_OK), _turn(sess))
        self.assertEqual(len(spawned), 0)

    def test_zero_interval_disables(self) -> None:
        sess, _ = _make_session()

        class _Zero(_Settings):
            memory_review_interval = 0

        spawned: list[threading.Thread] = []
        with patch.object(threading.Thread, "start", lambda self: spawned.append(self)):
            with patch("src.settings.settings.get_settings", return_value=_Zero()):
                for _ in range(4):
                    sess._maybe_spawn_memory_review(dict(_OK), _turn(sess))
        self.assertEqual(len(spawned), 0)

    def test_missing_memory_tool_skips(self) -> None:
        sess, _ = _make_session()
        sess.tool_registry.get.return_value = None
        spawned: list[threading.Thread] = []
        with patch.object(threading.Thread, "start", lambda self: spawned.append(self)):
            self._hook(sess, _turn(sess))
            self._hook(sess, _turn(sess))
        self.assertEqual(len(spawned), 0)


class TestReviewEmission(unittest.TestCase):
    def _fork_env(self):
        """Stub the fork's own-provider resolution (design-critic B1: the
        fork never shares the session's provider object)."""
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch(
            "src.config.get_provider_config", return_value={"default_model": "m"},
        ))
        stack.enter_context(patch(
            "src.providers.resolve_api_key", return_value="k",
        ))
        fork_provider = MagicMock()
        stack.enter_context(patch(
            "src.providers.get_provider_class",
            return_value=lambda **kw: fork_provider,
        ))
        return stack, fork_provider

    def test_summary_emitted_as_review_summary_frame(self) -> None:
        sess, emitted = _make_session()
        stack, fork_provider = self._fork_env()
        with stack:
            with patch("src.settings.settings.get_settings", return_value=_Settings()):
                with patch(
                    "src.memory.review_fork.run_memory_review",
                    return_value="💾 Self-improvement review: Memory updated",
                ) as fork_run:
                    _turn(sess)
                    pre = _turn(sess)
                    sess._stats_turns = 2  # cadence: 2nd completed turn (interval 2)
                    sess._maybe_spawn_memory_review(dict(_OK), pre)
                    thread = sess._memory_review_thread
                    self.assertIsNotNone(thread)
                    thread.join(timeout=5)
        frames = [e for e in emitted if e.get("subtype") == "review_summary"]
        self.assertEqual(len(frames), 1)
        self.assertEqual(
            frames[0]["message"], "💾 Self-improvement review: Memory updated"
        )
        self.assertEqual(frames[0]["type"], "system")
        # The fork received its OWN provider instance, not the session's.
        self.assertIs(fork_run.call_args.kwargs["provider"], fork_provider)
        self.assertIsNot(fork_run.call_args.kwargs["provider"], sess.provider)

    def test_none_summary_emits_nothing(self) -> None:
        sess, emitted = _make_session()
        stack, _ = self._fork_env()
        with stack:
            with patch("src.settings.settings.get_settings", return_value=_Settings()):
                with patch(
                    "src.memory.review_fork.run_memory_review", return_value=None
                ):
                    _turn(sess)
                    pre = _turn(sess)
                    sess._stats_turns = 2  # cadence: 2nd completed turn (interval 2)
                    sess._maybe_spawn_memory_review(dict(_OK), pre)
                    sess._memory_review_thread.join(timeout=5)
        self.assertEqual(
            [e for e in emitted if e.get("subtype") == "review_summary"], []
        )

    def test_provider_resolution_failure_skips_review(self) -> None:
        sess, emitted = _make_session()
        with patch(
            "src.config.get_provider_config", side_effect=RuntimeError("no config"),
        ):
            with patch("src.settings.settings.get_settings", return_value=_Settings()):
                with patch(
                    "src.memory.review_fork.run_memory_review",
                    return_value="should never run",
                ) as fork_run:
                    _turn(sess)
                    pre = _turn(sess)
                    sess._stats_turns = 2  # cadence: 2nd completed turn (interval 2)
                    sess._maybe_spawn_memory_review(dict(_OK), pre)
                    sess._memory_review_thread.join(timeout=5)
        fork_run.assert_not_called()  # never falls back to the shared provider
        self.assertEqual(
            [e for e in emitted if e.get("subtype") == "review_summary"], []
        )


class TestMemoryManageControl(unittest.TestCase):
    def test_control_routes_to_manage(self) -> None:
        sess, emitted = _make_session()
        with patch(
            "src.memory.manage.handle_memory_manage", return_value="STATUS TEXT"
        ) as handler:
            asyncio.run(sess._handle_control_request({
                "type": "control_request",
                "request_id": "r1",
                "request": {"subtype": "memory_manage", "arg": "status"},
            }))
        handler.assert_called_once_with("status")
        replies = [e for e in emitted if e.get("type") == "control_response"]
        self.assertTrue(replies)
        resp = replies[-1]["response"]["response"]
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["text"], "STATUS TEXT")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
