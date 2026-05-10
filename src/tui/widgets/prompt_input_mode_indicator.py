"""Reactive mode-indicator pill — shows INSERT / NORMAL / VISUAL.

Mirrors ``typescript/src/components/PromptInput/PromptInputModeIndicator.tsx``.
The widget is a tiny one-line pill the prompt-input row mounts above
itself; it stays visible whenever vim mode is enabled. Insert mode
shows nothing (matches the chapter — the absence of a mode pill IS the
insert state) so ordinary users never see chrome they don't need.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


_MODE_LABELS = {
    "normal": ("NORMAL", "bold yellow on black"),
    "visual": ("VISUAL", "bold magenta on black"),
    "visual-line": ("V-LINE", "bold magenta on black"),
    "visual-block": ("V-BLOCK", "bold magenta on black"),
    "insert": ("", ""),
}


class PromptInputModeIndicator(Widget):
    """One-line mode label that auto-hides in INSERT (and when vim is off)."""

    DEFAULT_CSS = """
    PromptInputModeIndicator {
        height: auto;
        max-height: 1;
        padding: 0 1;
        background: transparent;
    }
    PromptInputModeIndicator.-hidden {
        display: none;
    }
    """

    mode: reactive[str] = reactive("insert", layout=True)
    enabled: reactive[bool] = reactive(False, layout=True)

    def compose(self) -> ComposeResult:
        yield Static(Text(""), classes="-pill", markup=False)

    def on_mount(self) -> None:
        self._refresh()

    def watch_mode(self, _old: str, _new: str) -> None:
        self._refresh()

    def watch_enabled(self, _old: bool, _new: bool) -> None:
        self._refresh()

    def set_state(self, *, enabled: bool, mode: str) -> None:
        """Atomic update — used by the prompt input on every vim transition."""

        self.enabled = enabled
        self.mode = mode

    # ---- internals ----
    def _refresh(self) -> None:
        label, style = _MODE_LABELS.get(
            (self.mode or "insert").lower(), ("", "")
        )
        if not self.enabled or not label:
            self.add_class("-hidden")
        else:
            self.remove_class("-hidden")
        try:
            pill = self._pill()
        except Exception:
            return
        if pill is None:
            return
        if label:
            pill.update(Text(f" {label} ", style=style))
        else:
            pill.update(Text(""))

    def _pill(self) -> Static | None:
        try:
            for static in self.query(Static):
                if static.has_class("-pill"):
                    return static
        except Exception:
            return None
        return None


__all__ = ["PromptInputModeIndicator"]
