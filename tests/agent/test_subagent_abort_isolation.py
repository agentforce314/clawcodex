"""Regression tests: async-subagent abort-controller isolation.

Concern: when the user presses ESC on the parent engine and then types
"please resume", the engine calls ``reset_abort_controller()``. The
``_dispatch_single_tool`` REJECT_MESSAGE override (added by the prior
fix) checks ``tool_use_context.abort_controller.signal.aborted`` — if
an async subagent spawned BEFORE the ESC were to see a stale
(previously-aborted) controller, every tool call from the subagent
would spuriously return REJECT_MESSAGE.

TS reference: ``typescript/src/tools/AgentTool/runAgent.ts:533-541``
gives async agents a fresh ``new AbortController()`` (UNLINKED from the
parent), and sync agents share the parent's controller directly. Python
mirrors this at ``src/agent/run_agent.py:281-287``:

* async path → ``abort_controller = AbortController()`` (fresh, unlinked)
* sync path → ``abort_controller = params.parent_context.abort_controller``

These tests lock the parity invariants down so a future refactor can't
silently break the isolation by, e.g., switching the async path to a
child-linked controller (which would propagate the parent's abort and
re-introduce the bug the REJECT_MESSAGE override would amplify).
"""
from __future__ import annotations

from pathlib import Path

from src.agent.subagent_context import (
    SubagentContextOverrides,
    create_subagent_context,
)
from src.tool_system.context import ToolContext
from src.utils.abort_controller import AbortController


def _make_parent_context(tmp_path: Path) -> ToolContext:
    """Build a minimal parent ToolContext with its own controller."""
    ctx = ToolContext(workspace_root=tmp_path)
    ctx.abort_controller = AbortController()
    return ctx


def test_async_subagent_context_has_fresh_unlinked_controller(tmp_path: Path) -> None:
    """The async path in ``create_subagent_context`` (overrides carrying
    a brand-new AbortController) MUST produce a child context whose
    controller is the fresh one — NOT child-linked to the parent.

    If a future change wires this up as a child controller, then a
    parent abort would propagate to the subagent and ``_dispatch_single_tool``
    would emit REJECT_MESSAGE for every tool the subagent runs.
    """
    parent = _make_parent_context(tmp_path)
    fresh = AbortController()
    overrides = SubagentContextOverrides(
        abort_controller=fresh,
        # async agent does NOT share the parent's controller
        share_abort_controller=False,
    )

    child = create_subagent_context(parent, overrides)

    assert child.abort_controller is fresh, (
        "an explicit abort_controller override must be the identity used "
        "in the child context — not wrapped in a child-linked controller"
    )

    # Trip the parent and verify the child stays clean. This is the
    # invariant that protects async subagents from parent ESC.
    parent.abort_controller.abort("user_interrupt")
    assert parent.abort_controller.signal.aborted is True
    assert child.abort_controller.signal.aborted is False, (
        "parent ESC must NOT propagate to an async subagent's controller. "
        "If this fires, the REJECT_MESSAGE override in _dispatch_single_tool "
        "will fire for every tool the async subagent runs — spurious "
        "user-rejected results on tools the user never cancelled."
    )


def test_sync_subagent_shares_parent_controller_reference(tmp_path: Path) -> None:
    """The sync path (``share_abort_controller=True``) MUST install the
    SAME controller instance on the child context — not a child-linked
    copy. This preserves the TS-mirrored "sync agents inherit ESC from
    parent" contract that ``StreamingToolExecutor`` relies on to unwind
    when the parent aborts mid-tool.
    """
    parent = _make_parent_context(tmp_path)
    overrides = SubagentContextOverrides(
        # No abort_controller override; share_abort_controller=True takes the
        # parent's controller directly.
        share_abort_controller=True,
    )

    child = create_subagent_context(parent, overrides)

    assert child.abort_controller is parent.abort_controller, (
        "sync subagent must SHARE the parent's controller (same object) "
        "so parent ESC reaches the subagent without an extra hop"
    )


def test_async_subagent_unaffected_by_parent_reset(tmp_path: Path) -> None:
    """End-to-end: parent ESC fires, parent's controller swapped for a
    new one (mirrors ``QueryEngine.reset_abort_controller``), the async
    subagent's controller must remain untouched throughout.
    """
    parent = _make_parent_context(tmp_path)
    fresh = AbortController()
    overrides = SubagentContextOverrides(
        abort_controller=fresh,
        share_abort_controller=False,
    )
    child = create_subagent_context(parent, overrides)

    # 1. User presses ESC: parent's controller aborts.
    parent.abort_controller.abort("user_interrupt")
    assert parent.abort_controller.signal.aborted is True
    assert child.abort_controller.signal.aborted is False

    # 2. User types "resume": engine swaps in a fresh controller (we
    #    simulate the engine's reset by re-assigning the field).
    new_parent_controller = AbortController()
    parent.abort_controller = new_parent_controller

    # 3. The async subagent's controller is the same fresh one we
    #    handed in at creation — neither the abort nor the reset
    #    touched it.
    assert child.abort_controller is fresh
    assert child.abort_controller.signal.aborted is False


def test_create_subagent_context_default_path_is_child_linked(tmp_path: Path) -> None:
    """When neither ``abort_controller`` nor ``share_abort_controller`` is
    set on the overrides, ``create_subagent_context`` must fall through to
    the child-linked branch (``create_child_abort_controller(parent…)``).
    This is the safety default for "subagent we forgot to wire up": parent
    abort still propagates so ESC isn't silently lost — but the child is
    a distinct controller, so a child-side abort doesn't poison the
    parent's lifecycle.

    Mirrors the priority order at ``src/agent/subagent_context.py:81-86``
    and TS ``typescript/src/utils/forkedAgent.ts:349-354``.
    """
    parent = _make_parent_context(tmp_path)
    overrides = SubagentContextOverrides()  # No abort_controller, no share

    child = create_subagent_context(parent, overrides)

    # Distinct controller object — not shared, not the parent's.
    assert child.abort_controller is not parent.abort_controller

    # Child-linked: parent abort propagates downward.
    parent.abort_controller.abort("user_interrupt")
    assert child.abort_controller.signal.aborted is True

    # But child-side abort does NOT propagate upward (one-way semantic).
    fresh_parent = _make_parent_context(tmp_path)
    fresh_overrides = SubagentContextOverrides()
    fresh_child = create_subagent_context(fresh_parent, fresh_overrides)
    fresh_child.abort_controller.abort("child_local")
    assert fresh_parent.abort_controller.signal.aborted is False, (
        "create_child_abort_controller documents one-way semantics: child "
        "abort does NOT propagate up. Breaking that would let any subagent "
        "tear down the parent turn."
    )
