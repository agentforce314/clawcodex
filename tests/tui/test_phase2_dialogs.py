"""End-to-end tests for the Phase 2 dialog screens.

We use Textual's ``App.run_test`` harness to push each dialog onto a
lightweight host screen, drive a few key presses, and assert the
dismissal result. The tests deliberately avoid coupling to the full
``ClawCodexTUI`` boot path (which pulls in providers and the agent
loop) so failures stay scoped to dialog behaviour.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Static

from src.tui.screens import (
    CostThresholdScreen,
    EffortPickerScreen,
    ExitFlowScreen,
    HistoryEntry,
    HistorySearchScreen,
    IdleReturnScreen,
    ModelPickerScreen,
    ThemePickerScreen,
    fuzzy_score,
)


class _Host(Screen):
    def compose(self) -> ComposeResult:
        yield Static("host")


class _DialogHost(App):
    """Minimal harness that boots straight into a blank screen so tests
    can push dialogs without dragging in the full TUI.
    """

    def on_mount(self) -> None:
        self.push_screen(_Host())


def _push(app: App, screen) -> asyncio.Future:
    """Push ``screen`` and return a future that resolves with the dismissal.

    Textual's ``wait_for_dismiss=True`` path requires an active
    worker, so we register an explicit callback instead.
    """

    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def _callback(result):
        if not future.done():
            future.set_result(result)

    app.push_screen(screen, callback=_callback)
    return future


# ------------------------------------------------------------------
# ModelPicker
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_picker_resolves_with_selected_model(tmp_path):
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = ModelPickerScreen(
            models=["gpt-4o", "claude-sonnet", "glm-4.5"],
            current_model="claude-sonnet",
        )
        result_future = _push(app, picker)
        await pilot.pause()
        # Cursor should start at the current model.
        assert picker._select is not None
        assert picker._select.current.value == "claude-sonnet"
        await pilot.press("down")
        await pilot.press("enter")
        result = await result_future
        assert result == "glm-4.5"


@pytest.mark.asyncio
async def test_model_picker_cancel_resolves_none():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = ModelPickerScreen(models=["a", "b"], current_model="a")
        result_future = _push(app, picker)
        await pilot.pause()
        await pilot.press("escape")
        result = await result_future
        assert result is None


# ------------------------------------------------------------------
# EffortPicker
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_effort_picker_returns_persisted_true_on_select():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = EffortPickerScreen(current="medium")
        result_future = _push(app, picker)
        await pilot.pause()
        await pilot.press("enter")  # confirm "medium"
        effort, persisted = await result_future
        assert persisted is True
        assert effort == "medium"


@pytest.mark.asyncio
async def test_effort_picker_auto_maps_to_none():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = EffortPickerScreen(current="high")
        result_future = _push(app, picker)
        await pilot.pause()
        await pilot.press("home")  # jump to "Auto"
        await pilot.press("enter")
        effort, persisted = await result_future
        assert persisted is True
        assert effort is None


# ------------------------------------------------------------------
# HistorySearch
# ------------------------------------------------------------------


def test_fuzzy_score_ranks_substring_above_subsequence():
    substring_match, substring_score = fuzzy_score("git status", "status")
    subsequence_match, subsequence_score = fuzzy_score("greatest", "gst")
    assert substring_match and subsequence_match
    assert substring_score > subsequence_score


def test_fuzzy_score_missing_subsequence_returns_false():
    matched, score = fuzzy_score("abcdef", "xyz")
    assert matched is False and score == 0


def test_fuzzy_score_empty_query_matches_everything():
    matched, score = fuzzy_score("whatever", "")
    assert matched is True and score == 0


@pytest.mark.asyncio
async def test_history_search_resolves_with_selected_prompt():
    entries = [
        HistoryEntry(prompt="git status"),
        HistoryEntry(prompt="git log"),
        HistoryEntry(prompt="rm -rf node_modules"),
    ]
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        dlg = HistorySearchScreen(entries=entries)
        result_future = _push(app, dlg)
        await pilot.pause()
        # Type "git" to filter — both matching entries should remain.
        for ch in "git":
            await pilot.press(ch)
        await pilot.pause()
        assert dlg._list is not None
        labels = [opt.label for opt in dlg._list.options]
        assert all("git" in label for label in labels)
        await pilot.press("enter")
        result = await result_future
        assert result in ("git status", "git log")


@pytest.mark.asyncio
async def test_history_search_escape_returns_none():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        dlg = HistorySearchScreen(entries=[HistoryEntry(prompt="anything")])
        result_future = _push(app, dlg)
        await pilot.pause()
        await pilot.press("escape")
        result = await result_future
        assert result is None


# ------------------------------------------------------------------
# CostThreshold / IdleReturn / ExitFlow / ThemePicker
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_threshold_enter_resolves_true():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        dlg = CostThresholdScreen(provider="openai", amount_usd=7.25)
        result_future = _push(app, dlg)
        await pilot.pause()
        await pilot.press("enter")
        assert (await result_future) is True


@pytest.mark.asyncio
async def test_idle_return_selects_clear():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        dlg = IdleReturnScreen(idle_minutes=12, total_input_tokens=120_000)
        result_future = _push(app, dlg)
        await pilot.pause()
        await pilot.press("down")  # move to "Start a new conversation"
        await pilot.press("enter")
        assert (await result_future) == "clear"


@pytest.mark.asyncio
async def test_exit_flow_cancel_returns_cancel():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        dlg = ExitFlowScreen(has_inflight_work=False)
        result_future = _push(app, dlg)
        await pilot.pause()
        await pilot.press("escape")
        assert (await result_future) == "cancel"


@pytest.mark.asyncio
async def test_theme_picker_selects_light():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        dlg = ThemePickerScreen(
            themes=["auto", "dark", "light", "claude"], current="dark"
        )
        result_future = _push(app, dlg)
        await pilot.pause()
        await pilot.press("down")  # cursor starts at "dark" (current)
        await pilot.press("enter")
        assert (await result_future) == "light"
