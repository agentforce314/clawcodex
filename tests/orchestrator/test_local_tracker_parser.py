"""Regression coverage for the local issue frontmatter parser.

Background
----------
The F-40 dispatch bug surfaced when a hand-authored issue at
``/tmp/clawcodex-issues/001-f40-progress-sink.md`` used ``--`` (two
dashes) instead of the canonical ``---`` as its frontmatter delimiter.
``LocalTrackerAdapter.fetch_candidate_issues`` returned zero
candidates because the parser silently dropped the whole frontmatter
block, leaving ``state`` and ``priority`` as ``None`` so the
``active_states`` filter rejected the issue.

These tests pin the parser to tolerate 2+ dashes and leading blank
lines, and to log a warning when no frontmatter is found at all.
"""

from __future__ import annotations

import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from extensions.orchestrator.local_tracker.parser import (
    _split_frontmatter,
    parse_markdown_issue,
    write_markdown_frontmatter,
)


class SplitFrontmatterTests(unittest.TestCase):
    """Direct coverage for the tolerant delimiter / blank-line handling."""

    def test_canonical_three_dash_delimiter(self) -> None:
        text = "---\nid: F-1\nstate: open\n---\nbody\n"
        metadata, body = _split_frontmatter(text)
        self.assertEqual(metadata, {"id": "F-1", "state": "open"})
        self.assertEqual(body, "body\n")

    def test_two_dash_typo_is_accepted(self) -> None:
        # The exact F-40 hand-authored shape.
        text = "\n--\nid: F-40-progress-sink\nstate: open\npriority: 1\n---\n# Title\n"
        metadata, body = _split_frontmatter(text)
        self.assertEqual(
            metadata,
            {"id": "F-40-progress-sink", "state": "open", "priority": 1},
        )
        self.assertTrue(body.startswith("# Title"))

    def test_four_dash_delimiter_accepted(self) -> None:
        text = "----\nid: F-2\n----\nbody\n"
        metadata, body = _split_frontmatter(text)
        self.assertEqual(metadata, {"id": "F-2"})
        self.assertEqual(body, "body\n")

    def test_leading_blank_line_tolerated(self) -> None:
        text = "\n\n---\nid: F-3\nstate: ready\n---\nbody\n"
        metadata, body = _split_frontmatter(text)
        self.assertEqual(metadata, {"id": "F-3", "state": "ready"})
        self.assertEqual(body, "body\n")

    def test_no_frontmatter_returns_empty_metadata(self) -> None:
        text = "no frontmatter here\njust body\n"
        metadata, body = _split_frontmatter(text)
        self.assertEqual(metadata, {})
        self.assertEqual(body, text)

    def test_unterminated_frontmatter_returns_empty_metadata(self) -> None:
        text = "---\nid: F-4\nstate: open\n"
        metadata, body = _split_frontmatter(text)
        self.assertEqual(metadata, {})
        self.assertEqual(body, text)

    def test_missing_frontmatter_logs_warning(self) -> None:
        with self.assertLogs("extensions.orchestrator.local_tracker.parser", level="WARNING") as captured:
            _split_frontmatter("no frontmatter at all\n")
        self.assertTrue(any("no YAML frontmatter" in line for line in captured.output))


class ParseMarkdownIssueTests(unittest.TestCase):
    """End-to-end coverage for ``parse_markdown_issue``."""

    def test_two_dash_delimiter_yields_active_candidate(self) -> None:
        # Mirrors the F-40 file shape: leading newline, ``--`` delimiter,
        # state and priority present, body starts with a ``# Title`` heading.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "001-f40-progress-sink.md"
            path.write_text(
                "\n--\n"
                "id: F-40-progress-sink\n"
                "identifier: F-40\n"
                "title: ProgressReporter Sink 协议重构\n"
                "state: open\n"
                "priority: 1\n"
                "branch_name: dev-decoupling-refactor-58ea488\n"
                "base_branch: dev-decoupling-refactor-58ea488\n"
                "---\n"
                "# Body\n",
                encoding="utf-8",
            )

            document = parse_markdown_issue(path)
            self.assertEqual(document.issue.id, "F-40-progress-sink")
            self.assertEqual(document.issue.identifier, "F-40")
            self.assertEqual(document.issue.state, "open")
            self.assertEqual(document.issue.priority, 1)
            self.assertEqual(
                document.issue.branch_name, "dev-decoupling-refactor-58ea488"
            )
            self.assertEqual(
                document.metadata.get("base_branch"),
                "dev-decoupling-refactor-58ea488",
            )

    def test_no_frontmatter_uses_path_stem_and_no_state(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "001-foo.md"
            path.write_text("just a body, no frontmatter\n", encoding="utf-8")
            document = parse_markdown_issue(path)
            # No frontmatter ⇒ id falls back to path.stem and state/priority
            # are None. ``LocalTrackerAdapter.fetch_candidate_issues``
            # rejects such issues via the active_states filter, which is
            # why the F-40 issue was silently dropped from the dispatch
            # queue.
            self.assertEqual(document.issue.id, "001-foo")
            self.assertIsNone(document.issue.state)
            self.assertIsNone(document.issue.priority)


class WriteMarkdownFrontmatterTests(unittest.TestCase):
    """``write_markdown_frontmatter`` always emits the canonical 3-dash form."""

    def test_write_uses_three_dash_delimiter_even_when_input_was_two(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "issue.md"
            path.write_text(
                "\n--\nid: F-X\nstate: open\n---\nbody\n",
                encoding="utf-8",
            )
            write_markdown_frontmatter(path, {"state": "completed"})
            text = path.read_text(encoding="utf-8")
            # Both opening and closing fences must be the canonical 3-dash form.
            self.assertTrue(text.startswith("---\n"))
            self.assertTrue("\n---\nbody" in text)


if __name__ == "__main__":
    unittest.main()
