"""Tests for ch03 round-2 R2.1: Session.save persists a cost block
matching the cost_restore reader schema, and Session.resume restores it.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from src.agent.session import Session
from src.agent.conversation import Conversation
from src.bootstrap.state import (
    ModelUsage,
    add_to_tool_duration,
    add_to_total_cost_state,
    add_to_total_duration_state,
    add_to_total_lines_changed,
    get_total_cost_usd,
    get_total_lines_added,
    get_total_lines_removed,
    get_total_tool_duration,
    reset_state_for_tests,
)


class _SessionDirFixture:
    """Helper: redirect ~/.clawcodex to a tmpdir via Path.home patching."""

    def __init__(self, tmp_home: Path) -> None:
        self.tmp_home = tmp_home
        self._patch = mock.patch.object(Path, "home", return_value=tmp_home)

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *args):
        self._patch.stop()


class SaveCostBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_state_for_tests()
        self.tmpdir = Path(self._mk_tmpdir())
        self._fixture = _SessionDirFixture(self.tmpdir).__enter__()

    def tearDown(self) -> None:
        self._fixture.__exit__(None, None, None)
        reset_state_for_tests()

    def _mk_tmpdir(self) -> str:
        import tempfile
        return tempfile.mkdtemp(prefix="ch03-r2-")

    def _read_saved(self, sid: str) -> dict:
        path = self.tmpdir / ".clawcodex" / "sessions" / f"{sid}.json"
        return json.loads(path.read_text())

    def test_save_emits_cost_block_with_current_state(self) -> None:
        # Prime bootstrap state.
        usage = ModelUsage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            cost_usd=0.0123,
        )
        add_to_total_cost_state(0.0123, usage, "claude-opus-4-7")
        add_to_total_duration_state(2500, 2000)
        add_to_tool_duration(1500)
        add_to_total_lines_changed(10, 5)

        sess = Session.create("anthropic", "claude-opus-4-7")
        sess.save()

        data = self._read_saved(sess.session_id)
        cost = data.get("cost")
        self.assertIsNotNone(cost, "save() must emit a 'cost' key")
        self.assertAlmostEqual(cost["total_cost_usd"], 0.0123, places=6)
        self.assertEqual(cost["total_api_duration"], 2500)
        self.assertEqual(cost["total_api_duration_without_retries"], 2000)
        self.assertEqual(cost["total_tool_duration"], 1500)
        self.assertEqual(cost["total_lines_added"], 10)
        self.assertEqual(cost["total_lines_removed"], 5)
        self.assertIn("last_duration", cost)
        self.assertIn("model_usage", cost)
        mu = cost["model_usage"]
        self.assertIn("claude-opus-4-7", mu)
        self.assertEqual(mu["claude-opus-4-7"]["input_tokens"], 100)
        self.assertEqual(mu["claude-opus-4-7"]["output_tokens"], 50)
        self.assertAlmostEqual(
            mu["claude-opus-4-7"]["cost_usd"], 0.0123, places=6
        )

    def test_save_emits_empty_model_usage_when_no_usage_recorded(self) -> None:
        sess = Session.create("anthropic", "claude-opus-4-7")
        sess.save()

        cost = self._read_saved(sess.session_id)["cost"]
        self.assertEqual(cost["model_usage"], {})
        self.assertEqual(cost["total_cost_usd"], 0.0)
        self.assertEqual(cost["total_lines_added"], 0)

    def test_save_then_resume_round_trip(self) -> None:
        usage = ModelUsage(
            input_tokens=200,
            output_tokens=80,
            cache_creation_input_tokens=10,
            cache_read_input_tokens=20,
            cost_usd=0.05,
        )
        add_to_total_cost_state(0.05, usage, "claude-sonnet-4-6")
        add_to_total_duration_state(5000, 4500)
        add_to_tool_duration(2000)
        add_to_total_lines_changed(42, 8)

        sess = Session.create("anthropic", "claude-sonnet-4-6")
        sess.save()
        sid = sess.session_id

        # Wipe bootstrap state — simulates a process restart.
        reset_state_for_tests()
        self.assertEqual(get_total_cost_usd(), 0.0)
        self.assertEqual(get_total_lines_added(), 0)

        resumed = Session.resume(sid)
        self.assertIsNotNone(resumed)
        self.assertAlmostEqual(get_total_cost_usd(), 0.05, places=6)
        self.assertEqual(get_total_lines_added(), 42)
        self.assertEqual(get_total_lines_removed(), 8)
        self.assertEqual(get_total_tool_duration(), 2000)


if __name__ == "__main__":
    unittest.main()
