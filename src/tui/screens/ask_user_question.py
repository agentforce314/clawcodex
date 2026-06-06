"""AskUserQuestion modal screen for the TUI.

Mirrors the legacy REPL's :meth:`src.repl.core.ClawcodexREPL._ask_user_questions`
UX (numbered options + synthetic 'Other' free-text row) but rendered as a
Textual :class:`ModalScreen` so it can block the agent-loop worker thread
the same way :class:`PermissionModal` does.

Lifecycle:

1. The agent calls the ``AskUserQuestion`` tool, which invokes
   ``context.ask_user(questions)``.
2. :class:`src.tui.agent_bridge.AgentBridge._ask_user_handler` posts an
   :class:`~src.tui.messages.AskUserQuestionRequested` to the app, enqueues
   a :class:`~src.tui.state.PendingAskUser` on :class:`AppState`, and blocks
   on a :class:`threading.Event`.
3. :meth:`REPLScreen.on_ask_user_question_requested` pushes this modal.
4. The user picks one or more options (per question), optionally filling in
   the 'Other' free-text row, and presses Enter. The modal calls
   ``self.dismiss(answers)`` where ``answers`` is
   ``{question_text: chosen_label_or_free_text}``.
5. :meth:`AskUserQuestionModal._resolve` (or :meth:`_cancel`) calls
   ``request.decide(answers)`` which sets the worker's :class:`Event` and
   posts :class:`~src.tui.messages.AskUserQuestionResolved` so other UI
   surfaces (status line, transcript) can react.
"""

from __future__ import annotations

import re
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ..messages import AskUserQuestionResolved
from ..state import PendingAskUser


_OTHER_LABEL = "Other"
_QUICK_KEY_RE = re.compile(r"^[0-9]$")
_MAX_QUICK_KEYS = 10  # digits 0-9 in the footer binding


class _QuestionPanel(Vertical):
    """Renders a single question: header, numbered options, 'Other' input.

    Internal state: ``selected`` is a set of option indices the user has
    picked (single-select collapses to len<=1 at submit time, multi-select
    keeps the full set in user-pick order). ``other_text`` is the free-text
    fallback used when 'Other' is selected. The submit button is owned by
    the parent modal so a single Enter binding dismisses the whole form.
    """

    DEFAULT_CSS = """
    _QuestionPanel {
        height: auto;
        margin-bottom: 1;
    }
    _QuestionPanel .qheader {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }
    _QuestionPanel .qrow {
        height: 1;
        color: $text;
    }
    _QuestionPanel .qrow.-selected {
        color: $success;
        text-style: bold;
    }
    _QuestionPanel .qrow.-other {
        color: $warning;
    }
    _QuestionPanel Input {
        margin-left: 4;
        margin-top: 0;
    }
    """

    def __init__(self, question: dict[str, Any], index: int) -> None:
        super().__init__()
        self._question = question
        self._index = index
        options = question.get("options") or []
        self._options: list[dict[str, Any]] = [
            opt if isinstance(opt, dict) else {"label": str(opt), "description": ""}
            for opt in options
            if isinstance(opt, (dict, str))
        ]
        self._multi = bool(question.get("multiSelect", False))
        # selected option indices, 0-based into self._options. ``dict``
        # (not ``set``) so multi-select preserves user pick order at
        # submit time. Empty means nothing chosen yet (we default to the
        # first option on submit for single-select).
        self._selected: dict[int, None] = {}
        # Whether the "Other" row is selected.
        self._other_selected: bool = False
        # Free text for "Other".
        self._other_text: str = ""
        # Cached "Other" Input widget (lazily mounted when selected).

    @property
    def question_text(self) -> str:
        return str(self._question.get("question") or "")

    @property
    def multi(self) -> bool:
        return self._multi

    @property
    def other_text(self) -> str:
        return self._other_text

    @property
    def other_selected(self) -> bool:
        return self._other_selected

    def other_input(self) -> Input | None:
        try:
            return self.query_one(".other-input", Input)
        except Exception:
            return None

    def compose(self) -> ComposeResult:
        header = self._question.get("header") or f"Question {self._index + 1}"
        yield Static(f"[{header}]  {self.question_text}", classes="qheader")
        for i, opt in enumerate(self._options, start=1):
            label = str(opt.get("label", "")).strip() or f"Option {i}"
            desc = str(opt.get("description", "")).strip()
            line = f"  {i}. {label}"
            if desc:
                line += f"  [dim]{desc}[/dim]"
            yield Static(line, classes=f"qrow qopt-{i}", markup=True)
        other_idx = len(self._options) + 1
        yield Static(
            f"  {other_idx}. {_OTHER_LABEL}  [dim]Provide custom text[/dim]",
            classes="qrow qrow-other qopt-other",
            markup=True,
        )

    # ---- selection state mutation ----
    def _row_widgets(self) -> list[Static]:
        try:
            return list(self.query(".qrow"))
        except Exception:
            return []

    def _refresh_highlight(self) -> None:
        rows = self._row_widgets()
        for i, row in enumerate(rows):
            try:
                row.remove_class("-selected")
            except Exception:
                pass
            opt_idx = i  # 0-based into options
            if i < len(self._options) and opt_idx in self._selected:
                row.add_class("-selected")
            if i == len(self._options) and self._other_selected:
                row.add_class("-selected")

    def select_option(self, idx: int) -> None:
        """0-based option index, or ``-1`` for 'Other'."""
        if idx == -1:
            self._other_selected = True
            if not self._multi:
                self._selected.clear()
        else:
            if not 0 <= idx < len(self._options):
                return
            if self._multi:
                if idx in self._selected:
                    del self._selected[idx]
                else:
                    self._selected[idx] = None
                # Selecting a real option deselects 'Other'.
                self._other_selected = False
            else:
                self._selected = {idx: None}
                self._other_selected = False
        # Reveal / focus the "Other" input when 'Other' becomes selected.
        if self._other_selected:
            inp = self._ensure_other_input()
            if inp is not None:
                inp.focus()
        self._refresh_highlight()

    def _ensure_other_input(self) -> Input | None:
        existing = self.other_input()
        if existing is not None:
            return existing
        # Place the Input under the 'Other' row.
        try:
            inp = Input(placeholder="Custom text…", classes="other-input")
            self.mount(inp)
            return inp
        except Exception:
            return None

    def update_other_text(self, text: str) -> None:
        self._other_text = text

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.has_class("other-input"):
            self._other_text = event.value

    # ---- submit-time answer shaping ----
    def build_answer(self) -> str:
        """Return the chosen answer string for this question.

        - Multi-select: comma-joined labels in user-pick order.
        - Single-select: the chosen label, or free text if 'Other'.
        - Nothing chosen: default to the first option's label.
        """
        labels: list[str] = []
        if self._multi:
            # ``_selected`` is a dict (not a set) so iteration order is
            # the user's pick order, not the option-index order.
            for i in self._selected:
                if 0 <= i < len(self._options):
                    labels.append(
                        str(self._options[i].get("label", "")).strip()
                        or f"Option {i + 1}"
                    )
            if self._other_selected and self._other_text.strip():
                labels.append(self._other_text.strip())
        else:
            if self._other_selected and self._other_text.strip():
                return self._other_text.strip()
            if self._selected:
                idx = next(iter(self._selected))
                if 0 <= idx < len(self._options):
                    return (
                        str(self._options[idx].get("label", "")).strip()
                        or f"Option {idx + 1}"
                    )
            # Default to first option when nothing was picked.
            if self._options:
                return (
                    str(self._options[0].get("label", "")).strip()
                    or "Option 1"
                )
        return ", ".join(labels)


class AskUserQuestionModal(ModalScreen[dict[str, str] | None]):
    """Collects answers for one or more :class:`AskUserQuestion` questions.

    Returns ``None`` on cancel (Esc / Ctrl+C) so the agent loop can treat
    cancellation as a no-op or abort, and ``{question_text: answer}`` on
    submit. A single Enter binding submits the whole batch — the
    legacy REPL UX asked one question at a time and consumed a line per
    question; the TUI renders them stacked so a single submit button is
    enough.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+c", "cancel", "Cancel"),
        Binding("enter", "submit", "Submit", show=False, priority=False),
    ]

    DEFAULT_CSS = """
    AskUserQuestionModal {
        align: center middle;
    }
    AskUserQuestionModal > Middle > Center > #panel {
        width: 84;
        max-width: 95%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    AskUserQuestionModal #title {
        color: $primary;
        text-style: bold;
        margin-bottom: 1;
    }
    AskUserQuestionModal #scroll {
        height: auto;
        max-height: 70vh;
    }
    AskUserQuestionModal #footer {
        margin-top: 1;
        color: $text-muted;
    }
    AskUserQuestionModal #buttons {
        height: auto;
        margin-top: 1;
    }
    AskUserQuestionModal Button {
        min-width: 12;
        margin-right: 2;
    }
    AskUserQuestionModal Button.-submit {
        background: $success;
        color: $background;
    }
    AskUserQuestionModal Button.-cancel {
        background: $error;
        color: $background;
    }
    """

    def __init__(self, request: PendingAskUser) -> None:
        super().__init__()
        self._request = request

    def compose(self) -> ComposeResult:
        panel = Vertical(id="panel")
        panel.border_title = "[ AskUserQuestion ]"
        with Middle():
            with Center():
                yield panel

    def on_mount(self) -> None:
        try:
            panel = self.query_one("#panel", Vertical)
        except Exception:
            return
        panel.mount(
            Static(
                f"{len(self._request.questions)} question(s) pending",
                id="title",
                markup=False,
            )
        )
        scroll = VerticalScroll(id="scroll")
        panel.mount(scroll)
        for i, q in enumerate(self._request.questions):
            if not isinstance(q, dict):
                continue
            scroll.mount(_QuestionPanel(q, i))
        panel.mount(
            Static(
                "Type a number (1-9) to pick · Tab/click to navigate · "
                "Enter to submit · Esc to cancel",
                id="footer",
                markup=False,
            )
        )
        buttons = Vertical(id="buttons")
        panel.mount(buttons)
        buttons.mount(Button("Submit (Enter)", id="submit", classes="-submit"))
        buttons.mount(Button("Cancel (Esc)", id="cancel", classes="-cancel"))

    # ---- key handling ----
    def on_key(self, event: Any) -> None:
        """Number keys pick an option for the focused question.

        We don't use a ``Binding`` per digit because BINDINGS is global
        to the modal and we want the key to act on whichever question
        panel currently has focus (or the modal itself if no panel is
        focused). Mouse-driven users still get the buttons.
        """
        key = getattr(event, "key", "") or ""
        if not _QUICK_KEY_RE.match(key):
            return
        idx = int(key) - 1
        if idx < 0:
            # '0' is the tenth option (indices 0-8 are 1-9, 9 = Other).
            idx = 9
        panel = self._focused_panel()
        if panel is None:
            return
        if idx == len(panel._options):
            panel.select_option(-1)
        else:
            panel.select_option(idx)
        event.prevent_default()
        event.stop()

    def _focused_panel(self) -> _QuestionPanel | None:
        focused = self.focused
        if focused is None:
            return None
        # Walk up the DOM to find a _QuestionPanel.
        node: Any = focused
        while node is not None and node is not self:
            if isinstance(node, _QuestionPanel):
                return node
            node = getattr(node, "parent", None)
        return None

    # ---- button + binding handlers ----
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.action_submit()
        elif event.button.id == "cancel":
            self.action_cancel()

    def action_submit(self) -> None:
        answers: dict[str, str] = {}
        for child in self._iter_panels():
            if not child.question_text:
                continue
            answers[child.question_text] = child.build_answer()
        self._resolve(answers)

    def action_cancel(self) -> None:
        self._resolve(None)

    def _iter_panels(self):
        try:
            scroll = self.query_one("#scroll", VerticalScroll)
        except Exception:
            return
        for child in scroll.children:
            if isinstance(child, _QuestionPanel):
                yield child

    def _resolve(self, answers: dict[str, str] | None) -> None:
        # ``decide`` unblocks the worker thread BEFORE the message is
        # posted so the message handler doesn't need to synchronize
        # with the worker.
        try:
            self._request.decide(answers)
        except Exception:
            pass
        if answers is not None:
            self.app.post_message(
                AskUserQuestionResolved(
                    request_id=self._request.request_id,
                    answers=answers,
                )
            )
        self.dismiss(answers)


__all__ = ["AskUserQuestionModal"]
