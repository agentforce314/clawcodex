"""Four-region layout container matching ``FullscreenLayout.tsx``.

The ink reference splits the interactive screen into four regions that
render concurrently:

* ``scrollable`` — the transcript and header.
* ``overlay``    — in-region overlays that float on top of the
  transcript (e.g. the pre-tool :class:`PermissionRequest`). Textual
  screens are a better fit for *modal* overlays, so Phase 1 keeps this
  region reserved but empty; full overlay parity arrives with the Phase
  2 modal work.
* ``modal``      — centered "tall slash JSX" panel for diff-style flows.
* ``bottom``     — the status line, queued-commands strip, and prompt.

Exposing the regions as named mount points (instead of CSS grid
positions) keeps the REPL screen declarative; widgets mounted into a
region inherit its sizing automatically.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical


class FullscreenLayout(Container):
    """Layout shell with four addressable regions."""

    DEFAULT_CSS = """
    FullscreenLayout {
        layout: vertical;
        height: 1fr;
        width: 1fr;
    }
    FullscreenLayout > #scroll {
        height: 1fr;
        width: 1fr;
        overflow: hidden;
    }
    FullscreenLayout > #overlay {
        height: auto;
        width: 1fr;
        display: none;
    }
    FullscreenLayout > #overlay.-active {
        display: block;
    }
    FullscreenLayout > #modal {
        height: auto;
        width: 1fr;
        display: none;
    }
    FullscreenLayout > #modal.-active {
        display: block;
    }
    FullscreenLayout > #bottom {
        height: auto;
        width: 1fr;
        dock: bottom;
    }
    """

    def compose(self) -> ComposeResult:
        yield Vertical(id="scroll")
        yield Vertical(id="overlay")
        yield Vertical(id="modal")
        yield Vertical(id="bottom")

    # ---- region accessors ----
    def scroll_region(self) -> Vertical:
        return self.query_one("#scroll", Vertical)

    def overlay_region(self) -> Vertical:
        return self.query_one("#overlay", Vertical)

    def modal_region(self) -> Vertical:
        return self.query_one("#modal", Vertical)

    def bottom_region(self) -> Vertical:
        return self.query_one("#bottom", Vertical)

    # ---- visibility toggles ----
    def set_overlay_active(self, active: bool) -> None:
        region = self.overlay_region()
        region.set_class(active, "-active")

    def set_modal_active(self, active: bool) -> None:
        region = self.modal_region()
        region.set_class(active, "-active")
