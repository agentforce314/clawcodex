"""The ``?`` shortcuts help panel.

Port of the ink ``PromptInputHelpMenu`` (``components/PromptInput/
PromptInputHelpMenu.tsx``): typing ``?`` into an empty prompt toggles a
muted panel listing the keyboard shortcuts. ``?`` is muscle memory for
Claude Code users; before this the key did nothing.

This panel lists **only the shortcuts actually wired in this port** — the
TS menu advertises many more (``& for background``, ``ctrl+t`` tasks,
model picker, stash, ``$EDITOR``, …) that are gated on subsystems the
Python TUI hasn't ported. Advertising an unimplemented key is worse than
omitting it, so :data:`_BASE_SHORTCUTS` is the honest subset. The same
list feeds the ``/help`` output (single source of truth).
"""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from ..vim import VimState

# (key, action) pairs — every entry maps to a binding the TUI wires today.
# ``@ for file paths`` is intentionally absent until the @-mention dropdown
# lands; ``double-tap esc`` / ``shift+tab`` / ``ctrl+r`` likewise wait for
# their wiring PRs.
_BASE_SHORTCUTS: list[tuple[str, str]] = [
    ("/", "for commands"),
    ("!", "for bash mode"),
    ("#", "to add a memory"),
    ("tab", "to complete a command"),
    ("↑ ↓", "to navigate history"),
    ("ctrl+l", "to clear the draft"),
    ("ctrl+o", "to expand last output"),
    ("esc", "to interrupt"),
    ("?", "for shortcuts"),
    ("/exit", "to quit"),
]
_VIM_SHORTCUTS: list[tuple[str, str]] = [
    ("i / esc", "vim insert / normal"),
]


def wired_shortcuts(vim_enabled: bool) -> list[tuple[str, str]]:
    """The (key, action) pairs to advertise, vim chords appended when on."""
    pairs = list(_BASE_SHORTCUTS)
    if vim_enabled:
        pairs.extend(_VIM_SHORTCUTS)
    return pairs


def shortcut_lines(vim_enabled: bool) -> list[str]:
    """``["/ for commands", …]`` — used by ``/help`` (plain text)."""
    return [f"{key} {action}" for key, action in wired_shortcuts(vim_enabled)]


class ShortcutsHelp(Static):
    """Muted two-column shortcut grid, shown while ``?`` is toggled on."""

    DEFAULT_CSS = """
    ShortcutsHelp {
        height: auto;
        padding: 0 2;
        color: $text-muted;
    }
    ShortcutsHelp.-hidden {
        display: none;
    }
    """

    def __init__(
        self, *, vim_state: VimState | None = None, classes: str | None = None
    ) -> None:
        super().__init__("", markup=False, classes=classes)
        self._vim = vim_state

    def on_mount(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        """Rebuild the grid (call when vim mode toggles before showing)."""
        vim_on = self._vim is not None and self._vim.enabled
        pairs = wired_shortcuts(vim_on)
        grid = Table.grid(padding=(0, 3))
        grid.add_column()
        grid.add_column()
        mid = (len(pairs) + 1) // 2
        left, right = pairs[:mid], pairs[mid:]
        for i in range(mid):
            lk, la = left[i]
            cells = [Text(f"{lk} {la}", style="dim")]
            if i < len(right):
                rk, ra = right[i]
                cells.append(Text(f"{rk} {ra}", style="dim"))
            else:
                cells.append(Text(""))
            grid.add_row(*cells)
        self.update(grid)


__all__ = ["ShortcutsHelp", "wired_shortcuts", "shortcut_lines"]
