"""`?` shortcuts help panel (TUI UX PR 4).

Ports the ink ``PromptInputHelpMenu``: typing ``?`` into an empty prompt
toggles a muted shortcut panel (previously ``?`` did nothing). The panel
lists only wired bindings; the same list feeds ``/help``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult

from src.tui.widgets.prompt_input import PromptInput
from src.tui.widgets.shortcuts_help import (
    ShortcutsHelp,
    shortcut_lines,
    wired_shortcuts,
)


# ---- pure data ------------------------------------------------------------


def test_wired_shortcuts_lists_real_bindings_only():
    keys = {k for k, _ in wired_shortcuts(vim_enabled=False)}
    # Wired today (@ + ctrl+r + double-esc landed in later PRs):
    assert {
        "/", "!", "@", "#", "tab", "ctrl+r", "ctrl+l", "ctrl+o",
        "esc", "esc esc", "?", "/exit",
    } <= keys
    # NOT wired yet — must not be advertised (cosmetic toggle would mislead):
    assert "shift+tab" not in keys


def test_vim_shortcuts_appended_only_when_vim_on():
    off = wired_shortcuts(vim_enabled=False)
    on = wired_shortcuts(vim_enabled=True)
    assert len(on) == len(off) + 1
    assert any("vim" in action for _, action in on)
    assert all("vim" not in action for _, action in off)


def test_shortcut_lines_are_key_space_action():
    assert "/ for commands" in shortcut_lines(vim_enabled=False)
    assert "! for bash mode" in shortcut_lines(vim_enabled=False)


# ---- widget harness -------------------------------------------------------


class _Host(App):
    def __init__(self, prompt: PromptInput) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        yield self._prompt


def _make_prompt(vim_mode: bool = False) -> PromptInput:
    return PromptInput(words_provider=lambda: [], vim_mode=vim_mode)


@pytest.mark.asyncio
async def test_question_mark_toggles_help_and_is_consumed():
    prompt = _make_prompt()
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        await pilot.press("?")
        await pilot.pause()
        assert prompt.help_open is True
        # The `?` is consumed — it never lands in the draft.
        assert prompt.current_text() == ""
        # Panel visible, footer hidden while open.
        assert not prompt._help.has_class("-hidden")
        assert prompt._footer.has_class("-hidden")
        # `?` again toggles it back off.
        await pilot.press("?")
        await pilot.pause()
        assert prompt.help_open is False
        assert prompt._help.has_class("-hidden")
        assert not prompt._footer.has_class("-hidden")


@pytest.mark.asyncio
async def test_escape_closes_help():
    prompt = _make_prompt()
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        await pilot.press("?")
        await pilot.pause()
        assert prompt.help_open is True
        await pilot.press("escape")
        await pilot.pause()
        assert prompt.help_open is False


@pytest.mark.asyncio
async def test_typing_after_help_closes_it_and_inserts():
    prompt = _make_prompt()
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        await pilot.press("?")
        await pilot.pause()
        assert prompt.help_open is True
        await pilot.press("h", "i")
        await pilot.pause()
        # Help closed and the keystrokes landed in the draft.
        assert prompt.help_open is False
        assert prompt.current_text() == "hi"


@pytest.mark.asyncio
async def test_footer_stays_hidden_when_loading_starts_during_help():
    """A run starting while the `?` panel is open must not render the
    footer's "esc to interrupt" beneath the panel (TS short-circuits the
    footer entirely while helpOpen)."""
    prompt = _make_prompt()
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        await pilot.press("?")
        await pilot.pause()
        assert prompt.help_open is True
        # Simulate the agent going busy while help is open.
        prompt._footer.set_loading(True)
        await pilot.pause()
        assert prompt._footer.has_class("-hidden")  # still suppressed
        assert not prompt._help.has_class("-hidden")  # panel still shown
        # Closing help restores the footer, now showing the interrupt hint.
        await pilot.press("escape")
        await pilot.pause()
        assert not prompt._footer.has_class("-hidden")
        assert prompt._footer.last_line == "esc to interrupt"


@pytest.mark.asyncio
async def test_question_mark_mid_text_is_literal_not_a_toggle():
    prompt = _make_prompt()
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        await pilot.press("a", "b", "?")
        await pilot.pause()
        # `?` after text is a normal character; no toggle.
        assert prompt.help_open is False
        assert prompt.current_text() == "ab?"
