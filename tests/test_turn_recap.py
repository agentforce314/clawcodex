"""End-of-turn recap + suggestion — service unit tests and server wiring.

Covers ``src/services/turn_recap.py`` (transcript serialization, the tagged
two-line parse, the generator's never-raise contract) and the
``_AgentSession._maybe_spawn_recap`` post-turn hook (spawn guards, the
staleness gate, the emitted ``system/recap`` frame).
"""

from __future__ import annotations

import asyncio
import queue as _queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.services.turn_recap import (
    MAX_SUGGESTION_CHARS,
    generate_turn_recap,
    parse_recap_response,
    serialize_transcript_tail,
)


def _msg(role: str, content):
    return SimpleNamespace(role=role, content=content)


def _text(text: str):
    return SimpleNamespace(type="text", text=text)


class TestSerializeTranscriptTail(unittest.TestCase):
    def test_renders_roles_and_collapses_tool_blocks(self):
        messages = [
            _msg("user", "port the recap feature"),
            _msg("assistant", [
                _text("On it."),
                SimpleNamespace(type="tool_use", name="Read", input={}),
            ]),
            _msg("user", [SimpleNamespace(type="tool_result", content="…")]),
            _msg("assistant", [_text("Done. Next: tests.")]),
        ]
        out = serialize_transcript_tail(messages)
        self.assertEqual(out.split("\n\n"), [
            "User: port the recap feature",
            "Assistant: On it. [tool: Read]",
            "User: [tool result]",
            "Assistant: Done. Next: tests.",
        ])

    def test_skips_non_conversation_roles_and_empty_messages(self):
        messages = [
            _msg("system", "system prompt"),
            _msg("user", ""),
            _msg("assistant", [SimpleNamespace(type="thinking", thinking="…")]),
            _msg("user", "real prompt"),
        ]
        out = serialize_transcript_tail(messages)
        self.assertEqual(out, "User: real prompt")

    def test_accepts_dict_shaped_blocks(self):
        messages = [_msg("assistant", [{"type": "text", "text": "hi"},
                                       {"type": "tool_use", "name": "Bash"}])]
        self.assertEqual(serialize_transcript_tail(messages),
                         "Assistant: hi [tool: Bash]")

    def test_truncates_long_messages_and_collapses_whitespace(self):
        messages = [_msg("user", "a b\n\n  c " + "x" * 2000)]
        out = serialize_transcript_tail(messages, per_message_chars=40)
        self.assertTrue(out.startswith("User: a b c x"))
        self.assertTrue(out.endswith("…"))
        # "User: " prefix + 40-char body budget
        self.assertEqual(len(out), len("User: ") + 40)

    def test_window_and_total_budget_keep_the_most_recent(self):
        messages = [_msg("user", f"prompt {i} " + "x" * 100) for i in range(50)]
        out = serialize_transcript_tail(
            messages, max_messages=30, total_chars=500
        )
        self.assertNotIn("prompt 19 ", out)  # outside the 30-message window
        self.assertIn("prompt 49 ", out)     # newest always survives
        self.assertLessEqual(len(out), 500)
        # Head-dropped: the oldest inside the window is gone too.
        self.assertNotIn("prompt 20 ", out)


class TestParseRecapResponse(unittest.TestCase):
    def test_happy_path(self):
        parsed = parse_recap_response(
            "RECAP: Goal was X. Done. Next: re-run.\n"
            "SUGGESTION: fix all four issues and re-run"
        )
        self.assertEqual(parsed, (
            "Goal was X. Done. Next: re-run.",
            "fix all four issues and re-run",
        ))

    def test_lenient_tags_fences_and_multiline_recap(self):
        parsed = parse_recap_response(
            "```\nrecap: Goal was X.\nStill the recap line.\n"
            "Suggestion: \"run the tests\"\n```"
        )
        self.assertEqual(parsed, (
            "Goal was X. Still the recap line.",
            "run the tests",
        ))

    def test_none_suggestion_maps_to_empty(self):
        for token in ("NONE", "none", "None."):
            parsed = parse_recap_response(f"RECAP: Done.\nSUGGESTION: {token}")
            self.assertEqual(parsed, ("Done.", ""), token)

    def test_missing_suggestion_tag_is_fine(self):
        self.assertEqual(parse_recap_response("RECAP: Done."), ("Done.", ""))

    def test_suggestion_capped(self):
        parsed = parse_recap_response(
            "RECAP: Done.\nSUGGESTION: " + "y" * 400
        )
        assert parsed is not None
        self.assertEqual(len(parsed[1]), MAX_SUGGESTION_CHARS)

    def test_no_recap_kills_the_feature_for_the_turn(self):
        self.assertIsNone(parse_recap_response("SUGGESTION: do things"))
        self.assertIsNone(parse_recap_response("free-form prose only"))
        self.assertIsNone(parse_recap_response(""))
        self.assertIsNone(parse_recap_response(None))


class _FakeProvider:
    def __init__(self, reply: str | None):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat_async(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.reply is None:
            return None
        return SimpleNamespace(content=self.reply)


class TestGenerateTurnRecap(unittest.TestCase):
    def _messages(self):
        return [_msg("user", "port the recap"), _msg("assistant", "done")]

    def test_generates_and_parses(self):
        provider = _FakeProvider("RECAP: Ported. Next: tests.\nSUGGESTION: run the tests")
        result = asyncio.run(generate_turn_recap(self._messages(), provider))
        self.assertEqual(result, {
            "recap": "Ported. Next: tests.",
            "suggestion": "run the tests",
        })
        # One bounded small side-call: system prompt + max_tokens are pinned.
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0]["max_tokens"], 1200)
        self.assertIn("RECAP", provider.calls[0]["system"])
        self.assertIn("port the recap", provider.calls[0]["messages"][0]["content"])

    def test_small_model_pin_rides_when_resolved(self):
        provider = _FakeProvider("RECAP: X.\nSUGGESTION: NONE")
        with patch(
            "src.services.turn_recap._resolve_recap_model",
            return_value="small-fast",
        ):
            result = asyncio.run(generate_turn_recap(self._messages(), provider))
        self.assertEqual(result, {"recap": "X.", "suggestion": ""})
        self.assertEqual(provider.calls[0]["model"], "small-fast")
        # No effort pin for a provider without a declared vocabulary.
        self.assertNotIn("reasoning_effort", provider.calls[0])

    def test_low_effort_pin_for_declaring_providers(self):
        provider = _FakeProvider("RECAP: X.\nSUGGESTION: NONE")
        provider.supported_reasoning_efforts = ("low", "high", "max")
        asyncio.run(generate_turn_recap(self._messages(), provider))
        self.assertEqual(provider.calls[0]["reasoning_effort"], "low")

    def test_no_effort_pin_when_low_not_in_vocabulary(self):
        provider = _FakeProvider("RECAP: X.\nSUGGESTION: NONE")
        provider.supported_reasoning_efforts = ("medium", "high")
        asyncio.run(generate_turn_recap(self._messages(), provider))
        self.assertNotIn("reasoning_effort", provider.calls[0])

    def test_list_shaped_content_is_flattened(self):
        class _ListProvider(_FakeProvider):
            async def chat_async(self, messages, **kwargs):
                self.calls.append({"messages": messages, **kwargs})
                return SimpleNamespace(content=[
                    {"type": "text", "text": "RECAP: Flat.\n"},
                    {"type": "text", "text": "SUGGESTION: NONE"},
                ])

        result = asyncio.run(
            generate_turn_recap(self._messages(), _ListProvider(None))
        )
        self.assertEqual(result, {"recap": "Flat.", "suggestion": ""})

    def test_never_raises(self):
        class _Boom:
            async def chat_async(self, *a, **k):
                raise RuntimeError("boom")

        self.assertIsNone(asyncio.run(generate_turn_recap(self._messages(), _Boom())))
        self.assertIsNone(asyncio.run(generate_turn_recap(self._messages(), None)))
        self.assertIsNone(asyncio.run(generate_turn_recap([], _FakeProvider("x"))))
        self.assertIsNone(
            asyncio.run(generate_turn_recap(self._messages(), _FakeProvider(None)))
        )
        self.assertIsNone(
            asyncio.run(
                generate_turn_recap(self._messages(), _FakeProvider("no tags here"))
            )
        )


def _session(**overrides):
    from src.server.agent_server import AgentServerConfig, _AgentSession

    sess = _AgentSession(
        session_id="s1", cwd="/tmp",
        config=AgentServerConfig(single_session=True),
        loop=MagicMock(), out_queue=MagicMock(),
    )
    sess.provider = SimpleNamespace(model="m1", chat_async=None)
    sess.provider_name = "anthropic"
    sess.session = SimpleNamespace(
        conversation=SimpleNamespace(messages=[_msg("user", "hi")])
    )
    sess._stats_turns = 3
    for key, value in overrides.items():
        setattr(sess, key, value)
    return sess


_SETTINGS_ON = SimpleNamespace(recap_enabled=True)
_SETTINGS_OFF = SimpleNamespace(recap_enabled=False)

_OUTCOME = {"subtype": "success", "response_text": "done"}


class TestMaybeSpawnRecap(unittest.TestCase):
    def _spawn_and_join(self, sess, outcome=_OUTCOME):
        sess._maybe_spawn_recap(outcome)
        thread = sess._recap_thread
        if thread is not None:
            thread.join(timeout=10)
        return thread

    def _run(self, sess, *, gen_result, outcome=_OUTCOME):
        """Spawn with the provider fork + generator patched; return emits."""
        emitted: list[dict] = []
        sess._emit = emitted.append

        async def _fake_generate(messages, provider):
            return gen_result

        fake_cls = MagicMock(return_value=SimpleNamespace(model="m1"))
        with patch("src.settings.settings.get_settings", return_value=_SETTINGS_ON), \
             patch("src.config.get_provider_config", return_value={}), \
             patch("src.providers.get_provider_class", return_value=fake_cls), \
             patch("src.providers.resolve_api_key", return_value="k"), \
             patch("src.services.turn_recap.generate_turn_recap", _fake_generate):
            self._spawn_and_join(sess, outcome)
        return emitted

    def test_emits_recap_frame_for_a_completed_real_turn(self):
        sess = _session()
        emitted = self._run(
            sess, gen_result={"recap": "Goal was X. Next: Y.", "suggestion": "do Y"}
        )
        self.assertEqual(emitted, [{
            "type": "system",
            "subtype": "recap",
            "session_id": "s1",
            "recap": "Goal was X. Next: Y.",
            "suggestion": "do Y",
        }])

    def test_no_spawn_on_failed_or_empty_turns(self):
        for outcome in (
            None,
            {"subtype": "error", "response_text": "x"},
            {"subtype": "cancelled", "response_text": ""},
            {"subtype": "success", "response_text": "  "},
        ):
            sess = _session()
            with patch("src.settings.settings.get_settings",
                       return_value=_SETTINGS_ON):
                sess._maybe_spawn_recap(outcome)
            self.assertIsNone(sess._recap_thread, outcome)

    def test_no_spawn_when_disabled(self):
        sess = _session()
        with patch("src.settings.settings.get_settings",
                   return_value=_SETTINGS_OFF):
            sess._maybe_spawn_recap(_OUTCOME)
        self.assertIsNone(sess._recap_thread)

    def test_no_spawn_while_goal_loop_is_active(self):
        sess = _session(_goal_mgr=SimpleNamespace(is_active=lambda: True))
        with patch("src.settings.settings.get_settings",
                   return_value=_SETTINGS_ON):
            sess._maybe_spawn_recap(_OUTCOME)
        self.assertIsNone(sess._recap_thread)

    def test_no_spawn_with_a_queued_prompt(self):
        sess = _session()
        sess._inbox.put("next prompt")
        with patch("src.settings.settings.get_settings",
                   return_value=_SETTINGS_ON):
            sess._maybe_spawn_recap(_OUTCOME)
        self.assertIsNone(sess._recap_thread)

    def test_no_second_fork_while_one_runs(self):
        sess = _session()
        gate = threading.Event()
        holder = threading.Thread(target=gate.wait, daemon=True)
        holder.start()
        sess._recap_thread = holder
        try:
            with patch("src.settings.settings.get_settings",
                       return_value=_SETTINGS_ON):
                sess._maybe_spawn_recap(_OUTCOME)
            self.assertIs(sess._recap_thread, holder)
        finally:
            gate.set()

    def test_stale_serial_drops_the_emit(self):
        sess = _session()
        emitted: list[dict] = []
        sess._emit = emitted.append

        async def _fake_generate(messages, provider):
            sess._stats_turns += 1  # a new turn completed while we generated
            return {"recap": "stale", "suggestion": "stale"}

        fake_cls = MagicMock(return_value=SimpleNamespace(model="m1"))
        with patch("src.settings.settings.get_settings", return_value=_SETTINGS_ON), \
             patch("src.config.get_provider_config", return_value={}), \
             patch("src.providers.get_provider_class", return_value=fake_cls), \
             patch("src.providers.resolve_api_key", return_value="k"), \
             patch("src.services.turn_recap.generate_turn_recap", _fake_generate):
            self._spawn_and_join(sess)
        self.assertEqual(emitted, [])

    def test_in_flight_turn_drops_the_emit(self):
        sess = _session()
        sess._current_abort = object()  # a turn is running at emit time
        emitted = self._run(sess, gen_result={"recap": "r", "suggestion": "s"})
        self.assertEqual(emitted, [])

    def test_generator_none_emits_nothing(self):
        sess = _session()
        emitted = self._run(sess, gen_result=None)
        self.assertEqual(emitted, [])

    def test_recap_hook_wired_exactly_once_in_the_real_prompt_branch(self):
        """The "real user turns only" invariant, pinned structurally: the
        module contains exactly ONE ``_maybe_spawn_recap(`` call (so it
        cannot ALSO be wired into the btw/internal/goal/notification
        paths), that call lives in ``_run_worker``, and it sits after the
        memory-review hook in the real-prompt branch."""
        import inspect

        from src.server import agent_server
        from src.server.agent_server import _AgentSession

        module_src = inspect.getsource(agent_server)
        self.assertEqual(module_src.count("self._maybe_spawn_recap("), 1)

        worker_src = inspect.getsource(_AgentSession._run_worker)
        self.assertEqual(worker_src.count("self._maybe_spawn_recap("), 1)
        review = worker_src.index("self._maybe_spawn_memory_review(")
        recap = worker_src.index("self._maybe_spawn_recap(")
        self.assertGreater(recap, review)

    def test_aba_odometer_move_drops_the_emit(self):
        """The critic-proven ABA case: /clear (or /rewind) moves the
        odometer away and a follow-up turn moves it BACK to the captured
        value while the fork is still generating. The monotonic
        ``_recap_serial`` (bumped by every completed turn and every
        conversation-identity change) must drop the emit even though
        ``_stats_turns`` matches again."""
        sess = _session()
        emitted: list[dict] = []
        sess._emit = emitted.append

        async def _fake_generate(messages, provider):
            # /clear wipes (odometer 3 → 0, serial bump), then a new turn
            # completes (odometer back to 3, serial bump again).
            sess._recap_serial += 2
            return {"recap": "describes the WIPED conversation",
                    "suggestion": "continue the deleted work"}

        fake_cls = MagicMock(return_value=SimpleNamespace(model="m1"))
        with patch("src.settings.settings.get_settings", return_value=_SETTINGS_ON), \
             patch("src.config.get_provider_config", return_value={}), \
             patch("src.providers.get_provider_class", return_value=fake_cls), \
             patch("src.providers.resolve_api_key", return_value="k"), \
             patch("src.services.turn_recap.generate_turn_recap", _fake_generate):
            self._spawn_and_join(sess)
        self.assertEqual(emitted, [])

    def test_every_identity_change_site_bumps_the_recap_serial(self):
        """/clear, /rewind, and /resume each bump ``_recap_serial`` (the
        odometer they also touch is non-monotonic, which is the whole ABA
        hole), and the turn-completion path bumps it for EVERY turn —
        btw/internal included — so any completed turn invalidates an
        in-flight fork. Pinned per-FUNCTION (not a module-wide count), so a
        bump relocated to an unrelated method fails the site that lost it,
        and a legitimate future fifth site doesn't break this test."""
        import inspect

        from src.server.agent_server import _AgentSession

        for fn in (
            _AgentSession._handle_control_request,  # session.clear
            _AgentSession._do_resume,
            _AgentSession._do_rewind,
            _AgentSession._run_turn,
        ):
            self.assertIn(
                "self._recap_serial += 1",
                inspect.getsource(fn),
                f"missing recap-serial bump in {fn.__name__}",
            )


class TestSetRecapControl(unittest.TestCase):
    def test_persists_and_invalidates(self):
        sess = _session()
        replies: list[tuple] = []
        sess._reply = lambda rid, payload: replies.append((rid, payload))

        mgr = MagicMock()
        mgr.load_global.return_value = {"settings": {"theme": "dark"}}
        with patch("src.config._get_default_manager", return_value=mgr), \
             patch("src.settings.settings.invalidate_settings_cache") as inv, \
             patch("src.server.agent_server._recap_setting_enabled",
                   return_value=False):
            sess._do_set_recap("r1", "off")

        saved = mgr.save_global.call_args[0][0]
        self.assertEqual(saved["settings"]["recap_enabled"], False)
        self.assertEqual(saved["settings"]["theme"], "dark")
        inv.assert_called_once()
        self.assertEqual(replies, [("r1", {"ok": True, "value": "off"})])

    def test_reply_reports_effective_state_when_an_override_wins(self):
        """The write is global; a project/local override wins at merge.
        "/recap off" must reply with the EFFECTIVE state (on) plus a note —
        not confidently echo the request."""
        sess = _session()
        replies: list[tuple] = []
        sess._reply = lambda rid, payload: replies.append((rid, payload))

        mgr = MagicMock()
        mgr.load_global.return_value = {}
        with patch("src.config._get_default_manager", return_value=mgr), \
             patch("src.settings.settings.invalidate_settings_cache"), \
             patch("src.server.agent_server._recap_setting_enabled",
                   return_value=True):
            sess._do_set_recap("r1", "off")

        self.assertEqual(len(replies), 1)
        payload = replies[0][1]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["value"], "on")
        self.assertIn("override", payload["note"])

    def test_rejects_bad_values(self):
        sess = _session()
        replies: list[tuple] = []
        sess._reply = lambda rid, payload: replies.append((rid, payload))
        sess._do_set_recap("r1", "sideways")
        self.assertEqual(len(replies), 1)
        self.assertFalse(replies[0][1]["ok"])


if __name__ == "__main__":
    unittest.main()
