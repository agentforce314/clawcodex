"""Unit tests for :mod:`src.tui.terminal_chrome`."""

from __future__ import annotations

import io

import pytest

from src.tui import terminal_chrome


@pytest.fixture
def captured(monkeypatch):
    """Redirect ``sys.__stdout__`` writes to an in-memory buffer."""

    buf = io.StringIO()

    class _Fake:
        def __init__(self) -> None:
            self.buf = buf

        def write(self, data: str) -> int:
            return buf.write(data)

        def flush(self) -> None:
            pass

    monkeypatch.setattr(terminal_chrome.sys, "__stdout__", _Fake())
    # Default: no multiplexer / no kitty so we see a bare BEL terminator.
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
    return buf


def test_set_terminal_title_emits_osc_zero(captured, monkeypatch):
    monkeypatch.setattr(terminal_chrome.os, "name", "posix")
    terminal_chrome.set_terminal_title("hello world")
    out = captured.getvalue()
    assert out.startswith("\x1b]0;hello world")
    assert out.endswith("\x07")


def test_set_terminal_title_strips_ansi(captured, monkeypatch):
    monkeypatch.setattr(terminal_chrome.os, "name", "posix")
    terminal_chrome.set_terminal_title("\x1b[31mred\x1b[0m title")
    assert captured.getvalue() == "\x1b]0;red title\x07"


def test_kitty_uses_st_terminator(captured, monkeypatch):
    monkeypatch.setattr(terminal_chrome.os, "name", "posix")
    monkeypatch.setenv("KITTY_WINDOW_ID", "1")
    terminal_chrome.set_terminal_title("hi")
    out = captured.getvalue()
    assert out.endswith("\x1b\\")


def test_tmux_wraps_osc_sequence(captured, monkeypatch):
    monkeypatch.setattr(terminal_chrome.os, "name", "posix")
    monkeypatch.setenv("TMUX", "/tmp/tmux")
    terminal_chrome.set_terminal_title("t")
    out = captured.getvalue()
    assert out.startswith("\x1bPtmux;\x1b")
    # The inner escape gets doubled-up inside a tmux passthrough.
    assert "\x1b\x1b]0;t\x07" in out
    assert out.endswith("\x1b\\")


def test_tab_status_emits_osc_21337(captured, monkeypatch):
    monkeypatch.setattr(terminal_chrome.os, "name", "posix")
    terminal_chrome.set_tab_status("busy")
    out = captured.getvalue()
    assert out.startswith("\x1b]21337;")
    assert "status=busy" in out
    assert out.endswith("\x07")


def test_tab_status_idle_clears(captured, monkeypatch):
    monkeypatch.setattr(terminal_chrome.os, "name", "posix")
    terminal_chrome.set_tab_status("idle")
    out = captured.getvalue()
    assert "indicator=;status=;status-color=" in out


def test_ring_bell_writes_bel(captured):
    terminal_chrome.ring_bell()
    assert captured.getvalue() == "\x07"


def test_notify_iterm2_emits_osc9(captured, monkeypatch):
    monkeypatch.setattr(terminal_chrome.os, "name", "posix")
    terminal_chrome.notify_iterm2("done")
    out = captured.getvalue()
    assert out == "\x1b]9;done\x07"


def test_set_progress_clamps_percent(captured, monkeypatch):
    monkeypatch.setattr(terminal_chrome.os, "name", "posix")
    terminal_chrome.set_progress("start", 150)
    out = captured.getvalue()
    assert out == "\x1b]9;4;1;100\x07"


def test_focus_enable_disable_sequences(captured):
    terminal_chrome.enable_focus_events()
    terminal_chrome.disable_focus_events()
    assert captured.getvalue() == "\x1b[?1004h\x1b[?1004l"


def test_set_terminal_title_windows_uses_ctypes(monkeypatch):
    monkeypatch.setattr(terminal_chrome.os, "name", "nt")
    calls: list[str] = []

    class _Fake:
        def SetConsoleTitleW(self, value: str) -> None:  # noqa: N802
            calls.append(value)

    class _Kernel:
        kernel32 = _Fake()

    class _Ctypes:
        windll = _Kernel()

    monkeypatch.setitem(__import__("sys").modules, "ctypes", _Ctypes())
    terminal_chrome.set_terminal_title("win-title")
    assert calls == ["win-title"]
