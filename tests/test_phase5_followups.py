"""Phase-5 follow-ups (D3 + D4) regression tests.

D3 — production wiring of ``forked_skill_runner``. Pre-D3, the prior
Phase-5 commit summary claimed bootstrap wired the runner but in fact
``bootstrap_graph.py`` / ``bootstrap/state.py`` had zero references.
D3 adds ``make_forked_skill_runner(provider, tool_registry)`` and
``wire_forked_skill_runner(...)`` plus the bootstrap-side wiring
(tui, repl, headless, subagent_context).

D4 — hook registration ordering for forked skills. Pre-D4,
``execute_forked_skill`` registered hooks AFTER the runner returned.
The forked sub-agent's SubagentStop had already fired by then, so the
hook would only fire on FUTURE sub-agents' stops, not THIS skill's
own stop. The B1 conversion was plumbed but its outcome was unrealized.
D4 moves registration BEFORE the runner with rollback on error.

Tests:
  * D3 — factory shape + signature + bootstrap-side smoke.
  * D4 — registration-order regression (entries visible BEFORE runner
    sees its first call); firing-semantic test (driving
    ``_run_hooks_for_event("SubagentStop", ...)`` AFTER forked execution
    fires the registered hook); rollback-on-runner-error.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.hooks.config_manager import HookConfigManager, HookConfigSnapshot
from src.hooks.hook_executor import _run_hooks_for_event
from src.hooks.registry import AsyncHookRegistry
from src.hooks.session_hooks import SessionHookRegistry, get_session_hooks
from src.tool_system.tools.skill import SkillTool
from src.tool_system.tools.skill_fork import (
    make_forked_skill_runner,
    wire_forked_skill_runner,
)


@dataclass
class _MockOptions:
    hooks: dict[str, Any] | None = None
    tools: list[Any] = field(default_factory=list)


@dataclass
class _MockContext:
    options: _MockOptions = field(default_factory=_MockOptions)
    hook_config_manager: Any | None = None
    workspace_trusted: bool = True
    abort_controller: Any | None = None
    session_hook_registry: Any | None = None
    session_id: str | None = None
    workspace_root: Path | None = None
    forked_skill_runner: Any | None = None
    tool_use_id: str | None = None


def _empty_config_manager() -> HookConfigManager:
    m = HookConfigManager(registry=AsyncHookRegistry(), settings_path="/dev/null")
    m._snapshot = HookConfigSnapshot(hooks={}, timestamp=0.0, source_path=None)
    return m


def _write_skill_with_stop_hook(skills_dir: Path, *, name: str) -> Path:
    """Write a SKILL.md with frontmatter ``context: fork`` AND a Stop hook
    so the D4 path (Stop→SubagentStop conversion + firing semantic)
    becomes exercisable.
    """
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: D4 firing-semantic test skill\n"
        "context: fork\n"
        "hooks:\n"
        "  Stop:\n"
        "    - matcher: \"\"\n"
        "      hooks:\n"
        "        - type: command\n"
        "          command: echo cleanup-on-stop\n"
        "---\n\nbody\n"
    )
    return d / "SKILL.md"


# ---------------------------------------------------------------------------
# D3 — production runner factory
# ---------------------------------------------------------------------------


class TestD3ProductionRunnerFactory:
    def test_factory_returns_async_callable(self):
        # Factory has the right shape: returns a coroutine function with
        # the kwargs the execute_forked_skill caller uses.
        runner = make_forked_skill_runner(
            provider=object(),       # placeholder; we don't invoke
            tool_registry=object(),
        )
        import inspect
        assert inspect.iscoroutinefunction(runner)
        sig = inspect.signature(runner)
        params = sig.parameters
        for name in ("prompt", "allowed_tools", "model", "effort", "parent_context"):
            assert name in params, f"runner missing param: {name}"

    def test_wire_forked_skill_runner_sets_field(self):
        # Bootstrap-side wiring: a fresh context with no runner gets one
        # mounted by ``wire_forked_skill_runner``.
        ctx = _MockContext()
        assert ctx.forked_skill_runner is None
        wire_forked_skill_runner(
            tool_context=ctx,
            provider=object(),
            tool_registry=object(),
        )
        assert ctx.forked_skill_runner is not None

    def test_wire_is_idempotent_does_not_clobber_test_stub(self):
        # Test fixtures inject stub runners; bootstrap-style wiring must
        # NOT overwrite them. Idempotency check.
        async def stub(**kwargs):
            return ""
        ctx = _MockContext(forked_skill_runner=stub)
        wire_forked_skill_runner(
            tool_context=ctx,
            provider=object(),
            tool_registry=object(),
        )
        assert ctx.forked_skill_runner is stub  # unchanged


# ---------------------------------------------------------------------------
# D4 — hook registration ordering
# ---------------------------------------------------------------------------


class TestD4RegistrationOrdering:
    @pytest.mark.asyncio
    async def test_hooks_registered_before_runner_runs(self, tmp_path):
        # Pre-D4 ordering: register AFTER runner. Test inverts that:
        # capture the registry state at the moment the runner is called;
        # if the hook is already there, registration happened before.
        skills_dir = tmp_path / "skills"
        _write_skill_with_stop_hook(skills_dir, name="d4skill")

        registry = SessionHookRegistry()
        captured_hooks_at_runner_call: list = []

        async def stub_runner(**kwargs):
            # At this point, hooks must already be registered.
            captured_hooks_at_runner_call.extend(
                await get_session_hooks(
                    registry=registry, session_id="s-d4", event="SubagentStop",
                )
            )
            return "result"

        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            session_hook_registry=registry,
            session_id="s-d4",
            forked_skill_runner=stub_runner,
        )

        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            result = await SkillTool.call({"skill": "d4skill"}, ctx)

        assert result.is_error is False
        # Hook was already registered when the runner was called.
        # Pre-D4 this list would have been empty — the bug.
        assert len(captured_hooks_at_runner_call) == 1
        assert captured_hooks_at_runner_call[0].config.command == "echo cleanup-on-stop"

    @pytest.mark.asyncio
    async def test_subagentstop_hook_fires_after_forked_execution(self, tmp_path):
        # The chapter intent ("an agent's stop-verification hook fires
        # when this agent stops") realized end-to-end:
        #   1. Forked skill registers a Stop hook (converted to SubagentStop).
        #   2. After forked execution completes, the parent fires
        #      SubagentStop for the sub-agent.
        #   3. The registered hook fires.
        skills_dir = tmp_path / "skills"
        _write_skill_with_stop_hook(skills_dir, name="d4fire")

        registry = SessionHookRegistry()

        async def stub_runner(**kwargs):
            return "sub-agent done"

        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            session_hook_registry=registry,
            session_id="s-fire",
            forked_skill_runner=stub_runner,
        )

        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            result = await SkillTool.call({"skill": "d4fire"}, ctx)

        assert result.is_error is False

        # After forked execution: drive SubagentStop and verify the
        # registered hook fires. This is the firing-semantic test the
        # team-lead asked for.
        fired_commands: list[str] = []
        async for item in _run_hooks_for_event(
            "SubagentStop", None,
            {"hook_event": "SubagentStop", "subagent_id": "sub-1"},
            ctx,
        ):
            msg = item.get("message")
            data = getattr(msg, "data", None) or {}
            if isinstance(data, dict) and data.get("command"):
                fired_commands.append(str(data["command"]))

        assert any("cleanup-on-stop" in c for c in fired_commands), (
            f"forked-skill SubagentStop hook did not fire after the "
            f"sub-agent's stop. Pre-D4 this was the bug: registration "
            f"happened AFTER runner return so SubagentStop had already "
            f"fired. fired_commands={fired_commands!r}"
        )


# ---------------------------------------------------------------------------
# D4 — rollback on runner error
# ---------------------------------------------------------------------------


class TestD4RollbackOnRunnerError:
    @pytest.mark.asyncio
    async def test_runner_exception_rolls_back_hook_registration(self, tmp_path):
        # When the runner raises, the hooks that were registered before
        # the runner ran are rolled back. The session is left as if the
        # forked-skill invocation had never happened.
        skills_dir = tmp_path / "skills"
        _write_skill_with_stop_hook(skills_dir, name="d4rollback")

        registry = SessionHookRegistry()

        async def failing_runner(**kwargs):
            raise RuntimeError("simulated forked-skill failure")

        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            session_hook_registry=registry,
            session_id="s-rollback",
            forked_skill_runner=failing_runner,
        )

        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            result = await SkillTool.call({"skill": "d4rollback"}, ctx)

        # Runner errored → ToolResult is_error=True.
        assert result.is_error is True
        assert "simulated forked-skill failure" in result.output["error"]

        # Hooks rolled back: registry empty under SubagentStop.
        sub_hooks = await get_session_hooks(
            registry=registry, session_id="s-rollback", event="SubagentStop",
        )
        assert sub_hooks == [], (
            f"D4 rollback failed: hooks remained after runner error. "
            f"left over: {[e.config.command for e in sub_hooks]!r}"
        )

    @pytest.mark.asyncio
    async def test_runner_success_keeps_hook_registration(self, tmp_path):
        # Counterpart: when the runner succeeds, hooks stay registered
        # (so the firing-semantic test above remains green).
        skills_dir = tmp_path / "skills"
        _write_skill_with_stop_hook(skills_dir, name="d4keep")

        registry = SessionHookRegistry()

        async def stub_runner(**kwargs):
            return "ok"

        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            session_hook_registry=registry,
            session_id="s-keep",
            forked_skill_runner=stub_runner,
        )

        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            await SkillTool.call({"skill": "d4keep"}, ctx)

        sub_hooks = await get_session_hooks(
            registry=registry, session_id="s-keep", event="SubagentStop",
        )
        assert len(sub_hooks) == 1
