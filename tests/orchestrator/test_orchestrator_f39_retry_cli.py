"""F-39 Sub-E: CLI 兜底命令 — `orchestrator issue retry`.

Covers:
  - IssueRegistry.unblock(): rolls ABANDONED → PENDING, clears intent
  - IssueRegistry.unblock(): no-op on non-ABANDONED status (idempotent)
  - IssueRegistry.unblock(): returns None for unknown issue
  - _append_audit_log(): writes JSONL with required fields
  - _append_audit_log(): creates parent directories
  - _append_audit_log(): force flag → priority=high
  - _resolve_operator(): --operator > $USER > os.getlogin() > "unknown"
  - _run_retry(): --mode reset  → mark_intent(RETRY) + increment_retry_count
  - _run_retry(): --mode followup → mark_intent(FOLLOWUP)
  - _run_retry(): --mode unblock → registry.unblock()
  - _run_retry(): audit log entry written for every mode
  - _run_retry(): auto-registers unknown issue ids
  - _run_retry(): rejects missing --id / invalid --mode
  - add_issue_parser: registers `retry` subcommand with --mode choices
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from extensions.orchestrator.cli.issue import (
    _DEFAULT_AUDIT_LOG_PATH,
    _append_audit_log,
    _resolve_operator,
    _run_retry,
    add_issue_parser,
)
from extensions.orchestrator.issue_registry import (
    IssueRecord,
    IssueRegistry,
    IssueStatus,
)
from extensions.orchestrator.tracker import Intent


# ---------------------------------------------------------------------------
# IssueRegistry.unblock
# ---------------------------------------------------------------------------


class TestIssueRegistryUnblock(unittest.TestCase):
    def test_rolls_abandoned_to_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_abandoned("1")
            record = reg.get("1")
            assert record is not None
            self.assertIs(record.status, IssueStatus.ABANDONED)

            result = reg.unblock("1")
            assert result is not None
            self.assertIs(result.status, IssueStatus.PENDING)

            # Persisted on disk too.
            reloaded = IssueRegistry(Path(tmp) / "r.json").get("1")
            assert reloaded is not None
            self.assertIs(reloaded.status, IssueStatus.PENDING)

    def test_clears_intent_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_intent("1", Intent.BLOCKED, source="label", command="agent:blocked")
            reg.mark_abandoned("1")

            record = reg.unblock("1")
            assert record is not None
            self.assertIs(record.intent, Intent.NONE)
            self.assertIsNone(record.intent_source)
            self.assertIsNone(record.last_command)

    def test_noop_on_completed(self) -> None:
        # Unblocking a healthy issue must be idempotent — it should
        # NOT clobber a completed/failed status.
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_completed("1")
            reg.mark_intent("1", Intent.RETRY, source="cli")

            record = reg.unblock("1")
            assert record is not None
            self.assertIs(record.status, IssueStatus.COMPLETED)
            # Intent still cleared (the unblock is about intent, not
            # status; the status only resets when it was ABANDONED).
            self.assertIs(record.intent, Intent.NONE)

    def test_unknown_issue_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            self.assertIsNone(reg.unblock("999"))

    def test_preserves_retry_count(self) -> None:
        # F-39 design note: rate limit still applies after unblock.
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "r.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_abandoned("1")
            reg.increment_retry_count("1")
            reg.increment_retry_count("1")
            reg.increment_retry_count("1")

            record = reg.unblock("1")
            assert record is not None
            self.assertEqual(record.retry_count, 3)


# ---------------------------------------------------------------------------
# _append_audit_log
# ---------------------------------------------------------------------------


class TestAppendAuditLog(unittest.TestCase):
    def test_writes_jsonl_with_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "audit.jsonl"
            written = _append_audit_log(
                issue_id="1",
                mode="reset",
                reason="wrong approach",
                operator="alice",
                force=False,
                path=log_path,
            )
            self.assertEqual(written, log_path)
            content = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(content), 1)
            entry = json.loads(content[0])
            for required in ("ts", "ts_iso", "operator", "issue_id", "mode", "reason", "force", "priority"):
                self.assertIn(required, entry)
            self.assertEqual(entry["operator"], "alice")
            self.assertEqual(entry["issue_id"], "1")
            self.assertEqual(entry["mode"], "reset")
            self.assertEqual(entry["reason"], "wrong approach")
            self.assertFalse(entry["force"])
            self.assertEqual(entry["priority"], "normal")

    def test_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "deep" / "nested" / "audit.jsonl"
            written = _append_audit_log(
                issue_id="1", mode="unblock", reason="",
                operator="bob", force=False, path=log_path,
            )
            self.assertEqual(written, log_path)
            self.assertTrue(log_path.exists())

    def test_force_marks_high_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "audit.jsonl"
            _append_audit_log(
                issue_id="1", mode="reset", reason="bypass",
                operator="alice", force=True, path=log_path,
            )
            entry = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertTrue(entry["force"])
            self.assertEqual(entry["priority"], "high")

    def test_appends_multiple_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "audit.jsonl"
            for i in range(3):
                _append_audit_log(
                    issue_id=str(i), mode="reset", reason=f"r{i}",
                    operator="alice", force=False, path=log_path,
                )
            content = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(content), 3)

    def test_extra_kwargs_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "audit.jsonl"
            _append_audit_log(
                issue_id="1", mode="reset", reason="x",
                operator="alice", force=False, path=log_path,
                extra={"issue_identifier": "ISSUE-1", "platform": "github"},
            )
            entry = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["issue_identifier"], "ISSUE-1")
            self.assertEqual(entry["platform"], "github")

    def test_returns_none_on_io_error(self) -> None:
        # Pointing at an invalid path (e.g. a file in place of a
        # directory) should not crash; the helper returns None and
        # prints a warning.
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("not a directory", encoding="utf-8")
            # Try to write to `<tmp>/blocker/sub/audit.jsonl` — the
            # parent mkdir should fail.
            bad_path = blocker / "sub" / "audit.jsonl"
            # The print to stderr is noisy — capture it.
            import io
            from contextlib import redirect_stderr
            buf = io.StringIO()
            with redirect_stderr(buf):
                result = _append_audit_log(
                    issue_id="1", mode="reset", reason="",
                    operator="alice", force=False, path=bad_path,
                )
            self.assertIsNone(result)


# ---------------------------------------------------------------------------
# _resolve_operator
# ---------------------------------------------------------------------------


class TestResolveOperator(unittest.TestCase):
    def test_explicit_wins(self) -> None:
        self.assertEqual(
            _resolve_operator("explicit-login"),
            "explicit-login",
        )

    def test_user_env_fallback(self) -> None:
        # Pass None to skip explicit; ensure the function falls back
        # to $USER (which pytest-internal env usually has).
        original = os.environ.get("USER")
        os.environ["USER"] = "env-user"
        try:
            self.assertEqual(_resolve_operator(None), "env-user")
        finally:
            if original is None:
                os.environ.pop("USER", None)
            else:
                os.environ["USER"] = original

    def test_unknown_fallback(self) -> None:
        # If explicit=None and both USER env vars are unset, expect
        # "unknown" (or os.getlogin() if it works in the test env).
        original_user = os.environ.pop("USER", None)
        original_username = os.environ.pop("USERNAME", None)
        try:
            result = _resolve_operator(None)
            self.assertIn(result, {"unknown"})  # os.getlogin() may also work
        finally:
            if original_user is not None:
                os.environ["USER"] = original_user
            if original_username is not None:
                os.environ["USERNAME"] = original_username


# ---------------------------------------------------------------------------
# _run_retry
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
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class TestRunRetry(unittest.TestCase):
    def test_reset_marks_intent_and_increments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            seed = IssueRegistry(reg_path)
            seed.register(issue_id="1", issue_identifier="ISSUE-1")
            seed.mark_completed("1")
            before_retry_count = seed.get("1").retry_count

            audit_path = Path(tmp) / "audit.jsonl"
            args = _make_args(mode="reset")
            with patch(
                "extensions.orchestrator.cli.issue._DEFAULT_AUDIT_LOG_PATH",
                audit_path,
            ):
                rc = _run_retry(reg_path, args)

            self.assertEqual(rc, 0)
            # _run_retry built its own IssueRegistry from disk, so
            # reload to see the writes.
            reloaded = IssueRegistry(reg_path)
            record = reloaded.get("1")
            assert record is not None
            self.assertIs(record.intent, Intent.RETRY)
            self.assertEqual(record.intent_source, "cli")
            self.assertEqual(record.retry_count, before_retry_count + 1)

            # Audit log entry was written.
            entries = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").strip().splitlines()
            ]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["mode"], "reset")
            self.assertEqual(entries[0]["reason"], "wrong approach")

    def test_followup_marks_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            IssueRegistry(reg_path).register(issue_id="1", issue_identifier="ISSUE-1")

            args = _make_args(mode="followup", reason="please add tests")
            rc = _run_retry(reg_path, args)
            self.assertEqual(rc, 0)

            reloaded = IssueRegistry(reg_path)
            record = reloaded.get("1")
            assert record is not None
            self.assertIs(record.intent, Intent.FOLLOWUP)
            self.assertEqual(record.intent_source, "cli")

    def test_unblock_rolls_abandoned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            seed = IssueRegistry(reg_path)
            seed.register(issue_id="1", issue_identifier="ISSUE-1")
            seed.mark_abandoned("1")
            seed.mark_intent("1", Intent.BLOCKED, source="label")

            args = _make_args(mode="unblock", reason="manual review ok")
            rc = _run_retry(reg_path, args)
            self.assertEqual(rc, 0)

            reloaded = IssueRegistry(reg_path)
            record = reloaded.get("1")
            assert record is not None
            self.assertIs(record.status, IssueStatus.PENDING)
            self.assertIs(record.intent, Intent.NONE)
            self.assertIsNone(record.intent_source)

    def test_auto_registers_unknown_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            seed = IssueRegistry(reg_path)
            seed.register(issue_id="1", issue_identifier="ISSUE-1")
            # Issue 999 is not yet in the registry.
            self.assertIsNone(IssueRegistry(reg_path).get("999"))

            args = _make_args(id="999", mode="reset")
            rc = _run_retry(reg_path, args)
            self.assertEqual(rc, 0)

            reloaded = IssueRegistry(reg_path)
            record = reloaded.get("999")
            assert record is not None
            self.assertEqual(record.issue_identifier, "999")
            self.assertIs(record.intent, Intent.RETRY)

    def test_missing_id_returns_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            IssueRegistry(reg_path)
            args = _make_args(id=None)
            rc = _run_retry(reg_path, args)
            self.assertEqual(rc, 2)

    def test_invalid_mode_returns_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            IssueRegistry(reg_path)
            args = _make_args(mode="bogus")
            rc = _run_retry(reg_path, args)
            self.assertEqual(rc, 2)

    def test_no_registry_returns_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.json"
            args = _make_args()
            rc = _run_retry(missing, args)
            self.assertEqual(rc, 1)

    def test_force_flag_writes_high_priority_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            IssueRegistry(reg_path).register(issue_id="1", issue_identifier="ISSUE-1")

            audit_path = Path(tmp) / "audit.jsonl"
            args = _make_args(mode="reset", force=True, reason="bypass rate limit")
            with patch(
                "extensions.orchestrator.cli.issue._DEFAULT_AUDIT_LOG_PATH",
                audit_path,
            ):
                rc = _run_retry(reg_path, args)
            self.assertEqual(rc, 0)

            entry = json.loads(audit_path.read_text(encoding="utf-8").strip())
            self.assertTrue(entry["force"])
            self.assertEqual(entry["priority"], "high")

    def test_audit_log_contains_operator_and_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registry.json"
            IssueRegistry(reg_path).register(issue_id="42", issue_identifier="ISSUE-42")

            audit_path = Path(tmp) / "audit.jsonl"
            args = _make_args(
                id="42", mode="followup", operator="ci-bot", reason="automated",
            )
            with patch(
                "extensions.orchestrator.cli.issue._DEFAULT_AUDIT_LOG_PATH",
                audit_path,
            ):
                _run_retry(reg_path, args)
            entry = json.loads(audit_path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["operator"], "ci-bot")
            self.assertEqual(entry["issue_id"], "42")
            self.assertEqual(entry["issue_identifier"], "ISSUE-42")
            self.assertEqual(entry["mode"], "followup")
            self.assertEqual(entry["reason"], "automated")


# ---------------------------------------------------------------------------
# add_issue_parser: `retry` subcommand registered with correct choices
# ---------------------------------------------------------------------------


class TestAddRetryParser(unittest.TestCase):
    def test_retry_parser_registered(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        add_issue_parser(sub)

        # Bare `issue retry --help` must not raise.
        try:
            args = parser.parse_args(["issue", "retry", "--id", "1", "--mode", "reset"])
        except SystemExit:
            self.fail("retry subcommand not registered")
        self.assertEqual(args.issue_subcommand, "retry")
        self.assertEqual(args.id, "1")
        self.assertEqual(args.mode, "reset")

    def test_retry_mode_choices(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        add_issue_parser(sub)

        with self.assertRaises(SystemExit):
            parser.parse_args(["issue", "retry", "--id", "1", "--mode", "bogus"])

    def test_retry_force_flag(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        add_issue_parser(sub)

        args = parser.parse_args(
            ["issue", "retry", "--id", "1", "--mode", "reset", "--force"],
        )
        self.assertTrue(args.force)

    def test_retry_operator_flag(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        add_issue_parser(sub)

        args = parser.parse_args(
            ["issue", "retry", "--id", "1", "--mode", "followup",
             "--operator", "alice", "--reason", "follow-up please"],
        )
        self.assertEqual(args.operator, "alice")
        self.assertEqual(args.reason, "follow-up please")


if __name__ == "__main__":
    unittest.main(verbosity=2)
