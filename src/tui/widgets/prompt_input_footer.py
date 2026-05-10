"""Keybinding-hint footer below the prompt input.

Mirrors ``typescript/src/components/PromptInput/PromptInputFooter.tsx``.
Shows a one-line summary of the most relevant keys for the current
context — ``Ctrl+C cancel | Esc close palette | / commands`` etc. The
hint set updates as the prompt-input mode changes (vim normal vs.
insert), and the widget reads its hint copy from the configurable
keybindings layer (Phase 2 ``KeybindingDispatcher``) so user-config
overrides are reflected automatically.
"""

from __future__ import annotations

from typing import Iterable

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class PromptInputFooter(Widget):
    """One-line keybinding hint."""

    DEFAULT_CSS = """
    PromptInputFooter {
        height: auto;
        max-height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    PromptInputFooter.-hidden {
        display: none;
    }
    """

    hints: reactive[tuple[tuple[str, str], ...]] = reactive(
        (), always_update=True, layout=True
    )

    def compose(self) -> ComposeResult:
        yield Static(Text(""), classes="-row", markup=False)

    def on_mount(self) -> None:
        self._refresh()

    def watch_hints(
        self,
        _old: tuple[tuple[str, str], ...],
        _new: tuple[tuple[str, str], ...],
    ) -> None:
        self._refresh()

    def set_hints(self, hints: Iterable[tuple[str, str]]) -> None:
        """Replace the active hint set.

        ``hints`` is an iterable of ``(key, label)`` pairs, e.g.
        ``[("Ctrl+C", "cancel"), ("Esc", "close")]``.
        """

        self.hints = tuple((str(k), str(v)) for k, v in hints if k and v)

    # ---- internals ----
    def _refresh(self) -> None:
        row = self._row()
        if row is None:
            return
        if not self.hints:
            self.add_class("-hidden")
            row.update(Text(""))
            return
        self.remove_class("-hidden")
        rendered = Text("")
        for i, (key, label) in enumerate(self.hints):
            if i:
                rendered.append("  ", style="dim")
            rendered.append(key, style="bold")
            rendered.append(" ", style="dim")
            rendered.append(label, style="dim")
        row.update(rendered)

    def _row(self) -> Static | None:
        try:
            for static in self.query(Static):
                if static.has_class("-row"):
                    return static
        except Exception:
            return None
        return None


__all__ = ["PromptInputFooter"]
