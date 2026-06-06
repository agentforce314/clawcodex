"""Back-compat shim for the F-38 :class:`ProgressReporter` (F-40).

After F-40 the canonical type is :class:`extensions.orchestrator.progress_sink.ProgressSink`,
with :class:`ToolContextProgressSink` as the default implementation that
writes events to ``ToolContext.tasks``. This module is kept so that any
existing code still calling the old ``ProgressReporter(context)`` /
``set_task_id`` / ``on_event`` trio continues to work — the shim owns
a single :class:`ToolContextProgressSink` instance per ``set_task_id``
call and dispatches the legacy ``on_event`` calls onto the new
``on_*_complete`` methods.

New code should depend on :class:`ProgressSink` /
:class:`ToolContextProgressSink` / :class:`CompositeProgressSink` from
:mod:`extensions.orchestrator.progress_sink` directly.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from ..api.query import PhaseComplete, SessionComplete, TurnComplete

if TYPE_CHECKING:
    from .agent_runner import AgentSession
    from src.tool_system.context import ToolContext
    from .progress_sink import ToolContextProgressSink

logger = logging.getLogger(__name__)


class ProgressReporter:
    """Back-compat shim. Use :class:`ToolContextProgressSink` directly.

    Holds a single :class:`ToolContextProgressSink` instance that is
    re-created on every :meth:`set_task_id` call, so two reporters
    configured for different tasks never share state. ``on_event``
    dispatches to the three ``on_*_complete`` methods based on the
    event's runtime type, matching the original F-38 behavior.
    """

    def __init__(self, context: "ToolContext") -> None:
        self._context = context
        self._current_task_id: str | None = None
        self._phase_count = 0
        self._sink: "ToolContextProgressSink | None" = None

    @property
    def task_id(self) -> str:
        """Expose the bound task id (matches :class:`ProgressSink.task_id`)."""
        return self._current_task_id or ""

    def set_task_id(self, task_id: str | None) -> None:
        """Bind the reporter to ``task_id``; resets the per-task phase counter.

        A fresh :class:`ToolContextProgressSink` is created so the shim
        never reuses state across tasks. Passing ``None`` detaches the
        reporter (subsequent :meth:`on_event` calls become no-ops).
        """
        self._current_task_id = task_id
        self._phase_count = 0
        self._sink = (
            self._build_sink(task_id) if task_id else None
        )

    # -- sink plumbing ---------------------------------------------------

    def _build_sink(self, task_id: str) -> "ToolContextProgressSink":
        # Local import to avoid a circular dependency with
        # ``progress_sink.py`` (which itself only imports the agent
        # runner under ``TYPE_CHECKING``).
        from .progress_sink import ToolContextProgressSink

        return ToolContextProgressSink(task_id, self._context)

    def _get_sink(self) -> "ToolContextProgressSink | None":
        return self._sink

    # -- back-compat dispatch --------------------------------------------

    def on_event(self, event: Any, session: "AgentSession") -> None:
        """Dispatch ``event`` to the underlying sink by runtime type.

        Recognizes :class:`PhaseComplete`, :class:`TurnComplete` and
        :class:`SessionComplete`. Other event types are ignored (the
        original F-38 implementation behaved the same way).
        """
        sink = self._get_sink()
        if sink is None:
            return
        if isinstance(event, PhaseComplete):
            sink.on_phase_complete(event, session)
        elif isinstance(event, TurnComplete):
            sink.on_turn_complete(event, session)
        elif isinstance(event, SessionComplete):
            sink.on_session_complete(event, session)
        # Other event types are intentionally ignored.

    # -- ProgressSink-protocol shims -------------------------------------
    # These make the legacy ProgressReporter also satisfy the new
    # :class:`ProgressSink` protocol so that callers can pass it as the
    # ``progress_sink`` argument to :class:`AgentRunner.run`.

    def on_phase_complete(
        self,
        event: PhaseComplete,
        session: "AgentSession",
    ) -> None:
        self.on_event(event, session)

    def on_turn_complete(
        self,
        event: TurnComplete,
        session: "AgentSession",
    ) -> None:
        self.on_event(event, session)

    def on_session_complete(
        self,
        event: SessionComplete,
        session: "AgentSession",
    ) -> None:
        self.on_event(event, session)
