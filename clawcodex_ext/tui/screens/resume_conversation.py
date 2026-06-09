"""Resume-conversation modal screen with fuzzy search.

Lists past sessions from :class:`~src.services.session_storage.SessionStorage`
with a live fuzzy filter (mirroring :class:`HistorySearchScreen`), and returns
the selected session ID on dismissal. Esc / Ctrl+C returns ``None`` so callers
can ignore the dismissal.

Entry points:
* ``clawcodex --tui --resume`` (no SESSION_ID) → shows this browser on mount
* ``/resume`` slash command → pushes this screen from the REPL
* After Ctrl+B background → user picks a session to re-attach
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Sequence

from rich.text import Text
from textual.binding import Binding
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static

from ..widgets.select_list import SelectList, SelectOption
from .dialog_base import DialogScreen
from .history_search import fuzzy_score


@dataclass
class SessionEntry:
    """Lightweight wrapper around session metadata for display and ranking."""

    session_id: str
    title: str = ""
    model: str = ""
    msg_count: int = 0
    last_updated: float = 0.0
    cwd: str = ""
    last_user_input: str = ""
    # Cached transcript text for content search (loaded lazily).
    _transcript_text: str = field(default="", repr=False)

    @property
    def display_label(self) -> str:
        """Format the session as a one-line label with metadata."""
        parts: list[str] = []
        # Timestamp
        if self.last_updated:
            try:
                dt_str = datetime.fromtimestamp(self.last_updated).strftime(
                    "%Y-%m-%d %H:%M"
                )
                parts.append(dt_str)
            except Exception:
                pass
        # Last user input (truncated) — more useful than title
        if self.last_user_input:
            preview = self.last_user_input
            if len(preview) > 60:
                preview = preview[:57] + "…"
            parts.append(preview)
        elif self.title:
            parts.append(self.title)
        else:
            parts.append("(untitled)")
        label = " | ".join(parts)
        # Description: model + msg count
        desc_parts: list[str] = []
        if self.model:
            desc_parts.append(self.model)
        if self.msg_count:
            desc_parts.append(f"{self.msg_count} msgs")
        # Short session ID (first 8 chars)
        if self.session_id:
            desc_parts.append(f"id:{self.session_id[:8]}")
        if desc_parts:
            label += "  " + "  ".join(desc_parts)
        return label

    @property
    def searchable_text(self) -> str:
        """All text that should be matched by fuzzy search."""
        parts = [
            self.title,
            self.model,
            self.cwd,
            self.session_id,
            self.last_user_input,
        ]
        # Include transcript text if cached (for content search).
        if self._transcript_text:
            parts.append(self._transcript_text)
        return " ".join(p for p in parts if p)


class ResumeConversation(DialogScreen[str | None]):
    """Modal listing past sessions with fuzzy search; resolves with chosen session id.

    Esc returns ``None`` so callers (slash commands, mount hooks) can ignore
    the dismissal.
    """

    title_text = "Resume conversation"
    subtitle_text = "Select a previous session to resume, or press Esc to start fresh."
    footer_hint = "Enter to resume · Esc to cancel"

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("up", "move(-1)", "Previous", show=False),
        Binding("down", "move(1)", "Next", show=False),
        Binding("ctrl+p", "move(-1)", "Previous", show=False),
        Binding("ctrl+n", "move(1)", "Next", show=False),
        Binding("enter", "select_current", "Select", show=False),
    ]

    filter_text: reactive[str] = reactive("")

    def __init__(
        self,
        *,
        sessions: Sequence[SessionEntry] | None = None,
        on_resume: object | None = None,
    ) -> None:
        super().__init__()
        self._sessions = list(sessions) if sessions else self._load_sessions()
        self._on_resume = on_resume
        self._input: Input | None = None
        self._list: SelectList | None = None
        self._count_label: Static | None = None

    def build_body(self) -> Iterator[Widget]:
        self._input = Input(placeholder="type to filter sessions…", value="")
        yield self._input
        self._count_label = Static(Text(""), markup=False)
        yield self._count_label
        self._list = SelectList(self._options_for_query(""))
        yield self._list

    def _post_mount(self) -> None:
        if self._input is not None:
            self._input.focus()
        self.filter_text = ""
        self._update_count_label()

    # ---- input handling ----
    def on_input_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value

    def on_input_submitted(self, _: Input.Submitted) -> None:
        self.action_select_current()

    def watch_filter_text(self, value: str) -> None:
        if self._list is None:
            return
        self._list.set_options(self._options_for_query(value))
        self._update_count_label()

    def _update_count_label(self) -> None:
        if self._count_label is None or self._list is None:
            return
        total = len(self._sessions)
        shown = len(self._list.options)
        self._count_label.update(
            Text(f"  {shown} / {total} session{'s' if total != 1 else ''}", style="dim")
        )

    # ---- navigation while Input has focus ----
    def action_move(self, delta: int) -> None:
        if self._list is not None:
            self._list.action_move(delta)

    def action_select_current(self) -> None:
        if self._list is None or self._list.current is None:
            self.dismiss(None)
            return
        option = self._list.current
        session_id = str(option.value)
        if self._on_resume is not None and callable(self._on_resume):
            try:
                self._on_resume(session_id)
            except Exception:
                pass
        self.dismiss(session_id)

    # ---- helpers ----
    def _options_for_query(self, query: str) -> list[SelectOption]:
        scored: list[tuple[SessionEntry, int]] = []
        for entry in self._sessions:
            matched, score = fuzzy_score(entry.searchable_text, query)
            if matched:
                scored.append((entry, score))
        # Higher score first; stable-sort preserves insertion order for ties
        scored.sort(key=lambda pair: pair[1], reverse=True)

        out: list[SelectOption] = []
        for entry, _ in scored[:50]:
            label = entry.display_label
            if len(label) > 90:
                label = label[:87] + "…"
            out.append(SelectOption(label=label, value=entry.session_id))
        return out

    # ---- data loading ----
    @staticmethod
    def _load_sessions() -> list[SessionEntry]:
        """Load sessions from :class:`SessionStorage`."""
        try:
            from src.services.session_storage import SessionStorage

            metas = SessionStorage.list_sessions()
            out: list[SessionEntry] = []
            for meta in metas:
                session_id = getattr(meta, "session_id", None) or ""
                if not session_id:
                    continue
                # Load transcript text for content search.
                transcript_text = ""
                try:
                    storage = SessionStorage(session_id=session_id)
                    messages = storage.read_messages()
                    text_parts: list[str] = []
                    for msg in messages:
                        role = getattr(msg, "role", None) or ""
                        content = getattr(msg, "content", None) or ""
                        if isinstance(content, str):
                            text_parts.append(content)
                        elif isinstance(content, list):
                            for item in content:
                                if isinstance(item, str):
                                    text_parts.append(item)
                                elif isinstance(item, dict):
                                    if item.get("type") in (None, "text"):
                                        text_parts.append(str(item.get("text") or ""))
                    transcript_text = " ".join(text_parts)
                    # Truncate to avoid excessive memory use.
                    if len(transcript_text) > 2000:
                        transcript_text = transcript_text[:2000]
                except Exception:
                    pass
                out.append(SessionEntry(
                    session_id=session_id,
                    title=getattr(meta, "title", None) or "",
                    model=getattr(meta, "model", None) or "",
                    msg_count=getattr(meta, "message_count", None) or 0,
                    last_updated=getattr(meta, "last_updated", None) or 0.0,
                    cwd=getattr(meta, "cwd", None) or "",
                    last_user_input=getattr(meta, "last_user_input", None) or "",
                    _transcript_text=transcript_text,
                ))
            return out
        except Exception:
            return []


__all__ = ["ResumeConversation", "SessionEntry"]
