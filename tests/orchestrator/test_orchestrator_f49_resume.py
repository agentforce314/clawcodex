"""F-49 Phase 3 tests for the ``issue resume-session`` CLI subcommand.

Covers:
  * ``argparse`` registration: ``clawcodex issue resume-session --id X``
    parses into ``args.issue_subcommand == "resume-session"``.
  * ``run()`` dispatcher routes ``resume-session`` to
    ``_run_resume_session``.
  * ``_resolve_run_id`` returns the run_id from IssueRegistry or
    ``None`` for missing inputs.
  * End-to-end: orchestrator writes a JSONL transcript via
    ``SessionStorage``; ``_run_resume_session`` reads it via
    ``resume_session()`` and prints a summary containing the
    metadata + last turns.
  * ``_render_summary`` formats text/tool_use/tool_result blocks
    correctly.

Uses ``unittest.IsolatedAsyncioTestCase`` (the repo's canonical
async pattern, per ``test_orchestrator_f49_control_socket.py``) and
``tempfile.TemporaryDirectory`` for both registry + SessionStorage
isolation. Patches ``src.services.session_storage.SESSIONS_DIR`` to
the tmp dir so the test does not pollute the user's real
``~/.clawcodex/sessions``.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from extensions.orchestrator.cli.resume_session import (
    _render_summary,
    _resolve_run_id,
    _run_resume_session,
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
    ``IssueRegistry._save`` for the canonical shape.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {record.issue_id: asdict(record)}
    # IssueRegistry stores ``status`` as the enum value; serialise
    # as its ``.value`` so the loader's ``IssueStatus(v)`` round-trip
    # succeeds.
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
        branch_name="f-49-test",
        base_branch="main",
        workspace_path=workspace_path,
        workspace_strategy="worktree",
        run_id=run_id,
    )


# ------------------------------------------------------------------
# Parser + dispatcher
# ------------------------------------------------------------------


class TestResumeSessionParser(unittest.TestCase):
    """The new subcommand is registered in the issue subparser."""

    def test_resume_session_parser_registered(self) -> None:
        from extensions.orchestrator.cli.issue import add_issue_parser

        parent = argparse.ArgumentParser()
        sub = parent.add_subparsers(dest="top")
        add_issue_parser(sub)
        args = parent.parse_args(["issue", "resume-session", "--id", "X"])
        self.assertEqual(args.issue_subcommand, "resume-session")
        self.assertEqual(args.id, "X")
        self.assertIsNone(args.run)

    def test_resume_session_parser_accepts_run(self) -> None:
        from extensions.orchestrator.cli.issue import add_issue_parser

        parent = argparse.ArgumentParser()
        sub = parent.add_subparsers(dest="top")
        add_issue_parser(sub)
        args = parent.parse_args(["issue", "resume-session", "--run", "r-1"])
        self.assertEqual(args.issue_subcommand, "resume-session")
        self.assertIsNone(args.id)
        self.assertEqual(args.run, "r-1")


class TestResumeSessionDispatch(unittest.IsolatedAsyncioTestCase):
    """The ``run()`` dispatcher routes to ``_run_resume_session``."""

    async def test_dispatch_to_run_resume_session(self) -> None:
        from extensions.orchestrator import cli as cli_mod
        from extensions.orchestrator.cli import issue as cli_issue

        captured: dict = {}

        def fake(registry_path, args) -> int:
            captured["called"] = True
            captured["id"] = getattr(args, "id", None)
            return 0

        with patch.object(cli_issue, "_run_resume_session", side_effect=fake):
            args = argparse.Namespace(
                issue_subcommand="resume-session",
                id="X",
                run=None,
            )
            rc = cli_issue.run(args)
        self.assertEqual(rc, 0)
        self.assertTrue(captured.get("called"))
        self.assertEqual(captured.get("id"), "X")


# ------------------------------------------------------------------
# _resolve_run_id
# ------------------------------------------------------------------


class TestResolveRunId(unittest.TestCase):
    """The lookup helper returns the correct (run_id, label) pair."""

    def test_resolve_returns_none_when_both_missing(self) -> None:
        self.assertIsNone(_resolve_run_id(None, None, None))

    def test_resolve_via_issue_id(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(registry_path, _make_record())
            result = _resolve_run_id(registry_path, "owner/repo#42", None)
        self.assertIsNotNone(result)
        assert result is not None
        run_id, label = result
        self.assertEqual(run_id, "run-abc")
        self.assertEqual(label, "owner/repo#42")

    def test_resolve_returns_none_for_missing_issue(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(registry_path, _make_record())
            result = _resolve_run_id(registry_path, "MISSING", None)
        self.assertIsNone(result)

    def test_resolve_returns_none_for_no_run_id_on_record(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(
                registry_path, _make_record(run_id=None),
            )
            result = _resolve_run_id(registry_path, "owner/repo#42", None)
        self.assertIsNone(result)

    def test_resolve_via_run_id(self) -> None:
        # --run mode: registry is not consulted, label is synthetic.
        result = _resolve_run_id(None, None, "run-xyz")
        self.assertIsNotNone(result)
        assert result is not None
        run_id, label = result
        self.assertEqual(run_id, "run-xyz")
        self.assertEqual(label, "run:run-xyz")

    def test_resolve_returns_none_when_registry_missing(self) -> None:
        result = _resolve_run_id(
            Path("/nonexistent/registry.json"), "X", None,
        )
        self.assertIsNone(result)


# ------------------------------------------------------------------
# _render_summary
# ------------------------------------------------------------------


class _StubMessage:
    """Minimal stand-in for src.types.messages.Message."""

    def __init__(self, role: str, content) -> None:
        self.role = role
        self.content = content
        self.isCompactSummary = False


class _StubBlock:
    def __init__(self, type_: str, **kwargs) -> None:
        self.type = type_
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestRenderSummary(unittest.TestCase):
    def test_renders_metadata_and_message_count(self) -> None:
        md = type(
            "MD", (), {
                "model": "claude-sonnet-4-20250514",
                "cwd": "/work",
                "title": "orchestrator-X",
                "message_count": 5,
            },
        )()
        result = type(
            "R", (), {
                "messages": [], "metadata": md, "warnings": [],
                "success": True, "message_count": 0, "has_warnings": False,
            },
        )()
        text = _render_summary("ISSUE-1", result)
        self.assertIn("Resumed session for ISSUE-1", text)
        self.assertIn("claude-sonnet-4-20250514", text)
        self.assertIn("/work", text)
        self.assertIn("rehydrated messages: 0", text)

    def test_renders_text_block_snippets(self) -> None:
        msgs = [
            _StubMessage("user", [
                _StubBlock("text", text="  hello world  "),
            ]),
            _StubMessage("assistant", [
                _StubBlock("text", text="thinking..."),
            ]),
        ]
        result = type(
            "R", (), {
                "messages": msgs, "metadata": None, "warnings": [],
                "success": True, "message_count": 2, "has_warnings": False,
            },
        )()
        text = _render_summary("X", result)
        self.assertIn("[user] hello world", text)
        self.assertIn("[assistant] thinking...", text)

    def test_renders_tool_use_and_tool_result_blocks(self) -> None:
        msgs = [
            _StubMessage("assistant", [
                _StubBlock("tool_use", name="Read", id="t1"),
            ]),
            _StubMessage("user", [
                _StubBlock("tool_result", tool_use_id="t1"),
            ]),
        ]
        result = type(
            "R", (), {
                "messages": msgs, "metadata": None, "warnings": [],
                "success": True, "message_count": 2, "has_warnings": False,
            },
        )()
        text = _render_summary("X", result)
        self.assertIn("[tool:Read]", text)
        self.assertIn("[tool_result]", text)

    def test_truncates_long_text_snippet(self) -> None:
        long_text = "x" * 200
        msgs = [
            _StubMessage("user", [
                _StubBlock("text", text=long_text),
            ]),
        ]
        result = type(
            "R", (), {
                "messages": msgs, "metadata": None, "warnings": [],
                "success": True, "message_count": 1, "has_warnings": False,
            },
        )()
        text = _render_summary("X", result)
        # 117 chars + "..." + prefix
        self.assertIn("...", text)
        # Sanity: the full 200-x string is NOT in the output.
        self.assertNotIn(long_text, text)

    def test_includes_warnings(self) -> None:
        result = type(
            "R", (), {
                "messages": [], "metadata": None,
                "warnings": ["orphan tool_use fixed"],
                "success": True, "message_count": 0, "has_warnings": True,
            },
        )()
        text = _render_summary("X", result)
        self.assertIn("warnings:", text)
        self.assertIn("orphan tool_use fixed", text)


# ------------------------------------------------------------------
# End-to-end: orchestrator writes a transcript, resume-session reads it
# ------------------------------------------------------------------


class TestResumeSessionEndToEnd(unittest.IsolatedAsyncioTestCase):
    """Simulate: orchestrator writes JSONL → resume-session reads it.

    The orchestrator's ``SessionStorage`` is keyed by ``run_id`` (the
    same identifier stored in ``IssueRecord.run_id``). We patch
    ``SESSIONS_DIR`` so the test does not touch the real
    ``~/.clawcodex/sessions`` tree.
    """

    def _build_storage(self, sessions_dir: Path, run_id: str):
        from src.services.session_storage import SessionStorage

        storage = SessionStorage(
            session_id=run_id, sessions_dir=sessions_dir,
        )
        storage.init_metadata(
            model="claude-sonnet-4-20250514",
            cwd="/work",
            title=f"orchestrator-test",
        )
        return storage

    def _write_user_prompt(self, storage, text: str) -> None:
        from src.services.session_storage import SessionStorage

        # Use write_raw to match the orchestrator's path: it stores
        # conversation messages as raw dicts (not typed Messages).
        from src.types.messages import UserMessage, message_to_dict

        msg = UserMessage(content=[{"type": "text", "text": text}])
        storage.write_raw(message_to_dict(msg))
        storage.flush()

    def _write_assistant_turn(
        self, storage, text: str, tool_name: str | None = None,
    ) -> None:
        from src.services.session_storage import SessionStorage
        from src.types.messages import AssistantMessage, message_to_dict

        content: list[dict] = [{"type": "text", "text": text}]
        if tool_name is not None:
            content.append(
                {"type": "tool_use", "id": "tool-1", "name": tool_name,
                 "input": {"path": "/x.py"}},
            )
        msg = AssistantMessage(content=content, model="claude-sonnet-4-20250514")
        storage.write_raw(message_to_dict(msg))
        storage.flush()

    async def test_writes_then_resumes_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sessions_dir = tmp_path / "sessions"
            registry_path = tmp_path / "registry.json"

            run_id = "run-roundtrip"
            _write_registry(
                registry_path, _make_record(run_id=run_id),
            )

            # Orchestrator writes a few messages.
            storage = self._build_storage(sessions_dir, run_id)
            self._write_user_prompt(storage, "fix the bug")
            self._write_assistant_turn(
                storage, "I'll read the file first.", tool_name="Read",
            )

            from src.services.session_storage import SESSIONS_DIR as _real

            # Patch the singleton SESSIONS_DIR so resume_session()
            # reads from our tmp dir.
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_dir,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    args = argparse.Namespace(
                        id="owner/repo#42", run=None,
                    )
                    rc = _run_resume_session(registry_path, args)
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("Resumed session for owner/repo#42", out)
            self.assertIn("claude-sonnet-4-20250514", out)
            # last 2 messages rendered
            self.assertIn("[tool:Read]", out)

    async def test_missing_run_returns_1(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            registry_path = tmp_path / "registry.json"
            _write_registry(registry_path, _make_record(run_id=None))

            err = io.StringIO()
            with redirect_stdout(err):
                with redirect_stdout(sys.stderr) if False else \
                        __import__("contextlib").nullcontext():
                    pass
            from contextlib import redirect_stderr
            with redirect_stderr(err):
                args = argparse.Namespace(id="owner/repo#42", run=None)
                rc = _run_resume_session(registry_path, args)
            self.assertEqual(rc, 1)
            self.assertIn("nothing to resume", err.getvalue().lower())

    async def test_usage_error_when_no_args(self) -> None:
        from contextlib import redirect_stderr

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            registry_path = tmp_path / "registry.json"
            err = io.StringIO()
            with redirect_stderr(err):
                args = argparse.Namespace(id=None, run=None)
                rc = _run_resume_session(registry_path, args)
            self.assertEqual(rc, 2)
            self.assertIn("--id", err.getvalue())


if __name__ == "__main__":
    unittest.main()
