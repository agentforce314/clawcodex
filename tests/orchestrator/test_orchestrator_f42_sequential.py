from __future__ import annotations

import argparse
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.orchestrator.config.schema import WorkflowConfig
from extensions.api.orchestration import OrchestrationSubsystem
from extensions.orchestrator.cli.issue import _run_diff
from extensions.orchestrator.issue_registry import IssueRegistry
from extensions.orchestrator.orchestrator import Orchestrator
from extensions.orchestrator.workspace import WorkspaceConfig, WorkspaceManager


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


class _Tracker:
    pass


class _Runner:
    pass


class _Workspace:
    def __init__(self, path: Path) -> None:
        self.path = path


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

    def test_sequential_ignore_sync_writes_git_exclude(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git(["init"], root)
            workflow = WorkflowConfig.from_dict(
                {
                    "tracker": {"kind": "local", "issues_path": str(root / "issues")},
                    "workspace": {
                        "root": str(root),
                        "strategy": "sequential",
                        "gitignore_patterns": [".clawcodex_workspace.lock"],
                    },
                    "agent": {"max_concurrent_agents": 1},
                }
            )
            workspace = WorkspaceManager(WorkspaceConfig(root=root, strategy="sequential"))
            orchestrator = Orchestrator(
                workflow=workflow,
                tracker=_Tracker(),  # type: ignore[arg-type]
                workspace=workspace,
                agent_runner=_Runner(),  # type: ignore[arg-type]
            )

            orchestrator._sync_gitignore_to_workspace(_Workspace(root))

            self.assertFalse((root / ".gitignore").exists())
            self.assertIn(
                ".clawcodex_workspace.lock",
                (root / ".git" / "info" / "exclude").read_text(encoding="utf-8"),
            )

    def test_orchestration_subsystem_forwards_sequential_workspace_config(self) -> None:
        config = WorkflowConfig.from_dict(
            {
                "tracker": {"kind": "local", "issues_path": "/tmp/issues"},
                "workspace": {
                    "root": "/tmp/workspace",
                    "repo_clone_url": "/tmp/source",
                    "strategy": "sequential",
                    "base_branch": "dev-decoupling",
                    "integration_branch": "dev-decoupling-refactor",
                    "require_clean_start": False,
                    "require_clean_between_issues": False,
                    "preserve_on_terminal": False,
                    "sequential_lock": False,
                },
                "agent": {"max_concurrent_agents": 1},
            }
        )

        subsystem = OrchestrationSubsystem(config)
        workspace_config = subsystem.workspace_manager.config

        self.assertEqual(workspace_config.strategy, "sequential")
        self.assertEqual(workspace_config.base_branch, "dev-decoupling")
        self.assertEqual(workspace_config.integration_branch, "dev-decoupling-refactor")
        self.assertFalse(workspace_config.require_clean_start)
        self.assertFalse(workspace_config.require_clean_between_issues)
        self.assertFalse(workspace_config.preserve_on_terminal)
        self.assertFalse(workspace_config.sequential_lock)

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
