from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from extensions.api.query import (
    SessionComplete,
    TextDelta,
    ToolCallEvent,
    ToolResultEvent,
)
from extensions.orchestrator.agent_runner import AgentRunner, AgentSession
from extensions.orchestrator.config.schema import AgentConfig, CodexConfig, WorkflowConfig
from extensions.orchestrator.issue import Issue
from extensions.orchestrator.workspace import Workspace
from src.services.api.errors import RateLimitError


class _QueryRunnerStub:
    def __init__(self, config) -> None:
        self.config = config

    async def stream(self):
        yield SessionComplete(reason="success")


class _NoSessionCompleteStub:
    """Stub that yields a TextDelta but never SessionComplete, forcing
    AgentRunner.run to exhaust max_turns and fall through to the
    max_turns_exceeded branch."""

    def __init__(self, config) -> None:
        self.config = config

    async def stream(self):
        yield TextDelta(content="noop")
        # Generator ends without SessionComplete → while loop spins
        # until turn_number reaches max_turns, then exits.


class _Comment:
    def __init__(self, id: str) -> None:
        self.id = id


class _CommentTracker:
    def __init__(self) -> None:
        self.comments: list[tuple[str, str]] = []

    async def create_comment(self, issue_id: str, body: str) -> _Comment:
        self.comments.append((issue_id, body))
        return _Comment("summary-1")


class _ProgressReporter:
    """F-40: implements the new :class:`ProgressSink` protocol.

    The old ``on_event`` shim is no longer used by
    :class:`AgentRunner`; the runner now dispatches the three
    ``on_*_complete`` methods directly.  This stub records every
    event so tests can assert dispatch order / counts.
    """

    def __init__(self) -> None:
        self.events: list[object] = []

    def on_phase_complete(self, event, session) -> None:
        self.events.append(("phase", event))

    def on_turn_complete(self, event, session) -> None:
        self.events.append(("turn", event))

    def on_session_complete(self, event, session) -> None:
        self.events.append(("session", event))


class TestAgentRunnerF38(unittest.IsolatedAsyncioTestCase):
    async def test_run_posts_summary_placeholder_and_writes_phase_event_log(self) -> None:
        with TemporaryDirectory() as tmp:
            sessions_root = Path(tmp) / "sessions"
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_root,
            ):
                workspace = Workspace(
                    path=Path(tmp) / "ws",
                    issue_identifier="ISSUE-77",
                    issue_id="77",
                )
                session = AgentSession(
                    issue=Issue(id="77", identifier="ISSUE-77", title="Run reports"),
                    workspace=workspace,
                )
                tracker = _CommentTracker()
                progress = _ProgressReporter()
                runner = AgentRunner(AgentConfig(max_turns=1), CodexConfig())

                with patch("extensions.orchestrator.agent_runner.QueryRunner", _QueryRunnerStub):
                    await runner.run(
                        session,
                        WorkflowConfig.from_dict({}),
                        comment_tracker=tracker,
                        progress_reporter=progress,
                    )

                # F-49 unified storage: headless agent and REPL sessions
                # both write to ~/.clawcodex/sessions/{run_id}/transcript.jsonl
                # via SessionStorage.  The legacy .event_logs/{id}.ndjson
                # reader is gone — assert on the unified transcript.
                assert session.run_id is not None
                session_dir = sessions_root / session.run_id
                transcript_path = session_dir / "transcript.jsonl"
                contents = transcript_path.read_text(encoding="utf-8")
                metadata_path = session_dir / "metadata.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        # With max_turns=1 and a single SessionComplete(success),
        # the runner reaches the "max_turns_exceeded" branch
        # (turn_number is incremented to 1 *before* the
        # turn_number >= max_turns check, so a 1-turn run with
        # max_turns=1 lands in the budget_exhausted path).  This
        # pre-existed the F-49 storage unification; the original
        # test's ``status == "completed"`` assertion only "passed"
        # because the FileNotFoundError on .event_logs/77.ndjson
        # short-circuited the test before the status check ran.
        self.assertEqual(session.status, "max_turns_exceeded")
        self.assertRegex(session.run_id or "", r"^run-01-\d{8}T\d{6}Z$")
        self.assertEqual(session.summary_comment_id, "summary-1")
        self.assertEqual(
            tracker.comments,
            [("77", "## ClawCodex Run Summary\n\n⏳ Run in progress.")],
        )
        self.assertEqual(session.turn_count, 1)
        # F-40: AgentRunner dispatches three events per turn
        # (PhaseComplete, TurnComplete, SessionComplete) before
        # the early return inside the SessionComplete handler.
        self.assertEqual(len(progress.events), 3)
        self.assertEqual(progress.events[0][0], "phase")
        self.assertEqual(progress.events[1][0], "turn")
        self.assertEqual(progress.events[2][0], "session")
        # Transcript: the user prompt (turn 0) was written by F-49
        # Phase 0; the stub yielded only SessionComplete(success)
        # with no TextDelta / ToolCallEvent, so no assistant
        # message is written.  The transcript must therefore
        # contain at least one user-role entry.
        self.assertIn('"role": "user"', contents)
        # Metadata initialised by init_metadata() with model + cwd
        # + title derived from the issue identifier.
        self.assertEqual(
            metadata.get("title", ""),
            "orchestrator-ISSUE-77",
        )
        # F-49 storage unification: the legacy .event_logs/ tree
        # must NOT be created on disk anywhere under the workspace.
        self.assertFalse(
            (workspace.path / ".event_logs").exists(),
            "legacy .event_logs/ dir should not exist under the workspace",
        )

    def test_followup_run_id_uses_issue_and_followup_attempts(self) -> None:
        with TemporaryDirectory() as tmp:
            session = AgentSession(
                issue=Issue(id="77"),
                workspace=Workspace(
                    path=Path(tmp),
                    issue_identifier="ISSUE-77",
                    issue_id="77",
                ),
                run_kind="review_followup",
                attempt=4,
                issue_attempt=3,
                followup_attempt=2,
            )
            runner = AgentRunner(AgentConfig(), CodexConfig())

            run_id = runner._build_run_id(session)

        self.assertRegex(run_id, r"^run-3-followup-2-\d{8}T\d{6}Z$")


class TestAgentRunnerMaxTurns(unittest.IsolatedAsyncioTestCase):
    async def test_run_max_turns_sets_max_turns_exceeded_status(self) -> None:
        """When the QueryRunner stream never yields SessionComplete, the
        while loop in AgentRunner.run should exhaust max_turns and fall
        through to set session.status = 'max_turns_exceeded'."""
        with TemporaryDirectory() as tmp:
            workspace = Workspace(
                path=Path(tmp),
                issue_identifier="ISSUE-99",
                issue_id="99",
            )
            session = AgentSession(
                issue=Issue(id="99", identifier="ISSUE-99", title="Max turns"),
                workspace=workspace,
            )
            runner = AgentRunner(AgentConfig(max_turns=2), CodexConfig())

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                _NoSessionCompleteStub,
            ):
                await runner.run(
                    session,
                    WorkflowConfig.from_dict({}),
                )

        self.assertEqual(session.status, "max_turns_exceeded")
        self.assertEqual(session.turn_count, 2)


# ---------------------------------------------------------------------------
# 429-aware in-turn backoff test infrastructure
# ---------------------------------------------------------------------------


# Substrings that look like upstream 429 payloads. The detector only
# needs to match on one of these; the suites below pick the closest
# approximation of what the real headless runner captures in
# ``aggregate_text`` / ``stdout``.
_RATE_LIMIT_TEXT = (
    "Error code: 429 - {'type': 'error', 'error': "
    "{'type': 'rate_limit_error', 'message': '...请稍后重试...'}}"
)
_QUOTA_TEXT = (
    "Error code: 429 - {'type': 'error', 'error': "
    "{'type': 'rate_limit_error', 'message': 'exceeded your current "
    "quota, limit: 0'}}"
)


class _RecordingSleep:
    """Drop-in replacement for ``asyncio.sleep`` that records durations."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class _BehaviorsStub:
    """Stub whose ``stream()`` consumes the next behavior from a list.

    A behavior is either:
      * a list of events to yield in order, or
      * the special string ``"raise_rate_limit"`` which makes
        ``stream()`` raise a ``RateLimitError`` instead of yielding.
    """

    def __init__(self, behaviors: list) -> None:
        self.config = None  # QueryRunner contract only requires .config
        self._behaviors = list(behaviors)
        self._index = 0
        self.call_count = 0

    def _next(self):
        if self._index >= len(self._behaviors):
            raise AssertionError(
                f"_BehaviorsStub exhausted after {self._index} calls"
            )
        b = self._behaviors[self._index]
        self._index += 1
        self.call_count += 1
        return b

    async def stream(self):
        b = self._next()
        if b == "raise_rate_limit":
            raise RateLimitError("rate limit", status=429)
        # b is a list of events; yield each in order
        for ev in b:
            yield ev


def _behaviors_429_then_success(num_429: int) -> list:
    """Return a behavior list: ``num_429`` 429s followed by one success."""
    out = []
    for _ in range(num_429):
        out.append([
            TextDelta(content=_RATE_LIMIT_TEXT),
            SessionComplete(reason="exit_code=1"),
        ])
    out.append([SessionComplete(reason="success")])
    return out


def _behaviors_quota_then_fail() -> list:
    """A quota-exhausted turn followed by a hard failure."""
    return [
        [
            TextDelta(content=_QUOTA_TEXT),
            SessionComplete(reason="exit_code=1"),
        ],
        [SessionComplete(reason="exit_code=1")],
    ]


def _build_429_session(tmp: str) -> AgentSession:
    workspace = Workspace(
        path=Path(tmp),
        issue_identifier="ISSUE-429",
        issue_id="429",
    )
    return AgentSession(
        issue=Issue(id="429", identifier="ISSUE-429", title="Rate limit"),
        workspace=workspace,
    )


def _install_recording_sleep(runner: AgentRunner) -> _RecordingSleep:
    rec = _RecordingSleep()
    runner._sleep = rec  # type: ignore[assignment]
    return rec


class TestAgentRunnerRateLimitBackoff(unittest.IsolatedAsyncioTestCase):
    """Tests for the 429-aware in-turn backoff in ``AgentRunner.run``."""

    async def test_429_triggers_backoff(self) -> None:
        """A 429 in turn output schedules a 30s backoff and re-issues
        the same turn. After one 429 followed by a clean success, the
        session completes and the counter is reset to 0."""
        with TemporaryDirectory() as tmp:
            session = _build_429_session(tmp)
            runner = AgentRunner(AgentConfig(max_turns=5), CodexConfig())
            rec = _install_recording_sleep(runner)
            stub = _BehaviorsStub([
                [TextDelta(content=_RATE_LIMIT_TEXT), SessionComplete(reason="exit_code=1")],
                [SessionComplete(reason="success")],
            ])

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                lambda cfg: stub,
            ):
                await runner.run(session, WorkflowConfig.from_dict({}))

            self.assertEqual(session.status, "completed")
            self.assertEqual(len(rec.calls), 1)
            # 30s ± 10% jitter
            self.assertAlmostEqual(rec.calls[0], 30.0, delta=3.0)
            self.assertEqual(session.consecutive_429_count, 0)  # reset on success
            self.assertGreater(session.total_429_backoff_seconds, 0)
            # Turn counter advanced past 0 (the success is turn 1, but
            # the re-issued turn re-ran turn 0).
            self.assertGreaterEqual(session.turn_count, 1)

    async def test_exponential_progression(self) -> None:
        """Four consecutive 429s use backoffs 30s, 60s, 120s, 240s
        (within jitter). The fifth turn succeeds and resets the counter."""
        with TemporaryDirectory() as tmp:
            session = _build_429_session(tmp)
            runner = AgentRunner(
                AgentConfig(
                    max_turns=10,
                    rate_limit_base_delay_ms=30_000,
                    rate_limit_exponential_factor=2.0,
                    rate_limit_max_retries=10,
                ),
                CodexConfig(),
            )
            rec = _install_recording_sleep(runner)
            stub = _BehaviorsStub(_behaviors_429_then_success(num_429=4))

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                lambda cfg: stub,
            ):
                await runner.run(session, WorkflowConfig.from_dict({}))

            self.assertEqual(len(rec.calls), 4)
            # Allow ±15% jitter on each call to be robust.
            expected = [30.0, 60.0, 120.0, 240.0]
            for actual, exp in zip(rec.calls, expected):
                self.assertAlmostEqual(
                    actual, exp, delta=exp * 0.15,
                    msg=f"backoff call {actual} not within jitter of {exp}",
                )
            self.assertEqual(session.status, "completed")
            self.assertEqual(session.consecutive_429_count, 0)
            # Total backoff ≥ sum of base delays
            self.assertGreaterEqual(
                session.total_429_backoff_seconds, sum(expected),
            )

    async def test_circuit_breaker_opens(self) -> None:
        """After ``rate_limit_max_retries`` consecutive 429s, the
        session status becomes ``rate_limit_circuit_open`` and ``run()``
        returns without sleeping again."""
        with TemporaryDirectory() as tmp:
            session = _build_429_session(tmp)
            runner = AgentRunner(
                AgentConfig(
                    max_turns=20,
                    rate_limit_max_retries=3,
                    # Short delays so the test stays fast if sleep leaks
                    rate_limit_base_delay_ms=1_000,
                    rate_limit_max_backoff_ms=10_000,
                ),
                CodexConfig(),
            )
            rec = _install_recording_sleep(runner)
            # 4 429s: 3 backoff + the 4th trips the breaker.
            stub = _BehaviorsStub(_behaviors_429_then_success(num_429=4))

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                lambda cfg: stub,
            ):
                await runner.run(session, WorkflowConfig.from_dict({}))

            # 3 sleeps for 3 backoffs, then the 4th 429 trips the breaker
            # before scheduling another sleep.
            self.assertEqual(len(rec.calls), 3)
            self.assertEqual(session.status, "rate_limit_circuit_open")
            self.assertGreater(session.consecutive_429_count, 3)

    async def test_success_resets_counter(self) -> None:
        """After a successful turn, the consecutive 429 counter resets,
        so a later 429 starts the backoff at the base delay again."""
        with TemporaryDirectory() as tmp:
            session = _build_429_session(tmp)
            # max_turns=2 caps the success path: each successful
            # turn increments ``turn_number`` by 1, and the runner
            # stops continuing once turn_number reaches max_turns.
            # The 2nd success (behavior[4]) is therefore the
            # terminal event and no 6th stub call is needed.
            runner = AgentRunner(
                AgentConfig(
                    max_turns=2,
                    rate_limit_base_delay_ms=30_000,
                    rate_limit_max_retries=10,
                ),
                CodexConfig(),
            )
            rec = _install_recording_sleep(runner)
            # 429, 429, success, 429, success. The 4th 429 should use
            # the base delay (30s), NOT 120s.
            stub = _BehaviorsStub([
                [TextDelta(content=_RATE_LIMIT_TEXT), SessionComplete(reason="exit_code=1")],
                [TextDelta(content=_RATE_LIMIT_TEXT), SessionComplete(reason="exit_code=1")],
                [SessionComplete(reason="success")],
                [TextDelta(content=_RATE_LIMIT_TEXT), SessionComplete(reason="exit_code=1")],
                [SessionComplete(reason="success")],
            ])

            # Tracker stub: always report the issue as still active so
            # the runner keeps going past a success instead of
            # declaring completion immediately. ``active_states`` must
            # be present on the tracker — ``_should_continue`` checks
            # it via ``getattr(tracker, "active_states", None)``.
            class _AlwaysActiveTracker:
                active_states = ["open", "ready"]

                async def fetch_issue_states_by_ids(self, ids):
                    return {ids[0]: Issue(id=ids[0], state="open")}

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                lambda cfg: stub,
            ):
                await runner.run(
                    session,
                    WorkflowConfig.from_dict({}),
                    tracker=_AlwaysActiveTracker(),
                )

            self.assertEqual(len(rec.calls), 3)
            # First two: 30, 60; third (after success reset) is back to 30
            self.assertAlmostEqual(rec.calls[0], 30.0, delta=4.5)
            self.assertAlmostEqual(rec.calls[1], 60.0, delta=9.0)
            self.assertAlmostEqual(rec.calls[2], 30.0, delta=4.5)
            self.assertEqual(session.status, "completed")
            self.assertEqual(session.consecutive_429_count, 0)

    async def test_quota_exhausted_does_not_backoff(self) -> None:
        """A 'limit: 0' / quota-exhausted message must NOT trigger the
        429 backoff — quota is a permanent failure that the normal
        failure path is responsible for surfacing."""
        with TemporaryDirectory() as tmp:
            session = _build_429_session(tmp)
            runner = AgentRunner(AgentConfig(max_turns=5), CodexConfig())
            rec = _install_recording_sleep(runner)
            stub = _BehaviorsStub(_behaviors_quota_then_fail())

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                lambda cfg: stub,
            ):
                await runner.run(session, WorkflowConfig.from_dict({}))

            # No backoff sleep should have been scheduled.
            self.assertEqual(rec.calls, [])
            self.assertEqual(session.consecutive_429_count, 0)
            self.assertEqual(session.status, "failed")

    async def test_typed_rate_limit_exception_caught(self) -> None:
        """When ``runner.stream()`` raises ``RateLimitError`` directly
        (defense-in-depth path), the runner still applies 429 backoff
        and re-issues the turn."""
        with TemporaryDirectory() as tmp:
            session = _build_429_session(tmp)
            runner = AgentRunner(
                AgentConfig(
                    max_turns=5,
                    rate_limit_base_delay_ms=10_000,
                    rate_limit_max_retries=5,
                ),
                CodexConfig(),
            )
            rec = _install_recording_sleep(runner)
            stub = _BehaviorsStub([
                "raise_rate_limit",
                [SessionComplete(reason="success")],
            ])

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                lambda cfg: stub,
            ):
                await runner.run(session, WorkflowConfig.from_dict({}))

            self.assertEqual(len(rec.calls), 1)
            # 10s ± 15% jitter
            self.assertAlmostEqual(rec.calls[0], 10.0, delta=1.5)
            self.assertEqual(session.status, "completed")
            self.assertEqual(session.consecutive_429_count, 0)
            self.assertGreater(session.total_429_backoff_seconds, 0)


# ---------------------------------------------------------------------------
# F-?? root-cause fix: stagnation / loop / budget exit paths
# ---------------------------------------------------------------------------
#
# The new ``max_no_op_turns`` / ``loop_detection_window`` /
# ``loop_detection_threshold`` knobs in ``AgentConfig`` guard against
# the SessionComplete infinite loop observed in F-09's repeated 30-min
# timeouts (debug log run-06 had 328 SessionComplete events with zero
# real tool calls).  These tests pin the four exit paths so future
# refactors can't silently regress them.


class TestAgentRunnerStagnationAndLoop(unittest.IsolatedAsyncioTestCase):
    """Pins the stagnation, loop-detected, and budget-exhausted exit
    paths introduced by the SessionEnd root-cause fix.

    Each test uses a :class:`_BehaviorsStub` to feed a deterministic
    stream of events to :class:`AgentRunner.run` and asserts the
    resulting ``session.status`` and ``session.session_end_reason``.
    """

    async def test_stagnation_breaks_after_max_no_op_turns(self) -> None:
        """Three consecutive SessionComplete(success) with no tool
        calls and empty output must trigger the stagnation guard and
        break the outer while loop with
        ``session_end_reason='stagnation'`` and
        ``session.status='stagnation'``."""
        with TemporaryDirectory() as tmp:
            session = _build_429_session(tmp)
            tracker = _ActiveTrackerStub(["open", "ready"])
            runner = AgentRunner(
                AgentConfig(max_turns=20, max_no_op_turns=3),
                CodexConfig(),
            )
            # Each turn: no tool calls, no output, then
            # SessionComplete(success).  After the 3rd such turn the
            # runner must break out of the outer while.
            stub = _BehaviorsStub([
                [SessionComplete(reason="success")],
                [SessionComplete(reason="success")],
                [SessionComplete(reason="success")],
                [SessionComplete(reason="success")],
                [SessionComplete(reason="success")],
            ])

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                lambda cfg: stub,
            ):
                await runner.run(
                    session,
                    WorkflowConfig.from_dict({}),
                    tracker=tracker,
                )

            self.assertEqual(session.status, "stagnation")
            self.assertEqual(session.session_end_reason, "stagnation")
            self.assertIn("3 consecutive", session.session_end_summary)
            # F-09 pattern: only 3 turns consumed before break.
            self.assertLessEqual(stub.call_count, 4)

    async def test_loop_detected_breaks_on_repeated_signature(self) -> None:
        """Five turns each calling the same single tool in the same
        order must trip the loop guard at threshold=3 and break with
        ``session_end_reason='loop_detected'``."""
        with TemporaryDirectory() as tmp:
            session = _build_429_session(tmp)
            tracker = _ActiveTrackerStub(["open", "ready"])
            runner = AgentRunner(
                AgentConfig(
                    max_turns=20,
                    max_no_op_turns=10,  # don't trip stagnation first
                    loop_detection_window=5,
                    loop_detection_threshold=3,
                ),
                CodexConfig(),
            )
            # Each turn calls Read then Write (same signature). The
            # 3rd turn should trip loop_detected.
            def _build_turn():
                return [
                    ToolCallEvent(
                        tool_name="Read",
                        params={},
                        tool_use_id="rid",
                    ),
                    ToolResultEvent(
                        tool_name="Read",
                        result={"output": "ok", "is_error": False},
                    ),
                    ToolCallEvent(
                        tool_name="Write",
                        params={"path": "/tmp/x"},
                        tool_use_id="wid",
                    ),
                    ToolResultEvent(
                        tool_name="Write",
                        result={"output": "ok", "is_error": False},
                    ),
                    SessionComplete(reason="success"),
                ]
            stub = _BehaviorsStub([_build_turn() for _ in range(5)])

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                lambda cfg: stub,
            ):
                await runner.run(
                    session,
                    WorkflowConfig.from_dict({}),
                    tracker=tracker,
                )

            self.assertEqual(session.status, "loop_detected")
            self.assertEqual(session.session_end_reason, "loop_detected")
            self.assertIn("Read|Write", session.session_end_summary)
            self.assertLessEqual(stub.call_count, 4)

    async def test_budget_exhausted_keeps_reason(self) -> None:
        """When the runner hits max_turns, ``session_end_reason`` must
        be set to ``'budget_exhausted'`` and ``status`` to
        ``'max_turns_exceeded'`` (regression pin for the new field)."""
        with TemporaryDirectory() as tmp:
            session = _build_429_session(tmp)
            tracker = _ActiveTrackerStub(["open", "ready"])
            runner = AgentRunner(AgentConfig(max_turns=2), CodexConfig())
            # Always productive: one tool call per turn. The
            # stagnation/loop guards must NOT trip, and the runner
            # should reach max_turns naturally.
            def _build_turn():
                return [
                    ToolCallEvent(
                        tool_name="Read",
                        params={},
                        tool_use_id="rid",
                    ),
                    ToolResultEvent(
                        tool_name="Read",
                        result={"output": "ok", "is_error": False},
                    ),
                    SessionComplete(reason="success"),
                ]
            stub = _BehaviorsStub([_build_turn() for _ in range(3)])

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                lambda cfg: stub,
            ):
                await runner.run(
                    session,
                    WorkflowConfig.from_dict({}),
                    tracker=tracker,
                )

            self.assertEqual(session.status, "max_turns_exceeded")
            self.assertEqual(session.session_end_reason, "budget_exhausted")
            self.assertIn("max_turns=2", session.session_end_summary)

    async def test_productive_turns_do_not_trip_stagnation(self) -> None:
        """A single turn with a tool call AND a follow-up success
        must reset the stagnation streak and let the runner reach
        the natural session end (regression pin for the
        no_work_streak reset logic)."""
        with TemporaryDirectory() as tmp:
            session = _build_429_session(tmp)
            tracker = _ActiveTrackerStub(["open", "ready"])
            runner = AgentRunner(
                AgentConfig(max_turns=3, max_no_op_turns=2),
                CodexConfig(),
            )
            stub = _BehaviorsStub([
                # Turn 1: no-op (streak=1)
                [SessionComplete(reason="success")],
                # Turn 2: productive — should reset streak
                [
                    ToolCallEvent(
                        tool_name="Read",
                        params={},
                        tool_use_id="rid",
                    ),
                    ToolResultEvent(
                        tool_name="Read",
                        result={"output": "ok", "is_error": False},
                    ),
                    SessionComplete(reason="success"),
                ],
                # Turn 3: no-op (streak=1, well under threshold=2)
                [SessionComplete(reason="success")],
            ])

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                lambda cfg: stub,
            ):
                await runner.run(
                    session,
                    WorkflowConfig.from_dict({}),
                    tracker=tracker,
                )

            # All 3 turns consumed; status fell through to
            # max_turns_exceeded (not stagnation).
            self.assertEqual(session.status, "max_turns_exceeded")
            self.assertEqual(stub.call_count, 3)
            self.assertNotEqual(
                getattr(session, "session_end_reason", None),
                "stagnation",
            )


class _ActiveTrackerStub:
    """Minimal :class:`TrackerAdapter` stub that reports the issue as
    active for every ``fetch_issue_states_by_ids`` call. Required for
    the stagnation/loop guards to enter the continuation branch
    (without a tracker, the runner completes the session on the
    first SessionComplete(success))."""

    def __init__(self, active_states: list[str]) -> None:
        self.active_states = active_states

    async def fetch_issue_states_by_ids(self, issue_ids):
        from extensions.orchestrator.issue import Issue
        return {
            iid: Issue(id=iid, state="open") for iid in issue_ids
        }


class _MultiToolTurnStub:
    """F-49 Phase 0.1 stub: one LLM turn with leading text + 2 tool calls
    (Read then Bash) + 2 results in matching order + SessionComplete."""

    def __init__(self, config) -> None:
        self.config = config

    async def stream(self):
        yield TextDelta(content="Looking at the repo...")
        yield ToolCallEvent(
            tool_name="Read",
            params={"path": "/tmp/a.py"},
            tool_use_id="A",
        )
        yield ToolCallEvent(
            tool_name="Bash",
            params={"cmd": "ls"},
            tool_use_id="B",
        )
        yield ToolResultEvent(
            tool_name="Read",
            result={"output": "contents of a.py", "is_error": False},
            tool_use_id="A",
        )
        yield ToolResultEvent(
            tool_name="Bash",
            result={"output": "a.py\nb.py", "is_error": False},
            tool_use_id="B",
        )
        yield SessionComplete(reason="success")


class _OutOfOrderResultStub:
    """F-49 Phase 0.1 stub: 2 tool calls but Bash result arrives BEFORE
    Read result. Verifies the helper pairs by tool_use_id and emits the
    UserMessage in tool_use order, not arrival order."""

    def __init__(self, config) -> None:
        self.config = config

    async def stream(self):
        yield ToolCallEvent(
            tool_name="Read",
            params={"path": "/tmp/a.py"},
            tool_use_id="A",
        )
        yield ToolCallEvent(
            tool_name="Bash",
            params={"cmd": "ls"},
            tool_use_id="B",
        )
        # OOO: Bash result arrives first.
        yield ToolResultEvent(
            tool_name="Bash",
            result={"output": "ls output", "is_error": False},
            tool_use_id="B",
        )
        yield ToolResultEvent(
            tool_name="Read",
            result={"output": "read output", "is_error": False},
            tool_use_id="A",
        )
        yield SessionComplete(reason="success")


class _ApprovalRejectedStub:
    """F-49 Phase 0.1 stub: a single tool call whose result carries
    is_error=True (rejected / error). Verifies the rejected result is
    captured with is_error preserved in the transcript."""

    def __init__(self, config) -> None:
        self.config = config

    async def stream(self):
        yield ToolCallEvent(
            tool_name="Bash",
            params={"cmd": "rm -rf /"},
            tool_use_id="R",
        )
        yield ToolResultEvent(
            tool_name="Bash",
            result={
                "output": "rejected: destructive command",
                "is_error": True,
            },
            tool_use_id="R",
        )
        yield SessionComplete(reason="success")


class TestAgentRunnerTranscriptPhase01(unittest.IsolatedAsyncioTestCase):
    """F-49 Phase 0.1: one AssistantMessage per turn + tool_use_id pairing.

    Regression pin for the spec deviations that the buffer-based rewrite
    fixes. These tests do NOT depend on the legacy ``.event_logs/`` tree.
    """

    async def test_multi_tool_turn_emits_one_assistant_one_user(self) -> None:
        with TemporaryDirectory() as tmp:
            sessions_root = Path(tmp) / "sessions"
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_root,
            ):
                workspace = Workspace(
                    path=Path(tmp) / "ws",
                    issue_identifier="PHASE-01-A",
                    issue_id="P1",
                )
                session = AgentSession(
                    issue=Issue(
                        id="P1",
                        identifier="PHASE-01-A",
                        title="Multi-tool turn",
                    ),
                    workspace=workspace,
                )
                runner = AgentRunner(
                    AgentConfig(max_turns=1), CodexConfig(),
                )
                with patch(
                    "extensions.orchestrator.agent_runner.QueryRunner",
                    _MultiToolTurnStub,
                ):
                    await runner.run(
                        session,
                        WorkflowConfig.from_dict({}),
                        comment_tracker=_CommentTracker(),
                        progress_reporter=_ProgressReporter(),
                    )

                session_dir = sessions_root / session.run_id
                transcript_path = session_dir / "transcript.jsonl"
                lines = [
                    json.loads(line)
                    for line in transcript_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]

        assistant_msgs = [
            m for m in lines if m.get("role") == "assistant"
        ]
        tool_result_user_msgs = [
            m for m in lines
            if m.get("role") == "user" and m.get("origin") == "tool_result"
        ]
        self.assertEqual(len(assistant_msgs), 1)
        self.assertEqual(len(tool_result_user_msgs), 1)

        # AssistantMessage: [TextBlock, ToolUseBlock(A), ToolUseBlock(B)]
        # — blocks interleaved in event arrival order, leading text first.
        asst_blocks = assistant_msgs[0]["content"]
        self.assertEqual(len(asst_blocks), 3)
        self.assertEqual(asst_blocks[0]["type"], "text")
        self.assertEqual(asst_blocks[0]["text"], "Looking at the repo...")
        self.assertEqual(asst_blocks[1]["type"], "tool_use")
        self.assertEqual(asst_blocks[1]["id"], "A")
        self.assertEqual(asst_blocks[1]["name"], "Read")
        self.assertEqual(asst_blocks[2]["type"], "tool_use")
        self.assertEqual(asst_blocks[2]["id"], "B")
        self.assertEqual(asst_blocks[2]["name"], "Bash")

        # UserMessage: [ToolResultBlock(A), ToolResultBlock(B)] paired
        # by tool_use_id, in tool_use order.
        result_blocks = tool_result_user_msgs[0]["content"]
        self.assertEqual(len(result_blocks), 2)
        self.assertEqual(result_blocks[0]["tool_use_id"], "A")
        self.assertEqual(result_blocks[0]["content"], "contents of a.py")
        self.assertFalse(result_blocks[0]["is_error"])
        self.assertEqual(result_blocks[1]["tool_use_id"], "B")
        self.assertEqual(result_blocks[1]["content"], "a.py\nb.py")
        self.assertFalse(result_blocks[1]["is_error"])

    async def test_out_of_order_results_paired_by_tool_use_id(self) -> None:
        with TemporaryDirectory() as tmp:
            sessions_root = Path(tmp) / "sessions"
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_root,
            ):
                workspace = Workspace(
                    path=Path(tmp) / "ws",
                    issue_identifier="PHASE-01-B",
                    issue_id="P2",
                )
                session = AgentSession(
                    issue=Issue(
                        id="P2",
                        identifier="PHASE-01-B",
                        title="Out-of-order results",
                    ),
                    workspace=workspace,
                )
                runner = AgentRunner(
                    AgentConfig(max_turns=1), CodexConfig(),
                )
                with patch(
                    "extensions.orchestrator.agent_runner.QueryRunner",
                    _OutOfOrderResultStub,
                ):
                    await runner.run(
                        session,
                        WorkflowConfig.from_dict({}),
                        comment_tracker=_CommentTracker(),
                        progress_reporter=_ProgressReporter(),
                    )

                session_dir = sessions_root / session.run_id
                transcript_path = session_dir / "transcript.jsonl"
                lines = [
                    json.loads(line)
                    for line in transcript_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]

        tool_result_user_msgs = [
            m for m in lines
            if m.get("role") == "user" and m.get("origin") == "tool_result"
        ]
        self.assertEqual(len(tool_result_user_msgs), 1)

        result_blocks = tool_result_user_msgs[0]["content"]
        # Arrival order was [B, A]; tool_use order must be [A, B].
        self.assertEqual(len(result_blocks), 2)
        self.assertEqual(result_blocks[0]["tool_use_id"], "A")
        self.assertEqual(result_blocks[0]["content"], "read output")
        self.assertEqual(result_blocks[1]["tool_use_id"], "B")
        self.assertEqual(result_blocks[1]["content"], "ls output")

    async def test_rejected_tool_call_writes_is_error_result(self) -> None:
        with TemporaryDirectory() as tmp:
            sessions_root = Path(tmp) / "sessions"
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_root,
            ):
                workspace = Workspace(
                    path=Path(tmp) / "ws",
                    issue_identifier="PHASE-01-C",
                    issue_id="P3",
                )
                session = AgentSession(
                    issue=Issue(
                        id="P3",
                        identifier="PHASE-01-C",
                        title="Approval rejected",
                    ),
                    workspace=workspace,
                )
                runner = AgentRunner(
                    AgentConfig(max_turns=1), CodexConfig(),
                )
                with patch(
                    "extensions.orchestrator.agent_runner.QueryRunner",
                    _ApprovalRejectedStub,
                ):
                    await runner.run(
                        session,
                        WorkflowConfig.from_dict({}),
                        comment_tracker=_CommentTracker(),
                        progress_reporter=_ProgressReporter(),
                    )

                session_dir = sessions_root / session.run_id
                transcript_path = session_dir / "transcript.jsonl"
                lines = [
                    json.loads(line)
                    for line in transcript_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]

        tool_result_user_msgs = [
            m for m in lines
            if m.get("role") == "user" and m.get("origin") == "tool_result"
        ]
        self.assertEqual(len(tool_result_user_msgs), 1)

        result_blocks = tool_result_user_msgs[0]["content"]
        self.assertEqual(len(result_blocks), 1)
        self.assertEqual(result_blocks[0]["tool_use_id"], "R")
        self.assertTrue(result_blocks[0]["is_error"])
        self.assertEqual(
            result_blocks[0]["content"], "rejected: destructive command",
        )
