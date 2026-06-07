"""Shared chrome for modal dialogs.

Ports the visual vocabulary of ``components/Dialog.tsx`` and
``components/PermissionDialog.tsx`` in the TypeScript Ink reference:

* A titled, centered panel with a coloured border.
* An optional subtitle / body paragraph.
* A content region where the concrete dialog yields its own body
  widgets (usually a :class:`SelectList`, a form, or a preview).
* An optional footer hint ("Esc to cancel", "y/n", etc.).

All Phase 2 pickers inherit from :class:`DialogScreen` so keyboard
bindings (Esc = cancel, bubbling to the overlay stack), layout
styling, and palette use stay consistent without each dialog
re-inventing the chrome.
"""

from __future__ import annotations

from typing import Generic, Iterator, TypeVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Static

T = TypeVar("T")


class DialogScreen(ModalScreen[T], Generic[T]):
    """Base modal dialog.

    Subclasses override :meth:`build_body` as an iterator of
    :class:`textual.widget.Widget` instances to mount inside the
    panel's body region. The footer hint defaults to "Esc to cancel"
    and can be overridden via :attr:`footer_hint`.
    """

    title_text: str = ""
    subtitle_text: str = ""
    footer_hint: str = "Enter to confirm · Esc to cancel"
    border_variant: str = "primary"  # one of: primary, warning, error, success

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    DialogScreen {
        align: center middle;
    }
    DialogScreen > #dialog-panel {
        width: 72;
        max-width: 90%;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    DialogScreen.-warning > #dialog-panel { border: round $warning; }
    DialogScreen.-error   > #dialog-panel { border: round $error; }
    DialogScreen.-success > #dialog-panel { border: round $success; }

    DialogScreen #dialog-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    DialogScreen.-warning #dialog-title { color: $warning; }
    DialogScreen.-error   #dialog-title { color: $error; }
    DialogScreen.-success #dialog-title { color: $success; }

    DialogScreen #dialog-subtitle {
        color: $text-muted;
        margin-bottom: 1;
    }
    DialogScreen #dialog-body {
        height: auto;
        margin-top: 0;
    }
    DialogScreen #dialog-footer {
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        if self.border_variant != "primary":
            self.add_class(f"-{self.border_variant}")

    # ---- composition ----
    def compose(self) -> ComposeResult:
        panel = Vertical(id="dialog-panel")
        with panel:
            if self.title_text:
                yield Static(Text(self.title_text), id="dialog-title", markup=False)
            if self.subtitle_text:
                yield Static(
                    Text(self.subtitle_text), id="dialog-subtitle", markup=False
                )
            body = Vertical(id="dialog-body")
            with body:
                for widget in self.build_body():
                    yield widget
            if self.footer_hint:
                yield Static(
                    Text(self.footer_hint, style="dim"),
                    id="dialog-footer",
                    markup=False,
                )

    def on_mount(self) -> None:
        self._post_mount()

    # ---- subclass hooks ----
    def build_body(self) -> Iterator[Widget]:  # pragma: no cover - abstract
        """Yield the dialog-specific body widgets."""

        return iter(())

    def _post_mount(self) -> None:
        """Hook for subclasses that need focus set after mount."""

    # ---- default cancel ----
    def action_cancel(self) -> None:
        self.dismiss(None)  # type: ignore[arg-type]


__all__ = ["DialogScreen"]
