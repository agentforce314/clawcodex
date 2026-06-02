from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from extensions.api.query import SessionComplete, TextDelta
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
    def __init__(self) -> None:
        self.events: list[object] = []

    def on_event(self, event, session) -> None:
        self.events.append(event)


class TestAgentRunnerF38(unittest.IsolatedAsyncioTestCase):
    async def test_run_posts_summary_placeholder_and_writes_phase_event_log(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Workspace(
                path=Path(tmp),
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

            event_log = workspace.path / ".event_logs" / "77.ndjson"
            contents = event_log.read_text(encoding="utf-8")

        self.assertEqual(session.status, "completed")
        self.assertRegex(session.run_id or "", r"^run-01-\d{8}T\d{6}Z$")
        self.assertEqual(session.summary_comment_id, "summary-1")
        self.assertEqual(
            tracker.comments,
            [("77", "## ClawCodex Run Summary\n\n⏳ Run in progress.")],
        )
        self.assertEqual(session.turn_count, 1)
        self.assertEqual(len(progress.events), 1)
        self.assertIn('"type": "phase_complete"', contents)
        self.assertIn('"phase": 1', contents)

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
