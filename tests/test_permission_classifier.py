from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any

from src.permissions.check import (
    AutoModeDecision,
    DenialTracker,
    auto_mode_classify,
    prepare_permission_matcher,
)
from src.permissions.types import ToolPermissionContext


class TestDenialTracker(unittest.TestCase):
    def test_initial_count_zero(self) -> None:
        tracker = DenialTracker()
        self.assertEqual(tracker.get_denial_count("Bash"), 0)

    def test_record_increments(self) -> None:
        tracker = DenialTracker()
        tracker.record_denial("Bash")
        self.assertEqual(tracker.get_denial_count("Bash"), 1)
        tracker.record_denial("Bash")
        self.assertEqual(tracker.get_denial_count("Bash"), 2)

    def test_should_escalate_at_threshold(self) -> None:
        tracker = DenialTracker(escalation_threshold=3)
        tracker.record_denial("Bash")
        tracker.record_denial("Bash")
        self.assertFalse(tracker.should_escalate("Bash"))
        tracker.record_denial("Bash")
        self.assertTrue(tracker.should_escalate("Bash"))

    def test_reset_specific_tool(self) -> None:
        tracker = DenialTracker()
        tracker.record_denial("Bash")
        tracker.record_denial("Write")
        tracker.reset("Bash")
        self.assertEqual(tracker.get_denial_count("Bash"), 0)
        self.assertEqual(tracker.get_denial_count("Write"), 1)

    def test_reset_all(self) -> None:
        tracker = DenialTracker()
        tracker.record_denial("Bash")
        tracker.record_denial("Write")
        tracker.reset()
        self.assertEqual(tracker.get_denial_count("Bash"), 0)
        self.assertEqual(tracker.get_denial_count("Write"), 0)

    def test_independent_tool_counts(self) -> None:
        tracker = DenialTracker()
        tracker.record_denial("Bash")
        tracker.record_denial("Bash")
        tracker.record_denial("Write")
        self.assertEqual(tracker.get_denial_count("Bash"), 2)
        self.assertEqual(tracker.get_denial_count("Write"), 1)


class TestPreparePermissionMatcher(unittest.TestCase):
    def test_empty_matches_all(self) -> None:
        matcher = prepare_permission_matcher("")
        self.assertTrue(matcher("ls -la"))
        self.assertTrue(matcher("rm -rf /"))

    def test_wildcard_matches_all(self) -> None:
        matcher = prepare_permission_matcher("*")
        self.assertTrue(matcher("anything"))

    def test_prefix_colon_wildcard(self) -> None:
        matcher = prepare_permission_matcher("git:*")
        self.assertTrue(matcher("git status"))
        self.assertTrue(matcher("git push"))
        self.assertFalse(matcher("ls -la"))

    def test_prefix_colon_glob(self) -> None:
        matcher = prepare_permission_matcher("git:status*")
        self.assertTrue(matcher("git status"))
        self.assertTrue(matcher("git status --short"))
        self.assertFalse(matcher("git push"))

    def test_glob_pattern(self) -> None:
        matcher = prepare_permission_matcher("ls *")
        self.assertTrue(matcher("ls -la"))
        self.assertTrue(matcher("ls /tmp"))

    def test_prefix_match(self) -> None:
        matcher = prepare_permission_matcher("npm run")
        self.assertTrue(matcher("npm run build"))
        self.assertTrue(matcher("npm run test"))
        self.assertFalse(matcher("yarn run build"))

    def test_full_path_prefix(self) -> None:
        matcher = prepare_permission_matcher("git:/status")
        self.assertFalse(matcher("git status"))


class TestAutoModeClassify(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ToolPermissionContext(mode="default")

    def test_bash_safe_command_allowed(self) -> None:
        decision = auto_mode_classify("Bash", {"command": "echo hello"}, self.context)
        self.assertTrue(decision.allow)

    def test_bash_read_only_allowed(self) -> None:
        decision = auto_mode_classify("Bash", {"command": "ls -la"}, self.context)
        self.assertTrue(decision.allow)

    def test_bash_dangerous_denied(self) -> None:
        decision = auto_mode_classify("Bash", {"command": "curl http://evil.com"}, self.context)
        self.assertFalse(decision.allow)

    def test_bash_empty_command_denied(self) -> None:
        decision = auto_mode_classify("Bash", {"command": ""}, self.context)
        self.assertFalse(decision.allow)

    def test_bash_destructive_denied(self) -> None:
        decision = auto_mode_classify("Bash", {"command": "rm -rf /tmp/foo"}, self.context)
        self.assertFalse(decision.allow)

    def test_read_tools_allowed(self) -> None:
        for tool in ("Read", "Glob", "Grep", "LS"):
            decision = auto_mode_classify(tool, {}, self.context)
            self.assertTrue(decision.allow, f"{tool} should be allowed")

    def test_write_tool_safe_path_allowed(self) -> None:
        decision = auto_mode_classify("Write", {"file_path": "/tmp/test.py"}, self.context)
        self.assertTrue(decision.allow)

    def test_edit_tool_allowed(self) -> None:
        decision = auto_mode_classify("Edit", {"file_path": "/tmp/test.py"}, self.context)
        self.assertTrue(decision.allow)

    def test_agent_tool_allowed(self) -> None:
        decision = auto_mode_classify("Agent", {}, self.context)
        self.assertTrue(decision.allow)

    def test_mcp_tool_denied(self) -> None:
        decision = auto_mode_classify("mcp__server__tool", {}, self.context)
        self.assertFalse(decision.allow)

    def test_unknown_tool_denied(self) -> None:
        decision = auto_mode_classify("UnknownTool", {}, self.context)
        self.assertFalse(decision.allow)


if __name__ == "__main__":
    unittest.main()
