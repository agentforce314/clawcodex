"""Phase-3 / WI-3.1 — SessionHookRegistry tests.

Covers the in-memory registration API: add/remove/get/clear, concurrent
safety under ``asyncio.Lock`` (per N2), and the session-scoped indexing
that lets multiple sessions coexist without cross-pollution.
"""

from __future__ import annotations

import asyncio

import pytest

from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.session_hooks import (
    SessionHookEntry,
    SessionHookRegistry,
    add_session_hook,
    clear_session_hooks,
    get_session_hooks,
    remove_session_hook,
)


def _hook(cmd: str, *, matcher: str | None = None, once: bool = False) -> HookConfig:
    return HookConfig(
        type="command", command=cmd, matcher=matcher, once=once,
        source=HookSource.SESSION_HOOK,
    )


class TestAddRemove:
    @pytest.mark.asyncio
    async def test_add_then_get(self):
        reg = SessionHookRegistry()
        await add_session_hook(
            registry=reg, session_id="s1", event="PreToolUse",
            matcher="Bash", hook=_hook("echo a"),
        )
        entries = await get_session_hooks(
            registry=reg, session_id="s1", event="PreToolUse",
        )
        assert len(entries) == 1
        assert entries[0].config.command == "echo a"

    @pytest.mark.asyncio
    async def test_remove_returns_true(self):
        reg = SessionHookRegistry()
        h = _hook("echo a", matcher="Bash")
        await add_session_hook(
            registry=reg, session_id="s1", event="PreToolUse",
            matcher="Bash", hook=h,
        )
        removed = await remove_session_hook(
            registry=reg, session_id="s1", event="PreToolUse", hook=h,
        )
        assert removed is True
        entries = await get_session_hooks(
            registry=reg, session_id="s1", event="PreToolUse",
        )
        assert entries == []

    @pytest.mark.asyncio
    async def test_remove_nonexistent_returns_false(self):
        reg = SessionHookRegistry()
        h = _hook("echo nope")
        removed = await remove_session_hook(
            registry=reg, session_id="s1", event="PreToolUse", hook=h,
        )
        assert removed is False

    @pytest.mark.asyncio
    async def test_get_returns_empty_for_unknown_session(self):
        reg = SessionHookRegistry()
        entries = await get_session_hooks(
            registry=reg, session_id="never-added", event="PreToolUse",
        )
        assert entries == []

    @pytest.mark.asyncio
    async def test_session_isolation(self):
        # Hooks registered under session A don't leak into session B.
        reg = SessionHookRegistry()
        await add_session_hook(
            registry=reg, session_id="A", event="PreToolUse",
            matcher="Bash", hook=_hook("a"),
        )
        await add_session_hook(
            registry=reg, session_id="B", event="PreToolUse",
            matcher="Bash", hook=_hook("b"),
        )
        a_hooks = await get_session_hooks(
            registry=reg, session_id="A", event="PreToolUse",
        )
        b_hooks = await get_session_hooks(
            registry=reg, session_id="B", event="PreToolUse",
        )
        assert len(a_hooks) == 1
        assert len(b_hooks) == 1
        assert a_hooks[0].config.command == "a"
        assert b_hooks[0].config.command == "b"

    @pytest.mark.asyncio
    async def test_clear_removes_all_for_session(self):
        reg = SessionHookRegistry()
        for cmd in ("a", "b", "c"):
            await add_session_hook(
                registry=reg, session_id="s1", event="PreToolUse",
                matcher="Bash", hook=_hook(cmd),
            )
        cleared = await clear_session_hooks(registry=reg, session_id="s1")
        assert cleared == 3
        entries = await get_session_hooks(
            registry=reg, session_id="s1", event="PreToolUse",
        )
        assert entries == []

    @pytest.mark.asyncio
    async def test_skill_root_propagates_to_hookconfig(self):
        # The chapter wires CLAUDE_PLUGIN_ROOT (WI-1.5) from
        # ``hook.skill_root`` — registration must propagate the path.
        reg = SessionHookRegistry()
        await add_session_hook(
            registry=reg, session_id="s1", event="PreToolUse",
            matcher="Bash", hook=_hook("x"),
            skill_root="/path/to/skill",
        )
        entries = await get_session_hooks(
            registry=reg, session_id="s1", event="PreToolUse",
        )
        assert entries[0].config.skill_root == "/path/to/skill"


class TestConcurrentSafety:
    @pytest.mark.asyncio
    async def test_concurrent_adds_all_round_trip(self):
        # 50 concurrent adds across 5 coroutines → all 50 land. asyncio.Lock
        # serializes the inner mutation; outer concurrency is the test.
        reg = SessionHookRegistry()

        async def adder(start: int) -> None:
            for i in range(10):
                await add_session_hook(
                    registry=reg, session_id="s1", event="PreToolUse",
                    matcher="Bash", hook=_hook(f"cmd-{start}-{i}"),
                )

        await asyncio.gather(*(adder(s * 10) for s in range(5)))
        entries = await get_session_hooks(
            registry=reg, session_id="s1", event="PreToolUse",
        )
        assert len(entries) == 50

    @pytest.mark.asyncio
    async def test_concurrent_remove_idempotent(self):
        # Two coroutines try to remove the same hook concurrently. Lock
        # ensures exactly one wins.
        reg = SessionHookRegistry()
        h = _hook("once-cmd", matcher="Bash")
        await add_session_hook(
            registry=reg, session_id="s1", event="PreToolUse",
            matcher="Bash", hook=h,
        )
        results = await asyncio.gather(
            remove_session_hook(registry=reg, session_id="s1", event="PreToolUse", hook=h),
            remove_session_hook(registry=reg, session_id="s1", event="PreToolUse", hook=h),
        )
        # Exactly one remove succeeded.
        assert results.count(True) == 1
        assert results.count(False) == 1
