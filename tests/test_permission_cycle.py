"""Tests for src/permissions/cycle.py.

Mirrors transitions in
``typescript/src/utils/permissions/getNextPermissionMode.ts:34-79``.
"""
from __future__ import annotations

import unittest

from src.permissions.cycle import cycle_permission_mode, get_next_permission_mode
from src.permissions.types import ToolPermissionContext


class TestGetNextPermissionMode(unittest.TestCase):
    def test_default_to_acceptEdits(self) -> None:
        ctx = ToolPermissionContext(mode="default")
        self.assertEqual(get_next_permission_mode(ctx), "acceptEdits")

    def test_acceptEdits_to_plan(self) -> None:
        ctx = ToolPermissionContext(mode="acceptEdits")
        self.assertEqual(get_next_permission_mode(ctx), "plan")

    def test_plan_with_bypass_to_bypassPermissions(self) -> None:
        ctx = ToolPermissionContext(
            mode="plan", is_bypass_permissions_mode_available=True,
        )
        self.assertEqual(get_next_permission_mode(ctx), "bypassPermissions")

    def test_plan_without_bypass_to_default(self) -> None:
        ctx = ToolPermissionContext(
            mode="plan", is_bypass_permissions_mode_available=False,
        )
        self.assertEqual(get_next_permission_mode(ctx), "default")

    def test_bypassPermissions_to_default(self) -> None:
        ctx = ToolPermissionContext(mode="bypassPermissions")
        self.assertEqual(get_next_permission_mode(ctx), "default")

    def test_dontAsk_to_default(self) -> None:
        ctx = ToolPermissionContext(mode="dontAsk")
        self.assertEqual(get_next_permission_mode(ctx), "default")

    def test_auto_to_default(self) -> None:
        ctx = ToolPermissionContext(mode="auto")
        self.assertEqual(get_next_permission_mode(ctx), "default")

    def test_bubble_to_default(self) -> None:
        ctx = ToolPermissionContext(mode="bubble")
        self.assertEqual(get_next_permission_mode(ctx), "default")


class TestCyclePermissionMode(unittest.TestCase):
    def test_returns_next_mode_and_updated_context(self) -> None:
        ctx = ToolPermissionContext(mode="default")
        next_mode, next_ctx = cycle_permission_mode(ctx)
        self.assertEqual(next_mode, "acceptEdits")
        self.assertEqual(next_ctx.mode, "acceptEdits")

    def test_full_cycle_no_bypass(self) -> None:
        ctx = ToolPermissionContext(
            mode="default", is_bypass_permissions_mode_available=False,
        )
        modes: list[str] = []
        for _ in range(5):
            mode, ctx = cycle_permission_mode(ctx)
            modes.append(mode)
        # default → acceptEdits → plan → default → acceptEdits → plan
        self.assertEqual(modes, ["acceptEdits", "plan", "default", "acceptEdits", "plan"])

    def test_full_cycle_with_bypass(self) -> None:
        ctx = ToolPermissionContext(
            mode="default", is_bypass_permissions_mode_available=True,
        )
        modes: list[str] = []
        for _ in range(5):
            mode, ctx = cycle_permission_mode(ctx)
            modes.append(mode)
        # default → acceptEdits → plan → bypassPermissions → default → acceptEdits
        self.assertEqual(
            modes, ["acceptEdits", "plan", "bypassPermissions", "default", "acceptEdits"],
        )

    def test_cycle_preserves_other_context_fields(self) -> None:
        ctx = ToolPermissionContext(
            mode="default",
            always_allow_rules={"session": ["Read"]},
            is_bypass_permissions_mode_available=True,
        )
        _, next_ctx = cycle_permission_mode(ctx)
        self.assertEqual(next_ctx.always_allow_rules, {"session": ["Read"]})
        self.assertTrue(next_ctx.is_bypass_permissions_mode_available)


if __name__ == "__main__":
    unittest.main()
