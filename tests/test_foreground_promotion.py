"""WI-10.1 tests — foreground / background promotion.

Covers the four chapter abort scenarios:

1. Foreground completes naturally (no bg signal).
2. Background signal during foreground → promotion (is_backgrounded
   flips, optional callback fires).
3. Background signal AFTER promotion is a no-op (idempotent).
4. Iterator raises mid-stream → exception propagates (no promotion).

Plus the lifecycle helpers (``register_agent_foreground`` /
``register_agent_background`` / ``unregister_agent_foreground``).
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, AsyncIterator

import pytest

from src.agent.foreground_promotion import (
    register_agent_background,
    register_agent_foreground,
    run_with_background_escape,
    unregister_agent_foreground,
)
from src.task_registry import RuntimeTaskRegistry
from src.tasks.local_agent import (
    LocalAgentTaskState,
    register_async_agent,
)
from src.tasks_core import generate_task_id


def _spawn_running(reg: RuntimeTaskRegistry, *, is_backgrounded: bool = True) -> str:
    """Spawn a teammate-like running agent and return its id."""
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    if not is_backgrounded:
        register_agent_foreground(agent_id=agent_id, registry=reg)
    return agent_id


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


def test_register_agent_foreground_flips_flag() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = _spawn_running(reg)
    assert reg.get(agent_id).is_backgrounded is True  # default

    register_agent_foreground(agent_id=agent_id, registry=reg)
    assert reg.get(agent_id).is_backgrounded is False


def test_register_agent_background_promotes_running_foreground() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = _spawn_running(reg, is_backgrounded=False)
    promoted = register_agent_background(agent_id=agent_id, registry=reg)
    assert promoted is True
    assert reg.get(agent_id).is_backgrounded is True


def test_register_agent_background_idempotent_when_already_backgrounded() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = _spawn_running(reg, is_backgrounded=True)
    promoted = register_agent_background(agent_id=agent_id, registry=reg)
    assert promoted is False  # was already backgrounded


def test_register_agent_background_noop_for_terminal_state() -> None:
    """A terminal agent can't be promoted — no point flipping
    ``is_backgrounded`` on a state nobody will read again."""
    from src.tasks.local_agent import complete_agent_task

    reg = RuntimeTaskRegistry()
    agent_id = _spawn_running(reg, is_backgrounded=False)
    complete_agent_task(agent_id, result_text="done", registry=reg)

    promoted = register_agent_background(agent_id=agent_id, registry=reg)
    assert promoted is False


def test_unregister_agent_foreground_drops_entry() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = _spawn_running(reg)
    unregister_agent_foreground(agent_id=agent_id, registry=reg)
    assert reg.get(agent_id) is None


# ---------------------------------------------------------------------------
# run_with_background_escape — the four abort scenarios
# ---------------------------------------------------------------------------


async def _async_iter(items: list[Any]) -> AsyncIterator[Any]:
    """Helper: produce items with a small sleep between yields so
    bg_signal can fire mid-iteration."""
    for item in items:
        await asyncio.sleep(0.01)
        yield item


@pytest.mark.asyncio
async def test_foreground_completes_naturally_no_bg_signal() -> None:
    """Scenario 1: bg signal never fires; iterator yields all items
    and ``was_backgrounded`` is False."""
    bg = asyncio.Event()
    iterator = _async_iter(["a", "b", "c"])

    messages, was_backgrounded = await run_with_background_escape(
        iterator, background_signal=bg,
    )

    assert messages == ["a", "b", "c"]
    assert was_backgrounded is False


@pytest.mark.asyncio
async def test_bg_signal_during_iteration_promotes_cleanly() -> None:
    """Scenario 2: bg signal fires after some messages; the
    foreground iterator's pending ``next`` is cancelled, ``was_backgrounded``
    is True, and any messages received before the signal are
    preserved."""
    bg = asyncio.Event()

    async def slow_iter() -> AsyncIterator[str]:
        yield "first"
        await asyncio.sleep(0.5)  # bg signal fires during this sleep
        yield "second"

    async def trigger_bg() -> None:
        await asyncio.sleep(0.05)
        bg.set()

    iterator = slow_iter()

    messages, was_backgrounded = await asyncio.gather(
        run_with_background_escape(iterator, background_signal=bg),
        trigger_bg(),
    )
    msgs, was_bg = messages
    assert was_bg is True
    # First message arrived before bg signal; second was cancelled.
    assert msgs == ["first"]


@pytest.mark.asyncio
async def test_bg_signal_fires_on_background_callback() -> None:
    """The optional ``on_background`` callback runs in the bg-signal
    branch — caller's hook to swap abort controllers / flip
    is_backgrounded atomically with the iterator drain."""
    bg = asyncio.Event()
    fired = []

    async def slow_iter() -> AsyncIterator[str]:
        yield "x"
        await asyncio.sleep(0.5)

    async def trigger() -> None:
        await asyncio.sleep(0.05)
        bg.set()

    iterator = slow_iter()

    async def on_bg() -> None:
        fired.append("called")

    msgs_and_flag, _ = await asyncio.gather(
        run_with_background_escape(
            iterator, background_signal=bg, on_background=on_bg,
        ),
        trigger(),
    )
    _, was_bg = msgs_and_flag
    assert was_bg is True
    assert fired == ["called"]


@pytest.mark.asyncio
async def test_iterator_exception_propagates() -> None:
    """Scenario 4: the iterator raises; the exception propagates
    rather than being swallowed by the race."""
    bg = asyncio.Event()

    async def boom() -> AsyncIterator[str]:
        yield "ok"
        raise RuntimeError("kaboom")

    iterator = boom()

    with pytest.raises(RuntimeError, match="kaboom"):
        await run_with_background_escape(iterator, background_signal=bg)


@pytest.mark.asyncio
async def test_bg_callback_exception_does_not_break_promotion() -> None:
    """A misbehaving on_background callback shouldn't block the
    promotion path — log the exception and continue. ``was_backgrounded``
    is still True."""
    bg = asyncio.Event()

    async def slow_iter() -> AsyncIterator[str]:
        await asyncio.sleep(0.5)
        yield "never"

    async def trigger() -> None:
        await asyncio.sleep(0.05)
        bg.set()

    def bad_callback() -> None:
        raise ZeroDivisionError("on_background blew up")

    iterator = slow_iter()

    msgs_and_flag, _ = await asyncio.gather(
        run_with_background_escape(
            iterator, background_signal=bg, on_background=bad_callback,
        ),
        trigger(),
    )
    _, was_bg = msgs_and_flag
    assert was_bg is True


@pytest.mark.asyncio
async def test_bg_signal_already_set_returns_immediately() -> None:
    """Edge case: bg signal is already set when ``run_with_background_escape``
    starts. The race resolves to bg-signal on the first iteration;
    ``was_backgrounded`` is True with no messages."""
    bg = asyncio.Event()
    bg.set()  # pre-fired

    async def slow_iter() -> AsyncIterator[str]:
        await asyncio.sleep(0.5)
        yield "never"

    iterator = slow_iter()

    messages, was_backgrounded = await run_with_background_escape(
        iterator, background_signal=bg,
    )
    # Either no messages (bg won the first race) or some — both
    # are correct under asyncio scheduling. Key invariant:
    # ``was_backgrounded`` is True.
    assert was_backgrounded is True


# ---------------------------------------------------------------------------
# Integration — promotion through the registry mutator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_promotion_flow_via_on_background_callback() -> None:
    """End-to-end: foreground spawn → bg signal mid-iteration →
    on_background fires → registry shows ``is_backgrounded=True``."""
    reg = RuntimeTaskRegistry()
    agent_id = _spawn_running(reg, is_backgrounded=False)
    assert reg.get(agent_id).is_backgrounded is False

    bg = asyncio.Event()

    async def slow_iter() -> AsyncIterator[str]:
        yield "a"
        await asyncio.sleep(0.5)
        yield "b"

    async def trigger() -> None:
        await asyncio.sleep(0.05)
        bg.set()

    def on_bg() -> None:
        register_agent_background(agent_id=agent_id, registry=reg)

    iterator = slow_iter()

    msgs_and_flag, _ = await asyncio.gather(
        run_with_background_escape(
            iterator, background_signal=bg, on_background=on_bg,
        ),
        trigger(),
    )
    msgs, was_bg = msgs_and_flag
    assert was_bg is True
    # Registry reflects the promotion.
    assert reg.get(agent_id).is_backgrounded is True
