"""R5 round-5 (ch11) — memory-recall enable-blockers.

Covers the 4 fixes that make the memory-relevance recall safe to enable:
  #2 selector runs on the cheap small_fast_model when configured (not the
     session Opus/DeepSeek model);
  #3 the de-dup set resets on manual /compact (recall doesn't silently
     degrade on long sessions);
  #4 an aggregate per-turn byte cap on surfaced memory, and de-dup marks
     ONLY the memories that actually made it into the reminder.
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class _RelMem:
    def __init__(self, path):
        self.path = path


class TestRecallModelPin(unittest.TestCase):
    """#2 — the selector pins small_fast_model ONLY on a first-party
    AnthropicProvider (critic M1: the default id is Anthropic-only)."""

    def _run_select(self, small_fast_model, *, anthropic):
        import importlib
        frm = importlib.import_module("src.memdir.find_relevant_memories")
        captured = {}

        if anthropic:
            from src.providers.anthropic_provider import AnthropicProvider

            class _Provider(AnthropicProvider):
                def __init__(self):
                    super().__init__(api_key="k")

                async def chat_async(self, messages, **kwargs):
                    captured.update(kwargs)
                    r = MagicMock(); r.content = '{"selected_memories": []}'
                    return r
        else:
            class _Provider:  # non-Anthropic (e.g. DeepSeek/OpenAI)
                async def chat_async(self, messages, **kwargs):
                    captured.update(kwargs)
                    r = MagicMock(); r.content = '{"selected_memories": []}'
                    return r

        settings = MagicMock()
        settings.small_fast_model = small_fast_model
        with patch("src.settings.settings.get_settings", return_value=settings):
            asyncio.run(frm._select_with_provider(
                "q", [], provider=_Provider(),
                recent_tools=(), cancel_event=asyncio.Event(),
            ))
        return captured

    def test_anthropic_session_pins_model(self):
        captured = self._run_select("claude-3-5-haiku-20241022", anthropic=True)
        self.assertEqual(captured.get("model"), "claude-3-5-haiku-20241022")

    def test_anthropic_unset_omits_model(self):
        captured = self._run_select("", anthropic=True)
        self.assertNotIn("model", captured)  # → session model

    def test_non_anthropic_never_pins_even_when_set(self):
        # critic M1: the Anthropic-default id must NOT reach a non-Anthropic
        # endpoint (would 400 → silent no-recall). → session model.
        captured = self._run_select("claude-3-5-haiku-20241022", anthropic=False)
        self.assertNotIn("model", captured)

    def test_resolve_recall_model_none_on_settings_error(self):
        from src.memdir.find_relevant_memories import _resolve_recall_model
        from src.providers.anthropic_provider import AnthropicProvider
        p = AnthropicProvider(api_key="k")
        with patch("src.settings.settings.get_settings",
                   side_effect=RuntimeError("boom")):
            self.assertIsNone(_resolve_recall_model(p))


class TestAggregateByteCap(unittest.TestCase):
    """#4 — the turn's total surfaced bytes are capped, and de-dup tracks
    only what was actually surfaced."""

    def _write(self, tmp, name, kb):
        p = Path(tmp) / name
        p.write_text("x" * (kb * 1024), encoding="utf-8")
        return str(p)

    def test_total_cap_trims_tail_and_reports_surfaced(self):
        from src.memdir import surface_memories as sm
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            # Each file is per-file-capped at 4 KB; 5 of them would be ~20 KB,
            # over the 12 KB aggregate cap → only the first few survive.
            mems = [_RelMem(self._write(tmp, f"m{i}.md", 5)) for i in range(5)]
            reminder, surfaced = sm.build_relevant_memory_reminder_with_paths(mems)

        self.assertIsNotNone(reminder)
        self.assertLess(len(surfaced), 5)  # tail trimmed
        self.assertIn("omitted", reminder)
        # Every surfaced path is present in the reminder; trimmed ones are not.
        for p in surfaced:
            self.assertIn(p, reminder)

    def test_dedup_marks_only_surfaced_not_full_selection(self):
        from src.memdir import surface_memories as sm
        import tempfile

        async def _go():
            with tempfile.TemporaryDirectory() as tmp:
                mems = [_RelMem(self._write(tmp, f"m{i}.md", 5)) for i in range(5)]

                async def _fake_find(*a, **k):
                    return mems

                surfaced_set: set[str] = set()
                with patch(
                    "src.memdir.find_relevant_memories.find_relevant_memories",
                    _fake_find,
                ):
                    await sm.get_relevant_memory_reminder(
                        "q", tmp, provider=MagicMock(),
                        already_surfaced=surfaced_set,
                    )
                return surfaced_set, [m.path for m in mems]

        surfaced_set, all_paths = asyncio.run(_go())
        # Fewer than all selected are de-dup-marked (the capped-out tail
        # stays eligible for a later turn).
        self.assertLess(len(surfaced_set), len(all_paths))
        self.assertTrue(surfaced_set)

    def test_backcompat_string_wrapper(self):
        from src.memdir.surface_memories import build_relevant_memory_reminder
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.md"
            p.write_text("hello memory", encoding="utf-8")
            r = build_relevant_memory_reminder([_RelMem(str(p))])
        self.assertIsInstance(r, str)
        self.assertIn("hello memory", r)


class TestDedupResetOnCompact(unittest.TestCase):
    """#3 — manual /compact clears the recall de-dup set."""

    def test_compact_clears_memory_surfaced(self):
        from src.server.agent_server import AgentServerConfig, _AgentSession

        sess = _AgentSession(
            session_id="s1", cwd="/tmp",
            config=AgentServerConfig(single_session=True),
            loop=MagicMock(), out_queue=MagicMock(),
        )
        sess._memory_surfaced.update({"/mem/a.md", "/mem/b.md"})
        sess._emit = lambda e: None
        sess._reply = lambda rid, payload: None
        sess._current_abort = None  # idle → compaction allowed

        # Stub the conversation + compaction so _do_compact reaches success.
        sess.session = MagicMock()
        res = MagicMock(tokens_saved=10, pre_compact_count=5, post_compact_count=1)

        async def _fake_compact(*a, **k):
            return res

        with patch("src.compact_service.service.compact_conversation", _fake_compact), \
                patch("src.hooks.session_hooks.run_compact_hooks",
                      new=_async_noop):
            asyncio.run(sess._do_compact("r1", None))

        self.assertEqual(sess._memory_surfaced, set())  # reset


async def _async_noop(*a, **k):
    return []


if __name__ == "__main__":
    unittest.main()
