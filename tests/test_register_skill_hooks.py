"""Phase-3 / WI-3.2 — register_skill_hooks tests.

Verifies:
  * Frontmatter ``hooks`` blocks register as session-scoped entries.
  * Hooks tagged with ``HookSource.SESSION_HOOK``.
  * ``skill_root`` propagates through to ``HookConfig.skill_root``.
  * **Skill hooks do NOT get the ``Stop → SubagentStop`` conversion** (B1
    correction — that's exclusive to ``register_frontmatter_hooks``).
  * ``once: true`` registers an ``on_success`` callback.
"""

from __future__ import annotations

import pytest

from src.hooks.hook_types import HookSource
from src.hooks.register_skill_hooks import register_skill_hooks
from src.hooks.session_hooks import SessionHookRegistry, get_session_hooks


SKILL_FRONTMATTER_HOOKS = {
    "PreToolUse": [
        {
            "matcher": "Bash",
            "hooks": [
                {"type": "command", "command": "echo audit", "once": True},
                {"type": "command", "command": "echo log"},
            ],
        }
    ],
    # Skill declares a Stop hook — registration must NOT rewrite this to
    # SubagentStop (skills run in the parent session, not a sub-agent).
    "Stop": [
        {
            "matcher": "",
            "hooks": [{"type": "command", "command": "echo cleanup"}],
        }
    ],
}


class TestRegisterSkillHooks:
    @pytest.mark.asyncio
    async def test_returns_zero_for_empty_hooks(self):
        reg = SessionHookRegistry()
        n = await register_skill_hooks(
            registry=reg, session_id="s1",
            skill_hooks=None, skill_name="empty",
        )
        assert n == 0

    @pytest.mark.asyncio
    async def test_registers_each_hook(self):
        reg = SessionHookRegistry()
        n = await register_skill_hooks(
            registry=reg, session_id="s1",
            skill_hooks=SKILL_FRONTMATTER_HOOKS, skill_name="audit-skill",
        )
        # 2 PreToolUse hooks + 1 Stop hook = 3 registrations.
        assert n == 3

    @pytest.mark.asyncio
    async def test_pretooluse_hooks_routed_correctly(self):
        reg = SessionHookRegistry()
        await register_skill_hooks(
            registry=reg, session_id="s1",
            skill_hooks=SKILL_FRONTMATTER_HOOKS, skill_name="x",
        )
        pre = await get_session_hooks(
            registry=reg, session_id="s1", event="PreToolUse",
        )
        assert len(pre) == 2
        commands = {e.config.command for e in pre}
        assert commands == {"echo audit", "echo log"}

    @pytest.mark.asyncio
    async def test_stop_hook_NOT_converted_to_subagentstop(self):
        # B1: skill hooks forward verbatim — the Stop event stays Stop.
        reg = SessionHookRegistry()
        await register_skill_hooks(
            registry=reg, session_id="s1",
            skill_hooks=SKILL_FRONTMATTER_HOOKS, skill_name="x",
        )
        stop_hooks = await get_session_hooks(
            registry=reg, session_id="s1", event="Stop",
        )
        assert len(stop_hooks) == 1
        assert stop_hooks[0].config.command == "echo cleanup"
        # Definitively NOT under SubagentStop.
        sub = await get_session_hooks(
            registry=reg, session_id="s1", event="SubagentStop",
        )
        assert sub == []

    @pytest.mark.asyncio
    async def test_session_hook_source_tag(self):
        reg = SessionHookRegistry()
        await register_skill_hooks(
            registry=reg, session_id="s1",
            skill_hooks=SKILL_FRONTMATTER_HOOKS, skill_name="x",
        )
        pre = await get_session_hooks(
            registry=reg, session_id="s1", event="PreToolUse",
        )
        for entry in pre:
            assert entry.config.source == HookSource.SESSION_HOOK

    @pytest.mark.asyncio
    async def test_skill_root_propagated(self):
        reg = SessionHookRegistry()
        await register_skill_hooks(
            registry=reg, session_id="s1",
            skill_hooks=SKILL_FRONTMATTER_HOOKS, skill_name="x",
            skill_root="/skills/audit",
        )
        pre = await get_session_hooks(
            registry=reg, session_id="s1", event="PreToolUse",
        )
        for entry in pre:
            assert entry.config.skill_root == "/skills/audit"

    @pytest.mark.asyncio
    async def test_once_true_wires_on_success(self):
        reg = SessionHookRegistry()
        await register_skill_hooks(
            registry=reg, session_id="s1",
            skill_hooks=SKILL_FRONTMATTER_HOOKS, skill_name="x",
        )
        pre = await get_session_hooks(
            registry=reg, session_id="s1", event="PreToolUse",
        )
        # The audit hook (once=True) has on_success; the log hook does not.
        audit = next(e for e in pre if e.config.command == "echo audit")
        log = next(e for e in pre if e.config.command == "echo log")
        assert audit.on_success is not None
        assert log.on_success is None
