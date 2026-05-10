"""Diagnostics screen — environment + capability + storage probes.

Phase-8 of the ch13 refactor (gap #9). Surfaces the same checks the
legacy ``cli --doctor`` command runs, plus the Phase-10 hyperlink
capability detection and the Phase-11 frame-debug status. Reachable
from the ``/doctor`` slash command in ``/tui``.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class DoctorScreen(ModalScreen[None]):
    """Modal showing environment / capability / health checks."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("q", "dismiss_modal", "Close"),
    ]

    DEFAULT_CSS = """
    DoctorScreen {
        align: center middle;
    }
    DoctorScreen > Middle > Center > Vertical {
        width: 80%;
        max-width: 90;
        max-height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    DoctorScreen Static.-title {
        color: $primary;
        text-style: bold;
        padding: 0 0 1 0;
    }
    DoctorScreen Static.-section {
        padding: 0 0 1 0;
    }
    """

    def __init__(self, app_state: Any | None = None) -> None:
        super().__init__()
        self._app_state = app_state

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                yield Vertical()

    def on_mount(self) -> None:
        body = self.query_one(Vertical)
        body.mount(
            Static(
                Text("Diagnostics", style="bold"),
                classes="-title",
                markup=False,
            )
        )

        for title, content in self._collect_sections():
            body.mount(
                Static(
                    Panel(
                        content,
                        title=title,
                        border_style="bright_black",
                        padding=(0, 1),
                    ),
                    classes="-section",
                    markup=False,
                )
            )

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    # ---- internals ----
    def _collect_sections(self) -> list[tuple[str, Any]]:
        sections: list[tuple[str, Any]] = []
        sections.append(("environment", self._render_environment()))
        sections.append(("hyperlinks", self._render_hyperlinks()))
        sections.append(("frame metrics", self._render_frame_metrics()))
        sections.append(("storage", self._render_storage()))
        return sections

    def _render_environment(self) -> Any:
        """Provider, model, workspace, theme — read from app state when present."""

        rows: list[Text] = []

        def kv(label: str, value: Any) -> None:
            rows.append(
                Text(f"{label}: ", style="dim").append(
                    str(value) if value else "(unknown)", style="default"
                )
            )

        state = self._app_state
        kv("provider", getattr(state, "provider", None))
        kv("model", getattr(state, "model", None))
        kv("workspace", getattr(state, "workspace_root", None))
        kv("theme", getattr(state, "theme_name", None))
        return Group(*rows) if rows else Text("(no app state)", style="dim")

    def _render_hyperlinks(self) -> Any:
        from src.tui.hyperlinks import is_hyperlink_supported

        supported = is_hyperlink_supported()
        text = Text()
        text.append("OSC 8 hyperlinks: ", style="dim")
        if supported:
            text.append("supported", style="bold green")
        else:
            text.append("not detected", style="bold yellow")
        return text

    def _render_frame_metrics(self) -> Any:
        from src.tui.frame_metrics import FRAME_DEBUG_ENV, is_enabled

        text = Text()
        text.append(f"{FRAME_DEBUG_ENV}: ", style="dim")
        if is_enabled():
            text.append("enabled — frame events emitting", style="bold green")
        else:
            text.append(
                "disabled (set to 1 for debug repaints)",
                style="dim",
            )
        return text

    def _render_storage(self) -> Any:
        """Probe ``SessionStorage`` if importable; otherwise mark missing.

        Phase-8 audit (state 2): the module exists but no live caller
        writes transcripts. The probe distinguishes "module unavailable"
        from "no transcripts on disk" so future debugging is one
        screen-glance away.
        """

        rows: list[Text] = []
        try:
            from src.services.session_storage import SessionStorage

            storage = SessionStorage()
            list_fn = getattr(storage, "list_sessions", None)
            count: int | None = None
            if callable(list_fn):
                try:
                    count = len(list(list_fn()))
                except Exception:
                    count = None
            rows.append(
                Text("module: ", style="dim").append(
                    "src.services.session_storage", style="default"
                )
            )
            if count is None:
                rows.append(
                    Text(
                        "list_sessions(): unavailable",
                        style="yellow",
                    )
                )
            else:
                rows.append(
                    Text(f"sessions on disk: {count}", style="default")
                )
            rows.append(
                Text(
                    "wiring: deferred (see ch13-phase8-audit-result.md)",
                    style="dim",
                )
            )
        except Exception as exc:
            rows.append(
                Text(f"storage import failed: {exc}", style="red")
            )
        return Group(*rows)


__all__ = ["DoctorScreen"]
