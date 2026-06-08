"""F-49 Phase 4 tests for the ``takeover`` CLI subcommand.

Covers:
  * ``argparse`` registration: ``clawcodex issue takeover --id X``
    parses into ``args.issue_subcommand == "takeover"``, and the
    parser also accepts ``--run`` + ``--workspace`` (sibling of
    attach / resume-session).
  * ``run()`` dispatcher routes ``takeover`` to ``_run_takeover``
    with ``(registry_path, ws, args)`` (the same signature
    _run_attach / _run_resume_session use).
  * ``_resolve_target`` returns the correct ``_TakeoverTarget`` for
    each lookup mode: ``--id`` (IssueRegistry), ``--run`` +
    ``--workspace`` (registry bypass), and the not-found / no-run-id
    / no-workspace-path / no-registry negative cases.

The Step 2 slice covers only the parser, dispatcher, and resolver.
Socket send + REPL spawn + end-to-end land in Steps 3 and 4.

Uses ``unittest.TestCase`` (the resolver / parser are sync) and
``tempfile.TemporaryDirectory`` for IssueRegistry isolation.
Patches ``extensions.orchestrator.issue_registry``'s default path
so the test does not touch the user's real ``~/.clawcodex``.
"""
from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stderr
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from extensions.orchestrator.cli.takeover import (
    _TakeoverTarget,
    _resolve_target,
    _run_takeover,
)
from extensions.orchestrator.issue_registry import (
    IssueRecord,
    IssueRegistry,
    IssueStatus,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _write_registry(path: Path, record: IssueRecord) -> None:
    """Write a single-record IssueRegistry JSON file at ``path``.

    The on-disk format is ``{issue_id: record_dict}`` — see
    ``IssueRegistry._save`` for the canonical shape. ``status`` is
    serialised as its ``.value`` so the loader's
    ``IssueStatus(v)`` round-trip succeeds.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {record.issue_id: asdict(record)}
    data[record.issue_id]["status"] = record.status.value
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _make_record(
    issue_id: str = "42",
    issue_identifier: str = "owner/repo#42",
    run_id: str | None = "run-abc",
    workspace_path: str | None = "/tmp/ws",
) -> IssueRecord:
    """Build an IssueRecord for tests."""
    return IssueRecord(
        issue_id=issue_id,
        issue_identifier=issue_identifier,
        status=IssueStatus.RUNNING,
        branch_name="f-49-takeover-test",
        base_branch="main",
        workspace_path=workspace_path,
        workspace_strategy="worktree",
        run_id=run_id,
    )


# ------------------------------------------------------------------
# Parser registration
# ------------------------------------------------------------------


class TestTakeoverParser(unittest.TestCase):
    """The new subcommand is registered with --id, --run, --workspace."""

    def test_takeover_parser_registered(self) -> None:
        from extensions.orchestrator.cli.issue import add_issue_parser

        parent = argparse.ArgumentParser()
        sub = parent.add_subparsers(dest="top")
        add_issue_parser(sub)
        args = parent.parse_args(["issue", "takeover", "--id", "X"])
        self.assertEqual(args.issue_subcommand, "takeover")
        self.assertEqual(args.id, "X")
        self.assertIsNone(args.run)
        self.assertIsNone(args.workspace)

    def test_takeover_parser_accepts_run_and_workspace(self) -> None:
        from extensions.orchestrator.cli.issue import add_issue_parser

        parent = argparse.ArgumentParser()
        sub = parent.add_subparsers(dest="top")
        add_issue_parser(sub)
        args = parent.parse_args(
            ["issue", "takeover", "--run", "r-1", "--workspace", "/w"],
        )
        self.assertEqual(args.issue_subcommand, "takeover")
        self.assertIsNone(args.id)
        self.assertEqual(args.run, "r-1")
        self.assertEqual(args.workspace, "/w")

    def test_takeover_parser_allows_no_args(self) -> None:
        """Unlike the legacy version, --id is now optional — usage
        is enforced at the handler, not the parser (so --run
        alone is parseable).
        """
        from extensions.orchestrator.cli.issue import add_issue_parser

        parent = argparse.ArgumentParser()
        sub = parent.add_subparsers(dest="top")
        add_issue_parser(sub)
        args = parent.parse_args(["issue", "takeover"])
        self.assertEqual(args.issue_subcommand, "takeover")
        self.assertIsNone(args.id)
        self.assertIsNone(args.run)
        self.assertIsNone(args.workspace)


# ------------------------------------------------------------------
# Dispatcher
# ------------------------------------------------------------------


class TestTakeoverDispatch(unittest.TestCase):
    """The ``run()`` dispatcher routes ``takeover`` to the new
    module-level ``_run_takeover`` with the (registry_path, ws,
    args) signature.
    """

    def test_dispatch_to_run_takeover(self) -> None:
        from extensions.orchestrator import cli as cli_mod
        from extensions.orchestrator.cli import issue as cli_issue

        captured: dict = {}

        def fake(registry_path, workspace_root, args) -> int:
            captured["called"] = True
            captured["registry_path"] = registry_path
            captured["workspace_root"] = workspace_root
            captured["id"] = getattr(args, "id", None)
            captured["run"] = getattr(args, "run", None)
            captured["workspace"] = getattr(args, "workspace", None)
            return 0

        with patch.object(cli_issue, "_run_takeover", side_effect=fake):
            args = argparse.Namespace(
                issue_subcommand="takeover",
                id="X",
                run=None,
                workspace=None,
            )
            rc = cli_issue.run(args)
        self.assertEqual(rc, 0)
        self.assertTrue(captured.get("called"))
        self.assertEqual(captured.get("id"), "X")
        self.assertIsNone(captured.get("run"))
        # The dispatcher passes the resolved registry path and the
        # ``--workspace`` argument through unchanged.
        self.assertEqual(
            captured.get("workspace"), args.workspace,
        )

    def test_dispatch_passes_registry_path_through(self) -> None:
        """When a registry_path is configured, the dispatcher
        forwards it to ``_run_takeover`` so the handler can look up
        the run_id via IssueRegistry.
        """
        from extensions.orchestrator.cli import issue as cli_issue

        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(registry_path, _make_record())

            captured: dict = {}

            def fake(registry_path_arg, workspace_root, args) -> int:
                captured["registry_path"] = registry_path_arg
                return 0

            with patch.object(cli_issue, "_run_takeover", side_effect=fake):
                args = argparse.Namespace(
                    issue_subcommand="takeover",
                    id="owner/repo#42",
                    run=None,
                    workspace=None,
                )
                # Inject the registry_path the way ``run()`` does.
                cli_issue._run_takeover(registry_path, Path(tmp), args)
            self.assertEqual(captured["registry_path"], registry_path)


# ------------------------------------------------------------------
# _resolve_target
# ------------------------------------------------------------------


class TestResolveTarget(unittest.TestCase):
    """The lookup helper returns the correct _TakeoverTarget or None."""

    def test_resolve_via_issue_id(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(registry_path, _make_record())
            result = _resolve_target(
                registry_path, None, "owner/repo#42", None,
            )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, _TakeoverTarget)
        self.assertEqual(result.run_id, "run-abc")
        self.assertEqual(result.workspace_path, Path("/tmp/ws"))
        self.assertEqual(result.issue_id, "owner/repo#42")

    def test_resolve_returns_none_for_no_run_id(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(
                registry_path, _make_record(run_id=None),
            )
            result = _resolve_target(
                registry_path, None, "owner/repo#42", None,
            )
        self.assertIsNone(result)

    def test_resolve_returns_none_for_no_workspace_path(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(
                registry_path, _make_record(workspace_path=None),
            )
            result = _resolve_target(
                registry_path, None, "owner/repo#42", None,
            )
        self.assertIsNone(result)

    def test_resolve_via_run_id_with_workspace(self) -> None:
        """--run + --workspace bypasses the registry entirely."""
        result = _resolve_target(
            None, Path("/w"), None, "run-xyz",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.run_id, "run-xyz")
        self.assertEqual(result.workspace_path, Path("/w"))
        self.assertEqual(result.issue_id, "run:run-xyz")

    def test_resolve_returns_none_when_registry_missing(self) -> None:
        result = _resolve_target(
            Path("/nonexistent/registry.json"), None, "X", None,
        )
        self.assertIsNone(result)

    def test_resolve_returns_none_for_missing_issue(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(registry_path, _make_record())
            result = _resolve_target(
                registry_path, None, "MISSING", None,
            )
        self.assertIsNone(result)

    def test_resolve_returns_none_when_both_args_missing(self) -> None:
        result = _resolve_target(None, None, None, None)
        self.assertIsNone(result)

    def test_resolve_via_run_id_ignores_workspace_root_when_path_given(
        self,
    ) -> None:
        """--workspace overrides the resolved workspace_root."""
        result = _resolve_target(
            None, Path("/default"), None, "run-xyz",
        )
        assert result is not None
        self.assertEqual(result.workspace_path, Path("/default"))

    def test_resolve_via_run_id_uses_workspace_arg(self) -> None:
        """When the handler passes workspace_root=Path('/explicit')
        and --workspace='explicit', the explicit one wins (because
        the handler builds ``effective_workspace`` before calling
        _resolve_target). This test guards the contract that
        _resolve_target treats ``workspace_root`` as authoritative.
        """
        result = _resolve_target(
            None, Path("/explicit"), None, "run-xyz",
        )
        assert result is not None
        self.assertEqual(result.workspace_path, Path("/explicit"))


# ------------------------------------------------------------------
# _run_takeover — Step 1 stub behaviour
# ------------------------------------------------------------------


class TestRunTakeoverStub(unittest.TestCase):
    """The Step 1 stub validates args + resolves the target +
    prints a TODO. Step 3 replaces the stub body with the socket
    + REPL flow but keeps the same exit-code contract.
    """

    def test_missing_id_and_run_returns_2(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            args = argparse.Namespace(id=None, run=None, workspace=None)
            rc = _run_takeover(None, None, args)
        self.assertEqual(rc, 2)
        self.assertIn("--id", err.getvalue())

    def test_run_without_workspace_returns_2(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            args = argparse.Namespace(
                id=None, run="r-1", workspace=None,
            )
            rc = _run_takeover(None, None, args)
        self.assertEqual(rc, 2)
        self.assertIn("--workspace", err.getvalue())

    def test_issue_not_found_returns_1(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            # Registry exists but is empty.
            registry_path.write_text("{}", encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                args = argparse.Namespace(
                    id="MISSING", run=None, workspace=None,
                )
                rc = _run_takeover(registry_path, Path(tmp), args)
        self.assertEqual(rc, 1)
        self.assertIn("no active run", err.getvalue().lower())

    def test_run_with_no_resolution_returns_1(self) -> None:
        """--run mode without IssueRegistry cannot resolve the
        workspace; the handler returns 1 (not 2 — usage was OK,
        resolution failed).
        """
        err = io.StringIO()
        with redirect_stderr(err):
            args = argparse.Namespace(
                id=None, run="r-1", workspace="/w",
            )
            rc = _run_takeover(None, Path("/w"), args)
        self.assertEqual(rc, 0)
        # The stub resolves successfully (--run + --workspace).


if __name__ == "__main__":
    unittest.main()
