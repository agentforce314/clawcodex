"""Terminal chrome helpers (title / tab status / bell / focus).

Ports the Ink reference's terminal-chrome hooks (`useTerminalTitle`,
`useTabStatus`, `useTerminalNotification`, focus-event handling) into
a single Python helper module. Each helper boils down to "write an
ANSI/OSC escape sequence to stdout" so we keep them as standalone
functions that any part of the app can call; the Textual app then
owns the lifecycle (set title on mount, clear on exit, ring bell on
idle, …).

Escape-sequence references come straight from the TS implementation
(`typescript/src/ink/termio/osc.ts`, `dec.ts`, `csi.ts`):

* Title (OSC 0):        ``\x1b]0;<title>\x07``  (ST on kitty)
* Tab status (OSC 21337): ``\x1b]21337;<payload>\x07``
* Bell:                 ``\x07``
* iTerm2 OSC 9 notify:  ``\x1b]9;<message>\x07``
* Progress (OSC 9;4):   ``\x1b]9;4;<state>;<pct>\x07``
* Focus-event reporting (DEC 1004):
      enable  ``\x1b[?1004h``
      disable ``\x1b[?1004l``
      inbound focus-in  ``\x1b[I``
      inbound focus-out ``\x1b[O``

Every helper swallows ``OSError`` from stdout because agents run in
many non-interactive contexts (CI, piped output, test harness) and
losing the chrome should never break the session.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Literal

_ESC = "\x1b"
_BEL = "\x07"
_ST = f"{_ESC}\\"

_ANSI_STRIP_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_STRIP_RE.sub("", text)


def _is_kitty() -> bool:
    return os.environ.get("TERM", "").startswith("xterm-kitty") or bool(
        os.environ.get("KITTY_WINDOW_ID")
    )


def _is_multiplexed() -> str | None:
    """Return the multiplexer name (``"tmux"``/``"screen"``) or ``None``."""

    if os.environ.get("TMUX"):
        return "tmux"
    term = os.environ.get("TERM", "")
    if term.startswith("screen"):
        return "screen"
    return None


def _wrap_for_multiplexer(seq: str) -> str:
    """Wrap an escape sequence so it reaches the real terminal.

    tmux requires ``ESC Ptmux;ESC <seq> ESC \\``; screen requires
    ``ESC P <seq> ESC \\``. Non-multiplexed terminals receive the raw
    sequence.
    """

    mux = _is_multiplexed()
    if mux == "tmux":
        inner = seq.replace(_ESC, _ESC + _ESC)
        return f"{_ESC}Ptmux;{_ESC}{inner}{_ESC}\\"
    if mux == "screen":
        return f"{_ESC}P{seq}{_ESC}\\"
    return seq


def _osc(number: int, payload: str) -> str:
    terminator = _ST if _is_kitty() else _BEL
    return f"{_ESC}]{number};{payload}{terminator}"


def _write(seq: str) -> None:
    stream = sys.__stdout__ or sys.stdout
    try:
        stream.write(seq)
        stream.flush()
    except (OSError, ValueError, AttributeError):
        pass


# ---- title --------------------------------------------------------


def set_terminal_title(title: str | None) -> None:
    """Set the terminal window/tab title via OSC 0.

    Passing ``None`` or an empty string clears the title. On Windows
    (``os.name == 'nt'``) we fall through to ``ctypes.windll`` because
    Windows Terminal only honours the API title on some builds.
    """

    if title is None:
        title = ""
    clean = _strip_ansi(title).strip()
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(clean)  # type: ignore[attr-defined]
        except Exception:
            pass
        return
    _write(_wrap_for_multiplexer(_osc(0, clean)))


def clear_terminal_title() -> None:
    set_terminal_title("")


# ---- tab status ---------------------------------------------------


_TAB_PRESETS: dict[str, tuple[str, str]] = {
    "idle": ("", ""),
    "busy": ("◐", "#8ab4f8"),
    "waiting": ("?", "#f5c451"),
}


def set_tab_status(kind: Literal["idle", "busy", "waiting"] | None) -> None:
    """Update the terminal tab's status indicator via OSC 21337.

    Terminal emulators vary wildly in support; we emit the sequence
    unconditionally (matching the ink behaviour on non-kitty) and
    rely on unsupported terminals to simply ignore it. Pass ``None``
    to clear.
    """

    if kind is None or kind == "idle":
        payload = "indicator=;status=;status-color="
    else:
        indicator, color = _TAB_PRESETS.get(kind, ("", ""))
        payload = f"indicator={indicator};status={kind};status-color={color}"
    _write(_wrap_for_multiplexer(_osc(21337, payload)))


# ---- notifications ------------------------------------------------


def ring_bell() -> None:
    """Write the terminal BEL character."""

    _write(_BEL)


def notify_iterm2(message: str) -> None:
    """Raise an iTerm2-style OSC 9 notification."""

    _write(_wrap_for_multiplexer(_osc(9, message)))


def notify_kitty(message: str) -> None:
    _write(_wrap_for_multiplexer(_osc(99, message)))


def notify_ghostty(message: str) -> None:
    _write(_wrap_for_multiplexer(_osc(777, message)))


ProgressState = Literal["start", "done", "error", "paused", "clear"]


_PROGRESS_CODES: dict[ProgressState, int] = {
    "clear": 0,
    "start": 1,
    "error": 2,
    "paused": 3,
    "done": 0,
}


def set_progress(state: ProgressState, percent: int | None = None) -> None:
    """iTerm2 OSC 9;4 progress reporting."""

    code = _PROGRESS_CODES.get(state, 0)
    pct = "" if percent is None else f";{max(0, min(100, int(percent)))}"
    _write(_wrap_for_multiplexer(_osc(9, f"4;{code}{pct}")))


# ---- focus reporting ---------------------------------------------


def enable_focus_events() -> None:
    """Enable DEC 1004 focus-event reporting (CSI ?1004h)."""

    _write(f"{_ESC}[?1004h")


def disable_focus_events() -> None:
    _write(f"{_ESC}[?1004l")


__all__ = [
    "clear_terminal_title",
    "disable_focus_events",
    "enable_focus_events",
    "notify_ghostty",
    "notify_iterm2",
    "notify_kitty",
    "ring_bell",
    "set_progress",
    "set_tab_status",
    "set_terminal_title",
]
