from __future__ import annotations

import argparse
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.orchestrator.config.schema import WorkflowConfig
from extensions.orchestrator.cli.issue import _run_diff
from extensions.orchestrator.issue_registry import IssueRegistry


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


class TestF42SequentialWorkspace(unittest.TestCase):
    def test_sequential_requires_single_agent(self) -> None:
        with self.assertRaises(ValueError):
            WorkflowConfig.from_dict(
                {
                    "workspace": {"strategy": "sequential"},
                    "agent": {"max_concurrent_agents": 2},
                }
            )

    def test_sequential_requires_state_limits_at_most_one(self) -> None:
        with self.assertRaises(ValueError):
            WorkflowConfig.from_dict(
                {
                    "workspace": {"strategy": "sequential"},
                    "agent": {
                        "max_concurrent_agents": 1,
                        "max_concurrent_agents_by_state": {"open": 2},
                    },
                }
            )

    def test_registry_round_trip_preserves_sequential_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            registry = IssueRegistry(path)
            registry.register(
                issue_id="1",
                issue_identifier="ISSUE-1",
                branch_name="integration/f42",
                base_branch="main",
                workspace_strategy="sequential",
                workspace_path="/tmp/workspace",
                base_commit_sha="abc123",
                start_commit_sha="def456",
                previous_issue_id="0",
                sequence_index=2,
            )

            reloaded = IssueRegistry(path)
            record = reloaded.get("1")

            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.workspace_strategy, "sequential")
            self.assertEqual(record.workspace_path, "/tmp/workspace")
            self.assertEqual(record.base_commit_sha, "abc123")
            self.assertEqual(record.start_commit_sha, "def456")
            self.assertEqual(record.previous_issue_id, "0")
            self.assertEqual(record.sequence_index, 2)
            self.assertEqual(reloaded.latest_sequential_record(), record)

    def test_issue_diff_uses_registry_workspace_for_sequential_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            _git(["init"], workspace)
            _git(["config", "user.email", "test@example.com"], workspace)
            _git(["config", "user.name", "Test User"], workspace)
            (workspace / "README.md").write_text("before\n", encoding="utf-8")
            _git(["add", "README.md"], workspace)
            _git(["commit", "-m", "initial"], workspace)
            (workspace / "README.md").write_text("after\n", encoding="utf-8")
            _git(["add", "README.md"], workspace)
            _git(["commit", "-m", "change"], workspace)

            registry_path = workspace / ".clawcodex_issue_registry.json"
            registry = IssueRegistry(registry_path)
            registry.register(
                issue_id="1",
                issue_identifier="ISSUE-1",
                branch_name="integration/f42",
                base_branch="main",
                workspace_strategy="sequential",
                workspace_path=str(workspace),
            )
            registry.mark_synced("1", commit_sha="abc123")

            args = argparse.Namespace(id="1", workspace=str(workspace), full=False, stat=True)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = _run_diff(registry_path, args)

            self.assertEqual(exit_code, 0)
            self.assertIn("Issue 1 — Changes", stdout.getvalue())
