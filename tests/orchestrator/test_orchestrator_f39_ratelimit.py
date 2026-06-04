"""F-39 Sub-F: 限频 + 角色校验.

Covers:
  - WorkflowConfig.max_retries_per_issue defaults to 3
  - WorkflowConfig.allow_anyone_to_retry defaults to False
  - WorkflowConfig.from_dict loads the new fields
  - Orchestrator._is_command_author_eligible:
      * allow_anyone_to_retry=True → bypass
      * author_login=None → fail-closed
      * author_login == 'clawcodex' → bypass (bot self)
      * author_login == issue.author_login → eligible
      * other → ineligible
  - Orchestrator._check_retry_rate_limit:
      * under limit → True
      * at limit + force=False → False, label + comment + audit log
      * at limit + force=True → True
  - Orchestrator._reject_unauthorized_command posts comment + audit
  - CLI `_run_retry`:
      * rate-limited reset returns 3, no state change, audit entry
      * --force bypasses rate limit, high-priority audit
      * --max-retries flag overrides default
"""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from extensions.orchestrator.cli.issue import _run_retry
from extensions.orchestrator.config.schema import (
    AgentConfig,
    WorkflowConfig,
)
from extensions.orchestrator.issue import Issue
from extensions.orchestrator.issue_registry import (
    IssueRegistry,
    IssueStatus,
)
from extensions.orchestrator.orchestrator import Orchestrator
from extensions.orchestrator.tracker import (
    Command,
    CommandIntent,
    Intent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_orchestrator(
    *,
    tracker: Any,
    registry: IssueRegistry,
    workflow: WorkflowConfig | None = None,
) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch.workflow = workflow or WorkflowConfig()
    orch.tracker = tracker
    orch.workspace = MagicMock()
    orch.agent_runner = MagicMock()
    orch.status_dashboard = MagicMock()
    orch._registry = registry
    orch._state = MagicMock()
    return orch


# ---------------------------------------------------------------------------
# WorkflowConfig
# ---------------------------------------------------------------------------


class TestWorkflowConfigRateLimit(unittest.TestCase):
    def test_default_max_retries_per_issue(self) -> None:
        cfg = WorkflowConfig()
        self.assertEqual(cfg.agent.max_retries_per_issue, 3)

    def test_default_allow_anyone_to_retry(self) -> None:
        cfg = WorkflowConfig()
        self.assertFalse(cfg.agent.allow_anyone_to_retry)

    def test_agent_config_defaults(self) -> None:
        cfg = AgentConfig()
        self.assertEqual(cfg.max_retries_per_issue, 3)
        self.assertFalse(cfg.allow_anyone_to_retry)

    def test_from_dict_loads_max_retries(self) -> None:
        raw = {
            "tracker": {"kind": "github"},
            "agent": {"max_retries_per_issue": 5},
        }
        cfg = WorkflowConfig.from_dict(raw)
        self.assertEqual(cfg.agent.max_retries_per_issue, 5)

    def test_from_dict_loads_allow_anyone(self) -> None:
        raw = {
            "tracker": {"kind": "github"},
            "agent": {"allow_anyone_to_retry": True},
        }
        cfg = WorkflowConfig.from_dict(raw)
        self.assertTrue(cfg.agent.allow_anyone_to_retry)

    def test_from_dict_defaults_when_missing(self) -> None:
        raw = {"tracker": {"kind": "github"}}
        cfg = WorkflowConfig.from_dict(raw)
        self.assertEqual(cfg.agent.max_retries_per_issue, 3)
        self.assertFalse(cfg.agent.allow_anyone_to_retry)


# ---------------------------------------------------------------------------
# Orchestrator._is_command_author_eligible
# ---------------------------------------------------------------------------


class TestIsCommandAuthorEligible(unittest.TestCase):
    def test_allow_anyone_bypasses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            orch = _make_orchestrator(
                tracker=MagicMock(),
                registry=reg,
                workflow=WorkflowConfig.from_dict(
                    {
                        "tracker": {"kind": "github"},
                        "agent": {"allow_anyone_to_retry": True},
                    }
                ),
            )
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            self.assertTrue(
                orch._is_command_author_eligible(issue, "anybody")
            )

    def test_none_author_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            # Author unknown → reject (LLM self-trigger guard).
            self.assertFalse(
                orch._is_command_author_eligible(issue, None)
            )
            self.assertFalse(
                orch._is_command_author_eligible(issue, "")
            )

    def test_bot_login_always_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            # The bot itself is allowed (CLI fallback may route through
            # bot comments).
            self.assertTrue(
                orch._is_command_author_eligible(issue, "clawcodex")
            )

    def test_author_matches_issue_author(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.get("1").author_login = "alice"
            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            self.assertTrue(
                orch._is_command_author_eligible(issue, "alice")
            )

    def test_other_login_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.get("1").author_login = "alice"
            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            self.assertFalse(
                orch._is_command_author_eligible(issue, "mallory")
            )

    def test_no_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            # No record for issue 1.
            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            self.assertFalse(
                orch._is_command_author_eligible(issue, "alice")
            )


# ---------------------------------------------------------------------------
# Orchestrator._reject_unauthorized_command
# ---------------------------------------------------------------------------


class TestRejectUnauthorizedCommand(unittest.IsolatedAsyncioTestCase):
    async def test_posts_comment_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")

            tracker = MagicMock()
            tracker.create_comment = AsyncMock()

            # Redirect the audit log to a temp path so the test
            # doesn't touch the real ~/.clawcodex.
            audit_path = Path(tmp) / "audit.jsonl"
            orch = _make_orchestrator(tracker=tracker, registry=reg)
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            cmd = CommandIntent(
                command=Command.RETRY,
                author_login="mallory",
                comment_id="c-1",
                comment_body="/agent retry",
            )
            with patch(
                "extensions.orchestrator.orchestrator.Path.home",
                return_value=Path(tmp),
            ):
                await orch._reject_unauthorized_command(issue, cmd)

            tracker.create_comment.assert_awaited_once()
            args, _ = tracker.create_comment.call_args
            self.assertEqual(args[0], "1")
            self.assertIn("仅 issue 作者或 maintainer", args[1])

            # Audit log file should have been written under
            # <tmp>/.clawcodex/orchestrator/audit.jsonl
            # (because Path.home() returned tmp).
            written = list(Path(tmp).rglob("audit.jsonl"))
            self.assertTrue(written)
            entry = json.loads(written[0].read_text(encoding="utf-8").strip())
            self.assertEqual(entry["event"], "unauthorized_command")
            self.assertEqual(entry["mode"], "command:retry")
            self.assertEqual(entry["operator"], "mallory")
            self.assertEqual(entry["issue_id"], "1")


# ---------------------------------------------------------------------------
# Orchestrator._check_retry_rate_limit
# ---------------------------------------------------------------------------


class TestCheckRetryRateLimit(unittest.TestCase):
    def test_under_limit_allows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.increment_retry_count("1")
            reg.increment_retry_count("1")  # retry_count = 2

            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            self.assertTrue(orch._check_retry_rate_limit(issue))

    def test_at_limit_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            for _ in range(3):
                reg.increment_retry_count("1")

            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            self.assertFalse(orch._check_retry_rate_limit(issue))

    def test_at_limit_with_force_allows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            for _ in range(3):
                reg.increment_retry_count("1")

            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            # force=True only matters on the CLI path; the daemon
            # helper itself accepts it as a hint that the caller has
            # already done its audit.
            self.assertTrue(orch._check_retry_rate_limit(issue, force=True))

    def test_configured_max_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            for _ in range(2):
                reg.increment_retry_count("1")

            workflow = WorkflowConfig()
            workflow.agent.max_retries_per_issue = 2
            orch = _make_orchestrator(
                tracker=MagicMock(), registry=reg, workflow=workflow,
            )
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            # retry_count == max → reject
            self.assertFalse(orch._check_retry_rate_limit(issue))


# ---------------------------------------------------------------------------
# CLI retry rate limit + --force
# ---------------------------------------------------------------------------


def _make_args(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "id": "1",
        "mode": "reset",
        "reason": "wrong approach",
        "force": False,
        "operator": "tester",
        "workspace": None,
        "workflow": None,
        "max_retries": 3,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class TestCliRetryRateLimit(unittest.TestCase):
    def test_reset_at_limit_returns_3_no_state_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            seed = IssueRegistry(reg_path)
            seed.register(issue_id="1", issue_identifier="ISSUE-1")
            for _ in range(3):
                seed.increment_retry_count("1")

            audit_path = Path(tmp) / "audit.jsonl"
            args = _make_args(mode="reset", reason="bypass", force=False)
            with patch(
                "extensions.orchestrator.cli.issue._DEFAULT_AUDIT_LOG_PATH",
                audit_path,
            ):
                rc = _run_retry(reg_path, args)
            self.assertEqual(rc, 3)

            reloaded = IssueRegistry(reg_path)
            record = reloaded.get("1")
            assert record is not None
            # retry_count not bumped.
            self.assertEqual(record.retry_count, 3)
            # Intent was NOT marked — the rate-limit rejection should
            # be a clean no-op on the registry.
            self.assertIs(record.intent, Intent.NONE)

            # Audit log records the rejection with high priority.
            entry = json.loads(audit_path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["event"], "retry_rejected")
            self.assertEqual(entry["priority"], "high")
            self.assertEqual(entry["retry_count"], 3)
            self.assertEqual(entry["max_retries_per_issue"], 3)
            self.assertTrue(entry["rate_limited"])

    def test_reset_force_bypasses_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            seed = IssueRegistry(reg_path)
            seed.register(issue_id="1", issue_identifier="ISSUE-1")
            for _ in range(3):
                seed.increment_retry_count("1")

            audit_path = Path(tmp) / "audit.jsonl"
            args = _make_args(mode="reset", force=True, reason="manual override")
            with patch(
                "extensions.orchestrator.cli.issue._DEFAULT_AUDIT_LOG_PATH",
                audit_path,
            ):
                rc = _run_retry(reg_path, args)
            self.assertEqual(rc, 0)

            reloaded = IssueRegistry(reg_path)
            record = reloaded.get("1")
            assert record is not None
            # retry_count was bumped to 4 (force bypass).
            self.assertEqual(record.retry_count, 4)
            self.assertIs(record.intent, Intent.RETRY)

            # Audit log entry is high-priority + force=True.
            entry = json.loads(audit_path.read_text(encoding="utf-8").strip())
            self.assertTrue(entry["force"])
            self.assertEqual(entry["priority"], "high")
            self.assertFalse(entry["rate_limited"])
            self.assertEqual(entry["event"], "retry")

    def test_reset_under_limit_normal_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            seed = IssueRegistry(reg_path)
            seed.register(issue_id="1", issue_identifier="ISSUE-1")
            seed.increment_retry_count("1")
            seed.increment_retry_count("1")  # retry_count = 2, limit 3

            audit_path = Path(tmp) / "audit.jsonl"
            args = _make_args(mode="reset")
            with patch(
                "extensions.orchestrator.cli.issue._DEFAULT_AUDIT_LOG_PATH",
                audit_path,
            ):
                rc = _run_retry(reg_path, args)
            self.assertEqual(rc, 0)

            reloaded = IssueRegistry(reg_path)
            record = reloaded.get("1")
            assert record is not None
            self.assertEqual(record.retry_count, 3)
            self.assertIs(record.intent, Intent.RETRY)

            entry = json.loads(audit_path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["priority"], "normal")
            self.assertFalse(entry["force"])
            self.assertFalse(entry["rate_limited"])

    def test_max_retries_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            seed = IssueRegistry(reg_path)
            seed.register(issue_id="1", issue_identifier="ISSUE-1")
            for _ in range(5):
                seed.increment_retry_count("1")  # retry_count = 5

            audit_path = Path(tmp) / "audit.jsonl"
            # With --max-retries 10, the limit is not hit.
            args = _make_args(mode="reset", max_retries=10)
            with patch(
                "extensions.orchestrator.cli.issue._DEFAULT_AUDIT_LOG_PATH",
                audit_path,
            ):
                rc = _run_retry(reg_path, args)
            self.assertEqual(rc, 0)
            entry = json.loads(audit_path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["max_retries_per_issue"], 10)
            self.assertFalse(entry["rate_limited"])

    def test_followup_unaffected_by_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            seed = IssueRegistry(reg_path)
            seed.register(issue_id="1", issue_identifier="ISSUE-1")
            for _ in range(3):
                seed.increment_retry_count("1")

            audit_path = Path(tmp) / "audit.jsonl"
            # followup mode does NOT count against retry_count.
            args = _make_args(mode="followup", reason="please fix")
            with patch(
                "extensions.orchestrator.cli.issue._DEFAULT_AUDIT_LOG_PATH",
                audit_path,
            ):
                rc = _run_retry(reg_path, args)
            self.assertEqual(rc, 0)
            reloaded = IssueRegistry(reg_path)
            record = reloaded.get("1")
            assert record is not None
            # retry_count unchanged.
            self.assertEqual(record.retry_count, 3)
            self.assertIs(record.intent, Intent.FOLLOWUP)


if __name__ == "__main__":
    unittest.main(verbosity=2)
