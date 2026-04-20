"""Status line widget — parity with ``StatusLine.tsx`` + ``SpinnerWithVerb``.

The ink reference renders a single line at the bottom of the transcript
that shows:

* On the left: provider · model.
* In the middle: the current "verb" (``Synthesizing…``, ``Gathering…``,
  ``Running bash…``) paired with an animated spinner while the agent is
  busy, plus an elapsed-time indicator.
* On the right: turn count, queued prompts pill, and usage (total
  tokens) once a run has completed.

Phase 1 renders this as a static-looking Rich ``Text`` updated by an
interval timer. The animation is strictly cosmetic — the authoritative
"am I busy?" signal comes from :class:`AppState.is_thinking`.
"""

from __future__ import annotations

import time
from pathlib import Path

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ..state import AppState


_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class StatusLine(Static):
    """Single-line status footer."""

    DEFAULT_CSS = """
    StatusLine {
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    """

    turns: reactive[int] = reactive(0)
    is_thinking: reactive[bool] = reactive(False)
    queued: reactive[int] = reactive(0)

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        workspace_root: Path,
        app_state: AppState | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._workspace_root = Path(workspace_root)
        self._app_state = app_state
        self._frame = 0
        self._timer = None
        initial = Text(f"{provider} · {model}    ready    turn 0")
        super().__init__(initial, markup=False)

    # ---- lifecycle ----
    def on_mount(self) -> None:
        self._timer = self.set_interval(1 / 10, self._tick)
        self._redraw()

    def _tick(self) -> None:
        if self._app_state is not None:
            self.is_thinking = self._app_state.is_thinking
            self.queued = len(self._app_state.queued_prompts)
        if self.is_thinking:
            self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)
        self._redraw()

    # ---- public API ----
    def bind_state(self, state: AppState) -> None:
        self._app_state = state
        self._redraw()

    def refresh_identity(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        if provider is not None:
            self._provider = provider
        if model is not None:
            self._model = model
        self._redraw()

    def bump_turn(self) -> None:
        self.turns += 1

    def set_busy(self, verb: str = "Synthesizing") -> None:
        self.is_thinking = True
        if self._app_state is not None:
            self._app_state.set_thinking(True, verb=verb)

    def set_idle(self) -> None:
        self.is_thinking = False
        if self._app_state is not None:
            self._app_state.set_thinking(False)

    # ---- render ----
    def watch_is_thinking(self, _: bool) -> None:
        self._redraw()

    def watch_turns(self, _: int) -> None:
        self._redraw()

    def watch_queued(self, _: int) -> None:
        self._redraw()

    def _redraw(self) -> None:
        spinner = _SPINNER_FRAMES[self._frame] if self.is_thinking else " "
        self.update(self._compose_text(spinner=spinner))

    def _compose_text(self, *, spinner: str) -> Text:
        state = self._app_state
        verb = state.verb if state else ("thinking" if self.is_thinking else "ready")
        elapsed = ""
        if state and state.is_thinking and state.verb_started_at:
            secs = int(time.time() - state.verb_started_at)
            if secs > 0:
                elapsed = f" {secs}s"

        left = f"{self._provider} · {self._model}"
        cwd = self._display_cwd()
        middle = f"{spinner} {verb}{elapsed}" if self.is_thinking else verb
        right_bits: list[str] = [f"turn {self.turns}"]
        if self.queued:
            right_bits.append(f"queued {self.queued}")
        if state and state.usage:
            total = state.usage.get("input_tokens", 0) + state.usage.get("output_tokens", 0)
            if total:
                right_bits.append(f"tokens {total}")
        right = " · ".join(right_bits)
        return Text(f"{left}    {middle}    {cwd}    {right}")

    def _display_cwd(self) -> str:
        try:
            home = Path.home()
            rel = self._workspace_root.relative_to(home)
            return f"~/{rel}" if str(rel) != "." else "~"
        except Exception:
            return str(self._workspace_root)
