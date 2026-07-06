"""The ``session_turns`` odometer behind the TUI's session-stats line (the
deleted REPL's bottom toolbar, repl/core.py ``_bottom_toolbar``).

Covers:
* ``_count_prompt_turns`` — the /resume-fallback & /rewind recount predicate
  (real prompts only: no meta reminders, no tool_result carrier messages).
* ``_result_message`` — stamps ``session_turns`` only when provided.
* ``_run_turn`` — a successful turn increments the odometer and stamps the
  result payload; an aborted turn stamps the current value unchanged;
  internal (notification) and btw (ephemeral) turns never move it.
* Lifecycle: ``clear`` zeroes it (reply rider included), ``_do_rewind``
  recounts it, ``_do_resume`` seeds it (persisted counter first, recount
  fallback for old files), ``_save_session`` persists it.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.server.agent_server import (
    AgentServerConfig,
    _AgentSession,
    _count_prompt_turns,
    _result_message,
)


def _msg(role: str, content, is_meta: bool = False) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content, isMeta=is_meta)


class TestCountPromptTurns(unittest.TestCase):
    def test_counts_string_and_text_block_prompts(self) -> None:
        msgs = [
            _msg("user", "hello"),
            _msg("assistant", "hi"),
            _msg("user", [{"type": "text", "text": "second"}]),
        ]
        self.assertEqual(_count_prompt_turns(msgs), 2)

    def test_skips_meta_tool_results_and_non_user(self) -> None:
        msgs = [
            _msg("user", "real prompt"),
            _msg("user", "<system-reminder>…</system-reminder>", is_meta=True),
            _msg("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
            _msg("assistant", [{"type": "text", "text": "answer"}]),
            _msg("user", None),
        ]
        self.assertEqual(_count_prompt_turns(msgs), 1)

    def test_multimodal_prompt_with_text_block_counts_once(self) -> None:
        msgs = [
            _msg("user", [
                {"type": "image", "source": {}},
                {"type": "text", "text": "what is this?"},
            ]),
        ]
        self.assertEqual(_count_prompt_turns(msgs), 1)

    def test_empty_conversation(self) -> None:
        self.assertEqual(_count_prompt_turns([]), 0)


class TestResultMessageRider(unittest.TestCase):
    def test_omitted_when_none(self) -> None:
        msg = _result_message(
            "sid", subtype="success", num_turns=1, result="ok", is_error=False,
        )
        self.assertNotIn("session_turns", msg)

    def test_stamped_when_given(self) -> None:
        msg = _result_message(
            "sid", subtype="success", num_turns=1, result="ok", is_error=False,
            session_turns=7,
        )
        self.assertEqual(msg["session_turns"], 7)


class TestRunTurnOdometer(unittest.TestCase):
    def _session(self) -> tuple[_AgentSession, list[dict]]:
        emitted: list[dict] = []
        sess = _AgentSession(
            session_id="s1", cwd="/tmp",
            config=AgentServerConfig(single_session=True),
            loop=MagicMock(), out_queue=MagicMock(),
        )
        sess._emit = lambda env: emitted.append(env)
        sess.session = MagicMock()
        sess.session.conversation.messages = []
        sess._run_user_prompt_submit_hooks = lambda p: None
        sess._fire_session_start_once = lambda: None
        sess._build_turn_pipeline_config = lambda provider: None
        sess._save_session = lambda: None
        return sess, emitted

    def _loop_result(self, num_turns: int = 1):
        return SimpleNamespace(
            response_text="done",
            usage={"input_tokens": 5, "output_tokens": 3},
            num_turns=num_turns,
        )

    def _run(self, sess: _AgentSession, *, result=None, error: Exception | None = None, **kwargs) -> None:
        async def _fake_query(**_kw):
            if error is not None:
                raise error
            return result if result is not None else self._loop_result()

        # patch() swaps the async loop entry point for an AsyncMock; an async
        # side_effect is awaited, so raising AbortError here surfaces exactly
        # like a real aborted query.
        with patch(
            "src.query.agent_loop_compat.run_query_as_agent_loop",
            side_effect=_fake_query,
        ), patch(
            "src.coordinator.mode.coordinator_main_loop_registry",
            side_effect=lambda reg: reg,
        ):
            sess._run_turn("hello", **kwargs)

    @staticmethod
    def _last_result(emitted: list[dict]) -> dict:
        results = [e for e in emitted if e.get("type") == "result"]
        assert results, f"no result message emitted: {emitted}"
        return results[-1]

    def test_success_increments_and_stamps(self) -> None:
        sess, emitted = self._session()
        self._run(sess)
        payload = self._last_result(emitted)
        self.assertEqual(payload["subtype"], "success")
        self.assertEqual(payload["session_turns"], 1)
        self.assertEqual(sess._stats_turns, 1)

        self._run(sess)
        self.assertEqual(self._last_result(emitted)["session_turns"], 2)

    def test_abort_stamps_without_increment(self) -> None:
        from src.utils.abort_controller import AbortError

        sess, emitted = self._session()
        self._run(sess)  # one real turn first
        self._run(sess, error=AbortError("interrupted"))
        payload = self._last_result(emitted)
        self.assertEqual(payload["subtype"], "cancelled")
        self.assertEqual(payload["session_turns"], 1)
        self.assertEqual(sess._stats_turns, 1)

    def test_internal_turn_does_not_increment(self) -> None:
        sess, emitted = self._session()
        self._run(sess, internal=True)
        payload = self._last_result(emitted)
        self.assertEqual(payload["subtype"], "success")
        self.assertEqual(payload["session_turns"], 0)
        self.assertEqual(sess._stats_turns, 0)

    def test_btw_turn_does_not_increment(self) -> None:
        sess, emitted = self._session()
        self._run(sess, btw=True)
        payload = self._last_result(emitted)
        self.assertEqual(payload["subtype"], "success")
        self.assertEqual(payload["session_turns"], 0)
        self.assertEqual(sess._stats_turns, 0)


class TestLifecycleSync(unittest.TestCase):
    """clear zeroes, rewind recounts, resume seeds, save persists."""

    def _session(self) -> tuple[_AgentSession, list[dict]]:
        from src.agent.conversation import Conversation

        emitted: list[dict] = []
        sess = _AgentSession(
            session_id="s1", cwd="/tmp",
            config=AgentServerConfig(single_session=True),
            loop=MagicMock(), out_queue=MagicMock(),
        )
        sess._emit = lambda env: emitted.append(env)
        sess.session = SimpleNamespace(conversation=Conversation())
        return sess, emitted

    @staticmethod
    def _reply_of(emitted: list[dict]) -> dict:
        responses = [e for e in emitted if e.get("type") == "control_response"]
        assert responses, f"no control_response emitted: {emitted}"
        return responses[-1]["response"]["response"]

    def test_clear_zeroes_odometer_and_stamps_reply(self) -> None:
        sess, emitted = self._session()
        sess._stats_turns = 4
        sess.session.conversation.add_user_message("hello")
        asyncio.run(sess._handle_control_request({
            "type": "control_request",
            "request_id": "r1",
            "request": {"subtype": "clear"},
        }))
        self.assertEqual(sess._stats_turns, 0)
        self.assertEqual(sess.session.conversation.messages, [])
        reply = self._reply_of(emitted)
        self.assertEqual(reply["session_turns"], 0)
        self.assertIn("cost", reply)

    def test_rewind_recounts_from_remaining_messages(self) -> None:
        sess, _ = self._session()
        conv = sess.session.conversation
        for i in range(3):
            conv.add_user_message(f"prompt {i}")
            conv.add_assistant_message(f"answer {i}")
        sess._stats_turns = 3
        sess._do_rewind("r1", 2)
        self.assertEqual(sess._stats_turns, 1)
        self.assertEqual(_count_prompt_turns(conv.messages), 1)

    def _write_session_file(self, tmpdir, *, turns: int | None) -> str:
        from src.agent.conversation import Conversation

        conv = Conversation()
        conv.add_user_message("first prompt")
        conv.add_assistant_message("first answer")
        conv.add_user_message("second prompt")
        payload = {
            "session_id": "saved1",
            "conversation": conv.to_dict(),
        }
        if turns is not None:
            payload["turns"] = turns
        (tmpdir / "saved1.json").write_text(json.dumps(payload), encoding="utf-8")
        return "saved1"

    def test_resume_prefers_persisted_counter(self) -> None:
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tmpdir = pathlib.Path(td)
            sid = self._write_session_file(tmpdir, turns=7)
            sess, emitted = self._session()
            with patch("src.server.agent_server._sessions_dir", return_value=tmpdir):
                sess._do_resume("r1", sid)
            self.assertEqual(sess._stats_turns, 7)
            reply = self._reply_of(emitted)
            self.assertTrue(reply["ok"])
            self.assertEqual(reply["session_turns"], 7)
            self.assertIn("cost", reply)

    def test_resume_falls_back_to_recount_for_old_files(self) -> None:
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tmpdir = pathlib.Path(td)
            sid = self._write_session_file(tmpdir, turns=None)
            sess, _ = self._session()
            with patch("src.server.agent_server._sessions_dir", return_value=tmpdir):
                sess._do_resume("r1", sid)
            # Two real prompts restored through the REAL Conversation/message
            # round-trip — pins the predicate to the production message type.
            self.assertEqual(sess._stats_turns, 2)

    def test_save_session_persists_odometer(self) -> None:
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tmpdir = pathlib.Path(td)
            sess, _ = self._session()
            sess.session.conversation.add_user_message("hello")
            sess._stats_turns = 5
            with patch("src.server.agent_server._sessions_dir", return_value=tmpdir):
                sess._save_session()
            data = json.loads((tmpdir / "s1.json").read_text(encoding="utf-8"))
            self.assertEqual(data["turns"], 5)


if __name__ == "__main__":
    unittest.main()
