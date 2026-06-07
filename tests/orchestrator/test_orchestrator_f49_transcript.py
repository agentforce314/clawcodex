"""F-49 Phase 0.2: tests for the ``issue transcript`` CLI subcommand.

The transcript subcommand reads ``~/.clawcodex/sessions/{run_id}/transcript.jsonl``
(same unified storage as the headless AgentRunner) and prints a
human-readable, filterable conversation history.  It is a pure read
operation — no agent interaction, no event stream — designed for
operator review and shell piping.

These tests cover:
  * Basic read of all messages
  * Filtering by role (user / assistant)
  * Filtering by tool_use_id (matches both the tool_use and tool_result)
  * Limit on number of messages
  * Error path: missing run_id, missing transcript file
  * Block-content rendering (text / tool_use / tool_result)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from extensions.orchestrator.cli.issue import (
    _msg_references_tool,
    _print_message,
    _run_transcript,
)
from extensions.orchestrator.issue_registry import IssueRegistry


def _make_msg(role: str, content, origin: str | None = None) -> dict:
    """Build a Message dict for tests."""
    msg = {"role": role, "content": content}
    if origin is not None:
        msg["origin"] = origin
    return msg


def _write_transcript(session_dir: Path, msgs: list[dict]) -> Path:
    """Write a transcript.jsonl with the given messages."""
    session_dir.mkdir(parents=True, exist_ok=True)
    transcript = session_dir / "transcript.jsonl"
    with open(transcript, "w", encoding="utf-8") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    return transcript


def _seed_registry(tmp: Path, identifier: str, run_id: str) -> Path:
    """Create a registry file mapping an issue to a run_id."""
    registry = IssueRegistry(tmp / "registry.json")
    record = registry.register(
        issue_id=identifier,
        issue_identifier=identifier,
    )
    record.run_id = run_id
    registry._save()
    return tmp / "registry.json"


class TestMsgReferencesTool(unittest.TestCase):
    """Unit tests for the _msg_references_tool filter helper."""

    def test_tool_use_match(self) -> None:
        msg = _make_msg("assistant", [
            {"type": "tool_use", "id": "A", "name": "Read", "input": {}},
        ])
        self.assertTrue(_msg_references_tool(msg, "A"))

    def test_tool_result_match(self) -> None:
        msg = _make_msg("user", [
            {"type": "tool_result", "tool_use_id": "A", "content": "ok",
             "is_error": False},
        ], origin="tool_result")
        self.assertTrue(_msg_references_tool(msg, "A"))

    def test_no_match(self) -> None:
        msg = _make_msg("assistant", [
            {"type": "tool_use", "id": "B", "name": "Read", "input": {}},
        ])
        self.assertFalse(_msg_references_tool(msg, "A"))

    def test_string_content_no_match(self) -> None:
        msg = _make_msg("user", "hello")
        self.assertFalse(_msg_references_tool(msg, "A"))


class TestPrintMessage(unittest.TestCase):
    """Unit tests for _print_message block rendering."""

    def test_assistant_text_and_tool_use(self) -> None:
        msg = _make_msg("assistant", [
            {"type": "text", "text": "Looking..."},
            {"type": "tool_use", "id": "A", "name": "Read",
             "input": {"path": "/tmp/a.py"}},
        ])
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_message(msg)
        out = buf.getvalue()
        self.assertIn("## assistant", out)
        self.assertIn("Text: Looking...", out)
        self.assertIn("Tool Use: Read (id=A)", out)
        self.assertIn("path: /tmp/a.py", out)

    def test_tool_result_with_error(self) -> None:
        msg = _make_msg("user", [
            {"type": "tool_result", "tool_use_id": "A", "content": "boom",
             "is_error": True},
        ], origin="tool_result")
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_message(msg)
        out = buf.getvalue()
        self.assertIn("## user (origin=tool_result)", out)
        self.assertIn("Tool Result: A [ERROR]", out)
        self.assertIn("boom", out)

    def test_string_content(self) -> None:
        msg = _make_msg("user", "hello world")
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_message(msg)
        out = buf.getvalue()
        self.assertIn("## user", out)
        self.assertIn("Text: hello world", out)


class TestRunTranscript(unittest.TestCase):
    """Integration tests for the _run_transcript subcommand."""

    def _build_args(
        self, identifier: str | None = None, run_id: str | None = None,
        role: str | None = None, tool_use_id: str | None = None,
        limit: int | None = None,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            id=identifier,
            run=run_id,
            issue_id=None,
            run_id=None,
            role=role,
            tool_use_id=tool_use_id,
            limit=limit,
        )

    def test_read_all_messages(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sessions_root = tmp_path / "sessions"
            run_id = "run-1"
            _write_transcript(sessions_root / run_id, [
                _make_msg("user", [{"type": "text", "text": "init prompt"}]),
                _make_msg("assistant", [
                    {"type": "text", "text": "Looking..."},
                    {"type": "tool_use", "id": "A", "name": "Read",
                     "input": {"path": "/x"}},
                ]),
                _make_msg("user", [
                    {"type": "tool_result", "tool_use_id": "A",
                     "content": "ok", "is_error": False},
                ], origin="tool_result"),
            ])
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_root,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = _run_transcript(
                        None,
                        self._build_args(run_id=run_id),
                    )
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("## user", out)
        self.assertIn("## assistant", out)
        self.assertIn("Tool Use: Read (id=A)", out)
        self.assertIn("3 message(s) shown", out)

    def test_filter_by_role_assistant(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sessions_root = tmp_path / "sessions"
            run_id = "run-1"
            _write_transcript(sessions_root / run_id, [
                _make_msg("user", [{"type": "text", "text": "p1"}]),
                _make_msg("assistant", [
                    {"type": "text", "text": "a1"},
                ]),
                _make_msg("user", [{"type": "text", "text": "p2"}]),
            ])
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_root,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = _run_transcript(
                        None,
                        self._build_args(run_id=run_id, role="assistant"),
                    )
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("a1", out)
        self.assertNotIn("Text: p1", out)
        self.assertNotIn("Text: p2", out)
        self.assertIn("1 message(s) shown", out)

    def test_filter_by_tool_use_id(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sessions_root = tmp_path / "sessions"
            run_id = "run-1"
            _write_transcript(sessions_root / run_id, [
                _make_msg("user", [{"type": "text", "text": "prompt"}]),
                _make_msg("assistant", [
                    {"type": "tool_use", "id": "A", "name": "Read",
                     "input": {}},
                    {"type": "tool_use", "id": "B", "name": "Bash",
                     "input": {}},
                ]),
                _make_msg("user", [
                    {"type": "tool_result", "tool_use_id": "A",
                     "content": "read output", "is_error": False},
                ], origin="tool_result"),
                _make_msg("user", [
                    {"type": "tool_result", "tool_use_id": "B",
                     "content": "bash output", "is_error": False},
                ], origin="tool_result"),
            ])
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_root,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = _run_transcript(
                        None,
                        self._build_args(
                            run_id=run_id, tool_use_id="A",
                        ),
                    )
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        # Should include the assistant tool_use(A) and user tool_result(A)
        self.assertIn("Tool Use: Read (id=A)", out)
        self.assertIn("read output", out)
        # Should NOT include the B pair
        self.assertNotIn("Tool Use: Bash (id=B)", out)
        self.assertNotIn("bash output", out)

    def test_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sessions_root = tmp_path / "sessions"
            run_id = "run-1"
            _write_transcript(sessions_root / run_id, [
                _make_msg("user", [{"type": "text", "text": "p1"}]),
                _make_msg("assistant", [
                    {"type": "text", "text": "a1"},
                ]),
                _make_msg("user", [{"type": "text", "text": "p2"}]),
                _make_msg("assistant", [
                    {"type": "text", "text": "a2"},
                ]),
            ])
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_root,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = _run_transcript(
                        None,
                        self._build_args(run_id=run_id, limit=2),
                    )
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("p1", out)
        self.assertIn("a1", out)
        self.assertNotIn("p2", out)
        self.assertNotIn("a2", out)
        self.assertIn("2 message(s) shown", out)

    def test_no_id_no_run_returns_error(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = _run_transcript(
                None,
                self._build_args(),
            )
        # --id and --run both missing → exit code 2
        self.assertEqual(rc, 2)

    def test_missing_transcript_returns_error(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sessions_root = tmp_path / "sessions"
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_root,
            ):
                rc = _run_transcript(
                    None,
                    self._build_args(run_id="nonexistent"),
                )
        self.assertEqual(rc, 1)

    def test_resolve_via_registry(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sessions_root = tmp_path / "sessions"
            run_id = "run-XYZ"
            identifier = "ISSUE-42"
            _write_transcript(sessions_root / run_id, [
                _make_msg("user", [{"type": "text", "text": "hi"}]),
            ])
            registry_path = _seed_registry(
                tmp_path, identifier, run_id,
            )
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_root,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = _run_transcript(
                        registry_path,
                        self._build_args(identifier=identifier),
                    )
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("Text: hi", out)
        self.assertIn(f"# (issue {identifier})", out)
