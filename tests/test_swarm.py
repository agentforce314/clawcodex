"""Tests for Swarm/Teammates subsystem."""
from __future__ import annotations

import unittest

from src.services.swarm.helpers import format_team_summary, get_active_teammates
from src.services.swarm.permissions import SwarmPermissionSync
from src.services.swarm.teammate import (
    Teammate,
    TeammateConfig,
    TeammateManager,
    TeammateStatus,
)


class TestTeammate(unittest.TestCase):
    def test_defaults(self) -> None:
        t = Teammate()
        self.assertEqual(t.status, TeammateStatus.PENDING)
        self.assertTrue(t.is_active)  # PENDING is active

    def test_is_active(self) -> None:
        t = Teammate(status=TeammateStatus.RUNNING)
        self.assertTrue(t.is_active)
        t.status = TeammateStatus.COMPLETED
        self.assertFalse(t.is_active)

    def test_elapsed(self) -> None:
        t = Teammate()
        self.assertGreater(t.elapsed_seconds, 0)


class TestTeammateManager(unittest.TestCase):
    def test_spawn(self) -> None:
        mgr = TeammateManager()
        config = TeammateConfig(prompt="Fix the bug in app.py")
        t = mgr.spawn(config)
        self.assertEqual(t.status, TeammateStatus.RUNNING)
        self.assertEqual(mgr.active_count, 1)

    def test_complete(self) -> None:
        mgr = TeammateManager()
        t = mgr.spawn(TeammateConfig(prompt="test"))
        mgr.complete(t.id, result="Done!")
        self.assertEqual(t.status, TeammateStatus.COMPLETED)
        self.assertEqual(t.result, "Done!")
        self.assertEqual(mgr.active_count, 0)

    def test_complete_with_error(self) -> None:
        mgr = TeammateManager()
        t = mgr.spawn(TeammateConfig(prompt="test"))
        mgr.complete(t.id, error="Something went wrong")
        self.assertEqual(t.status, TeammateStatus.FAILED)
        self.assertEqual(t.error, "Something went wrong")

    def test_cancel(self) -> None:
        mgr = TeammateManager()
        t = mgr.spawn(TeammateConfig(prompt="test"))
        mgr.cancel(t.id)
        self.assertEqual(t.status, TeammateStatus.CANCELLED)
        self.assertFalse(t.is_active)

    def test_max_concurrent(self) -> None:
        mgr = TeammateManager(max_concurrent=2)
        mgr.spawn(TeammateConfig(prompt="1"))
        mgr.spawn(TeammateConfig(prompt="2"))
        with self.assertRaises(RuntimeError):
            mgr.spawn(TeammateConfig(prompt="3"))

    def test_cancel_all(self) -> None:
        mgr = TeammateManager()
        mgr.spawn(TeammateConfig(prompt="1"))
        mgr.spawn(TeammateConfig(prompt="2"))
        count = mgr.cancel_all()
        self.assertEqual(count, 2)
        self.assertEqual(mgr.active_count, 0)

    def test_on_complete_callback(self) -> None:
        mgr = TeammateManager()
        completed = []
        mgr.on_complete(lambda t: completed.append(t.id))
        t = mgr.spawn(TeammateConfig(prompt="test"))
        mgr.complete(t.id, result="ok")
        self.assertEqual(len(completed), 1)

    def test_get(self) -> None:
        mgr = TeammateManager()
        t = mgr.spawn(TeammateConfig(prompt="test"))
        self.assertIsNotNone(mgr.get(t.id))
        self.assertIsNone(mgr.get("nonexistent"))


class TestSwarmPermissionSync(unittest.TestCase):
    def test_record_and_check(self) -> None:
        sync = SwarmPermissionSync()
        sync.record_decision("Bash", "ls -la", allowed=True)
        self.assertTrue(sync.check_decision("Bash", "ls -la"))
        self.assertIsNone(sync.check_decision("Bash", "rm -rf"))

    def test_deny_decision(self) -> None:
        sync = SwarmPermissionSync()
        sync.record_decision("Bash", "sudo rm -rf /", allowed=False)
        self.assertFalse(sync.check_decision("Bash", "sudo rm -rf /"))

    def test_clear(self) -> None:
        sync = SwarmPermissionSync()
        sync.record_decision("Bash", None, allowed=True)
        self.assertEqual(sync.decision_count, 1)
        sync.clear()
        self.assertEqual(sync.decision_count, 0)


class TestSwarmHelpers(unittest.TestCase):
    def test_get_active(self) -> None:
        mgr = TeammateManager()
        mgr.spawn(TeammateConfig(prompt="1"))
        t2 = mgr.spawn(TeammateConfig(prompt="2"))
        mgr.complete(t2.id, result="done")
        active = get_active_teammates(mgr)
        self.assertEqual(len(active), 1)

    def test_format_summary_empty(self) -> None:
        mgr = TeammateManager()
        self.assertEqual(format_team_summary(mgr), "No teammates.")

    def test_format_summary_with_teammates(self) -> None:
        mgr = TeammateManager()
        mgr.spawn(TeammateConfig(prompt="Fix bug"))
        summary = format_team_summary(mgr)
        self.assertIn("Fix bug", summary)
        self.assertIn("Active: 1", summary)


if __name__ == "__main__":
    unittest.main()
