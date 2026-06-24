"""Unit tests for :class:`PromptInputFooter`.

Round 2 / WI-R2.3 of the ch13 refactor.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult

from src.tui.vim import VimState
from src.tui.widgets.prompt_input_footer import (
    FooterHint,
    PromptInputFooter,
    _SEPARATOR,
)


class _Host(App):
    def __init__(self, footer: PromptInputFooter) -> None:
        super().__init__()
        self._footer = footer

    def compose(self) -> ComposeResult:
        yield self._footer


# ---- pure-function tests ----


def test_separator_matches_status_line_style():
    """The separator must match the one ``StatusLine`` uses for visual rhythm."""
    assert _SEPARATOR == " · "


def test_footer_hint_is_frozen_dataclass():
    """``FooterHint`` should be hashable so callers can use it in sets / dict keys."""
    hint = FooterHint(keys="Esc", label="cancel")
    assert hint.keys == "Esc"
    assert hint.label == "cancel"
    assert hint.when is None
    # Frozen dataclasses raise on attribute assignment.
    with pytest.raises(Exception):
        hint.keys = "x"  # type: ignore[misc]


# ---- widget tests ----


@pytest.mark.asyncio
async def test_default_hints_rendered_with_vim_off():
    vim = VimState(enabled=False)
    footer = PromptInputFooter(vim_state=vim)
    async with _Host(footer).run_test() as pilot:
        await pilot.pause()
        line = footer.last_line
        # TS idle footer is just "? for shortcuts" (full list in the ? panel).
        assert "? for shortcuts" in line
        assert "i/esc vim" not in line  # vim hint filtered when vim off
        assert "esc to interrupt" not in line  # not loading


@pytest.mark.asyncio
async def test_vim_hint_appears_when_vim_on():
    vim = VimState(enabled=True)
    footer = PromptInputFooter(vim_state=vim)
    async with _Host(footer).run_test() as pilot:
        await pilot.pause()
        line = footer.last_line
        assert "i/esc vim" in line


@pytest.mark.asyncio
async def test_refresh_hints_picks_up_vim_toggle():
    """Toggling vim mode after mount must update the visible hints."""
    vim = VimState(enabled=False)
    footer = PromptInputFooter(vim_state=vim)
    async with _Host(footer).run_test() as pilot:
        await pilot.pause()
        assert "i/esc vim" not in footer.last_line
        vim.set_enabled(True)
        footer.refresh_hints()
        await pilot.pause()
        assert "i/esc vim" in footer.last_line


@pytest.mark.asyncio
async def test_loading_collapses_to_interrupt_hint():
    """While a run is in flight the footer shows only 'esc to interrupt'."""
    footer = PromptInputFooter(vim_state=VimState(enabled=False))
    async with _Host(footer).run_test() as pilot:
        await pilot.pause()
        footer.set_loading(True)
        await pilot.pause()
        line = footer.last_line
        assert line == "esc to interrupt"
        assert "shortcuts" not in line  # idle hints hidden while busy
        footer.set_loading(False)
        await pilot.pause()
        assert "? for shortcuts" in footer.last_line


@pytest.mark.asyncio
async def test_bash_mode_shows_bash_hint():
    footer = PromptInputFooter(vim_state=VimState(enabled=False))
    async with _Host(footer).run_test() as pilot:
        await pilot.pause()
        footer.set_bash_mode(True)
        await pilot.pause()
        assert footer.last_line == "! for bash mode"


@pytest.mark.asyncio
async def test_bash_mode_takes_precedence_over_loading():
    # TS parity: ModeIndicator returns "! for bash mode" before any
    # loading logic (PromptInputFooterLeftSide.tsx:317-319 ahead of :375).
    footer = PromptInputFooter(vim_state=VimState(enabled=False))
    async with _Host(footer).run_test() as pilot:
        await pilot.pause()
        footer.set_loading(True)
        footer.set_bash_mode(True)
        await pilot.pause()
        assert footer.last_line == "! for bash mode"


@pytest.mark.asyncio
async def test_status_line_drives_footer_loading(monkeypatch):
    """The footer's loading state rides the StatusLine's is_thinking signal."""
    from pathlib import Path

    from src.tui.state import AppState
    from src.tui.widgets.status_line import StatusLine

    monkeypatch.setattr(StatusLine, "refresh_custom_status", lambda self: None)
    footer = PromptInputFooter(vim_state=VimState(enabled=False))
    status = StatusLine(
        provider="p", model="m", workspace_root=Path("/tmp"), app_state=AppState()
    )

    class _Host2(App):
        def compose(self) -> ComposeResult:
            yield status
            yield footer

    async with _Host2().run_test() as pilot:
        await pilot.pause()
        status.bind_footer(footer)
        status.set_busy()  # is_thinking True → footer shows interrupt hint
        await pilot.pause()
        assert footer.last_line == "esc to interrupt"
        status.set_idle()
        await pilot.pause()
        assert "? for shortcuts" in footer.last_line


@pytest.mark.asyncio
async def test_custom_hints_provider_overrides_defaults():
    def provider() -> list[FooterHint]:
        return [FooterHint(keys="F1", label="help")]

    footer = PromptInputFooter(hints_provider=provider)
    async with _Host(footer).run_test() as pilot:
        await pilot.pause()
        assert footer.last_line == "F1 help"


@pytest.mark.asyncio
async def test_empty_hints_hides_widget():
    def provider() -> list[FooterHint]:
        return []

    footer = PromptInputFooter(hints_provider=provider)
    async with _Host(footer).run_test() as pilot:
        await pilot.pause()
        assert footer.has_class("-hidden")
        assert footer.last_line == ""


@pytest.mark.asyncio
async def test_when_predicate_filters_hints():
    def provider() -> list[FooterHint]:
        return [
            FooterHint(keys="A", label="always"),
            FooterHint(keys="N", label="never", when=lambda: False),
            FooterHint(keys="Y", label="yes", when=lambda: True),
        ]

    footer = PromptInputFooter(hints_provider=provider)
    async with _Host(footer).run_test() as pilot:
        await pilot.pause()
        line = footer.last_line
        assert "A always" in line
        assert "N never" not in line
        assert "Y yes" in line


@pytest.mark.asyncio
async def test_when_predicate_exception_filters_silently():
    """A throwing predicate should be treated as 'false', not propagated.

    Widgets that call into application state must not crash the input
    UI when state lookup throws.
    """

    def boom() -> bool:
        raise RuntimeError("predicate bug")

    def provider() -> list[FooterHint]:
        return [
            FooterHint(keys="A", label="ok"),
            FooterHint(keys="B", label="broken", when=boom),
        ]

    footer = PromptInputFooter(hints_provider=provider)
    async with _Host(footer).run_test() as pilot:
        await pilot.pause()
        line = footer.last_line
        assert "A ok" in line
        assert "B broken" not in line


@pytest.mark.asyncio
async def test_provider_exception_falls_back_to_empty():
    def provider() -> list[FooterHint]:
        raise RuntimeError("provider bug")

    footer = PromptInputFooter(hints_provider=provider)
    async with _Host(footer).run_test() as pilot:
        await pilot.pause()
        # Empty hint set hides the widget.
        assert footer.has_class("-hidden")
        assert footer.last_line == ""


@pytest.mark.asyncio
async def test_hints_use_dot_separator():
    def provider() -> list[FooterHint]:
        return [
            FooterHint(keys="A", label="alpha"),
            FooterHint(keys="B", label="beta"),
        ]

    footer = PromptInputFooter(hints_provider=provider)
    async with _Host(footer).run_test() as pilot:
        await pilot.pause()
        assert footer.last_line == "A alpha · B beta"
