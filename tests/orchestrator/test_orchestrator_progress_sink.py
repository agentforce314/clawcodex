"""Unit tests for the F-40 ProgressSink protocol and concrete sinks.

Covers the four F-40 acceptance points:

* :class:`ToolContextProgressSink` writes phase / turn / session
  events into ``ToolContext.tasks`` (regression for the F-38 reporter
  that this class replaces).
* :class:`CompositeProgressSink` fans events out to multiple consumers
  and isolates exceptions so one bad consumer does not break the
  others.
* The ``ProgressReporter`` shim still supports the legacy
  ``set_task_id`` / ``on_event`` API and dispatches by event type.
* The :class:`WorkflowConfig` ``phases`` field parses from raw dicts
  and produces honest ``progress`` percentages.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from extensions.api.query import (
    PhaseComplete,
    SessionComplete,
    TurnComplete,
)
from extensions.orchestrator.config.schema import (
    AgentConfig,
    CodexConfig,
    WorkflowConfig,
)
from extensions.orchestrator.issue import Issue
from extensions.orchestrator.progress_reporter import ProgressReporter
from extensions.orchestrator.progress_sink import (
    CompositeProgressSink,
    ProgressSink,
    ToolContextProgressSink,
)
from extensions.orchestrator.workspace import Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingSink:
    """Minimal :class:`ProgressSink` that records every event it sees."""

    def __init__(self) -> None:
        self.task_id: str = "recorder"
        self.events: list[tuple[str, Any]] = []

    def on_phase_complete(self, event, session) -> None:
        self.events.append(("phase", event))

    def on_turn_complete(self, event, session) -> None:
        self.events.append(("turn", event))

    def on_session_complete(self, event, session) -> None:
        self.events.append(("session", event))


class _ExplodingSink(_RecordingSink):
    """Sink that raises on every callback (for exception-isolation tests)."""

    def on_phase_complete(self, event, session) -> None:
        super().on_phase_complete(event, session)
        raise RuntimeError("boom-phase")

    def on_turn_complete(self, event, session) -> None:
        super().on_turn_complete(event, session)
        raise RuntimeError("boom-turn")

    def on_session_complete(self, event, session) -> None:
        super().on_session_complete(event, session)
        raise RuntimeError("boom-session")


def _make_session() -> Workspace:
    # Just a stub; ToolContextProgressSink does not touch the session
    # beyond reading ``session.issue.identifier`` for debug logs.
    return Workspace(
        path="/tmp",
        issue_identifier="ISSUE-F-40",
        issue_id="f-40",
    )


def _make_session_obj() -> Any:
    """Build a minimal stand-in for an :class:`AgentSession`."""
    session = MagicMock()
    session.issue = Issue(id="f-40", identifier="F-40")
    session.status = "running"
    session.turn_count = 0
    return session


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProgressSinkProtocol(unittest.TestCase):
    """The :class:`ProgressSink` protocol is structural (Protocol)."""

    def test_recorder_satisfies_protocol(self) -> None:
        sink: Any = _RecordingSink()
        self.assertIsInstance(sink, ProgressSink)

    def test_exploding_sink_satisfies_protocol(self) -> None:
        sink: Any = _ExplodingSink()
        self.assertIsInstance(sink, ProgressSink)

    def test_composite_sink_satisfies_protocol(self) -> None:
        sink: Any = CompositeProgressSink([_RecordingSink()])
        self.assertIsInstance(sink, ProgressSink)


# ---------------------------------------------------------------------------
# CompositeProgressSink fan-out + exception isolation
# ---------------------------------------------------------------------------


class TestCompositeProgressSink(unittest.TestCase):
    def test_fans_out_to_every_sink(self) -> None:
        a, b = _RecordingSink(), _RecordingSink()
        composite = CompositeProgressSink([a, b])
        session = _make_session_obj()
        composite.on_phase_complete(PhaseComplete(phase=1, turn_count=1), session)
        composite.on_turn_complete(TurnComplete(turn=1), session)
        composite.on_session_complete(SessionComplete(reason="success"), session)

        for sink in (a, b):
            self.assertEqual(
                [e[0] for e in sink.events],
                ["phase", "turn", "session"],
            )

    def test_exception_is_isolated(self) -> None:
        """One bad sink must not break the others."""
        bad = _ExplodingSink()
        good = _RecordingSink()
        composite = CompositeProgressSink([bad, good])
        session = _make_session_obj()

        # All three callbacks should not raise out of the composite.
        composite.on_phase_complete(PhaseComplete(phase=1, turn_count=1), session)
        composite.on_turn_complete(TurnComplete(turn=1), session)
        composite.on_session_complete(SessionComplete(reason="success"), session)

        # The good sink still received every event despite the
        # exploding sink raising in between.
        self.assertEqual(
            [e[0] for e in good.events],
            ["phase", "turn", "session"],
        )
        # The exploding sink recorded the events before raising.
        self.assertEqual(
            [e[0] for e in bad.events],
            ["phase", "turn", "session"],
        )

    def test_add_appends_sink(self) -> None:
        composite = CompositeProgressSink()
        self.assertEqual(len(composite), 0)
        composite.add(_RecordingSink())
        composite.add(_RecordingSink())
        self.assertEqual(len(composite), 2)

    def test_sink_without_method_is_skipped(self) -> None:
        """A sink that implements only some methods must not break the fan-out."""

        class _PartialSink:
            task_id = "partial"

            def on_phase_complete(self, event, session):
                return None

            # no on_turn_complete / on_session_complete

        composite = CompositeProgressSink([_PartialSink(), _RecordingSink()])
        session = _make_session_obj()
        composite.on_turn_complete(TurnComplete(turn=1), session)
        composite.on_session_complete(SessionComplete(reason="success"), session)
        # The recording sink still received both events.
        self.assertEqual(
            [e[0] for e in composite._sinks[1].events],
            ["turn", "session"],
        )


# ---------------------------------------------------------------------------
# ToolContextProgressSink
# ---------------------------------------------------------------------------


class TestToolContextProgressSink(unittest.TestCase):
    def _make_context(self):
        # Use a real ToolContext — ToolContextProgressSink calls
        # ``_progress_report_call`` and ``_task_update_call`` which
        # require a real ``tasks`` dict.
        from src.tool_system.context import ToolContext

        ctx = ToolContext(workspace_root="/tmp")
        # Pre-register a task so ProgressReport has something to update.
        ctx.tasks["f-40"] = {
            "id": "f-40",
            "metadata": {},
        }
        return ctx

    def test_phase_complete_uses_workflow_phases_for_progress(self) -> None:
        ctx = self._make_context()
        sink = ToolContextProgressSink(
            task_id="f-40",
            context=ctx,
            workflow_phases=["analysis", "design", "impl", "test", "review"],
        )
        session = _make_session_obj()
        sink.on_phase_complete(PhaseComplete(phase=1, turn_count=1), session)
        sink.on_phase_complete(PhaseComplete(phase=2, turn_count=2), session)
        sink.on_phase_complete(PhaseComplete(phase=5, turn_count=5), session)

        stages = ctx.tasks["f-40"]["metadata"]["progress_stages"]
        self.assertEqual([s["stage"] for s in stages],
                         ["analysis", "design", "review"])
        # Honest progress: 1/5, 2/5, 5/5 → 20, 40, 100
        self.assertEqual([s["progress"] for s in stages], [20, 40, 100])

    def test_phase_complete_default_progress_is_none(self) -> None:
        """When ``phases`` is empty, the sink reports ``progress=None``
        instead of the misleading 25/50/75/100 sequence."""
        ctx = self._make_context()
        sink = ToolContextProgressSink(task_id="f-40", context=ctx)
        session = _make_session_obj()
        sink.on_phase_complete(PhaseComplete(phase=1, turn_count=1), session)
        sink.on_phase_complete(PhaseComplete(phase=2, turn_count=2), session)

        stages = ctx.tasks["f-40"]["metadata"]["progress_stages"]
        self.assertEqual([s.get("progress") for s in stages], [None, None])

    def test_fallback_to_phase_step_keeps_old_behavior(self) -> None:
        ctx = self._make_context()
        sink = ToolContextProgressSink(
            task_id="f-40",
            context=ctx,
            fallback_to_phase_step=True,
        )
        session = _make_session_obj()
        for i in range(1, 6):
            sink.on_phase_complete(PhaseComplete(phase=i, turn_count=i), session)

        stages = ctx.tasks["f-40"]["metadata"]["progress_stages"]
        self.assertEqual(
            [s["progress"] for s in stages],
            [25, 50, 75, 100, 100],
        )

    def test_session_complete_success_reports_100(self) -> None:
        ctx = self._make_context()
        sink = ToolContextProgressSink(task_id="f-40", context=ctx)
        session = _make_session_obj()
        sink.on_session_complete(SessionComplete(reason="success"), session)

        stages = ctx.tasks["f-40"]["metadata"]["progress_stages"]
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]["stage"], "session_success")
        self.assertEqual(stages[0]["progress"], 100)

    def test_session_complete_non_success_reports_none(self) -> None:
        """``reason=stagnation`` / ``loop_detected`` / ``noop_completed``
        / ``budget_exhausted`` must NOT fake ``progress=100``."""
        ctx = self._make_context()
        sink = ToolContextProgressSink(task_id="f-40", context=ctx)
        session = _make_session_obj()
        for reason in ("stagnation", "loop_detected", "noop_completed",
                       "budget_exhausted", "rate_limit_circuit_open"):
            sink.on_session_complete(SessionComplete(reason=reason), session)

        stages = ctx.tasks["f-40"]["metadata"]["progress_stages"]
        self.assertEqual(
            [s["stage"] for s in stages],
            [
                "session_stagnation",
                "session_loop_detected",
                "session_noop_completed",
                "session_budget_exhausted",
                "session_rate_limit_circuit_open",
            ],
        )
        for s in stages:
            # ``progress_report._progress_report_call`` only writes the
            # ``progress`` key when the value is not None. F-40's
            # contract is "no fake success percentage for non-success
            # terminations" — both an explicit ``None`` and a missing
            # key satisfy that contract.
            self.assertIsNone(
                s.get("progress"),
                f"reason={s['stage']} must not report a fake progress",
            )

    def test_empty_task_id_no_ops(self) -> None:
        ctx = self._make_context()
        sink = ToolContextProgressSink(task_id="", context=ctx)
        session = _make_session_obj()
        sink.on_phase_complete(PhaseComplete(phase=1, turn_count=1), session)
        sink.on_session_complete(SessionComplete(reason="success"), session)
        # No rows should have been written.
        self.assertNotIn("progress_stages", ctx.tasks["f-40"]["metadata"])

    def test_turn_complete_does_not_pollute_tasks(self) -> None:
        ctx = self._make_context()
        sink = ToolContextProgressSink(task_id="f-40", context=ctx)
        sink.on_turn_complete(TurnComplete(turn=1), _make_session_obj())
        # No writes to ToolContext — only debug log.
        self.assertNotIn("progress_stages", ctx.tasks["f-40"]["metadata"])


# ---------------------------------------------------------------------------
# ProgressReporter shim (back-compat)
# ---------------------------------------------------------------------------


class TestProgressReporterShim(unittest.TestCase):
    def _make_context(self):
        from src.tool_system.context import ToolContext

        ctx = ToolContext(workspace_root="/tmp")
        ctx.tasks["f-40"] = {"id": "f-40", "metadata": {}}
        return ctx

    def test_legacy_set_task_id_then_on_event(self) -> None:
        """Old call shape: set_task_id → on_event(event, session)."""
        ctx = self._make_context()
        reporter = ProgressReporter(ctx)
        session = _make_session_obj()
        reporter.set_task_id("f-40")
        reporter.on_event(PhaseComplete(phase=1, turn_count=1), session)
        reporter.on_event(SessionComplete(reason="success"), session)

        stages = ctx.tasks["f-40"]["metadata"]["progress_stages"]
        self.assertEqual([s["stage"] for s in stages],
                         ["phase_1", "session_success"])

    def test_legacy_on_event_without_set_task_id_is_noop(self) -> None:
        ctx = self._make_context()
        reporter = ProgressReporter(ctx)
        session = _make_session_obj()
        # No set_task_id → sink is None → on_event is a silent no-op.
        reporter.on_event(PhaseComplete(phase=1, turn_count=1), session)
        self.assertNotIn("progress_stages", ctx.tasks["f-40"]["metadata"])

    def test_set_task_id_resets_state(self) -> None:
        """Switching to a new task id must NOT carry counters over."""
        ctx = self._make_context()
        ctx.tasks["other"] = {"id": "other", "metadata": {}}
        reporter = ProgressReporter(ctx)
        session = _make_session_obj()
        reporter.set_task_id("f-40")
        reporter.on_event(PhaseComplete(phase=1, turn_count=1), session)
        reporter.set_task_id("other")
        reporter.on_event(PhaseComplete(phase=1, turn_count=1), session)

        f40_stages = ctx.tasks["f-40"]["metadata"]["progress_stages"]
        other_stages = ctx.tasks["other"]["metadata"]["progress_stages"]
        self.assertEqual([s["stage"] for s in f40_stages], ["phase_1"])
        self.assertEqual([s["stage"] for s in other_stages], ["phase_1"])

    def test_satisfies_progress_sink_protocol(self) -> None:
        """The shim must satisfy the new ``ProgressSink`` protocol so
        it can be passed as ``progress_sink`` to ``AgentRunner.run``."""
        ctx = self._make_context()
        reporter: Any = ProgressReporter(ctx)
        self.assertIsInstance(reporter, ProgressSink)
        # The protocol requires ``task_id``; ensure it is the bound id
        # after ``set_task_id``.
        reporter.set_task_id("f-40")
        self.assertEqual(reporter.task_id, "f-40")


# ---------------------------------------------------------------------------
# WorkflowConfig.phases parsing
# ---------------------------------------------------------------------------


class TestWorkflowConfigPhases(unittest.TestCase):
    def test_phases_default_empty(self) -> None:
        cfg = WorkflowConfig.from_dict({})
        self.assertEqual(cfg.agent.phases, [])
        self.assertFalse(cfg.agent.fallback_to_phase_step)

    def test_phases_parsed_from_dict(self) -> None:
        cfg = WorkflowConfig.from_dict(
            {
                "agent": {
                    "phases": ["analysis", "design", "impl", "test", "review"],
                    "fallback_to_phase_step": True,
                }
            }
        )
        self.assertEqual(
            cfg.agent.phases,
            ["analysis", "design", "impl", "test", "review"],
        )
        self.assertTrue(cfg.agent.fallback_to_phase_step)

    def test_phases_normalized_to_strings(self) -> None:
        cfg = WorkflowConfig.from_dict({"agent": {"phases": ["  a  ", "", "b"]}})
        # Empty strings are dropped, whitespace is trimmed.
        self.assertEqual(cfg.agent.phases, ["a", "b"])


# ---------------------------------------------------------------------------
# Concurrency: per-session isolation (F-40 acceptance criterion #1)
# ---------------------------------------------------------------------------


class TestPerSessionIsolation(unittest.TestCase):
    """Two concurrent sessions must not cross-talk.

    F-40 acceptance criterion #1: each session's
    ``ToolContext.tasks[id].metadata.progress_stages`` must contain
    only the events from that session. The orchestrator builds a
    fresh per-session :class:`CompositeProgressSink` rooted in a
    fresh :class:`ToolContextProgressSink` so the phase counter and
    task-id binding are private to the session.
    """

    def _make_context(self) -> Any:
        from src.tool_system.context import ToolContext

        ctx = ToolContext(workspace_root="/tmp")
        ctx.tasks["alpha"] = {"id": "alpha", "metadata": {}}
        ctx.tasks["beta"] = {"id": "beta", "metadata": {}}
        return ctx

    def test_two_sinks_share_context_but_isolated_state(self) -> None:
        """Two :class:`ToolContextProgressSink` instances share the
        underlying ``ToolContext`` (cheap, read-only metadata) but
        each owns its own phase counter."""
        ctx = self._make_context()
        alpha = ToolContextProgressSink(task_id="alpha", context=ctx)
        beta = ToolContextProgressSink(task_id="beta", context=ctx)
        session = _make_session_obj()

        # Drive alpha with 3 phases, beta with 1 phase.
        for i in range(1, 4):
            alpha.on_phase_complete(PhaseComplete(phase=i, turn_count=i), session)
        beta.on_phase_complete(PhaseComplete(phase=1, turn_count=1), session)

        # Each task's progress_stages only contain its own events.
        alpha_stages = ctx.tasks["alpha"]["metadata"]["progress_stages"]
        beta_stages = ctx.tasks["beta"]["metadata"]["progress_stages"]
        self.assertEqual(len(alpha_stages), 3)
        self.assertEqual(len(beta_stages), 1)
        # The stage names use the 1-based index from the per-sink
        # counter (alpha used indices 1, 2, 3; beta used index 1).
        self.assertEqual([s["stage"] for s in alpha_stages], ["phase_1", "phase_2", "phase_3"])
        self.assertEqual([s["stage"] for s in beta_stages], ["phase_1"])

    def test_orchestrator_builds_per_session_composite(self) -> None:
        """``Orchestrator._build_session_sink`` must return a fresh
        sink per call so the orchestrator can wire up isolated
        per-session fan-out. The composite wraps a private
        :class:`ToolContextProgressSink`."""
        from extensions.orchestrator.orchestrator import Orchestrator
        from src.tool_system.context import ToolContext

        with tempfile.TemporaryDirectory() as tmp:
            workflow = WorkflowConfig.from_dict(
                {
                    "tracker": {
                        "kind": "local",
                        "issues_path": str(Path(tmp) / "issues"),
                    },
                    "agent": {"max_concurrent_agents": 1},
                    "workspace": {"strategy": "sequential"},
                }
            )
            # Build a minimal orchestrator just to exercise
            # ``_build_session_sink``. The orchestrator constructor
            # only uses ``self.workflow`` and ``self._progress_context``
            # in the helper, so we can wire those by hand.
            orchestrator = Orchestrator.__new__(Orchestrator)
            orchestrator.workflow = workflow
            orchestrator._progress_context = ToolContext(workspace_root=tmp)
            orchestrator._progress_context.tasks["a"] = {"id": "a", "metadata": {}}
            orchestrator._progress_context.tasks["b"] = {"id": "b", "metadata": {}}

            sink_a = orchestrator._build_session_sink("a")
            sink_b = orchestrator._build_session_sink("b")
            self.assertIsInstance(sink_a, CompositeProgressSink)
            self.assertIsInstance(sink_b, CompositeProgressSink)
            # Distinct inner sinks → state is not shared.
            self.assertIsNot(sink_a, sink_b)

            session = _make_session_obj()
            sink_a.on_phase_complete(PhaseComplete(phase=1, turn_count=1), session)
            sink_a.on_phase_complete(PhaseComplete(phase=2, turn_count=2), session)
            sink_b.on_phase_complete(PhaseComplete(phase=1, turn_count=1), session)

            stages_a = orchestrator._progress_context.tasks["a"]["metadata"]["progress_stages"]
            stages_b = orchestrator._progress_context.tasks["b"]["metadata"]["progress_stages"]
            self.assertEqual(len(stages_a), 2)
            self.assertEqual(len(stages_b), 1)


if __name__ == "__main__":
    unittest.main()
