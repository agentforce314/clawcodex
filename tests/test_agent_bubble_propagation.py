"""Round-2 coverage for bubble-mode propagation into async sub-agents.

Mirrors ``typescript/src/tools/AgentTool/runAgent.ts:449-476`` (Step 5
of the sub-agent lifecycle described in
``book/ch08-sub-agents.md:252-305``).

The TS cascade for ``shouldAvoidPermissionPrompts`` is:

    canShowPermissionPrompts !== undefined
        ? !canShowPermissionPrompts
        : agentPermissionMode === 'bubble'
            ? false
            : isAsync

Python does not yet plumb ``canShowPermissionPrompts`` through
``RunAgentParams``; the cascade reduces to the bubble / isAsync
branch. The full plumbing is tracked in a future round, see
``my-docs/ch08-sub-agents-gap-analysis.md`` ("Adjacent Observations").

These tests focus on the bubble-mode preservation gap closed in this
PR. They cover the full 2-by-2-by-2 matrix over ``mode`` (bubble vs
default), ``is_async``, and parent ``should_avoid`` (True vs False).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.agent_definitions import AgentDefinition
from src.agent.run_agent import (
    _build_permission_context,
    resolve_permission_mode,
)
from src.permissions.types import PermissionMode, ToolPermissionContext
from src.tool_system.context import ToolContext
from src.utils.abort_controller import AbortController


def _make_context(
    mode: PermissionMode = "default",
    *,
    parent_avoids: bool = False,
) -> ToolContext:
    return ToolContext(
        workspace_root=Path("/tmp/test-ws"),
        permission_context=ToolPermissionContext(
            mode=mode,
            should_avoid_permission_prompts=parent_avoids,
        ),
        abort_controller=AbortController(),
    )


def _make_agent(
    permission_mode: PermissionMode | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        agent_type="test-bubble",
        when_to_use="test",
        permission_mode=permission_mode,
    )


# ---------------------------------------------------------------------------
# Matrix: (effective_mode, is_async, parent_avoids) -> (should_avoid, await)
# ---------------------------------------------------------------------------

# Row format: (effective_mode, is_async, parent_avoids,
#              expected_should_avoid, expected_await)
MATRIX = [
    # bubble + sync — prompts always enabled, no await flag.
    ("bubble",  False, False, False, False),
    ("bubble",  False, True,  True,  False),  # parent override wins
    # bubble + async — prompts enabled, await classifier first.
    ("bubble",  True,  False, False, True),
    ("bubble",  True,  True,  True,  False),  # parent override wins
    # default + sync — prompts enabled (sync always prompts directly).
    ("default", False, False, False, False),
    ("default", False, True,  True,  False),
    # default + async — prompts disabled (no terminal to bubble to).
    ("default", True,  False, True,  False),
    ("default", True,  True,  True,  False),
]


@pytest.mark.parametrize(
    "effective_mode,is_async,parent_avoids,exp_avoid,exp_await",
    MATRIX,
)
def test_permission_cascade_matrix(
    effective_mode: PermissionMode,
    is_async: bool,
    parent_avoids: bool,
    exp_avoid: bool,
    exp_await: bool,
) -> None:
    """Full 2-by-2-by-2 matrix matches the TS cascade.

    Each row is a single line of
    ``typescript/src/tools/AgentTool/runAgent.ts:449-476`` evaluated
    against fixed inputs.
    """
    ctx = _make_context(parent_avoids=parent_avoids)

    perm = _build_permission_context(ctx, effective_mode, is_async=is_async)

    assert perm.should_avoid_permission_prompts is exp_avoid, (
        f"mode={effective_mode} is_async={is_async} "
        f"parent_avoids={parent_avoids} -> expected should_avoid={exp_avoid}"
    )
    assert perm.await_automated_checks_before_dialog is exp_await, (
        f"mode={effective_mode} is_async={is_async} "
        f"parent_avoids={parent_avoids} -> expected await={exp_await}"
    )


# ---------------------------------------------------------------------------
# Integration: resolve_permission_mode + _build_permission_context
# ---------------------------------------------------------------------------

def test_bubble_agent_round_trip_through_resolver() -> None:
    """Defining a bubble agent + running async yields the bubble cascade.

    Confirms the public API surface (call site of
    ``run_agent._build_permission_context`` after
    ``resolve_permission_mode``) produces the bubble exception, not
    just the bare ``_build_permission_context`` helper.
    """
    parent = _make_context(mode="default")
    agent = _make_agent(permission_mode="bubble")

    effective = resolve_permission_mode(parent, agent, is_async=True)
    assert effective == "bubble"

    perm = _build_permission_context(parent, effective, is_async=True)

    assert perm.mode == "bubble"
    assert perm.should_avoid_permission_prompts is False
    assert perm.await_automated_checks_before_dialog is True


def test_bypass_parent_blocks_bubble_override() -> None:
    """Permissive parent modes still win over the agent's bubble mode.

    ``resolve_permission_mode`` returns ``bypassPermissions``, not
    ``bubble``, so the build step never sees the bubble path.
    """
    parent = _make_context(mode="bypassPermissions")
    agent = _make_agent(permission_mode="bubble")

    effective = resolve_permission_mode(parent, agent, is_async=True)
    assert effective == "bypassPermissions"

    perm = _build_permission_context(parent, effective, is_async=True)

    # bypassPermissions is not bubble — the async path applies the
    # default avoidance.
    assert perm.should_avoid_permission_prompts is True
    assert perm.await_automated_checks_before_dialog is False


def test_no_field_pollution_on_sync_default() -> None:
    """The default + sync path leaves both flags False.

    Guards against accidentally setting either flag in the most common
    code path (sync general-purpose agent).
    """
    parent = _make_context(mode="default")
    agent = _make_agent(permission_mode=None)

    effective = resolve_permission_mode(parent, agent, is_async=False)
    perm = _build_permission_context(parent, effective, is_async=False)

    assert perm.should_avoid_permission_prompts is False
    assert perm.await_automated_checks_before_dialog is False
