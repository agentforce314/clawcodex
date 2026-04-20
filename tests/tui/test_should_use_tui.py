"""Unit tests for :func:`src.entrypoints.tui.should_use_tui`.

The default interactive experience is the prompt_toolkit + rich REPL, which
matches the TS Ink reference's terminal-native behavior (transcript flows
into scrollback, only the prompt + status row are live, native mouse copy).
The Textual TUI is opt-in via ``--tui`` or ``CLAWCODEX_TUI=1``.

Contract summary:

* ``explicit=True``   -> Textual TUI when ``textual`` is importable and the
  terminal is a real TTY. Also enabled by ``CLAWCODEX_TUI=1``.
* ``explicit=False``  -> always REPL (also via ``CLAWCODEX_LEGACY_REPL=1`` /
  ``CLAWCODEX_TUI=0``).
* ``explicit=None``   -> REPL by default. Honor ``CLAWCODEX_TUI=1`` so users
  can pin the Textual UI without a flag.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("CLAWCODEX_TUI", raising=False)
    monkeypatch.delenv("CLAWCODEX_LEGACY_REPL", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    yield monkeypatch


def test_explicit_true_forces_tui(clean_env):
    """``--tui`` opts into the Textual UI on real TTYs."""

    from src.entrypoints.tui import should_use_tui

    with patch("src.entrypoints.tui._textual_available", return_value=True), \
         patch("sys.stdout.isatty", return_value=True), \
         patch("sys.stdin.isatty", return_value=True):
        assert should_use_tui(True) is True


def test_explicit_false_disables_tui(clean_env):
    from src.entrypoints.tui import should_use_tui

    with patch("src.entrypoints.tui._textual_available", return_value=True), \
         patch("sys.stdout.isatty", return_value=True), \
         patch("sys.stdin.isatty", return_value=True):
        assert should_use_tui(False) is False


def test_default_on_real_tty_is_repl(clean_env):
    """Default (explicit=None) prefers the inline REPL even on real TTYs."""

    from src.entrypoints.tui import should_use_tui

    with patch("src.entrypoints.tui._textual_available", return_value=True), \
         patch("sys.stdout.isatty", return_value=True), \
         patch("sys.stdin.isatty", return_value=True):
        assert should_use_tui(None) is False


def test_legacy_env_forces_rich_repl(clean_env):
    """``CLAWCODEX_LEGACY_REPL=1`` pins the legacy Rich REPL even on a TTY."""
    from src.entrypoints.tui import should_use_tui

    clean_env.setenv("CLAWCODEX_LEGACY_REPL", "1")
    with patch("src.entrypoints.tui._textual_available", return_value=True), \
         patch("sys.stdout.isatty", return_value=True), \
         patch("sys.stdin.isatty", return_value=True):
        assert should_use_tui(None) is False


def test_tui_env_zero_disables(clean_env):
    """``CLAWCODEX_TUI=0`` is an alternate opt-out for the Textual default."""
    from src.entrypoints.tui import should_use_tui

    clean_env.setenv("CLAWCODEX_TUI", "0")
    with patch("src.entrypoints.tui._textual_available", return_value=True), \
         patch("sys.stdout.isatty", return_value=True), \
         patch("sys.stdin.isatty", return_value=True):
        assert should_use_tui(None) is False


def test_env_opt_in_enables_tui_on_tty(clean_env):
    from src.entrypoints.tui import should_use_tui

    clean_env.setenv("CLAWCODEX_TUI", "1")
    with patch("src.entrypoints.tui._textual_available", return_value=True), \
         patch("sys.stdout.isatty", return_value=True), \
         patch("sys.stdin.isatty", return_value=True):
        assert should_use_tui(None) is True


def test_default_disabled_without_tty(clean_env):
    """Piped / non-interactive sessions fall back to the legacy REPL."""
    from src.entrypoints.tui import should_use_tui

    with patch("src.entrypoints.tui._textual_available", return_value=True), \
         patch("sys.stdout.isatty", return_value=False), \
         patch("sys.stdin.isatty", return_value=True):
        assert should_use_tui(None) is False


def test_default_disabled_on_dumb_term(clean_env):
    from src.entrypoints.tui import should_use_tui

    clean_env.setenv("TERM", "dumb")
    with patch("src.entrypoints.tui._textual_available", return_value=True), \
         patch("sys.stdout.isatty", return_value=True), \
         patch("sys.stdin.isatty", return_value=True):
        assert should_use_tui(None) is False


def test_default_disabled_when_textual_missing(clean_env):
    from src.entrypoints.tui import should_use_tui

    with patch("src.entrypoints.tui._textual_available", return_value=False), \
         patch("sys.stdout.isatty", return_value=True), \
         patch("sys.stdin.isatty", return_value=True):
        assert should_use_tui(None) is False
