"""Phase-3 acceptance gate — chapter example #2 end-to-end.

The chapter (``ch12-extensibility.md``) describes:

    A skill with frontmatter ``hooks: [PreToolUse, once: true]`` registers
    a PreToolUse hook on invocation; it fires the next time a Bash tool
    call happens; it is automatically removed after that first firing; it
    does not fire on subsequent Bash calls.

Pre-Phase-3 this was inert in the Python port (gap analysis #4 + #11).
This test exercises the full chain end-to-end:

  1. ``_run_markdown_skill`` (Phase 0 / I3 — now async) is invoked.
  2. The skill's frontmatter ``hooks`` field is registered via
     ``register_skill_hooks`` (Phase 3 / WI-3.2).
  3. ``_run_hooks_for_event`` collects the session-scoped hook in its
     ``_collect_hooks_for_event`` step (Phase 3 / WI-3.1, the I2 contract's
     Collect step).
  4. The hook fires on the first PreToolUse with a matching tool name.
  5. The on_success callback (registered for ``once=True``) schedules
     removal via ``asyncio.create_task``; the second firing finds nothing.

Failure mode this protects against: any of the chain's links breaking →
chapter example #2 silently no-ops or fires twice.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.hooks.config_manager import HookConfigManager, HookConfigSnapshot
from src.hooks.hook_executor import _run_hooks_for_event
from src.hooks.hook_types import HookConfig
from src.hooks.register_skill_hooks import register_skill_hooks
from src.hooks.registry import AsyncHookRegistry
from src.hooks.session_hooks import SessionHookRegistry, get_session_hooks


@dataclass
class _MockOptions:
    hooks: dict[str, Any] | None = None
    tools: list[Any] = field(default_factory=list)


@dataclass
class _MockContext:
    """Minimal stand-in for ToolContext for the E2E run."""
    options: _MockOptions = field(default_factory=_MockOptions)
    hook_config_manager: Any | None = None
    workspace_trusted: bool = True
    abort_controller: Any | None = None
    session_hook_registry: Any | None = None
    session_id: str | None = None
    workspace_root: Path | None = None


def _empty_config_manager() -> HookConfigManager:
    """Manager with an empty snapshot — needed because the executor reads
    the snapshot for non-session hooks. We don't want any snapshot-tier
    hooks for this test; the goal is to exercise *only* the session-scoped
    path.
    """
    m = HookConfigManager(registry=AsyncHookRegistry(), settings_path="/dev/null")
    m._snapshot = HookConfigSnapshot(hooks={}, timestamp=0.0, source_path=None)
    return m


# Skill frontmatter: a single PreToolUse hook with ``once: true``.
# Matcher ``"Bash"`` so it only fires for Bash tool calls.
SKILL_HOOKS_ONCE_PRETOOLUSE = {
    "PreToolUse": [
        {
            "matcher": "Bash",
            "hooks": [
                {"type": "command", "command": "echo audit", "once": True},
            ],
        }
    ],
}


class TestSkillOnceTruePreToolUseEndToEnd:
    @pytest.mark.asyncio
    async def test_skill_once_true_pretooluse_e2e(self, tmp_path):
        """Headline acceptance test for Phase 3.

        Setup: a SessionHookRegistry, an empty HookConfigManager snapshot,
        and a ToolContext wired to both. We register the skill's frontmatter
        hooks directly via ``register_skill_hooks`` (the call ``_run_markdown_skill``
        would make end-to-end; we don't spin up a full skill loader here to
        keep the test focused on the hook-pipeline contract).

        Then we drive ``_run_hooks_for_event`` twice for ``PreToolUse + Bash``
        and assert the hook fired exactly once.
        """
        reg = SessionHookRegistry()
        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            session_hook_registry=reg,
            session_id="session-abc",
        )

        # Step 1 — register the skill's frontmatter hooks (mirroring what
        # _run_markdown_skill does at the registration call site).
        n = await register_skill_hooks(
            registry=reg,
            session_id="session-abc",
            skill_hooks=SKILL_HOOKS_ONCE_PRETOOLUSE,
            skill_name="audit-skill",
        )
        assert n == 1

        # Sanity: registry has exactly the one hook before any firing.
        before = await get_session_hooks(
            registry=reg, session_id="session-abc", event="PreToolUse",
        )
        assert len(before) == 1

        # Step 2 — first PreToolUse firing for Bash. Hook should fire.
        # Drive the generator to completion (we don't care about progress
        # messages here, only that the hook actually executed).
        first_run_yields = []
        async for item in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_use_id": "u1"},
            ctx,
        ):
            first_run_yields.append(item)

        # The progress yield includes a message with the hook's command.
        # That's the proof-of-fire signal we use here (the actual subprocess
        # output is captured by the `result` mechanism but isn't yielded
        # back as a separate item unless there's a non-zero exit etc.).
        assert any(
            "echo audit" in str(item.get("message", "") or {})
            for item in first_run_yields
        ), f"hook did not fire on first PreToolUse; yields={first_run_yields!r}"

        # Step 3 — give the asyncio.create_task removal a tick to land.
        # The on_success callback fires synchronously after the hook
        # finishes, but the removal it schedules runs on the next loop
        # iteration. ``asyncio.sleep(0)`` yields control to let the
        # scheduled remove_session_hook task run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)  # one more tick for safety

        after_first = await get_session_hooks(
            registry=reg, session_id="session-abc", event="PreToolUse",
        )
        assert len(after_first) == 0, (
            f"hook should have been removed after first firing (once=True); "
            f"still present: {[e.config.command for e in after_first]}"
        )

        # Step 4 — second PreToolUse firing. Hook is gone; nothing fires.
        second_run_yields = []
        async for item in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_use_id": "u2"},
            ctx,
        ):
            second_run_yields.append(item)

        # No progress messages because no hooks matched.
        assert not any(
            "echo audit" in str(item.get("message", "") or {})
            for item in second_run_yields
        ), f"hook fired twice — once removal failed; yields={second_run_yields!r}"

    @pytest.mark.asyncio
    async def test_once_true_does_not_remove_on_blocking_error(self):
        """A ``once: true`` hook that BLOCKS (exit 2) is NOT removed —
        the on_success callback only fires for successful runs (exit 0).

        Otherwise an audit hook that detected a violation and blocked the
        tool call would silently uninstall itself, defeating the audit
        purpose.
        """
        reg = SessionHookRegistry()
        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            session_hook_registry=reg,
            session_id="s",
        )
        # Hook exits 2 → blocking error.
        await register_skill_hooks(
            registry=reg, session_id="s",
            skill_hooks={"PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": "echo blocked >&2; exit 2", "once": True},
            ]}]},
            skill_name="blocker",
        )

        # Fire it once.
        async for _ in _run_hooks_for_event(
            "PreToolUse", "Bash", {"tool_name": "Bash"}, ctx,
        ):
            pass
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Hook still registered — blocking-error firing didn't trigger removal.
        after = await get_session_hooks(
            registry=reg, session_id="s", event="PreToolUse",
        )
        assert len(after) == 1


class TestOnceTrueRaceRegression:
    @pytest.mark.asyncio
    async def test_concurrent_firings_of_once_true_only_remove_once(self):
        """Race regression: two concurrent firings of the same once=True hook.

        Both firings see the hook in the registry's collect step; both
        execute (we accept this — TS does too); both schedule removal via
        ``asyncio.create_task``. The first removal succeeds; the second
        finds nothing and returns False. The end state is "hook removed
        exactly once, registry is clean."

        We're testing the *removal* race, not the firing race — the chapter's
        ``once: true`` contract is "removed after first successful firing,"
        not "fires at most once under contention." The latter would require
        a check-and-set inside the executor, which neither TS nor Phase 3
        implements.
        """
        reg = SessionHookRegistry()
        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            session_hook_registry=reg,
            session_id="s",
        )
        await register_skill_hooks(
            registry=reg, session_id="s",
            skill_hooks={"PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": "echo once", "once": True},
            ]}]},
            skill_name="x",
        )

        async def fire():
            async for _ in _run_hooks_for_event(
                "PreToolUse", "Bash", {"tool_name": "Bash"}, ctx,
            ):
                pass

        # Two concurrent firings.
        await asyncio.gather(fire(), fire())
        # Let both removal tasks settle.
        for _ in range(5):
            await asyncio.sleep(0)

        # Registry is empty — exactly-once removal happened (the second
        # remove found nothing and returned False).
        after = await get_session_hooks(
            registry=reg, session_id="s", event="PreToolUse",
        )
        assert after == []
