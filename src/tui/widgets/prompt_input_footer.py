"""Footer hint row under the prompt input.

Port of ``typescript/src/components/PromptInput/PromptInputFooter.tsx``
(~135 lines). The footer renders a single muted line of keybinding hints
so first-time users can discover the most important shortcuts without
opening the help menu.

Round 2 / WI-R2.3 of the ch13 terminal-UI refactor. Default content is
curated and context-filtered (vim hints hide when vim mode is off). A
``hints_provider`` callback allows future rounds to feed the resolver
output directly without modifying the widget.

The widget does NOT subscribe to per-keystroke events; it relies on the
host calling :meth:`refresh_hints` after state transitions
(vim mode toggle, transcript-emptiness change, etc.). Keeping the
refresh model explicit avoids the per-keystroke re-render cost the
chapter calls out under "Separate the hot path from React".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rich.text import Text
from textual.widgets import Static

from ..vim import VimState

# Use the same separator the status line uses, so the visual rhythm of
# the bottom region is consistent across the two rows.
_SEPARATOR = " · "


@dataclass(frozen=True)
class FooterHint:
    """One keybinding hint rendered in the footer row.

    Mirrors the (key, description) pairs in
    ``typescript/src/components/PromptInput/PromptInputFooter.tsx`` but
    adds a ``when`` predicate so context-specific hints (e.g. vim-only)
    can be filtered in one place.
    """

    keys: str
    label: str
    when: Callable[[], bool] | None = None


class PromptInputFooter(Static):
    """Footer hint row below the prompt input."""

    DEFAULT_CSS = """
    PromptInputFooter {
        height: 1;
        width: 1fr;
        color: $text-muted;
        padding: 0 1;
    }
    PromptInputFooter.-hidden {
        display: none;
    }
    """

    def __init__(
        self,
        *,
        vim_state: VimState | None = None,
        hints_provider: Callable[[], list[FooterHint]] | None = None,
    ) -> None:
        super().__init__(Text(""), markup=False)
        self._vim = vim_state
        self._hints_provider = hints_provider
        # Context flags that swap the hint set (TS PromptInputFooterLeftSide):
        # while a run is in flight the footer collapses to "esc to interrupt";
        # in bash mode it shows "! for bash mode".
        self._loading: bool = False
        self._bash_mode: bool = False
        # Track the most recent rendered line as a test seam — same
        # pattern :class:`PromptInputModeIndicator` uses.
        self._last_line: str = ""

    # ---- lifecycle ----
    def on_mount(self) -> None:
        self._redraw()

    # ---- external triggers ----
    def refresh_hints(self) -> None:
        """Recompute the visible hint set and re-render.

        Call after any state change that affects ``when`` predicates:
        toggling vim mode, mounting/unmounting transcript content,
        switching the active screen, etc.
        """

        self._redraw()

    def set_loading(self, loading: bool) -> None:
        """Toggle the in-flight state (footer → ``esc to interrupt``).

        Driven by :class:`~src.tui.widgets.status_line.StatusLine` so the
        footer tracks the same ``is_thinking`` signal as the spinner — see
        ``StatusLine.bind_footer``.
        """

        if loading == self._loading:
            return
        self._loading = loading
        self._redraw()

    def set_bash_mode(self, bash_mode: bool) -> None:
        """Toggle bash mode (footer → ``! for bash mode``)."""

        if bash_mode == self._bash_mode:
            return
        self._bash_mode = bash_mode
        self._redraw()

    @property
    def last_line(self) -> str:
        """Most recently rendered hint line (test seam)."""
        return self._last_line

    # ---- internals ----
    def _resolve_hints(self) -> list[FooterHint]:
        if self._hints_provider is not None:
            try:
                provided = self._hints_provider() or []
            except Exception:
                provided = []
            return list(provided)
        return self._default_hints()

    def _default_hints(self) -> list[FooterHint]:
        """Context-aware default hints (TS ``PromptInputFooterLeftSide``).

        Mode precedence mirrors the ink footer: bash mode wins first —
        ``ModeIndicator`` returns ``! for bash mode`` before any loading
        logic (PromptInputFooterLeftSide.tsx:317-319, ahead of the
        ``getSpinnerHintParts(isLoading…)`` at :375). Then, while a run is
        in flight, the only hint is ``esc to interrupt``; otherwise the
        idle discoverability set. Keys are lowercased; ``to``-actions match
        the TS ``KeyboardShortcutHint`` grammar (``esc to interrupt``),
        while ``for``-hints (``/ for commands``, ``! for bash mode``) are
        raw TS strings. Every idle hint maps to a wired binding.
        """

        if self._bash_mode:
            return [FooterHint(keys="!", label="for bash mode")]
        if self._loading:
            return [FooterHint(keys="esc", label="to interrupt")]

        vim = self._vim

        def vim_active() -> bool:
            return vim is not None and vim.enabled

        return [
            FooterHint(keys="/", label="for commands"),
            FooterHint(keys="ctrl+l", label="to clear"),
            FooterHint(keys="i/esc", label="vim", when=vim_active),
        ]

    def _redraw(self) -> None:
        hints = self._resolve_hints()
        visible: list[FooterHint] = []
        for hint in hints:
            if hint.when is not None:
                try:
                    if not hint.when():
                        continue
                except Exception:
                    continue
            visible.append(hint)
        if not visible:
            self._last_line = ""
            self.add_class("-hidden")
            self.update(Text(""))
            return
        self.remove_class("-hidden")
        line = _SEPARATOR.join(f"{h.keys} {h.label}" for h in visible)
        self._last_line = line
        self.update(Text(line))


__all__ = ["FooterHint", "PromptInputFooter"]
