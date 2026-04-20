"""Widgets used by the Claw Codex Textual TUI.

Public API (Phase 1):

* :class:`StartupHeader`   — one-shot banner at the top of the scroll region.
* :class:`TranscriptView`  — scrollable message list (replaces
  ``RichLog``-based Phase 0 ``Transcript``).
* :class:`StatusLine`      — spinner + verb + metrics bar.
* :class:`PromptInput`     — multi-line input + slash palette.
* :class:`FullscreenLayout` — four-region parity shell.

Backward-compat aliases kept so Phase 0 callers (tests, handoff) work
unchanged: :class:`Transcript` (now :class:`TranscriptView`) and
:class:`StatusBar` (now :class:`StatusLine`).
"""

from .fullscreen_layout import FullscreenLayout
from .header import StartupHeader
from .prompt_input import PromptInput, PromptSubmitted
from .status_line import StatusLine
from .transcript_view import Transcript, TranscriptView


# Phase 0 backward-compat alias.
StatusBar = StatusLine


__all__ = [
    "FullscreenLayout",
    "StartupHeader",
    "PromptInput",
    "PromptSubmitted",
    "StatusLine",
    "StatusBar",
    "Transcript",
    "TranscriptView",
]
