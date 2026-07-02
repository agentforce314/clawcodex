"""ch11 round-4 acceptance tests: the LLM memory-relevance recall is wired
into the live adapter (gated), surfacing relevant memory BODIES as a
<system-reminder>, with per-session de-dup.

Covers my-docs/ch11-memory-round4-gap-analysis.md §2.
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from src.memdir.surface_memories import (
    build_relevant_memory_reminder,
    get_relevant_memory_reminder,
)


class _RelMem:
    def __init__(self, path):
        self.path = path
        self.mtime_ms = 0.0


class TestBuildReminder(unittest.TestCase):
    def test_reads_bodies_into_reminder(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "user_pref.md"
            p.write_text("Prefers real DB instances in tests, not mocks.")
            reminder = build_relevant_memory_reminder([_RelMem(str(p))])
            self.assertIsNotNone(reminder)
            self.assertIn("<system-reminder>", reminder)
            self.assertIn("real DB instances", reminder)
            self.assertIn(str(p), reminder)

    def test_empty_when_nothing_readable(self):
        self.assertIsNone(build_relevant_memory_reminder([_RelMem("/nope/x.md")]))

    def test_caps_long_body(self):
        import tempfile

        from src.memdir.surface_memories import MAX_SURFACE_LINES

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "big.md"
            p.write_text("\n".join(f"line {i}" for i in range(1000)))
            reminder = build_relevant_memory_reminder([_RelMem(str(p))])
            self.assertIn("truncated", reminder)
            # Well under 1000 lines surfaced.
            self.assertLess(reminder.count("\n"), MAX_SURFACE_LINES + 30)


class TestRecallGateAndDedup(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_no_provider_returns_none(self):
        out = self._run(get_relevant_memory_reminder(
            "some query", "/tmp/memdir", provider=None, already_surfaced=set(),
        ))
        self.assertIsNone(out)

    def test_surfaced_paths_recorded_for_dedup(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.md"
            p.write_text("a memory body")

            async def _fake_find(query, memdir, **kw):
                return [_RelMem(str(p))]

            surfaced = set()
            with patch(
                "src.memdir.find_relevant_memories.find_relevant_memories",
                _fake_find,
            ):
                out = self._run(get_relevant_memory_reminder(
                    "q", tmp, provider=object(), already_surfaced=surfaced,
                ))
            self.assertIsNotNone(out)
            self.assertIn(str(p), surfaced)  # recorded for de-dup


class TestAdapterGate(unittest.TestCase):
    """The adapter's _maybe_recall_memories respects the settings gate."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_gate_off_returns_none(self):
        from src.query.agent_loop_compat import _maybe_recall_memories
        from src.tool_system.context import ToolContext
        from src.types.messages import create_user_message

        class _S:
            memory_relevance_prefetch_enabled = False

        with patch("src.settings.settings.get_settings", return_value=_S()):
            out = self._run(_maybe_recall_memories(
                [create_user_message(content="hi")], object(),
                ToolContext(workspace_root=Path("/tmp")), set(),
            ))
        self.assertIsNone(out)

    def test_gate_on_fires_recall(self):
        import tempfile

        from src.query.agent_loop_compat import _maybe_recall_memories
        from src.tool_system.context import ToolContext
        from src.types.messages import create_user_message

        class _S:
            memory_relevance_prefetch_enabled = True

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "pref.md"
            p.write_text("use real DB in tests")

            async def _fake_find(query, memdir, **kw):
                return [_RelMem(str(p))]

            with patch("src.settings.settings.get_settings", return_value=_S()), \
                 patch("src.memdir.get_auto_mem_path", return_value=Path(tmp)), \
                 patch(
                     "src.memdir.find_relevant_memories.find_relevant_memories",
                     _fake_find,
                 ):
                out = self._run(_maybe_recall_memories(
                    [create_user_message(content="write the tests")],
                    object(), ToolContext(workspace_root=Path(tmp)), set(),
                ))
        self.assertIsNotNone(out)
        self.assertTrue(getattr(out, "isMeta", False))
        self.assertIn("real DB", str(out.content))


if __name__ == "__main__":
    unittest.main()
