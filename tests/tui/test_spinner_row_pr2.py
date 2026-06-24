"""Spinner / busy-row visual parity (TUI UX PR 2).

Ports the ink ``SpinnerAnimationRow`` look into the Textual status line's
middle segment (the de-facto spinner surface in this port):

* the signature "sparkle" glyph set ``· ✢ ✳ ✶ ✻ ✽`` (12-frame ping-pong),
* an ellipsis after the verb (``Synthesizing…``),
* a parenthesized status group with elapsed time (minute rollover) and a
  live token estimate (``round(chars/4)`` from the streamed response).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.app import App

from src.tui.state import AppState
from src.tui.widgets.status_line import (
    StatusLine,
    _SPINNER_BASE,
    _SPINNER_FRAMES,
    _format_elapsed,
    _format_token_count,
)


# ---- pure helpers ---------------------------------------------------------


def test_spinner_frames_are_the_sparkle_set():
    assert _SPINNER_BASE == ["·", "✢", "✳", "✶", "✻", "✽"]
    # 12-frame ping-pong: base then reversed base (Spinner/SpinnerGlyph.tsx:7).
    assert len(_SPINNER_FRAMES) == 12
    assert _SPINNER_FRAMES[:6] == _SPINNER_BASE
    assert _SPINNER_FRAMES[6:] == list(reversed(_SPINNER_BASE))
    # No braille left over from the old set.
    assert all(ch not in _SPINNER_FRAMES for ch in "⠋⠙⠹⠸")


def test_format_elapsed_rolls_over_at_a_minute():
    assert _format_elapsed(5) == "5s"
    assert _format_elapsed(59) == "59s"
    assert _format_elapsed(90) == "1m 30s"
    assert _format_elapsed(125) == "2m 5s"


def test_format_token_count_compacts_thousands():
    assert _format_token_count(0) == "0"
    assert _format_token_count(500) == "500"
    assert _format_token_count(1321) == "1.3k"
    assert _format_token_count(1_500_000) == "1.5m"
    # boundary must roll k→m, not print "1000.0k"
    assert _format_token_count(999_999) == "1.0m"


# ---- widget render --------------------------------------------------------


async def _widget(state, monkeypatch):
    monkeypatch.setattr(StatusLine, "refresh_custom_status", lambda self: None)
    widget = StatusLine(
        provider="prov",
        model="claude-opus-4-8",
        workspace_root=Path("/tmp"),
        app_state=state,
    )

    class _Host(App):
        def compose(self):
            yield widget

    return _Host(), widget


def _busy_state(*, elapsed_s: float = 5.0, streamed_chars: int = 0):
    state = AppState()
    state.is_thinking = True
    state.verb = "Synthesizing"
    state.verb_started_at = time.time() - elapsed_s
    state.streaming_text = "x" * streamed_chars
    return state


@pytest.mark.asyncio
async def test_busy_middle_has_glyph_ellipsis_and_elapsed(monkeypatch):
    app, widget = await _widget(_busy_state(elapsed_s=5.0), monkeypatch)
    async with app.run_test():
        widget.is_thinking = True
        text = widget._compose_text(spinner="✶").plain
    assert "✶ Synthesizing…" in text
    assert "(5s)" in text  # parenthesized elapsed, no tokens yet


@pytest.mark.asyncio
async def test_busy_middle_shows_live_token_estimate(monkeypatch):
    # 480 streamed chars → round(480/4) = 120 tokens.
    app, widget = await _widget(
        _busy_state(elapsed_s=5.0, streamed_chars=480), monkeypatch
    )
    async with app.run_test():
        widget.is_thinking = True
        text = widget._compose_text(spinner="✶").plain
    assert "↓ 120 tokens" in text
    assert "(5s · ↓ 120 tokens)" in text


@pytest.mark.asyncio
async def test_idle_row_has_no_ellipsis_or_spinner(monkeypatch):
    state = AppState()  # not thinking
    app, widget = await _widget(state, monkeypatch)
    async with app.run_test():
        widget.is_thinking = False
        text = widget._compose_text(spinner=" ").plain
    assert "…" not in text
    assert "Synthesizing" not in text
