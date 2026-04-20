"""Unit tests for :class:`VimState`.

The state machine is plain Python so we test it without Textual.
"""

from __future__ import annotations

from src.tui.vim import Mode, VimState


def _new(enabled: bool = True) -> VimState:
    return VimState(enabled=enabled)


def test_disabled_state_never_consumes_keys():
    vim = _new(enabled=False)
    assert vim.handle("escape").consumed is False
    assert vim.handle("i").consumed is False
    assert vim.handle("h").consumed is False


def test_escape_enters_normal_mode():
    vim = _new()
    result = vim.handle("escape")
    assert result.mode is Mode.NORMAL
    assert result.action == "enter-normal"


def test_i_returns_to_insert_mode():
    vim = _new()
    vim.handle("escape")
    result = vim.handle("i")
    assert result.mode is Mode.INSERT
    assert result.action == "insert-before"


def test_unknown_key_in_normal_is_consumed_silently():
    vim = _new()
    vim.handle("escape")
    result = vim.handle("z")
    assert result.consumed is True
    assert result.action is None
    assert result.mode is Mode.NORMAL


def test_motion_keys_emit_actions():
    vim = _new()
    vim.handle("escape")
    assert vim.handle("h").action == "move-left"
    assert vim.handle("l").action == "move-right"
    assert vim.handle("0").action == "move-start"
    assert vim.handle("$").action == "move-end"
    assert vim.handle("w").action == "move-word-next"
    assert vim.handle("b").action == "move-word-prev"


def test_dd_and_yy_chords():
    vim = _new()
    vim.handle("escape")
    first = vim.handle("d")
    assert first.consumed is True and first.action is None
    second = vim.handle("d")
    assert second.action == "delete-line"

    first = vim.handle("y")
    second = vim.handle("y")
    assert second.action == "yank-line"


def test_unknown_chord_drop():
    vim = _new()
    vim.handle("escape")
    vim.handle("d")
    result = vim.handle("x")  # "dx" is not a vim chord in our set
    assert result.consumed is True
    assert result.action is None


def test_insert_mode_passes_keys_through():
    vim = _new()
    result = vim.handle("a")
    assert result.consumed is False
    assert result.action is None


def test_set_enabled_resets_state():
    vim = _new()
    vim.handle("escape")
    vim.handle("d")
    vim.set_enabled(False)
    assert vim.mode is Mode.INSERT
    # Re-enabling puts us back in Insert mode (fresh vim session).
    vim.set_enabled(True)
    assert vim.mode is Mode.INSERT
    # A stray "d" in Insert mode is passed through (not consumed).
    second = vim.handle("d")
    assert second.consumed is False and second.action is None
    # After entering Normal explicitly the chord machinery works again.
    vim.handle("escape")
    first = vim.handle("d")
    second = vim.handle("d")
    assert first.consumed is True and first.action is None
    assert second.action == "delete-line"
