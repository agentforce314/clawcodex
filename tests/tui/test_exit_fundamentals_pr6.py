"""Ctrl+C / Ctrl+D exit fundamentals (TUI UX PR 6).

Before this, a single Ctrl+C instantly killed the app and discarded the
draft, and Ctrl+D on an empty prompt did nothing (the stock Input swallows
it). Now both use a double-press exit (TS handleCtrlC / handleEmptyCtrlD):
first press arms + warns (Ctrl+C also clears a non-empty draft), a second
press within the window exits. Ctrl+D on a non-empty buffer still deletes
forward.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("textual")

from src.tui.app import ClawCodexTUI
from src.tui.widgets import PromptInput
from src.tool_system.context import ToolContext
from src.tool_system.registry import ToolRegistry


class _FakeProvider:
    provider_name = "fake"
    model = "claude-opus-4-8"


def _make_app(root: Path) -> ClawCodexTUI:
    return ClawCodexTUI(
        provider=_FakeProvider(),
        provider_name="fake",
        workspace_root=root,
        tool_registry=ToolRegistry(),
        tool_context=ToolContext(workspace_root=root),
        stream=False,
    )


async def _boot(app: ClawCodexTUI, pilot) -> list:
    """Dismiss the bypass gate and stub exit; return the exit-call sink."""
    await pilot.pause()
    await pilot.press("down")  # → "Yes, I accept"
    await pilot.press("enter")
    await pilot.pause()
    exited: list = []
    app.exit = lambda *a, **k: exited.append(True)  # type: ignore[method-assign]
    return exited


@pytest.mark.asyncio
async def test_single_ctrl_c_does_not_exit(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        exited = await _boot(app, pilot)
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert exited == []  # armed, not exited
        assert app._pending_exit_at != 0.0


@pytest.mark.asyncio
async def test_double_ctrl_c_exits(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        exited = await _boot(app, pilot)
        await pilot.press("ctrl+c")
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert exited == [True]


@pytest.mark.asyncio
async def test_ctrl_c_clears_nonempty_draft_first(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        exited = await _boot(app, pilot)
        prompt = app.screen.query_one(PromptInput)
        for ch in "hello":
            await pilot.press(ch)
        await pilot.pause()
        assert prompt.current_text() == "hello"
        await pilot.press("ctrl+c")
        await pilot.pause()
        # First press cleared the draft and armed; did not exit.
        assert prompt.current_text() == ""
        assert exited == []


@pytest.mark.asyncio
async def test_pending_exit_uses_none_sentinel_not_zero(tmp_path):
    # Regression: Linux CLOCK_MONOTONIC starts at boot, so a 0.0 default would
    # read as "armed" (now - 0.0 <= 0.8) within the first 0.8s of uptime and
    # exit on a SINGLE press. The unarmed state must be an explicit None
    # sentinel, and the armed check must guard it (see _request_exit).
    app = _make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await _boot(app, pilot)
        assert app._pending_exit_at is None  # not 0.0


@pytest.mark.asyncio
async def test_ctrl_d_on_empty_arms_then_exits(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        exited = await _boot(app, pilot)
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert exited == []  # armed
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert exited == [True]


@pytest.mark.asyncio
async def test_ctrl_d_on_nonempty_deletes_forward(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        exited = await _boot(app, pilot)
        prompt = app.screen.query_one(PromptInput)
        for ch in "abc":
            await pilot.press(ch)
        # Move cursor to start so ctrl+d (delete-forward) has something to eat.
        prompt._input.cursor_position = 0
        await pilot.pause()
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert prompt.current_text() == "bc"  # deleted 'a' forward
        assert exited == []  # not an exit while buffer non-empty
