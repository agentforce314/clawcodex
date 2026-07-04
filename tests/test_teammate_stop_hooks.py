"""QUERY-1 — teammate TaskCompleted + TeammateIdle stop hooks.

Port of stopHooks.ts:335-453 + the executors (utils/hooks.ts:3920/:4000).
Execution-style per the plugins-round lesson: the real stop-hooks generator
is driven with real command hooks (echo/exit), asserting on YIELDED
messages and the result_out contract — not on shapes the runtime never
reads.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.hooks.hook_types import HookConfig, HookSource
from src.query.stop_hooks import StopHookResult, _handle_stop_hooks_generator


class _Snapshot:
    def __init__(self, hooks: dict):
        self.hooks = hooks


def _ctx(*, hooks: dict, teammate: bool = True, tasks: dict | None = None):
    from src.tool_system.context import ToolContext

    ctx = ToolContext(workspace_root=Path("/tmp"))
    ctx.workspace_trusted = True
    ctx.agent_id = "a1b2"  # teammates run as subagents
    if teammate:
        ctx.teammate_name = "researcher"
        ctx.team_name = "my-team"
    if tasks:
        ctx.tasks.update(tasks)
    mgr = MagicMock()
    mgr.snapshot = _Snapshot(hooks)
    ctx.hook_config_manager = mgr
    return ctx


def _hook(command: str) -> list:
    return [HookConfig(type="command", command=command, source=HookSource.PROJECT_SETTINGS)]


def _drive(ctx) -> tuple[list, StopHookResult]:
    result = StopHookResult()

    async def go():
        out = []
        async for msg in _handle_stop_hooks_generator(
            [], [], "", ctx, "repl", None, result,
        ):
            out.append(msg)
        return out

    return asyncio.run(go()), result


OWNED_TASK = {"1": {"id": "1", "subject": "port hooks", "description": "d",
                    "status": "in_progress", "owner": "researcher"}}
OTHER_TASKS = {
    "2": {"id": "2", "subject": "x", "status": "in_progress", "owner": "someone-else"},
    "3": {"id": "3", "subject": "y", "status": "completed", "owner": "researcher"},
}


class TestTeammateBlock:
    def test_both_events_fire_for_teammate(self, tmp_path):
        marker_tc = tmp_path / "tc"
        marker_ti = tmp_path / "ti"
        ctx = _ctx(
            hooks={
                "TaskCompleted": _hook(f"touch {marker_tc}"),
                "TeammateIdle": _hook(f"touch {marker_ti}"),
            },
            tasks=dict(OWNED_TASK),
        )
        _drive(ctx)
        assert marker_tc.exists(), "TaskCompleted must fire for the owned in-progress task"
        assert marker_ti.exists(), "TeammateIdle must always fire for a teammate"

    def test_non_owned_and_completed_tasks_skipped(self, tmp_path):
        marker = tmp_path / "tc"
        ctx = _ctx(
            hooks={"TaskCompleted": _hook(f"touch {marker}")},
            tasks=dict(OTHER_TASKS),
        )
        _drive(ctx)
        assert not marker.exists(), "no owned in-progress task → no TaskCompleted"

    def test_non_teammate_never_fires(self, tmp_path):
        marker = tmp_path / "m"
        ctx = _ctx(
            hooks={
                "TaskCompleted": _hook(f"touch {marker}"),
                "TeammateIdle": _hook(f"touch {marker}"),
            },
            teammate=False,
            tasks=dict(OWNED_TASK),
        )
        _drive(ctx)
        assert not marker.exists(), "the gate: no teammate identity → no teammate hooks"

    def test_runs_even_without_stop_hooks_configured(self, tmp_path):
        """The split-gate fix: only TeammateIdle configured (no Stop /
        SubagentStop) must still reach the teammate block."""
        marker = tmp_path / "ti"
        ctx = _ctx(hooks={"TeammateIdle": _hook(f"touch {marker}")})
        _drive(ctx)
        assert marker.exists()

    def test_blocking_error_surfaces_with_verbatim_prefix(self):
        ctx = _ctx(hooks={"TeammateIdle": _hook("echo 'not done yet' >&2; exit 2")})
        messages, result = _drive(ctx)
        metas = [m for m in messages
                 if getattr(m, "type", "") == "user" and getattr(m, "isMeta", False)]
        assert metas, "blocking exit-2 must yield a meta user message"
        text = str(getattr(metas[-1], "content", ""))
        # utils/hooks.ts:2091-2094 verbatim prefix
        assert text.startswith("TeammateIdle hook feedback:\n")
        assert "not done yet" in text
        assert result.blocking_errors and result.prevent_continuation is False

    def test_prevent_continuation_stops_with_attachment(self):
        payload = '{"preventContinuation": true, "stopReason": "keep working"}'
        ctx = _ctx(hooks={"TeammateIdle": _hook(f"echo '{payload}'")})
        messages, result = _drive(ctx)
        assert result.prevent_continuation is True
        att = [m for m in messages if getattr(m, "type", "") == "attachment"]
        joined = str([getattr(a, "attachments", None) for a in att])
        assert "hook_stopped_continuation" in joined
        assert "keep working" in joined

    def test_identity_threading_from_named_spawn(self):
        """W2 pin: a NAMED spawn inside a team threads both fields; an
        anonymous spawn threads neither."""
        from src.agent.subagent_context import (
            SubagentContextOverrides,
            create_subagent_context,
        )
        from src.tool_system.context import ToolContext

        parent = ToolContext(workspace_root=Path("/tmp"))
        parent.team = {"team_name": "my-team", "members": []}
        named = create_subagent_context(
            parent, SubagentContextOverrides(teammate_name="researcher"),
        )
        assert named.teammate_name == "researcher"
        assert named.team_name == "my-team"
        anon = create_subagent_context(parent, SubagentContextOverrides())
        assert anon.teammate_name is None

        parent_no_team = ToolContext(workspace_root=Path("/tmp"))
        named_no_team = create_subagent_context(
            parent_no_team, SubagentContextOverrides(teammate_name="solo"),
        )
        assert named_no_team.team_name is None  # named OUTSIDE a team ≠ teammate


class TestExecutors:
    def test_task_completed_stdin_contract(self, tmp_path):
        """The hook's stdin carries the TaskCompletedHookInput fields."""
        out = tmp_path / "stdin.json"
        ctx = _ctx(
            hooks={"TaskCompleted": _hook(f"cat > {out}")},
            tasks=dict(OWNED_TASK),
        )
        _drive(ctx)
        import json

        data = json.loads(out.read_text())
        # The port's uniform stdin convention is "hook_event" (all existing
        # events; TS uses hook_event_name — a pre-existing, uniform naming
        # divergence owned by the hooks docket, not QUERY-1).
        assert data["hook_event"] == "TaskCompleted"
        assert data["task_id"] == "1"
        assert data["task_subject"] == "port hooks"
        assert data["teammate_name"] == "researcher"
        assert data["team_name"] == "my-team"

    def test_teammate_idle_stdin_contract(self, tmp_path):
        out = tmp_path / "stdin.json"
        ctx = _ctx(hooks={"TeammateIdle": _hook(f"cat > {out}")})
        _drive(ctx)
        import json

        data = json.loads(out.read_text())
        assert data["hook_event"] == "TeammateIdle"
        assert data["teammate_name"] == "researcher"
        assert data["team_name"] == "my-team"


class TestRealPathLiveness:
    """The critic's liveness demand: fire the block through REAL context
    construction (TeamCreate-populated team + named spawn via
    create_subagent_context), not a hand-built context."""

    def test_teammate_context_from_real_spawn_fires_hooks(self, tmp_path):
        import json as _json

        from src.agent.subagent_context import (
            SubagentContextOverrides,
            create_subagent_context,
        )
        from src.tool_system.context import ToolContext
        from src.tool_system.tools.team import TeamCreateTool

        # Leader context; TeamCreate tool populates context.team (team.py:40)
        leader = ToolContext(workspace_root=tmp_path)
        leader.workspace_trusted = True
        TeamCreateTool.call({"team_name": "sweep-team"}, leader)
        assert leader.team and leader.team["team_name"] == "sweep-team"

        # Leader assigns a task on the shared board
        leader.tasks["7"] = {"id": "7", "subject": "port query folder",
                             "status": "in_progress", "owner": "researcher"}

        # Named spawn — the REAL seam (run_agent → create_subagent_context)
        tm_ctx = create_subagent_context(
            leader, SubagentContextOverrides(teammate_name="researcher"),
        )
        assert tm_ctx.teammate_name == "researcher"
        assert tm_ctx.team_name == "sweep-team"
        # The board is SHARED for teammates (TS single-board semantics)
        assert tm_ctx.tasks is leader.tasks

        marker_tc = tmp_path / "tc"
        marker_ti = tmp_path / "ti"
        mgr = MagicMock()
        mgr.snapshot = _Snapshot({
            "TaskCompleted": _hook(f"touch {marker_tc}"),
            "TeammateIdle": _hook(f"touch {marker_ti}"),
        })
        tm_ctx.hook_config_manager = mgr

        _, result = _drive(tm_ctx)
        assert marker_tc.exists(), "TaskCompleted fires on the SHARED board's owned task"
        assert marker_ti.exists()

    def test_anonymous_subagent_keeps_isolated_board(self, tmp_path):
        from src.agent.subagent_context import (
            SubagentContextOverrides,
            create_subagent_context,
        )
        from src.tool_system.context import ToolContext

        leader = ToolContext(workspace_root=tmp_path)
        leader.tasks["1"] = {"id": "1", "status": "in_progress", "owner": "x"}
        anon = create_subagent_context(leader, SubagentContextOverrides())
        assert anon.tasks == {} and anon.tasks is not leader.tasks
