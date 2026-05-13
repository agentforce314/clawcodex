"""Unit tests for :class:`PromptInputModeIndicator`.

Round 2 / WI-R2.2 of the ch13 refactor. The widget mounts inside a tiny
Textual harness so its lifecycle hooks (``on_mount`` / ``on_unmount``)
fire identically to a real :class:`PromptInput` host.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult

from src.tui.vim import Mode, VimState
from src.tui.widgets.prompt_input_mode_indicator import (
    PromptInputModeIndicator,
    _label_for,
)


class _Host(App):
    def __init__(self, indicator: PromptInputModeIndicator) -> None:
        super().__init__()
        self._indicator = indicator

    def compose(self) -> ComposeResult:
        yield self._indicator


# ---- pure-function tests (no Textual) ----


def test_label_for_insert_returns_insert_class():
    label, css = _label_for(Mode.INSERT)
    assert label == "[INSERT]"
    assert css == "-insert"


def test_label_for_normal_returns_normal_class():
    label, css = _label_for(Mode.NORMAL)
    assert label == "[NORMAL]"
    assert css == "-normal"


def test_add_mode_listener_fires_on_transition():
    """Sanity check the underlying VimState shim (WI-R2.1)."""
    vim = VimState(enabled=True)
    events: list[Mode] = []
    vim.add_mode_listener(events.append)
    vim.handle("escape")
    assert events == [Mode.NORMAL]
    # Redundant transition should NOT re-fire.
    vim.handle("escape")
    assert events == [Mode.NORMAL]


def test_add_mode_listener_unsubscribe_removes_callback():
    vim = VimState(enabled=True)
    events: list[Mode] = []
    unsub = vim.add_mode_listener(events.append)
    vim.handle("escape")
    unsub()
    vim.handle("i")
    # No further events after unsubscribe.
    assert events == [Mode.NORMAL]


def test_listener_exception_does_not_break_pipeline():
    vim = VimState(enabled=True)

    def boom(_: Mode) -> None:
        raise RuntimeError("listener bug")

    vim.add_mode_listener(boom)
    # Should not raise.
    result = vim.handle("escape")
    assert result.consumed is True


# ---- widget tests (Textual host) ----


@pytest.mark.asyncio
async def test_hidden_when_vim_off():
    vim = VimState(enabled=False)
    indicator = PromptInputModeIndicator(vim_state=vim)
    async with _Host(indicator).run_test() as pilot:
        await pilot.pause()
        assert indicator.has_class("-hidden")
        assert indicator.last_label == ""


@pytest.mark.asyncio
async def test_shows_insert_label_when_vim_on():
    vim = VimState(enabled=True)
    indicator = PromptInputModeIndicator(vim_state=vim)
    async with _Host(indicator).run_test() as pilot:
        await pilot.pause()
        assert not indicator.has_class("-hidden")
        assert indicator.last_label == "[INSERT]"
        assert indicator.has_class("-insert")


@pytest.mark.asyncio
async def test_transition_to_normal_updates_label():
    vim = VimState(enabled=True)
    indicator = PromptInputModeIndicator(vim_state=vim)
    async with _Host(indicator).run_test() as pilot:
        await pilot.pause()
        vim.handle("escape")
        await pilot.pause()
        assert indicator.last_label == "[NORMAL]"
        assert indicator.has_class("-normal")
        assert not indicator.has_class("-insert")


@pytest.mark.asyncio
async def test_transition_back_to_insert_clears_normal_class():
    vim = VimState(enabled=True)
    indicator = PromptInputModeIndicator(vim_state=vim)
    async with _Host(indicator).run_test() as pilot:
        await pilot.pause()
        vim.handle("escape")
        await pilot.pause()
        vim.handle("i")
        await pilot.pause()
        assert indicator.last_label == "[INSERT]"
        assert indicator.has_class("-insert")
        assert not indicator.has_class("-normal")


@pytest.mark.asyncio
async def test_refresh_mode_picks_up_enabled_toggle():
    """``VimState.set_enabled`` flips enabled state without changing the mode.

    The mode-listener fires only on real mode transitions, so the caller
    (``PromptInput.set_vim_mode``) must invoke :meth:`refresh_mode` to
    update visibility. Mirrors that contract here.
    """

    vim = VimState(enabled=False)
    indicator = PromptInputModeIndicator(vim_state=vim)
    async with _Host(indicator).run_test() as pilot:
        await pilot.pause()
        assert indicator.has_class("-hidden")
        vim.set_enabled(True)
        indicator.refresh_mode()
        await pilot.pause()
        assert not indicator.has_class("-hidden")
        assert indicator.last_label == "[INSERT]"


@pytest.mark.asyncio
async def test_prompt_input_mounts_indicator_and_footer():
    """The two new widgets must be composed inside :class:`PromptInput`.

    Round 2 / WI-R2.4 wiring check. Without this, the new widgets exist
    as files but are not visible to users.
    """
    from src.tui.widgets.prompt_input import PromptInput
    from src.tui.widgets.prompt_input_footer import PromptInputFooter

    class _Host2(App):
        def compose(self) -> ComposeResult:
            yield PromptInput(words_provider=lambda: [], vim_mode=False)

    async with _Host2().run_test() as pilot:
        await pilot.pause()
        prompt = pilot.app.screen.query_one(PromptInput)
        assert prompt.query_one(PromptInputModeIndicator) is not None
        assert prompt.query_one(PromptInputFooter) is not None


@pytest.mark.asyncio
async def test_prompt_input_set_vim_mode_refreshes_indicator():
    """``PromptInput.set_vim_mode`` must propagate to the indicator."""
    from src.tui.widgets.prompt_input import PromptInput

    class _Host3(App):
        def compose(self) -> ComposeResult:
            yield PromptInput(words_provider=lambda: [], vim_mode=False)

    async with _Host3().run_test() as pilot:
        await pilot.pause()
        prompt = pilot.app.screen.query_one(PromptInput)
        indicator = prompt.query_one(PromptInputModeIndicator)
        assert indicator.has_class("-hidden")
        prompt.set_vim_mode(True)
        await pilot.pause()
        assert not indicator.has_class("-hidden")
        assert indicator.last_label == "[INSERT]"


@pytest.mark.asyncio
async def test_unsubscribes_on_unmount():
    """No callbacks should fire after the widget is unmounted."""
    vim = VimState(enabled=True)
    indicator = PromptInputModeIndicator(vim_state=vim)
    async with _Host(indicator).run_test() as pilot:
        await pilot.pause()
        await indicator.remove()
        await pilot.pause()
        # If this triggered the listener, ``last_label`` would update
        # to ``[NORMAL]``. After unmount the indicator is detached.
        vim.handle("escape")
        # The cleanup path nulls out ``_unsubscribe`` — verify directly.
        assert indicator._unsubscribe is None
