"""Workflow permission fixes.

A) ``--dangerously-skip-permissions`` (bypassPermissions) is inherited by
   workflow subagents, not silently downgraded to acceptEdits — so a subagent is
   never *more* restricted than the session that launched it.
B) The internal tool-results spill dir is readable in any mode: the runtime
   offloads large tool results there and points the model back at them (a
   workflow subagent told to ``Read`` the offloaded result), so reading it must
   not trip the workspace allowlist.

Repro for both: ``/wc26-watch-guide`` under ``--dangerously-skip-permissions``
failed with ``ToolPermissionError: path is outside allowed working directories:
/private/tmp/clawcodex_tool_results/<pid>/tool-results/toolu_*.txt``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.permissions.types import ToolPermissionContext
from src.services.tool_execution.tool_result_persistence import resolve_tool_results_dir
from src.tool_system.context import ToolContext
from src.tool_system.errors import ToolPermissionError
from src.workflow.runner import _subagent_permission_override


def _parent(mode):
    return SimpleNamespace(permission_context=SimpleNamespace(mode=mode))


# ── Fix A: don't downgrade a permissive session ───────────────────────────────


def test_permissive_session_inherited_by_workflow_subagent():
    # None == "inherit via resolve_permission_mode" → parent mode carries through.
    assert _subagent_permission_override(_parent("bypassPermissions")) is None
    assert _subagent_permission_override(_parent("acceptEdits")) is None
    assert _subagent_permission_override(_parent("dontAsk")) is None


def test_restrictive_session_elevated_to_acceptedits():
    assert _subagent_permission_override(_parent("default")) == "acceptEdits"
    assert _subagent_permission_override(_parent("plan")) == "acceptEdits"
    # Missing / unknown mode → safe default of acceptEdits (auto-approve edits).
    assert _subagent_permission_override(_parent(None)) == "acceptEdits"
    assert _subagent_permission_override(SimpleNamespace()) == "acceptEdits"


# ── Fix B: the tool-results spill dir is readable outside the workspace ────────


def _acceptedits_ctx(workspace):
    # acceptEdits is NOT bypass, so ensure_allowed_path enforces the allowlist —
    # exactly the mode a workflow subagent runs in for a normal session.
    return ToolContext(
        workspace_root=workspace,
        permission_context=ToolPermissionContext(mode="acceptEdits"),
    )


def test_tool_results_spill_dir_allowed_in_non_bypass_mode(tmp_path):
    ctx = _acceptedits_ctx(tmp_path)
    target = resolve_tool_results_dir(ctx) / "toolu_vrtx_01CniSDiu45h5JZN2vUGFaTo.txt"
    out = ctx.ensure_allowed_path(str(target))
    assert str(out).endswith("toolu_vrtx_01CniSDiu45h5JZN2vUGFaTo.txt")
    # the spill dir is among the allowed roots
    assert any("clawcodex" in str(r).lower() and "tool-results" in str(r) for r in ctx.allowed_roots())


def test_unrelated_outside_path_still_blocked(tmp_path):
    ctx = _acceptedits_ctx(tmp_path)
    with pytest.raises(ToolPermissionError):
        ctx.ensure_allowed_path("/etc/passwd")


def test_workspace_path_still_allowed(tmp_path):
    ctx = _acceptedits_ctx(tmp_path)
    inside = tmp_path / "src" / "x.py"
    assert ctx.ensure_allowed_path(str(inside)) == inside.resolve()
