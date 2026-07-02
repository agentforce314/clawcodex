"""ch06 round-4 PR-A acceptance tests: the fail-open hook-ask fix, Read/Edit
permission-input backfill, and the InputValidationError prefix + guard
category.

Covers my-docs/port-improvement-round-4/ch06-tools-round4-plan-A.md.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.services.tool_execution.can_use_tool_adapter import build_can_use_tool
from src.tool_system.context import ToolContext


class TestForceDecisionAdapter(unittest.TestCase):
    """GAP A — the 6-arg can_use_tool honors a hook force-decision instead
    of raising TypeError and failing OPEN."""

    def _adapter(self):
        with tempfile.TemporaryDirectory() as _:
            ctx = ToolContext(workspace_root=Path("/tmp"))
        return build_can_use_tool(ctx)

    def test_accepts_six_args_without_typeerror(self):
        fn = self._adapter()
        # The 6th positional (force_decision) must not raise.
        import inspect

        sig = inspect.signature(fn)
        self.assertEqual(len(sig.parameters), 6)

    def test_force_allow_short_circuits(self):
        fn = self._adapter()
        tool = MagicMock()
        tool.name = "Bash"
        out = fn(tool, {"command": "ls"}, None, None, "t1",
                 {"behavior": "allow", "input": {"command": "ls -la"}})
        self.assertEqual(out["behavior"], "allow")
        self.assertEqual(out.get("updatedInput"), {"command": "ls -la"})

    def test_force_deny_short_circuits(self):
        fn = self._adapter()
        tool = MagicMock()
        tool.name = "Bash"
        out = fn(tool, {"command": "rm -rf /"}, None, None, "t1",
                 {"behavior": "deny", "message": "blocked by hook"})
        self.assertEqual(out["behavior"], "deny")
        self.assertIn("blocked by hook", out["message"])

    def test_force_ask_prompts_never_consults_rules(self):
        # critic M2 — an "ask" force-decision must reach the PROMPT and must
        # NOT consult rules. Even with an allow-rule that would match, the
        # hook's ask wins → the prompt fires. Here the prompt (handler)
        # denies; assert has_permissions_to_use_tool was NOT called.
        fn = self._adapter()
        tool = MagicMock()
        tool.name = "Bash"
        # has_permissions_to_use_tool / handle_permission_ask are imported
        # inside the adapter fn, so patch them at their source modules.
        with patch(
            "src.permissions.check.has_permissions_to_use_tool",
        ) as perm, patch(
            "src.permissions.handler.handle_permission_ask",
            return_value=(MagicMock(behavior="deny", message="user declined"),
                          ()),
        ):
            out = fn(tool, {"command": "ls"}, None, None, "t1",
                     {"behavior": "ask"})
        self.assertEqual(out["behavior"], "deny")
        perm.assert_not_called()  # rules never consulted for an ask-hook

    def test_force_ask_no_handler_fails_closed(self):
        # critic M1/M2 — ask + no handler → the real handle_permission_ask
        # returns a deny (handler-less contract). Never auto-allows.
        fn = self._adapter()
        tool = MagicMock()
        tool.name = "Bash"
        out = fn(tool, {"command": "ls"}, None, None, "t1",
                 {"behavior": "ask"})
        self.assertEqual(out["behavior"], "deny")

    def test_hook_ask_branch_prompts_not_autoallows(self):
        """End-to-end through resolve_hook_permission_decision: a PreToolUse
        hook returning 'ask' reaches the adapter's normal resolution, not
        the fail-open allow."""
        import asyncio

        from src.services.tool_execution.tool_hooks import (
            resolve_hook_permission_decision,
        )

        fn = self._adapter()
        tool = MagicMock()
        tool.name = "Bash"
        calls = {}

        def _spy(tool_, tinput, ctx, amsg, tid, force=None):
            calls["force"] = force
            # Simulate the normal-resolution outcome for an ask.
            return {"behavior": "deny", "message": "prompted → denied"}

        hook_result = {"behavior": "ask"}
        out = asyncio.run(resolve_hook_permission_decision(
            hook_result, tool, {"command": "curl x"}, MagicMock(),
            _spy, MagicMock(), "t1",
        ))
        # The adapter was called WITH the force-decision (not swallowed).
        self.assertEqual(calls.get("force"), {"behavior": "ask"})
        self.assertEqual(out["behavior"], "deny")

    def test_hook_ask_branch_fails_closed_on_adapter_exception(self):
        """critic M1 — if the adapter raises during ask resolution, the
        hook-ask branch DENIES (fail-closed), not allows."""
        import asyncio

        from src.services.tool_execution.tool_hooks import (
            resolve_hook_permission_decision,
        )

        tool = MagicMock()
        tool.name = "Bash"

        def _boom(*a, **k):
            raise RuntimeError("resolution blew up")

        out = asyncio.run(resolve_hook_permission_decision(
            {"behavior": "ask"}, tool, {"command": "x"}, MagicMock(),
            _boom, MagicMock(), "t1",
        ))
        self.assertEqual(out["behavior"], "deny")

    def test_hook_ask_branch_fails_closed_without_adapter(self):
        """critic M1 — no adapter → DENY, not allow."""
        import asyncio

        from src.services.tool_execution.tool_hooks import (
            resolve_hook_permission_decision,
        )

        tool = MagicMock()
        tool.name = "Bash"
        out = asyncio.run(resolve_hook_permission_decision(
            {"behavior": "ask"}, tool, {"command": "x"}, MagicMock(),
            None, MagicMock(), "t1",
        ))
        self.assertEqual(out["behavior"], "deny")


class TestReadEditBackfill(unittest.TestCase):
    """GAP B — Read/Edit expand file_path before permissions/hooks."""

    def test_backfill_helper_expands(self):
        from src.tool_system.tools.read import _backfill_read_edit_path

        home = str(Path.home())
        inp = {"file_path": "~/somefile.txt"}
        _backfill_read_edit_path(inp)
        self.assertTrue(inp["file_path"].startswith(home))
        self.assertNotIn("~", inp["file_path"])

    def test_read_and_edit_declare_backfill(self):
        from src.tool_system.tools.edit import EditTool
        from src.tool_system.tools.read import ReadTool

        self.assertIsNotNone(ReadTool.backfill_observable_input)
        self.assertIsNotNone(EditTool.backfill_observable_input)
        # Same shared function.
        self.assertIs(
            ReadTool.backfill_observable_input,
            EditTool.backfill_observable_input,
        )

    def test_backfill_leaves_non_string_untouched(self):
        from src.tool_system.tools.read import _backfill_read_edit_path

        inp = {"offset": 5}
        _backfill_read_edit_path(inp)
        self.assertEqual(inp, {"offset": 5})


class TestInputValidationErrorPrefix(unittest.TestCase):
    """GAP C — the prefix reconnects the tool-failure-loop guard's
    InputValidationError category."""

    def test_guard_categorizes_prefixed_error(self):
        from src.query.tool_failure_loop_guard import _normalize_error_category

        wrapped = "<tool_use_error>InputValidationError: missing 'command'</tool_use_error>"
        self.assertEqual(_normalize_error_category(wrapped), "InputValidationError")

    def test_guard_trips_on_repeated_validation_errors(self):
        from src.query.tool_failure_loop_guard import (
            create_tool_failure_loop_guard_state,
            update_tool_failure_loop_guard,
        )
        from src.types.content_blocks import ToolResultBlock
        from src.types.messages import UserMessage

        state = create_tool_failure_loop_guard_state()
        decision = None
        for i in range(3):
            blocks = [{
                "id": f"tu_{i}", "name": "Bash", "type": "tool_use",
                "input": {"command": "x"},
            }]
            results = [UserMessage(content=[ToolResultBlock(
                tool_use_id=f"tu_{i}",
                content="<tool_use_error>InputValidationError: bad</tool_use_error>",
                is_error=True,
            )])]
            decision = update_tool_failure_loop_guard(
                state=state, tool_use_blocks=blocks, tool_results=results,
            )
        self.assertTrue(decision.tripped)
        self.assertEqual(decision.error_category, "InputValidationError")


if __name__ == "__main__":
    unittest.main()
