"""Resume-conversation modal screen.

Phase-8 of the ch13 refactor (gap #9) — see
``my-docs/ch13-phase8-audit-result.md`` for the audit result and the
rationale for shipping a placeholder rather than the full wiring.

When the WI-8.0 audit found state (2) — `services/session_storage.py`
exists as a module but no live caller writes transcripts — the
Resume/Doctor screens were scope-limited to navigation surfaces that
honestly say "not yet wired" to the user. This module provides that
surface; future work fills in the listing once persistence is wired.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


class ResumeConversation(ModalScreen[str | None]):
    """Modal listing past sessions; dismisses with the chosen session id.

    Currently a placeholder — :meth:`_load_sessions` returns ``[]`` until
    transcript-persistence wiring lands. The Esc / Ctrl+C path returns
    ``None`` so callers (slash commands) can ignore the dismissal.
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("q", "dismiss_modal", "Close"),
    ]

    DEFAULT_CSS = """
    ResumeConversation {
        align: center middle;
    }
    ResumeConversation > Middle > Center > Vertical {
        width: 80%;
        max-width: 90;
        max-height: 70%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    ResumeConversation Static.-title {
        color: $primary;
        text-style: bold;
        padding: 0 0 1 0;
    }
    ResumeConversation Static.-empty {
        color: $text-muted;
        padding: 1 0 0 0;
    }
    """

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                yield Vertical()

    def on_mount(self) -> None:
        body = self.query_one(Vertical)
        body.mount(
            Static(
                Text("Resume conversation", style="bold"),
                classes="-title",
                markup=False,
            )
        )
        sessions = self._load_sessions()
        if not sessions:
            body.mount(
                Static(
                    Text(
                        "No persisted conversations yet.\n\n"
                        "Transcript persistence is not wired into this build "
                        "(see my-docs/ch13-phase8-audit-result.md). When the "
                        "wiring lands, prior sessions will appear here.",
                        style="dim",
                    ),
                    classes="-empty",
                    markup=False,
                )
            )
            return
        options = OptionList(
            *(Option(label, id=session_id) for session_id, label in sessions)
        )
        body.mount(options)

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        self.dismiss(event.option.id)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    # ---- internals ----
    def _load_sessions(self) -> list[tuple[str, str]]:
        """Return ``(session_id, label)`` pairs to display.

        Phase-8 close-out: ``SessionStorage.list_sessions()`` is now wired
        from ``agent_bridge.py``; this method reads the metadata files
        the agent loop writes during normal operation. Empty list when
        no sessions exist (clean install, first run).
        """

        try:
            from src.services.session_storage import SessionStorage

            metas = SessionStorage.list_sessions()
            out: list[tuple[str, str]] = []
            for meta in metas:
                session_id = getattr(meta, "session_id", None) or getattr(
                    meta, "id", None
                )
                label = (
                    getattr(meta, "title", None)
                    or getattr(meta, "summary", None)
                    or session_id
                    or "(unnamed session)"
                )
                if session_id:
                    out.append((session_id, str(label)))
            return out
        except Exception:
            return []


__all__ = ["ResumeConversation"]
