"""Resume-conversation modal screen.

Lists past sessions from :class:`~src.services.session_storage.SessionStorage`
and returns the selected session ID on dismissal. Esc / Ctrl+C returns
``None`` so callers can ignore the dismissal.

Part of the Ctrl+B → ``--resume`` round-trip: after backgrounding the
TUI (Ctrl+B), the user runs ``clawcodex --tui --resume <session_id>``
or picks a session from this dialog via ``/resume``.
"""

from __future__ import annotations

from datetime import datetime
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


class ResumeConversation(ModalScreen[str | None]):
    """Modal listing past sessions; dismisses with the chosen session id.

    Esc / Ctrl+C returns ``None`` so callers (slash commands) can ignore
    the dismissal.
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
                        "No previous sessions found.\n\n"
                        "Start a new conversation, then use Ctrl+B to "
                        "background it and resume later with:\n"
                        "  clawcodex --tui --resume <session_id>",
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

        Reads metadata from :class:`SessionStorage` and formats each
        entry with timestamp, title, model, and message count.
        """
        try:
            from src.services.session_storage import SessionStorage

            metas = SessionStorage.list_sessions()
            out: list[tuple[str, str]] = []
            for meta in metas:
                session_id = getattr(meta, "session_id", None) or ""
                if not session_id:
                    continue
                title = getattr(meta, "title", None) or "(untitled)"
                model = getattr(meta, "model", None) or ""
                msg_count = getattr(meta, "message_count", None) or 0
                last_updated = getattr(meta, "last_updated", None)
                if last_updated:
                    try:
                        dt_str = datetime.fromtimestamp(last_updated).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                    except Exception:
                        dt_str = ""
                else:
                    dt_str = ""
                label_parts = [title]
                if model:
                    label_parts.append(model)
                label = " — ".join(label_parts)
                if msg_count:
                    label += f" [{msg_count} msgs]"
                if dt_str:
                    label = f"{dt_str} | {label}"
                out.append((session_id, label))
            return out
        except Exception:
            return []


__all__ = ["ResumeConversation"]
