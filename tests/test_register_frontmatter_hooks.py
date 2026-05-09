"""Phase-3 / WI-3.3 — register_frontmatter_hooks tests.

The general frontmatter-hook entry point. Critically: **this is the home of
the ``Stop → SubagentStop`` conversion** (gap-analysis B1 correction).
``register_skill_hooks`` does NOT do the conversion.

Tests cover both modes:
  * ``is_agent=False`` (default; for skills) — Stop stays Stop.
  * ``is_agent=True`` (for sub-agent frontmatter) — Stop becomes SubagentStop.

Plus the basics already covered for ``register_skill_hooks``: registration
count, source tag, ``once: true`` wiring.
"""

from __future__ import annotations

import pytest

from src.hooks.hook_types import HookSource
from src.hooks.register_frontmatter_hooks import register_frontmatter_hooks
from src.hooks.session_hooks import SessionHookRegistry, get_session_hooks


AGENT_FRONTMATTER = {
    "Stop": [
        {"matcher": "", "hooks": [{"type": "command", "command": "echo cleanup"}]},
    ],
    "PreToolUse": [
        {"matcher": "Bash", "hooks": [
            {"type": "command", "command": "echo pre"},
        ]},
    ],
}


class TestStopToSubagentStopConversion:
    @pytest.mark.asyncio
    async def test_is_agent_true_converts_stop_to_subagentstop(self):
        # B1: this is the conversion's only home.
        reg = SessionHookRegistry()
        await register_frontmatter_hooks(
            registry=reg, session_id="s1",
            frontmatter_hooks=AGENT_FRONTMATTER,
            source_name="agent foo", is_agent=True,
        )
        # Stop hook is now under SubagentStop, NOT under Stop.
        stop_hooks = await get_session_hooks(
            registry=reg, session_id="s1", event="Stop",
        )
        assert stop_hooks == []
        sub_hooks = await get_session_hooks(
            registry=reg, session_id="s1", event="SubagentStop",
        )
        assert len(sub_hooks) == 1
        assert sub_hooks[0].config.command == "echo cleanup"

    @pytest.mark.asyncio
    async def test_is_agent_false_keeps_stop(self):
        reg = SessionHookRegistry()
        await register_frontmatter_hooks(
            registry=reg, session_id="s1",
            frontmatter_hooks=AGENT_FRONTMATTER,
            source_name="non-agent caller", is_agent=False,
        )
        stop_hooks = await get_session_hooks(
            registry=reg, session_id="s1", event="Stop",
        )
        assert len(stop_hooks) == 1
        sub_hooks = await get_session_hooks(
            registry=reg, session_id="s1", event="SubagentStop",
        )
        assert sub_hooks == []

    @pytest.mark.asyncio
    async def test_subagentstop_unchanged_regardless_of_is_agent(self):
        # If the frontmatter explicitly declares SubagentStop, both modes
        # leave it under SubagentStop. The conversion only touches Stop.
        fm = {
            "SubagentStop": [{"matcher": "", "hooks": [
                {"type": "command", "command": "echo s"}
            ]}],
        }
        for is_agent in (True, False):
            reg = SessionHookRegistry()
            await register_frontmatter_hooks(
                registry=reg, session_id="s1",
                frontmatter_hooks=fm,
                source_name="src", is_agent=is_agent,
            )
            sub_hooks = await get_session_hooks(
                registry=reg, session_id="s1", event="SubagentStop",
            )
            assert len(sub_hooks) == 1
            stop_hooks = await get_session_hooks(
                registry=reg, session_id="s1", event="Stop",
            )
            assert stop_hooks == []

    @pytest.mark.asyncio
    async def test_non_stop_events_unchanged_under_is_agent(self):
        # PreToolUse stays PreToolUse even with is_agent=True.
        reg = SessionHookRegistry()
        await register_frontmatter_hooks(
            registry=reg, session_id="s1",
            frontmatter_hooks=AGENT_FRONTMATTER,
            source_name="agent foo", is_agent=True,
        )
        pre = await get_session_hooks(
            registry=reg, session_id="s1", event="PreToolUse",
        )
        assert len(pre) == 1
        assert pre[0].config.command == "echo pre"


class TestBasicRegistration:
    @pytest.mark.asyncio
    async def test_returns_zero_for_empty(self):
        reg = SessionHookRegistry()
        n = await register_frontmatter_hooks(
            registry=reg, session_id="s1",
            frontmatter_hooks=None, source_name="x",
        )
        assert n == 0

    @pytest.mark.asyncio
    async def test_session_hook_source_tag(self):
        reg = SessionHookRegistry()
        await register_frontmatter_hooks(
            registry=reg, session_id="s1",
            frontmatter_hooks=AGENT_FRONTMATTER,
            source_name="x", is_agent=True,
        )
        sub = await get_session_hooks(
            registry=reg, session_id="s1", event="SubagentStop",
        )
        assert sub[0].config.source == HookSource.SESSION_HOOK
