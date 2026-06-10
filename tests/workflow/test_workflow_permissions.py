"""Tests for workflow permission classification (auto mode)."""

from __future__ import annotations

from src.permissions.check import auto_mode_classify
from src.permissions.types import ToolPermissionContext


def _ctx():
    return ToolPermissionContext(mode="auto")


def test_workflow_is_auto_allowed():
    decision = auto_mode_classify("Workflow", {"script": "..."}, _ctx())
    assert decision.allow is True
    assert "workflow" in decision.reason.lower()


def test_agent_still_auto_allowed():
    # Parity guard — the Workflow branch sits next to the Agent branch.
    assert auto_mode_classify("Agent", {}, _ctx()).allow is True


def test_unknown_tool_still_denied():
    assert auto_mode_classify("mcp__server__do", {}, _ctx()).allow is False
