"""Resume-conversation modal screen.

C2 of the components-folder parity plan upgraded this from the Phase-8
placeholder (which honestly said "not yet wired" while the persistence
producer didn't exist) to the real picker: the session-persistence
producer landed in PR #260 (``services/session_persistence`` driven by
``agent_bridge``), so ``SessionStorage.list_sessions()`` now has real
rows to show. Mirrors the degraded scope of TS
``components/LogSelector.tsx`` + ``screens/ResumeConversation.tsx``:
list + select → resume. (TS deep-search / worktree filters / tag tabs
ride on parked subsystems — gap doc §3.2 T2.)

Standing critic condition (gap doc §5 Q2): entries with
``message_count == 0`` or no transcript are FILTERED out — a headless
``/rename`` can create metadata-only sessions that would resume into an
empty conversation — and a footer reports how many were hidden.

The screen takes its entries by CONSTRUCTOR (the app does the listing
and filtering) so it stays a pure, testable view. The entry model +
filtering live in the UI-neutral ``src/services/session_listing`` (so
headless ``/resume`` never imports Textual); they are re-exported here
for compatibility.

Parked (documented divergence): the TS picker shows a transcript
preview pane (``SessionPreview.tsx``); Python's dormant
``widgets/session_preview.py`` is NOT wired in this phase — the picker
ships list+select only, preview rides with a later polish pass.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.services.session_listing import ResumeEntry, build_resume_entries


class ResumeConversation(ModalScreen[str | None]):
    """Modal listing resumable sessions; dismisses with the chosen id.

    Esc / q dismiss with ``None`` so callers can ignore the dismissal.
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
    ResumeConversation Static.-footer {
        color: $text-muted;
        padding: 1 0 0 0;
    }
    """

    def __init__(
        self,
        entries: list[ResumeEntry] | None = None,
        hidden_count: int = 0,
    ) -> None:
        super().__init__()
        self._entries = list(entries or [])
        self._hidden_count = hidden_count

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
        if not self._entries:
            empty = "No resumable conversations yet."
            if self._hidden_count:
                empty += (
                    f"\n({self._hidden_count} metadata-only "
                    "session(s) hidden — no stored messages.)"
                )
            body.mount(
                Static(Text(empty, style="dim"), classes="-empty", markup=False)
            )
            return
        options = OptionList(
            *(
                Option(entry.label(), id=entry.session_id)
                for entry in self._entries
            )
        )
        body.mount(options)
        if self._hidden_count:
            body.mount(
                Static(
                    Text(
                        f"{self._hidden_count} unresumable (metadata-only) "
                        "session(s) hidden",
                        style="dim",
                    ),
                    classes="-footer",
                    markup=False,
                )
            )
        options.focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        self.dismiss(event.option.id)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


__all__ = ["ResumeConversation", "ResumeEntry", "build_resume_entries"]
