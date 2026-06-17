"""Read-permission parity with TS ``checkReadPermissionForTool``.

The Python Read tool previously shipped no ``check_permissions`` and gated reads
purely by permission mode (``default`` asked for *every* read, even in-workspace;
``auto``/``bypass`` allowed everything). TS instead allows working-dir + internal-
harness reads silently and only asks for paths outside the workspace. These tests
pin the ported behavior, including the reported bug: reading back the runtime's
own spilled tool result under ``/tmp/claw_codex_budget/<pid>/`` must not prompt
(and must not raise at execution time).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.permissions.check import has_permissions_to_use_tool
from src.permissions.filesystem import (
    check_read_permission_for_tool,
    check_readable_internal_path,
)
from src.permissions.types import ToolPermissionContext
from src.memdir.paths import get_auto_mem_path
from src.services.compact.tool_result_budget import (
    TOOL_RESULT_BUDGET_ROOT,
    get_tool_result_budget_dir,
)
from src.tool_system.context import ToolContext, ToolPermissionError
from src.tool_system.tools.read import ReadTool


def _ctx(mode: str = "default", workspace: str | None = None) -> ToolContext:
    ws = Path(workspace or os.getcwd()).resolve()
    return ToolContext(
        workspace_root=ws,
        permission_context=ToolPermissionContext(mode=mode),
    )


def _read_behavior(ctx: ToolContext, file_path: str) -> str:
    decision = has_permissions_to_use_tool(
        ReadTool, {"file_path": file_path}, ctx.permission_context,
        tool_use_context=ctx,
    )
    return decision.behavior


class TestBudgetDirHelper(unittest.TestCase):
    def test_root_is_shared_tmp_location(self) -> None:
        self.assertEqual(TOOL_RESULT_BUDGET_ROOT, Path("/tmp/claw_codex_budget"))

    def test_dir_is_process_scoped(self) -> None:
        self.assertEqual(
            get_tool_result_budget_dir(),
            TOOL_RESULT_BUDGET_ROOT / str(os.getpid()),
        )

    def test_apply_budget_default_uses_helper(self) -> None:
        # The compaction writer must default to the same dir the read allowlist
        # trusts, or offloaded results become unreadable.
        from src.services.compact import tool_result_budget as trb

        msgs, saved = trb.apply_tool_result_budget([])
        self.assertEqual(msgs, [])
        self.assertEqual(saved, 0)


class TestCheckReadableInternalPath(unittest.TestCase):
    def test_budget_spill_is_internal(self) -> None:
        p = str(get_tool_result_budget_dir() / "result_abc.txt")
        self.assertTrue(check_readable_internal_path(p, _ctx()))

    def test_scratchpad_is_internal(self) -> None:
        tmp = os.environ.get("TMPDIR", "/tmp")
        p = str(Path(tmp) / "claude-scratchpad" / "note.txt")
        self.assertTrue(check_readable_internal_path(p, _ctx()))

    def test_tool_results_dir_is_internal(self) -> None:
        ctx = _ctx()
        # allowed_roots() folds in the tool-results spill dir as its last entry.
        p = str(ctx.allowed_roots()[-1] / "x.txt")
        self.assertTrue(check_readable_internal_path(p, ctx))

    def test_arbitrary_path_is_not_internal(self) -> None:
        self.assertFalse(check_readable_internal_path("/etc/hosts", _ctx()))

    def test_empty_path_is_not_internal(self) -> None:
        self.assertFalse(check_readable_internal_path("", _ctx()))

    def test_cross_process_budget_dir_is_not_trusted(self) -> None:
        # Another process's spill under the shared root must NOT be allowlisted.
        other = str(TOOL_RESULT_BUDGET_ROOT / "999999999" / "evil.txt")
        self.assertFalse(check_readable_internal_path(other, _ctx()))

    def test_memdir_is_internal(self) -> None:
        # Positive: the auto-memory subtree IS internal (guards under-allow).
        p = str(Path(get_auto_mem_path()) / "MEMORY.md")
        self.assertTrue(check_readable_internal_path(p, _ctx()))

    def test_claude_credentials_not_internal(self) -> None:
        # SECURITY regression guard: memdir must be the narrow projects/<slug>/
        # memory/ subtree, NOT the whole ~/.claude config home — credentials,
        # settings, and other projects' transcripts must never be allowlisted.
        home = Path.home()
        for sensitive in (
            home / ".claude" / ".credentials.json",
            home / ".claude" / "settings.json",
            home / ".claude" / "projects" / "some-OTHER-project" / "x.jsonl",
        ):
            self.assertFalse(
                check_readable_internal_path(str(sensitive), _ctx()),
                f"{sensitive} must not be a readable-internal path",
            )


class TestCheckReadPermissionForTool(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.mkdtemp()
        self.ctx = _ctx(workspace=self.workspace)

    def test_in_workspace_allows(self) -> None:
        p = os.path.join(self.workspace, "README.md")
        self.assertEqual(check_read_permission_for_tool(p, self.ctx).behavior, "allow")

    def test_budget_spill_allows(self) -> None:
        p = str(get_tool_result_budget_dir() / "result_abc.txt")
        self.assertEqual(check_read_permission_for_tool(p, self.ctx).behavior, "allow")

    def test_outside_workspace_passthrough(self) -> None:
        # passthrough → the caller renders the read ask.
        self.assertEqual(
            check_read_permission_for_tool("/etc/hosts", self.ctx).behavior,
            "passthrough",
        )

    def test_unc_path_asks(self) -> None:
        self.assertEqual(
            check_read_permission_for_tool("//server/share/x", self.ctx).behavior,
            "ask",
        )

    def test_empty_path_passthrough(self) -> None:
        self.assertEqual(
            check_read_permission_for_tool("", self.ctx).behavior, "passthrough"
        )


class TestEnsureReadablePath(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.mkdtemp()
        self.ctx = _ctx(workspace=self.workspace)

    def test_in_workspace_ok(self) -> None:
        p = os.path.join(self.workspace, "a.txt")
        self.assertEqual(self.ctx.ensure_readable_path(p), Path(p).resolve())

    def test_budget_spill_does_not_raise(self) -> None:
        # The reported bug: even after approving, the budget read used to raise.
        p = str(get_tool_result_budget_dir() / "result_abc.txt")
        self.assertEqual(self.ctx.ensure_readable_path(p), Path(p).resolve())

    def test_outside_workspace_raises(self) -> None:
        with self.assertRaises(ToolPermissionError):
            self.ctx.ensure_readable_path("/etc/hosts")


class TestReadToolEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.mkdtemp()

    def test_default_mode_allows_in_workspace(self) -> None:
        ctx = _ctx(mode="default", workspace=self.workspace)
        self.assertEqual(
            _read_behavior(ctx, os.path.join(self.workspace, "README.md")), "allow"
        )

    def test_default_mode_allows_budget_spill(self) -> None:
        ctx = _ctx(mode="default", workspace=self.workspace)
        p = str(get_tool_result_budget_dir() / "result_abc.txt")
        self.assertEqual(_read_behavior(ctx, p), "allow")

    def test_default_mode_asks_outside(self) -> None:
        ctx = _ctx(mode="default", workspace=self.workspace)
        self.assertEqual(_read_behavior(ctx, "/etc/hosts"), "ask")

    def test_default_mode_asks_for_claude_credentials(self) -> None:
        # The credential-exposure regression, end to end through the Read tool.
        ctx = _ctx(mode="default", workspace=self.workspace)
        cred = str(Path.home() / ".claude" / ".credentials.json")
        self.assertEqual(_read_behavior(ctx, cred), "ask")

    def test_tool_level_read_deny_wins(self) -> None:
        # A configured Read deny must beat the new working-dir allow.
        pc = ToolPermissionContext.from_iterables(deny_names=["Read"])
        ctx = ToolContext(workspace_root=Path(self.workspace).resolve(), permission_context=pc)
        self.assertEqual(
            _read_behavior(ctx, os.path.join(self.workspace, "README.md")), "deny"
        )

    def test_auto_mode_allows_outside(self) -> None:
        # auto mode still classifies read-only tools as allow regardless of path.
        ctx = _ctx(mode="auto", workspace=self.workspace)
        self.assertEqual(_read_behavior(ctx, "/etc/hosts"), "allow")


if __name__ == "__main__":
    unittest.main()
