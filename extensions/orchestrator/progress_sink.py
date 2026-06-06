"""ProgressSink protocol and concrete sinks (F-40).

This module replaces the F-38-era single-instance :class:`ProgressReporter`
with a per-session :class:`ProgressSink` protocol so that the orchestrator
can fan out agent progress events to multiple consumers without sharing
mutable state between concurrent issues.

Design references:

* :class:`ProgressSink` — minimal protocol; consumers implement three
  ``on_*_complete`` methods and own their private state via the bound
  ``task_id``.
* :class:`CompositeProgressSink` — synchronous fan-out with per-sink
  exception isolation; one bad consumer never blocks the others.
* :class:`ToolContextProgressSink` — the default implementation that
  writes events into ``ToolContext.tasks`` (preserving the original
  :class:`ProgressReporter` behavior). Uses :attr:`workflow_phases` to
  compute honest progress percentages when available, and falls back to
  ``None`` (UI shows "unknown") when no phase weights are configured.

The legacy :mod:`extensions.orchestrator.progress_reporter` module
remains as a back-compat shim that delegates to a single
:class:`ToolContextProgressSink` instance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Iterable, Protocol, runtime_checkable

from ..api.query import PhaseComplete, SessionComplete, TurnComplete

if TYPE_CHECKING:
    from .agent_runner import AgentSession
    from src.tool_system.context import ToolContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ProgressSink(Protocol):
    """A consumer of agent progress events for ONE task / session.

    Each :class:`ProgressSink` instance is bound to a single ``task_id``
    and its own private state (phase counter, configured phase list, …).
    Because the instance is never accessed by more than one
    :class:`AgentSession`, the protocol makes no threading guarantees —
    internal counters can be plain ints.

    The three ``on_*_complete`` methods are dispatched by
    :class:`extensions.orchestrator.agent_runner.AgentRunner` at well
    defined points in the session lifecycle:

    * :meth:`on_phase_complete` — a logical phase (one or more turns)
      finished. ``event.phase`` is the 1-based phase number.
    * :meth:`on_turn_complete` — a single turn finished; ``event.turn``
      is the 1-based turn counter.
    * :meth:`on_session_complete` — the whole session is ending.
      ``event.reason`` is one of ``"success"``, ``"stagnation"``,
      ``"loop_detected"``, ``"noop_completed"``, ``"budget_exhausted"``,
      ``"max_turns_exceeded"``, ``"rate_limit_circuit_open"``,
      ``"exit_code=N"`` (runner-level termination reason).
    """

    task_id: str

    def on_phase_complete(
        self,
        event: PhaseComplete,
        session: "AgentSession",
    ) -> None: ...

    def on_turn_complete(
        self,
        event: TurnComplete,
        session: "AgentSession",
    ) -> None: ...

    def on_session_complete(
        self,
        event: SessionComplete,
        session: "AgentSession",
    ) -> None: ...


# ---------------------------------------------------------------------------
# Composite (fan-out with exception isolation)
# ---------------------------------------------------------------------------


class CompositeProgressSink:
    """Synchronous fan-out over a list of :class:`ProgressSink` consumers.

    Each child sink runs sequentially in registration order; an exception
    raised by one sink is logged via :func:`logger.exception` and the
    remaining sinks still receive the event. The composite never raises
    out of its ``on_*_complete`` methods.

    Sinks are mutable: :meth:`add` lets the orchestrator register
    additional consumers (e.g. F-37 :class:`PRReviewAutoFixSink` or
    F-39 :class:`RetryLabelSink`) without touching the runner.
    """

    def __init__(self, sinks: Iterable[ProgressSink] = ()) -> None:
        self._sinks: list[ProgressSink] = list(sinks)

    # The composite itself satisfies the ProgressSink protocol — its
    # ``task_id`` is empty because it is not bound to a single task.
    task_id: str = ""

    def add(self, sink: ProgressSink) -> None:
        """Append ``sink`` to the fan-out list."""
        self._sinks.append(sink)

    def __len__(self) -> int:
        return len(self._sinks)

    def __iter__(self):
        return iter(self._sinks)

    def _dispatch(
        self,
        method_name: str,
        event: Any,
        session: "AgentSession",
    ) -> None:
        for sink in list(self._sinks):
            method = getattr(sink, method_name, None)
            if method is None:
                logger.debug(
                    "sink %s has no %s method, skipping",
                    sink,
                    method_name,
                )
                continue
            try:
                method(event, session)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "progress_sink.%s dispatch failed for sink=%r: %s",
                    method_name,
                    sink,
                    exc,
                )

    def on_phase_complete(
        self,
        event: PhaseComplete,
        session: "AgentSession",
    ) -> None:
        self._dispatch("on_phase_complete", event, session)

    def on_turn_complete(
        self,
        event: TurnComplete,
        session: "AgentSession",
    ) -> None:
        self._dispatch("on_turn_complete", event, session)

    def on_session_complete(
        self,
        event: SessionComplete,
        session: "AgentSession",
    ) -> None:
        self._dispatch("on_session_complete", event, session)


# ---------------------------------------------------------------------------
# Default ToolContext-backed implementation
# ---------------------------------------------------------------------------


class ToolContextProgressSink:
    """Default :class:`ProgressSink` that writes events into a :class:`ToolContext`.

    Mirrors the original :class:`ProgressReporter` behavior (events land
    in :attr:`ToolContext.tasks` via :func:`_progress_report_call` and
    :func:`_task_update_call`) but holds its own private state, so two
    sinks can run concurrently without cross-talk.

    Progress percentage policy (F-40 decision table):

    * When ``workflow_phases`` is configured, the nth phase receives
      ``(n / total) * 100`` so users see a meaningful number that
      tracks real workflow progress.
    * When ``fallback_to_phase_step`` is True, fall back to
      ``min(idx * 25, 100)`` (the old F-38 behavior) for soft migration.
    * Otherwise the sink writes ``None`` so the dashboard displays
      "Phase N (progress unknown)" instead of the misleading
      ``25 / 50 / 75 / 100`` sequence.

    :class:`SessionComplete` always emits a single stage named
    ``session_{reason}`` and only fakes ``progress=100`` for the
    ``"success"`` reason — other reasons (``stagnation``,
    ``loop_detected``, ``noop_completed``, ``budget_exhausted``, …) get
    ``progress=None`` so the dashboard never lies about a failed run.
    """

    def __init__(
        self,
        task_id: str,
        context: "ToolContext",
        workflow_phases: list[str] | None = None,
        fallback_to_phase_step: bool = False,
    ) -> None:
        self.task_id = task_id
        self._context = context
        self._phase_count = 0
        self._workflow_phases: list[str] = list(workflow_phases or [])
        self._fallback_to_phase_step = fallback_to_phase_step

    # -- helpers ---------------------------------------------------------

    def _named_phase(self, idx: int) -> str:
        if 1 <= idx <= len(self._workflow_phases):
            return self._workflow_phases[idx - 1]
        return f"phase_{idx}"

    def _phase_progress(self, idx: int) -> int | None:
        """Return the progress percentage for the nth phase, or None."""
        if self._workflow_phases:
            # Use the position the named phase actually occupies so that
            # the percentage is consistent even when idx > len(phases)
            # (a re-run that goes past the configured phase list still
            # reports 100% for the last configured phase).
            try:
                real_idx = self._workflow_phases.index(
                    self._named_phase(idx)
                )
            except ValueError:
                return 100
            return int((real_idx + 1) / len(self._workflow_phases) * 100)
        if self._fallback_to_phase_step:
            return min(idx * 25, 100)
        return None

    # -- dispatch --------------------------------------------------------

    def on_phase_complete(
        self,
        event: PhaseComplete,
        session: "AgentSession",
    ) -> None:
        if not self.task_id:
            return
        self._phase_count += 1
        # F-40: use ``event.phase`` (the 1-based phase number reported
        # by the runner) as the authoritative phase index so the stage
        # name and progress percentage stay aligned with what the
        # agent actually completed. ``self._phase_count`` is still
        # maintained for back-compat with the previous
        # ``ProgressReporter`` summary text.
        phase_idx = event.phase or self._phase_count
        phase_name = self._named_phase(phase_idx)
        progress = self._phase_progress(phase_idx)
        self._write_progress(
            stage=phase_name,
            progress=progress,
            summary=f"Completed phase {phase_idx}",
            metadata={
                "turn_count": event.turn_count,
                "phase": phase_idx,
                "auto": True,
            },
        )
        self._write_task_update(
            {
                "phase": phase_idx,
                "turn_count": event.turn_count,
                "phase_name": phase_name,
                "phase_complete": True,
            }
        )

    def on_turn_complete(
        self,
        event: TurnComplete,
        session: "AgentSession",
    ) -> None:
        # Turn events are noisy: don't pollute ToolContext.tasks with
        # one row per turn. They are useful for log-only debugging
        # downstream sinks may add later.
        logger.debug(
            "turn %d complete for task %s (issue=%s)",
            event.turn,
            self.task_id,
            getattr(getattr(session, "issue", None), "identifier", "unknown"),
        )

    def on_session_complete(
        self,
        event: SessionComplete,
        session: "AgentSession",
    ) -> None:
        if not self.task_id:
            return
        # F-40 decision: only "success" gets progress=100. Every other
        # termination reason leaves progress=None so the dashboard does
        # not show a misleading "100%" on failed / aborted sessions.
        progress = 100 if event.reason == "success" else None
        self._write_progress(
            stage=f"session_{event.reason}",
            progress=progress,
            summary=f"Session ended: {event.reason}",
            metadata={
                "session_status": getattr(session, "status", "unknown"),
                "turn_count": getattr(session, "turn_count", 0),
                "phase_count": self._phase_count,
            },
        )

    # -- low-level ToolContext writers -----------------------------------

    def _write_progress(
        self,
        *,
        stage: str,
        progress: int | None,
        summary: str,
        metadata: dict[str, Any],
    ) -> None:
        try:
            from src.tool_system.tools.progress_report import _progress_report_call

            _progress_report_call(
                {
                    "taskId": self.task_id,
                    "stage": stage,
                    "progress": progress,
                    "summary": summary,
                    "metadata": metadata,
                },
                self._context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to record progress for task %s stage=%s: %s",
                self.task_id,
                stage,
                exc,
            )

    def _write_task_update(self, metadata: dict[str, Any]) -> None:
        try:
            from src.tool_system.tools.tasks_v2 import _task_update_call

            _task_update_call(
                {
                    "taskId": self.task_id,
                    "metadata": metadata,
                },
                self._context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to update task metadata for task %s: %s",
                self.task_id,
                exc,
            )
