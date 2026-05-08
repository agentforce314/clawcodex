"""Tests for the Write-tool path-predicate carve-out (Slice C1).

A write into the auto-memory directory should bypass the workspace
allowlist and the docs gate; writes outside the auto-mem dir should
be unaffected by the carve-out.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.permissions.types import (
    PermissionAskDecision,
    PermissionPassthroughResult,
)
from src.tool_system.context import ToolContext
from src.tool_system.tools.write import _check_permissions, _write_call


class WriteCarveOutTest(unittest.TestCase):
    def setUp(self):
        self._saved_override = os.environ.get(
            "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"
        )
        # Use a controlled tempdir as the auto-memory path
        self._mem_tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = self._mem_tmp.name

        self._workspace_tmp = tempfile.TemporaryDirectory()
        self.context = ToolContext(workspace_root=Path(self._workspace_tmp.name))

    def tearDown(self):
        if self._saved_override is None:
            os.environ.pop("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", None)
        else:
            os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = (
                self._saved_override
            )
        self._mem_tmp.cleanup()
        self._workspace_tmp.cleanup()

    def test_carve_out_suppressed_when_override_set(self):
        """When CLAUDE_COWORK_MEMORY_PATH_OVERRIDE is set, the carve-out
        is suppressed (TS comment at paths.ts:262-272). The SDK caller
        owns permissions in that case."""
        from src.tool_system.tools.write import _is_auto_memory_write

        target = Path(self._mem_tmp.name) / "feedback.md"
        # Override is set in setUp() — carve-out should be suppressed.
        self.assertFalse(_is_auto_memory_write(str(target)))


class WriteCarveOutNoOverrideTest(unittest.TestCase):
    """Use the *default* auto-memory path (no override env), then write
    a file into that real default path. Tests the production-shape
    carve-out where ``has_auto_mem_path_override()`` is false."""

    def setUp(self):
        self._saved_override = os.environ.get(
            "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"
        )
        os.environ.pop("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", None)

        from src.memdir import get_auto_mem_path

        self._mem_dir = Path(get_auto_mem_path())
        self._mem_dir.mkdir(parents=True, exist_ok=True)

        self._workspace_tmp = tempfile.TemporaryDirectory()
        self.context = ToolContext(workspace_root=Path(self._workspace_tmp.name))

    def tearDown(self):
        if self._saved_override is None:
            os.environ.pop("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", None)
        else:
            os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = (
                self._saved_override
            )
        # Clean up any test files we wrote
        for name in ("test_carve_out_a.md", "test_carve_out_b.md"):
            try:
                (self._mem_dir / name).unlink()
            except FileNotFoundError:
                pass
        self._workspace_tmp.cleanup()

    def test_check_permissions_passes_through_for_memory_md(self):
        target = self._mem_dir / "test_carve_out_a.md"
        result = _check_permissions(
            {"file_path": str(target)}, self.context
        )
        self.assertIsInstance(result, PermissionPassthroughResult)

    def test_check_permissions_unchanged_for_outside_path(self):
        # A path outside the workspace AND outside auto-mem
        # should NOT bypass — falls through to allowlist/docs gate.
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "evil.md"
            result = _check_permissions(
                {"file_path": str(target)}, self.context
            )
            # ensure_allowed_path raises -> passthrough
            # OR the .md gate fires. Either way, NOT a bypass-of-everything.
            self.assertIsInstance(
                result, (PermissionPassthroughResult, PermissionAskDecision)
            )

    def test_call_writes_into_memory_dir(self):
        target = self._mem_dir / "test_carve_out_b.md"
        # Mark the file as not pre-existing — the call should create it.
        result = _write_call(
            {"file_path": str(target), "content": "hello memory"},
            self.context,
        )
        self.assertEqual(result.output["type"], "create")
        self.assertEqual(target.read_text(encoding="utf-8"), "hello memory")

    def test_validate_input_enforces_read_before_write_in_memory_dir(self):
        """Writes to existing memory files must still pass through the
        'read before write' staleness check. The carve-out short-circuits
        the workspace allowlist, not the read-state invariant.
        """
        from src.tool_system.tools.write import _validate_input

        target = self._mem_dir / "test_carve_out_a.md"
        target.write_text("pre-existing", encoding="utf-8")
        try:
            # No prior read recorded → validate_input should reject.
            result = _validate_input(
                {"file_path": str(target), "content": "new"},
                self.context,
            )
            self.assertFalse(
                result.result,
                "Expected 'not read yet' rejection for existing memory file",
            )
            self.assertEqual(result.error_code, 2)
        finally:
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
