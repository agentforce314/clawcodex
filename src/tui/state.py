"""In-memory UI state for the Claw Codex TUI.

Mirrors the minimum subset of ``typescript/src/state/AppState.tsx`` that
the REPL screen consults every frame. Kept as a plain dataclass rather
than Textual ``reactive`` attributes because the state is shared across
screens, widgets, and the agent-bridge worker thread — Textual's
reactive system is per-widget.

Widgets observe state by subscribing via :meth:`AppState.subscribe`;
updates coalesce through the Textual message pump (see
:class:`src.tui.messages.StateChanged`).
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class FocusedDialog(str, Enum):
    """Priority-ordered set of input-owning dialogs.

    Mirrors ``getFocusedInputDialog`` in ``typescript/src/screens/REPL.tsx``.
    The string value doubles as a stable id used by the focus router.
    """

    NONE = "none"
    PROMPT = "prompt"
    IDLE = "idle"
    COST = "cost"
    ELICITATION = "elicitation"
    HOOK = "hook"
    TOOL_PERMISSION = "tool-permission"
    SANDBOX = "sandbox"
    MESSAGE_SELECTOR = "message-selector"


# Priority order: **higher index wins**. Copied from the TS
# ``getFocusedInputDialog`` priority list.
_PRIORITY: list[FocusedDialog] = [
    FocusedDialog.PROMPT,
    FocusedDialog.IDLE,
    FocusedDialog.COST,
    FocusedDialog.ELICITATION,
    FocusedDialog.HOOK,
    FocusedDialog.TOOL_PERMISSION,
    FocusedDialog.SANDBOX,
    FocusedDialog.MESSAGE_SELECTOR,
]


def priority_of(dialog: FocusedDialog) -> int:
    try:
        return _PRIORITY.index(dialog)
    except ValueError:
        return -1


@dataclass
class PendingPermission:
    """A tool-permission request awaiting user decision.

    ``decide`` is called from the permission modal with the user's choice;
    it is safe to invoke from either thread.
    """

    request_id: str
    tool_name: str
    message: str
    suggestion: str | None
    tool_input: dict[str, Any] | None
    decide: Callable[[bool, bool], None]
    created_at: float = field(default_factory=time.time)


@dataclass
class AppState:
    """Observable UI state. See module docstring for semantics."""

    model: str = ""
    provider: str = ""
    is_thinking: bool = False
    verb: str = "Ready"
    verb_started_at: float = 0.0
    turn_count: int = 0
    in_progress_tool_use_ids: set[str] = field(default_factory=set)
    streaming_text: str = ""
    queued_prompts: list[str] = field(default_factory=list)
    pending_permissions: list[PendingPermission] = field(default_factory=list)
    focused_dialog: FocusedDialog = FocusedDialog.PROMPT
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    last_error: str | None = None

    _subscribers: list[Callable[[], None]] = field(default_factory=list, repr=False, compare=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)
    _ids: "itertools.count[int]" = field(default_factory=lambda: itertools.count(1), repr=False, compare=False)

    # ---- focus ----
    def recompute_focus(self) -> FocusedDialog:
        """Return the highest-priority dialog currently active.

        Widgets must call this after any mutation that could change focus
        (enqueuing a permission, showing a cost modal, etc.).
        """

        with self._lock:
            candidates: list[FocusedDialog] = [FocusedDialog.PROMPT]
            if self.pending_permissions:
                candidates.append(FocusedDialog.TOOL_PERMISSION)
            # Future phases populate more candidates (COST/IDLE/HOOK/...).
            best = max(candidates, key=priority_of)
            self.focused_dialog = best
            return best

    # ---- permission queue ----
    def enqueue_permission(
        self,
        tool_name: str,
        message: str,
        suggestion: str | None,
        tool_input: dict[str, Any] | None,
        decide: Callable[[bool, bool], None],
    ) -> PendingPermission:
        with self._lock:
            request = PendingPermission(
                request_id=f"perm-{next(self._ids)}",
                tool_name=tool_name,
                message=message,
                suggestion=suggestion,
                tool_input=tool_input,
                decide=decide,
            )
            self.pending_permissions.append(request)
        self.recompute_focus()
        self._notify()
        return request

    def resolve_permission(self, request_id: str) -> None:
        with self._lock:
            self.pending_permissions = [
                p for p in self.pending_permissions if p.request_id != request_id
            ]
        self.recompute_focus()
        self._notify()

    def pop_next_permission(self) -> PendingPermission | None:
        with self._lock:
            if not self.pending_permissions:
                return None
            return self.pending_permissions[0]

    # ---- in-progress tool tracking ----
    def mark_tool_started(self, tool_use_id: str) -> None:
        if not tool_use_id:
            return
        with self._lock:
            self.in_progress_tool_use_ids.add(tool_use_id)
        self._notify()

    def mark_tool_finished(self, tool_use_id: str) -> None:
        if not tool_use_id:
            return
        with self._lock:
            self.in_progress_tool_use_ids.discard(tool_use_id)
        self._notify()

    # ---- streaming text ----
    def append_streaming_text(self, chunk: str) -> None:
        if not chunk:
            return
        with self._lock:
            self.streaming_text += chunk
        self._notify()

    def clear_streaming_text(self) -> None:
        with self._lock:
            self.streaming_text = ""
        self._notify()

    # ---- thinking / verb ----
    def set_thinking(self, thinking: bool, verb: str = "") -> None:
        with self._lock:
            self.is_thinking = thinking
            if thinking:
                self.verb = verb or "Synthesizing"
                self.verb_started_at = time.time()
            else:
                self.verb = "Ready"
                self.verb_started_at = 0.0
        self._notify()

    # ---- subscription ----
    def subscribe(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._subscribers.append(cb)

        def _unsub() -> None:
            try:
                self._subscribers.remove(cb)
            except ValueError:
                pass

        return _unsub

    def _notify(self) -> None:
        for cb in list(self._subscribers):
            try:
                cb()
            except Exception:
                continue
