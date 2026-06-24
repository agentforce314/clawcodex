"""Wire two dormant keybindings (TUI UX PR 7).

* Ctrl+R opens the existing reverse-history-search dialog (was reachable
  only via /history; TS defaultBindings history:search = ctrl+r).
* Double-Esc on an idle, non-empty draft clears it and saves it to history
  (TS handleEscape). The existing Esc priorities — interrupt a running
  agent, then pop a queued prompt back into the input — still win first.
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


async def _boot(app: ClawCodexTUI, pilot) -> None:
    await pilot.pause()
    await pilot.press("down")  # → "Yes, I accept"
    await pilot.press("enter")
    await pilot.pause()


@pytest.mark.asyncio
async def test_ctrl_r_opens_history_search(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await _boot(app, pilot)
        app.history_store.append("an earlier prompt")  # non-empty history
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HistorySearchScreen"


@pytest.mark.asyncio
async def test_double_esc_clears_nonempty_draft_and_saves(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await _boot(app, pilot)
        prompt = app.screen.query_one(PromptInput)
        for ch in "draft":
            await pilot.press(ch)
        await pilot.pause()
        # First Esc: armed, draft intact.
        await pilot.press("escape")
        await pilot.pause()
        assert prompt.current_text() == "draft"
        assert app._pending_clear_at is not None
        # Second Esc: cleared + saved to history.
        await pilot.press("escape")
        await pilot.pause()
        assert prompt.current_text() == ""
        # Saved to BOTH the persistent store (ctrl+r dialog) and the
        # in-session ↑/↓ history (recoverable by pressing Up).
        assert any(r.prompt == "draft" for r in app.history_store.recent())
        assert "draft" in prompt._history


@pytest.mark.asyncio
async def test_single_esc_on_empty_draft_is_noop(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await _boot(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        # Nothing to clear → not armed, no crash.
        assert app._pending_clear_at is None


@pytest.mark.asyncio
async def test_esc_with_queued_prompt_pops_does_not_arm_clear(tmp_path):
    # Priority 2 (pop queue) must return before Priority 3 — the merged
    # draft must NOT arm the clear toast. Guards the `return` after pop.
    app = _make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await _boot(app, pilot)
        # Simulate an idle non-empty queue.
        app.app_state.queued_prompts.append("queued one")
        await pilot.press("escape")
        await pilot.pause()
        prompt = app.screen.query_one(PromptInput)
        assert "queued one" in prompt.current_text()  # popped into the input
        assert app._pending_clear_at is None  # Priority 3 did NOT run


@pytest.mark.asyncio
async def test_esc_while_busy_interrupts_does_not_clear(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await _boot(app, pilot)
        prompt = app.screen.query_one(PromptInput)
        for ch in "keep":
            await pilot.press(ch)
        # Simulate a run in flight: cancel() returns True (interrupt path).
        app._agent_bridge.cancel = lambda: True  # type: ignore[method-assign]
        await pilot.press("escape")
        await pilot.pause()
        # Priority 1 interrupt won — the draft is NOT touched by double-esc.
        assert prompt.current_text() == "keep"
        assert app._pending_clear_at is None
