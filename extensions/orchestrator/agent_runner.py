"""Run a single issue through ClawCodex query engine.

Port of Symphony's AgentRunner, replacing Codex JSON-RPC with QueryRunner.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..api.query import PhaseComplete, QueryConfig, QueryRunner
from ..api.query import SessionComplete, TextDelta, ToolCallEvent, ToolResultEvent, TurnComplete
from .approval_policy import ApprovalPolicy, get_approval_policy, ToolCallEvent as PolicyToolCallEvent
from src.utils.git import get_file_status
from .config.schema import AgentConfig, CodexConfig, WorkflowConfig
from .debug_log import append_debug_event
from .issue import Issue
from .prompt_builder import PromptBuilder
from .tool_event_log import ToolEventLog
from .workspace import Workspace

# Reuse the project's typed rate-limit error and helpers so the 429
# detection logic stays in lockstep with the rest of the codebase.
from src.services.api.errors import (
    RateLimitError,
    is_rate_limit_error,
)

if TYPE_CHECKING:
    from .progress_reporter import ProgressReporter

logger = logging.getLogger(__name__)

# If the agent runs this many consecutive turns without making any
# file changes, the runner assumes it is stuck (e.g. the issue
# deliverables already exist in the base branch / workspace) and
# force-completes the session to avoid wasting API calls and retries.
_NOOP_DETECTION_MAX_TURNS = 5

# F-45: tool-event audit log rotation threshold. When events.ndjson
# exceeds this size on next append, rotate to events.ndjson.1 (single
# generation, overwrite). v2.14 will hook a cron for 7-day cleanup.
_TOOL_EVENT_LOG_ROTATE_BYTES = 50 * 1024 * 1024

# F-40 root-cause fix: after this many consecutive turns where the
# agent makes ONLY read-only tool calls (Bash, Read, Grep, …) without
# a single modifying tool call (Write / Edit / …) AND without changing
# the workspace (no new untracked or modified files), the session is
# considered stuck in an investigation spiral and terminated with
# ``session_end_reason="read_only_loop"``.  The threshold is generous
# because genuine development also involves exploration; the guard is
# meant to catch degenerate cases (F-40's 100+ Python-debug Bash calls
# that spanned multiple outer-loop turns without any code change).
_MAX_READ_ONLY_TURNS = 4

# F-40 root-cause fix: tool names that modify workspace files.
# Only Write / Edit tools count toward ``has_made_progress`` so the
# stagnation guard can distinguish "exploring the codebase" turns
# from actual code-production work.  ``Bash`` is intentionally omitted
# because it can be used for both read (ls / grep / cat) and write
# (git add / rm / mv) and trying to classify it at this level would
# require deep output analysis that is better done elsewhere.
_MODIFYING_TOOL_NAMES = frozenset({
    "Write", "Edit",
    "FileWrite", "FileWriteTool", "FileEdit", "FileEditTool",
    "WriteTool", "EditTool",
})

# F-40 root-cause fix: tool names that are read-only (exploration /
# diagnostics).  When an agent spends multiple consecutive turns
# making ONLY read-only tool calls without any modifying tool call
# and without changing the workspace, it is likely stuck in an
# investigation spiral (F-40's Python env debugging loop).  The
# stagnation guard below tracks a separate ``read_only_streak`` and
# breaks after ``max_read_only_turns`` such turns.
_READ_ONLY_TOOL_NAMES = frozenset({
    "Read", "Bash", "Grep", "Glob",
    "WebFetch", "WebSearch",
    "TodoWrite", "TaskStop",
})


@dataclass
class AgentSession:
    """One active issue run."""

    issue: Issue
    workspace: Workspace
    turn_count: int = 0
    status: str = "running"  # running, completed, failed
    output_text: str = ""
    # Lifecycle control
    paused: bool = False
    paused_at: float | None = None
    pause_reason: str = ""
    pause_resume_event: "asyncio.Event | None" = None
    # Event stream for CLI tail command
    event_queue: "asyncio.Queue | None" = None
    prompt_override: str | None = None
    run_kind: str = "issue"
    run_id: str | None = None
    summary_comment_id: str | None = None
    tool_count: int = 0
    verification_status: str | None = None
    verification_output: str | None = None
    report_path: str | None = None
    # F-45: canonical path to ~/.clawcodex/tool-events/{run_id}/events.ndjson.
    # Set in AgentRunner.run() at session start; consumed by
    # report_writer.write() to dual-write the NDJSON to the persistent layer.
    tool_events_path: str | None = None
    # F-49 Phase 0: session-transcript storage for conversation recording.
    # Lazy-initialized in run() via SessionStorage; holds the SS instance,
    # accumulated assistant text, and pending tool_use_id for the current turn.
    _transcript_storage: Any | None = None
    _transcript_asst_text: str = ""
    _transcript_tool_use_id: str | None = None
    attempt: int = 1
    issue_attempt: int = 1
    followup_attempt: int = 1
    # 429-aware backoff bookkeeping. ``consecutive_429_count`` is
    # incremented on each rate-limit hit and reset on the next
    # successful turn. ``total_429_backoff_seconds`` is the cumulative
    # sleep time spent in in-turn backoff (visible on the dashboard
    # and useful for cost analysis). ``rate_limit_pending_turn``
    # records the turn number being re-issued after a 429 sleep so
    # the SessionComplete handler skips its turn_number increment.
    consecutive_429_count: int = 0
    total_429_backoff_seconds: float = 0.0
    rate_limit_pending_turn: int | None = None
    debug_log_path: str | None = None
    last_agent_event_at: float | None = None
    last_agent_event: str | None = None
    last_tool_name: str | None = None
    timeout_deadline_at: float | None = None
    # F-09 / F-40 root-cause fix: capture the reason the session ended
    # before the registry writeback. ``session_end_reason`` is one of
    # ``task_complete`` / ``noop_completed`` / ``budget_exhausted`` /
    # ``stagnation`` / ``loop_detected`` / ``failed`` / ``paused`` /
    # ``cancelled``; ``session_end_summary`` is a short human-readable
    # explanation surfaced in dashboard + registry.  The agent_runner
    # sets these on the appropriate exit branch so the orchestrator
    # can pass them to ``IssueRegistry.update_report`` instead of
    # silently inheriting ``status="completed"``.
    session_end_reason: str | None = None
    session_end_summary: str = ""


@dataclass
class RetryItem:
    """Item queued for retry."""

    issue_id: str
    attempt: int
    delay_seconds: float
    identifier: str = ""
    error: str = ""
    worker_host: str | None = None
    workspace_path: str = ""
    scheduled_at: float = field(default_factory=time.time)


class AgentRunner:
    """Execute a single issue via ClawCodex QueryRunner."""

    def __init__(
        self,
        agent_config: AgentConfig,
        codex_config: CodexConfig,
    ) -> None:
        self.agent_config = agent_config
        self.codex_config = codex_config
        self.max_turns = agent_config.max_turns
        self._approval_policy: ApprovalPolicy = get_approval_policy(
            getattr(codex_config, "approval_policy", "never") or "never"
        )
        # Injectable sleep hook for 429 backoff. Tests monkey-patch
        # this with a recording coroutine; production paths use the
        # real ``asyncio.sleep`` so cancellation still works.
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    def _handle_tool_call(
        self,
        event: ToolCallEvent,
        session_context: dict[str, Any],
    ) -> ToolCallEvent:
        """Intercept tool call, apply approval policy.

        Returns the same event object with _approved / _deny_reason set.
        """
        # Convert to policy event type
        policy_event = PolicyToolCallEvent(
            tool_name=event.tool_name,
            params=event.params,
            tool_use_id=event.tool_use_id,
        )
        self._approval_policy.evaluate(policy_event, session_context)

        # Mirror decision back to the caller's event (src.api.query.ToolCallEvent)
        # which uses _approved / _deny_reason fields directly.
        event._approved = policy_event._approved
        event._deny_reason = policy_event._deny_reason
        return event

    def _append_tool_event_log(
        self,
        event: ToolCallEvent,
        session_context: dict[str, Any],
    ) -> None:
        """Persist a per-tool decision row to events.ndjson (F-45).

        Writes one NDJSON line to
        ``~/.clawcodex/tool-events/{run_id}/events.ndjson``.  Decoupled
        from ``permission_mode`` — all 7 modes (default / plan /
        bypassPermissions / acceptEdits / dontAsk / auto / bubble) write
        the same row shape; only the ``permission_mode`` column value
        varies.  Failures are logged and swallowed: the audit log must
        never block the agent run.
        """
        try:
            run_id = session_context.get("run_id") or "unknown"
            base_dir = Path.home() / ".clawcodex" / "tool-events" / run_id
            try:
                base_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                # mkdir may fail in a sandboxed HOME; skip the rest
                # gracefully so the agent loop is never affected.
                logger.exception(
                    "tool-event log mkdir failed run_id=%s path=%s",
                    run_id,
                    base_dir,
                )
                return

            log_path = base_dir / "events.ndjson"

            # Single-generation rotate (F-45 Sub-E decision: 50MB
            # threshold, single backup). v2.14 will add 7-day cleanup.
            try:
                if log_path.exists() and log_path.stat().st_size >= _TOOL_EVENT_LOG_ROTATE_BYTES:
                    rotated = log_path.with_suffix(log_path.suffix + ".1")
                    try:
                        rotated.unlink(missing_ok=True)
                    except Exception:
                        pass
                    log_path.replace(rotated)
            except Exception:
                # Rotation is best-effort — log and continue writing to
                # the live file. A single oversized file is still better
                # than a failed write.
                logger.exception(
                    "tool-event log rotate failed path=%s", log_path
                )

            row = ToolEventLog(
                tool=event.tool_name,
                params=event.params,
                approved=event._approved,
                deny_reason=event._deny_reason,
                permission_mode=session_context.get(
                    "permission_mode", "unknown"
                ),
                turn=session_context.get("turn", 0),
                session_run_id=run_id,
            )
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(row.to_json() + "\n")
            except Exception:
                logger.exception(
                    "tool-event log append failed run_id=%s path=%s",
                    run_id,
                    log_path,
                )
        except Exception:
            # Defensive outer guard: never let audit logging break the
            # agent run. The audit log is observable infrastructure, not
            # a correctness gate.
            logger.exception("tool-event log unexpected failure")

    def _is_429_response(self, turn_output: str) -> bool:
        """Detect an upstream 429 rate limit in the accumulated turn output.

        The headless runner currently catches the provider's HTTPError
        and surfaces the message string in ``aggregate_text`` /
        ``stdout``, which the QueryRunner yields as a final ``TextDelta``
        before the ``SessionComplete(reason="exit_code=1")``. The string
        typically contains ``"Error code: 429"`` and a JSON body with
        ``"type": "rate_limit_error"``.

        Quota exhaustion is short-circuited to ``False`` — a permanent
        quota error is not helped by sleeping, and the normal failure
        path is the right place to surface it. Quota is detected by
        string match because the upstream message text mixes the
        429/rate_limit_error markers with quota-specific language
        ("exceeded your current quota", "limit: 0", or
        ``"Token Plan 主要面向个人开发者"``), and the typed
        ``is_quota_exhausted`` helper requires an exception object
        with a ``.status`` attribute that we don't have here.
        """
        if not turn_output:
            return False
        low = turn_output.lower()
        # Quota-style indicators win over rate-limit indicators. The
        # provider wraps quota in the same 429/rate_limit_error
        # envelope, so substring matching is the most robust signal
        # available without parsing the JSON body.
        # Temporary rate-limit phrasing from MiniMax that looks like quota
        # but is actually a retryable 429.  Check these FIRST so they
        # don't get caught by the broader "token plan" / "quota" match.
        temporary_rate_limit_indicators = (
            "请稍后重试",       # "please retry later"
            "当前请求量较高",   # "current request volume is high"
            "稍后重试",         # "retry later" (shorter variant)
        )
        if any(ind in turn_output for ind in temporary_rate_limit_indicators):
            # This is a temporary rate limit, not quota — fall through
            # to the 429/rate_limit_error detection below.
            pass
        else:
            quota_indicators = (
                "exceeded your current quota",
                "limit: 0",
                "token plan",  # MiniMax "Token Plan 主要面向个人开发者"
                "quota",
            )
            if any(ind in low for ind in quota_indicators):
                return False
        return (
            "error code: 429" in low
            or "rate_limit_error" in low
            or '"type": "rate_limit_error"' in low
            or "rate limit" in low
        )

    def _dispatch_sink(
        self,
        sink: Any,
        method: str,
        event: Any,
        session: "AgentSession",
    ) -> None:
        """Call ``sink.<method>(event, session)`` with logging on failure.

        A no-op shim for local-source-repo compatibility: the workspace
        has a full ProgressSink / CompositeProgressSink fan-out layer
        that this delegates to; in the local source repo the runner
        just calls back to ``sink.<method>`` directly.  Exceptions are
        caught and logged so a bad sink never crashes the agent run.
        """
        if sink is None:
            return
        try:
            getattr(sink, method)(event, session)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "progress_sink.%s dispatch failed: %s",
                method,
                exc,
            )

    def _compute_rate_limit_backoff(self, session: AgentSession) -> float:
        """Compute the next 429 backoff delay (seconds) for ``session``.

        Sequence: ``base * factor**(count-1)`` capped at
        ``rate_limit_max_backoff_ms``. A small jitter (±10% of delay)
        is added to avoid thundering-herd if the operator ever flips
        the workflow to parallel agents.
        """
        base_ms = self.agent_config.rate_limit_base_delay_ms
        max_ms = self.agent_config.rate_limit_max_backoff_ms
        factor = self.agent_config.rate_limit_exponential_factor
        count = max(1, session.consecutive_429_count)
        delay_ms = min(base_ms * (factor ** (count - 1)), max_ms)
        delay_s = delay_ms / 1000.0
        # Light jitter: up to +10% of the delay.  Keep it non-negative.
        jitter = random.uniform(0, 0.1 * delay_s) if delay_s > 0 else 0.0
        return delay_s + jitter

    async def _handle_rate_limit(
        self,
        session: AgentSession,
        turn_output: str,
        turn_number: int,
        status_dashboard: Any | None,
    ) -> str:
        """Apply 429 backoff for one cycle. Returns the new status.

        Increments ``session.consecutive_429_count``, computes the
        backoff delay, emits a ``TextDelta`` to the dashboard and
        event log, sleeps, and returns either ``"running"`` (re-issued
        the same turn) or ``"rate_limit_circuit_open"`` (circuit
        breaker tripped — caller should ``return``).
        """
        issue = session.issue
        session.consecutive_429_count += 1
        max_retries = self.agent_config.rate_limit_max_retries

        if session.consecutive_429_count > max_retries:
            session.status = "rate_limit_circuit_open"
            logger.error(
                "Rate limit circuit breaker open issue_id=%s consecutive=%d max=%d",
                issue.id,
                session.consecutive_429_count,
                max_retries,
            )
            return session.status

        delay_s = self._compute_rate_limit_backoff(session)
        session.total_429_backoff_seconds += delay_s
        notice = (
            f"\n[rate-limit] 429 detected "
            f"(attempt {session.consecutive_429_count}/{max_retries}); "
            f"sleeping {delay_s:.0f}s before retry\n"
        )
        session.output_text += notice

        # Surface to dashboard / event log so the operator sees
        # liveness during the backoff.
        text_event = TextDelta(content=notice)
        if status_dashboard is not None:
            try:
                status_dashboard.on_event(text_event, session)
            except Exception:
                pass
        self._write_event_log(session.workspace.path, issue.id, text_event, turn=turn_number)

        logger.warning(
            "Rate limit backoff issue_id=%s attempt=%d delay=%.1fs",
            issue.id,
            session.consecutive_429_count,
            delay_s,
        )

        # Mark the turn we are about to re-issue so the SessionComplete
        # handler (if it ever runs again on the same turn) skips its
        # own turn_number increment. Defensive — the current control
        # flow ``continue``s before incrementing.
        session.rate_limit_pending_turn = turn_number

        await self._sleep(delay_s)
        return "running"

    async def run(
        self,
        session: AgentSession,
        workflow: WorkflowConfig,
        status_dashboard: Any | None = None,
        tracker: Any = None,
        comment_tracker: Any | None = None,
        clarification_resolver: Any | None = None,
        progress_reporter: Any | None = None,
        diagnostics_callback: Callable[[AgentSession], None] | None = None,
    ) -> None:
        """Execute issue until completion or max_turns.

        Runs multi-turn continuation loop: each turn is a QueryRunner
        invocation; after each turn checks if the issue is still active
        via tracker.fetch_issue_states_by_ids and continues if so.
        """
        issue = session.issue
        workspace = session.workspace
        if session.run_id is None:
            session.run_id = self._build_run_id(session)
        if comment_tracker is not None and issue.id:
            await self._post_summary_placeholder(session, comment_tracker)

        # Pass delay_between_requests_ms to the query layer via env var.
        # _call_model_sync in src/query/query.py reads this to enforce a
        # minimum interval between successive provider API calls.
        delay_env = str(self.agent_config.delay_between_requests_ms)
        os.environ["CLAWCODEX_PROVIDER_REQUEST_DELAY_MS"] = delay_env
        if delay_env != "0":
            logger.info(
                "Provider request delay set to %s ms",
                delay_env,
            )

        logger.info(
            "Starting agent run issue_id=%s identifier=%s workspace=%s",
            issue.id,
            issue.identifier,
            workspace.path,
        )

        session_context = {
            "issue_id": issue.id,
            "issue_identifier": issue.identifier,
            "workspace_path": str(workspace.path),
            "workflow": workflow,
            # F-45: run_id + permission_mode are consumed by
            # _append_tool_event_log to write per-tool rows to
            # ~/.clawcodex/tool-events/{run_id}/events.ndjson.
            "run_id": session.run_id,
            "permission_mode": self.agent_config.permission_mode,
        }
        # F-45: stash the canonical NDJSON path on the session so
        # report_writer.write() can dual-write it to the persistent
        # layer (Sub-C).  Resolved here (not in the property) so the
        # path is concrete before the first event is appended.
        session.tool_events_path = str(
            Path.home() / ".clawcodex" / "tool-events" / (session.run_id or "unknown") / "events.ndjson"
        )
        session.debug_log_path = str(
            workspace.path / ".orchestrator_control" / "runs" / (session.run_id or "unknown") / "debug.ndjson"
        )
        append_debug_event(
            session.debug_log_path,
            "agent_runner.start",
            issue_id=issue.id,
            issue_identifier=issue.identifier,
            run_id=session.run_id,
            workspace=str(workspace.path),
            max_turns=self.max_turns,
            provider=self.agent_config.provider,
            permission_mode=self.agent_config.permission_mode,
        )

        turn_number = 0
        tool_count = 0
        consecutive_clean_turns = 0  # legacy workspace-dirty no-op counter
        # F-40: F-38 used a shared ``ProgressReporter`` singleton; the
        # orchestrator now passes a per-session :class:`ProgressSink`
        # via the ``progress_reporter`` kwarg. Bind it to ``sink`` so
        # the three ``_dispatch_sink`` calls (stagnation / loop /
        # no-op paths) reach a private, task-bound fan-out, and the
        # three PhaseComplete / TurnComplete / SessionComplete
        # dispatches below stay symmetric. ``sink`` is allowed to be
        # ``None`` for tests / direct call sites that don't wire a
        # reporter.
        sink = progress_reporter
        # F-?? root-cause fix: stagnation + loop guards. Independent of
        # the workspace-dirty heuristic above (which never fires when
        # the workspace has untracked files — the exact pattern observed
        # in F-09's repeated 30-min timeouts). no_work_streak counts
        # consecutive turns where the LLM produced zero tool calls AND
        # empty output. tool_signature_history tracks recent turn
        # signatures to detect repeated tool-call loops.
        no_work_streak = 0
        # F-40 root-cause fix: has_made_progress dual-threshold stagnation.
        # ``has_made_progress`` is set to True the first time the LLM
        # emits a modifying tool call (Write / Edit / …) in any turn.
        # When True, the stagnation guard requires 2× the configured
        # max_no_op_turns before triggering, because the agent has
        # already demonstrated it *can* produce useful work and the
        # empty-turn pattern is more likely a recoverable LLM tail
        # than a fundamental deadlock (as seen in F-40's run-06).
        # Stored on the session so ``_should_continue`` can read it.
        session.has_made_progress = False
        # Pre-existing bug (commit 8fb1b78): ``_dispatch_sink`` was added
        # but the ``sink`` variable was never assigned in ``run()``,
        # so stagnation/loop guard calls to ``_dispatch_sink(sink, ...)``
        # would raise ``NameError``.  Default to ``progress_reporter``
        # (None in test stubs, which ``_dispatch_sink`` treats as no-op).
        sink = progress_reporter
        # F-40 root-cause fix: read-only tool spiral detection.
        # Counts consecutive turns where the agent only made read-only
        # tool calls (Bash / Read / Grep / …) without any modifying
        # tool call (Write / Edit / …).  BashTool always produces
        # output (stdout/stderr), so ``turn_output`` is never empty
        # and cannot be used to distinguish exploration from empty
        # turns — we rely solely on the absence of modifying tools.
        # When this counter reaches ``_MAX_READ_ONLY_TURNS`` the
        # session is terminated with reason "read_only_loop".
        read_only_streak = 0
        tool_signature_history: list[str] = []
        max_no_op_turns = max(
            1, int(getattr(self.agent_config, "max_no_op_turns", 3) or 3)
        )
        loop_window = max(
            2, int(getattr(self.agent_config, "loop_detection_window", 5) or 5)
        )
        loop_threshold = max(
            2, int(getattr(self.agent_config, "loop_detection_threshold", 3) or 3)
        )

        def update_diagnostics() -> None:
            session.tool_count = tool_count
            if diagnostics_callback is not None:
                try:
                    diagnostics_callback(session)
                except Exception:
                    logger.exception(
                        "run diagnostics callback failed issue_id=%s",
                        issue.id,
                    )

        update_diagnostics()

        while turn_number < self.max_turns:
            # Build prompt for this turn
            if turn_number == 0:
                if session.prompt_override:
                    prompt = session.prompt_override
                else:
                    # Build clarification context if issue is in clarification flow
                    clarification_context = ""
                    pending_question = None
                    options = None

                    if clarification_resolver is not None and issue.id:
                        # Check if this issue has a pending clarification
                        resolved = clarification_resolver.get_answer(issue.id)
                        if resolved and resolved.status.value in (
                            "pending",
                            "awaiting_local",
                            "awaiting_author",
                        ):
                            # Get the pending item from queue to retrieve question + options
                            pending_item = clarification_resolver._queue.get(issue.id)
                            if pending_item:
                                pending_question = pending_item.question
                                options = pending_item.options if pending_item.options else None
                                clarification_context = PromptBuilder.build_clarification_context(
                                    pending_question=pending_question,
                                    options=options,
                                )

                    prompt = PromptBuilder.render(
                        issue,
                        clarification_context=clarification_context,
                        pending_question=pending_question,
                        options=options,
                        session=session,
                    )
                session._issue_context = prompt  # Store for continuation
            else:
                prompt = PromptBuilder.build_continuation_prompt(
                    turn_number=turn_number,
                    max_turns=self.max_turns,
                    issue_context=getattr(session, "_issue_context", None),
                    session=session,
                )
                logger.info(
                    "Continuation turn %d/%s for issue_id=%s",
                    turn_number,
                    self.max_turns,
                    issue.id,
                )

            # F-49 Phase 0: lazy-init SessionStorage and write user prompt
            if session.run_id:
                if session._transcript_storage is None:
                    try:
                        from src.services.session_storage import SessionStorage
                        session._transcript_storage = SessionStorage(
                            session_id=session.run_id,
                        )
                        session._transcript_storage.init_metadata(
                            model=self.agent_config.model or "",
                            cwd=str(session.workspace.path),
                            title=(
                                f"orchestrator-"
                                f"{session.issue.identifier or session.issue.id}"
                            ),
                        )
                    except Exception:
                        logger.exception(
                            "Failed to init transcript storage run_id=%s",
                            session.run_id,
                        )
                if session._transcript_storage is not None:
                    try:
                        from src.types.messages import create_user_message
                        from src.types.content_blocks import TextBlock
                        session._transcript_storage.write_message(
                            create_user_message(
                                content=[TextBlock(text=prompt)],
                                origin="human",
                            )
                        )
                    except Exception:
                        logger.exception(
                            "Failed to write transcript prompt run_id=%s",
                            session.run_id,
                        )

            append_debug_event(
                session.debug_log_path,
                "agent_runner.turn_start",
                issue_id=issue.id,
                run_id=session.run_id,
                turn=turn_number,
                prompt_len=len(prompt),
                output_len=len(session.output_text),
            )
            query_config = QueryConfig(
                prompt=prompt,
                workspace=workspace.path,
                provider=self.agent_config.provider,
                model=self.agent_config.model,
                max_turns=self.max_turns,
                permission_mode=self.agent_config.permission_mode,
                run_id=session.run_id,
                debug_log_path=session.debug_log_path,
            )
            runner = QueryRunner(query_config)

            turn_has_tool_calls = False
            turn_output = ""
            turn_has_modifying_tool = False
            # F-?? root-cause fix: per-turn tool-name accumulator feeding
            # the loop-detection signature history.
            turn_tool_names: list[str] = []

            try:
                stream_iter = runner.stream()
                while True:
                    try:
                        event = await stream_iter.__anext__()
                    except StopAsyncIteration:
                        break
                    event_type = type(event).__name__
                    session.last_agent_event_at = time.time()
                    session.last_agent_event = event_type
                    event_tool_name = getattr(event, "tool_name", None)
                    if event_tool_name:
                        session.last_tool_name = event_tool_name
                    append_debug_event(
                        session.debug_log_path,
                        "agent_runner.event",
                        issue_id=issue.id,
                        run_id=session.run_id,
                        type=event_type,
                        tool=event_tool_name,
                        turn=turn_number,
                        tool_count=tool_count,
                        output_len=len(session.output_text),
                    )
                    if isinstance(event, TextDelta):
                        session.output_text += event.content
                        turn_output += event.content
                        update_diagnostics()
                        if status_dashboard is not None:
                            try:
                                status_dashboard.on_event(event, session)
                            except Exception:
                                pass

                        # Push to event queue for CLI tail
                        if session.event_queue is not None:
                            try:
                                session.event_queue.put_nowait(event)
                            except Exception:
                                pass

                        # Also write to event log file for cross-process tail
                        self._write_event_log(session.workspace.path, issue.id, event, turn=turn_number)
                        # F-49 Phase 0: accumulate assistant text for transcript
                        session._transcript_asst_text += event.content

                    elif isinstance(event, ToolCallEvent):
                        turn_has_tool_calls = True
                        tool_count += 1
                        update_diagnostics()
                        # F-?? root-cause fix: collect tool names for the
                        # turn signature so the loop-detection guard can
                        # spot repeated tool-call patterns across turns.
                        if event.tool_name:
                            turn_tool_names.append(event.tool_name)
                        # F-40 root-cause fix: has_made_progress tracking.
                        # Once the LLM emits a modifying tool call, set the
                        # flag so the stagnation guard uses the relaxed
                        # (2×) threshold for subsequent empty turns.
                        if event.tool_name in _MODIFYING_TOOL_NAMES:
                            session.has_made_progress = True
                            turn_has_modifying_tool = True

                        # Pause support: wait for resume if session is paused
                        if session.paused and session.pause_resume_event is not None:
                            await session.pause_resume_event.wait()

                        # Operator hint injection: check .operator_hints.md
                        self._inject_operator_hints(session.workspace)

                        # F-45: in headless (orchestrator) mode the api.query
                        # stream yields ToolCallEvent with _approved=None
                        # (TS upstream's ToolContext.approval_policy =
                        # "bypassPermissions" + permission_handler = None
                        # bypasses the user-prompt layer, not the policy
                        # decision layer).  The orchestrator's ApprovalPolicy
                        # is the authoritative source of "allowed vs denied";
                        # call it here so the audit log captures real
                        # decisions.  Then mirror the policy's verdict into
                        # the per-tool NDJSON bypass.
                        event = self._handle_tool_call(event, session_context)
                        # Tag session_context with the current turn so the
                        # NDJSON row carries the right `turn` value.
                        session_context["turn"] = turn_number
                        self._append_tool_event_log(event, session_context)

                        if status_dashboard is not None:
                            try:
                                status_dashboard.on_event(event, session)
                            except Exception:
                                pass

                        # Push to event queue for CLI tail
                        if session.event_queue is not None:
                            try:
                                session.event_queue.put_nowait(event)
                            except Exception:
                                pass

                        # Also write to event log file for cross-process tail
                        self._write_event_log(session.workspace.path, issue.id, event, turn=turn_number)
                        # F-49 Phase 0: write assistant message with tool_use to transcript
                        if session._transcript_storage is not None:
                            try:
                                from src.types.messages import create_assistant_message
                                from src.types.content_blocks import (
                                    TextBlock,
                                    ToolUseBlock,
                                )
                                blocks = []
                                if session._transcript_asst_text:
                                    blocks.append(TextBlock(
                                        text=session._transcript_asst_text,
                                    ))
                                if event.tool_use_id:
                                    blocks.append(ToolUseBlock(
                                        id=event.tool_use_id,
                                        name=event.tool_name,
                                        input=event.params,
                                    ))
                                if blocks:
                                    session._transcript_storage.write_message(
                                        create_assistant_message(
                                            content=blocks,
                                            model=self.agent_config.model,
                                        ),
                                    )
                                session._transcript_asst_text = ""
                                session._transcript_tool_use_id = \
                                    event.tool_use_id
                            except Exception:
                                logger.exception(
                                    "Failed to write transcript tool_use "
                                    "run_id=%s", session.run_id,
                                )

                    elif isinstance(event, ToolResultEvent):
                        logger.debug(
                            "Tool result issue_id=%s tool=%s is_error=%s",
                            issue.id,
                            event.tool_name,
                            event.result.get("is_error", False),
                        )
                        if status_dashboard is not None:
                            try:
                                status_dashboard.on_event(event, session)
                            except Exception:
                                pass

                        # Push to event queue for CLI tail
                        if session.event_queue is not None:
                            try:
                                session.event_queue.put_nowait(event)
                            except Exception:
                                pass

                        # Also write to event log file for cross-process tail
                        self._write_event_log(session.workspace.path, issue.id, event, turn=turn_number)
                        # F-49 Phase 0: write tool_result as UserMessage to transcript
                        if session._transcript_storage is not None and session._transcript_tool_use_id:
                            try:
                                from src.types.messages import create_user_message
                                from src.types.content_blocks import (
                                    ToolResultBlock,
                                )
                                result_output = event.result.get("output", "")
                                is_error = event.result.get("is_error", False)
                                session._transcript_storage.write_message(
                                    create_user_message(
                                        content=[ToolResultBlock(
                                            tool_use_id=(
                                                session._transcript_tool_use_id
                                            ),
                                            content=(
                                                result_output
                                                if isinstance(result_output, str)
                                                else str(result_output)
                                            ),
                                            is_error=is_error,
                                        )],
                                        origin="tool_result",
                                    ),
                                )
                                session._transcript_tool_use_id = None
                            except Exception:
                                logger.exception(
                                    "Failed to write transcript tool_result "
                                    "run_id=%s", session.run_id,
                                )
                        update_diagnostics()
                        if status_dashboard is not None:
                            try:
                                status_dashboard.on_event(event, session)
                            except Exception:
                                pass

                    elif isinstance(event, SessionComplete):
                        # 429-aware backoff: detect rate limit BEFORE the
                        # normal completion handling so we can re-issue
                        # the same turn after sleeping instead of failing.
                        if self._is_429_response(turn_output):
                            new_status = await self._handle_rate_limit(
                                session,
                                turn_output,
                                turn_number,
                                status_dashboard,
                            )
                            if new_status == "rate_limit_circuit_open":
                                return
                            # Reset the per-turn accumulators so the
                            # re-issued turn starts with a clean slate.
                            turn_output = ""
                            turn_has_tool_calls = False
                            # Do NOT increment turn_number; the same
                            # turn's prompt will be re-rendered below
                            # when the outer while loop iterates.
                            continue

                        # Normal completion path — increment the turn
                        # counter and emit PhaseComplete.
                        turn_number += 1
                        session.turn_count = turn_number
                        # F-49 Phase 0: flush remaining assistant text + storage
                        if session._transcript_storage is not None:
                            try:
                                if session._transcript_asst_text:
                                    from src.types.messages import (
                                        create_assistant_message,
                                    )
                                    from src.types.content_blocks import (
                                        TextBlock,
                                    )
                                    session._transcript_storage.write_message(
                                        create_assistant_message(
                                            content=[TextBlock(
                                                text=session._transcript_asst_text,
                                            )],
                                            model=self.agent_config.model,
                                        ),
                                    )
                                    session._transcript_asst_text = ""
                                session._transcript_storage.flush()
                            except Exception:
                                logger.exception(
                                    "Failed to flush transcript "
                                    "run_id=%s", session.run_id,
                                )
                        append_debug_event(
                            session.debug_log_path,
                            "agent_runner.turn_complete",
                            issue_id=issue.id,
                            run_id=session.run_id,
                            turn=turn_number,
                            reason=event.reason,
                            tool_count=tool_count,
                            output_len=len(session.output_text),
                        )

                        # Emit PhaseComplete event for progress reporting
                        phase_event = PhaseComplete(
                            phase=turn_number,
                            turn_count=turn_number,
                        )
                        self._write_event_log(session.workspace.path, issue.id, phase_event, turn=turn_number)
                        if sink is not None:
                            # F-40: dispatch PhaseComplete + TurnComplete
                            # through the new protocol methods. The old
                            # ``on_event`` shim is no longer used by
                            # AgentRunner; the F-38 stub tests were
                            # already updated to record on these
                            # callbacks.
                            self._dispatch_sink(
                                sink, "on_phase_complete", phase_event, session
                            )
                            self._dispatch_sink(
                                sink,
                                "on_turn_complete",
                                TurnComplete(turn=turn_number),
                                session,
                            )

                        update_diagnostics()
                        if event.reason == "success":
                            # A successful turn resets the 429 backoff
                            # counter — a 429 followed by a clean run is
                            # a sign the rate window has passed.
                            session.consecutive_429_count = 0
                            session.rate_limit_pending_turn = None

                            # Check if issue is still active before declaring completion
                            # F-54 root-cause fix: pass the session so
                            # ``_should_continue`` can also check the
                            # workspace's git state and stop the
                            # continuation loop when work is done.
                            if tracker is not None and issue.id:
                                is_active, refreshed_issue = await self._should_continue(
                                    issue, tracker, session
                                )
                                if is_active and turn_number < self.max_turns:
                                    # F-?? Fix 4: include the running noop
                                    # streak in the continuation log so
                                    # operators can spot stuck-on-finished
                                    # runs from the daemon log alone
                                    # (the previous message had no
                                    # indicator that the agent was no
                                    # longer making progress).
                                    logger.info(
                                        "Issue %s still active, continuing turn %d/%d "
                                        "(noop_streak=%d/%d)",
                                        issue.id,
                                        turn_number,
                                        self.max_turns,
                                        no_work_streak,
                                        max_no_op_turns,
                                    )
                                    # F-?? root-cause fix: stagnation guard.
                                    # Counts consecutive turns where the LLM
                                    # produced zero tool calls AND empty
                                    # output — the exact pattern observed in
                                    # F-09's repeated 30-min timeouts (run-06
                                    # had 0 tool calls / 328 SessionComplete
                                    # events in a tight loop). Independent of
                                    # the workspace-dirty heuristic below,
                                    # which silently never fires when the
                                    # workspace has untracked files.
                                    if (
                                        not turn_has_tool_calls
                                        and not turn_output.strip()
                                    ):
                                        no_work_streak += 1
                                    else:
                                        no_work_streak = 0

                                    # F-40 root-cause fix: dual-threshold.
                                    # An agent that has already made progress
                                    # (emitted at least one modifying tool
                                    # call — Write / Edit / …) is given 2× the
                                    # configured max_no_op_turns before
                                    # stagnation fires, because empty-turn
                                    # streaks after productive work are
                                    # more likely recoverable (the LLM may be
                                    # in a temporary tail loop) than true
                                    # deadlocks from a broken provider.
                                    _stagnation_threshold = (
                                        max_no_op_turns * 2
                                        if session.has_made_progress
                                        else max_no_op_turns
                                    )
                                    if no_work_streak >= _stagnation_threshold:
                                        # F-54 root-cause fix: when the
                                        # agent never emitted a single
                                        # modifying tool call (Write/Edit)
                                        # AND tool_count is 0 (SessionComplete
                                        # returned immediately without any
                                        # tool), the real reason is "LLM gave
                                        # up without doing work", not
                                        # stagnation.  Mark it as such so
                                        # the orchestrator can retry.
                                        if (
                                            not getattr(session, "has_made_progress", False)
                                            and tool_count == 0
                                        ):
                                            # F-54 root-cause fix: before
                                            # declaring ``llm_gave_up``,
                                            # verify via test_command.
                                            if await self._run_verification(session):
                                                session.status = "completed"
                                                session.session_end_reason = (
                                                    "already_completed"
                                                )
                                                session.session_end_summary = (
                                                    "work already implemented "
                                                    "(verification passed)"
                                                )
                                                logger.info(
                                                    "Issue %s: work already done "
                                                    "(verification passed) — "
                                                    "marking completed",
                                                    issue.id,
                                                )
                                            else:
                                                session.session_end_reason = "llm_gave_up"
                                                session.session_end_summary = (
                                                    f"LLM returned SessionComplete(success) "
                                                    f"after 0 tool calls with no code changes"
                                                )
                                                logger.warning(
                                                    "LLM gave up immediately issue_id=%s "
                                                    "turns=%s tools=%s — "
                                                    "SessionComplete with 0 tools",
                                                    issue.id,
                                                    turn_number,
                                                    tool_count,
                                                )
                                        else:
                                            session.session_end_reason = "stagnation"
                                            session.session_end_summary = (
                                                f"{no_work_streak} consecutive "
                                                "turns with no tool calls and "
                                                "empty output"
                                            )
                                        logger.warning(
                                            "Stagnation detected issue_id=%s — "
                                            "%d consecutive no-op turns, "
                                            "breaking outer loop",
                                            issue.id,
                                            no_work_streak,
                                        )
                                        append_debug_event(
                                            session.debug_log_path,
                                            "agent_runner.stagnation_detected",
                                            issue_id=issue.id,
                                            run_id=session.run_id,
                                            turn=turn_number,
                                            no_work_streak=no_work_streak,
                                        )
                                        session.status = "stagnation"
                                        self._dispatch_sink(
                                            sink,
                                            "on_session_complete",
                                            SessionComplete(
                                                reason="stagnation"
                                            ),
                                            session,
                                        )
                                        return

                                    # F-40 root-cause fix: read-only
                                    # tool spiral guard.  When the agent
                                    # spends multiple consecutive turns
                                    # making ONLY read-only tool calls
                                    # (Bash / Read / Grep / …) without a
                                    # single Write / Edit, it is stuck
                                    # in an investigation spiral (F-54's
                                    # turn 1-6 pattern: 230+ Bash calls,
                                    # 0 code changes).  Bash output is
                                    # always non-empty, so we do NOT
                                    # check ``turn_output`` here —
                                    # the absence of modifying tools
                                    # is the reliable indicator.
                                    if (
                                        turn_number > 0
                                        and turn_has_tool_calls
                                        and not turn_has_modifying_tool
                                    ):
                                        read_only_streak += 1
                                    else:
                                        read_only_streak = 0

                                    if read_only_streak >= _MAX_READ_ONLY_TURNS:
                                        session.session_end_reason = "read_only_loop"
                                        session.session_end_summary = (
                                            f"{read_only_streak} consecutive "
                                            "turns with only read-only tool calls "
                                            "and no code changes"
                                        )
                                        logger.warning(
                                            "Read-only tool loop detected issue_id=%s — "
                                            "%d consecutive read-only turns, "
                                            "breaking outer loop",
                                            issue.id,
                                            read_only_streak,
                                        )
                                        append_debug_event(
                                            session.debug_log_path,
                                            "agent_runner.read_only_loop_detected",
                                            issue_id=issue.id,
                                            run_id=session.run_id,
                                            turn=turn_number,
                                            read_only_streak=read_only_streak,
                                        )
                                        session.status = "read_only_loop"
                                        self._dispatch_sink(
                                            sink,
                                            "on_session_complete",
                                            SessionComplete(
                                                reason="read_only_loop"
                                            ),
                                            session,
                                        )
                                        return

                                    # F-?? root-cause fix: loop guard.
                                    # Records this turn's tool-call
                                    # signature and breaks if the same
                                    # signature repeats >= threshold
                                    # times within the recent window.
                                    if turn_tool_names:
                                        signature = "|".join(
                                            sorted(turn_tool_names)
                                        )
                                    else:
                                        signature = "<empty>"
                                    tool_signature_history.append(signature)
                                    if len(tool_signature_history) > loop_window:
                                        tool_signature_history = (
                                            tool_signature_history[-loop_window:]
                                        )
                                    if (
                                        tool_signature_history.count(signature)
                                        >= loop_threshold
                                    ):
                                        session.session_end_reason = (
                                            "loop_detected"
                                        )
                                        session.session_end_summary = (
                                            f"signature {signature!r} "
                                            f"repeated "
                                            f"{tool_signature_history.count(signature)} "
                                            f"times in last {loop_window} turns"
                                        )
                                        logger.warning(
                                            "Loop detected issue_id=%s — "
                                            "signature %r repeated %d times, "
                                            "breaking outer loop",
                                            issue.id,
                                            signature,
                                            tool_signature_history.count(
                                                signature
                                            ),
                                        )
                                        append_debug_event(
                                            session.debug_log_path,
                                            "agent_runner.loop_detected",
                                            issue_id=issue.id,
                                            run_id=session.run_id,
                                            turn=turn_number,
                                            signature=signature,
                                            repeat_count=(
                                                tool_signature_history.count(
                                                    signature
                                                )
                                            ),
                                        )
                                        session.status = "loop_detected"
                                        self._dispatch_sink(
                                            sink,
                                            "on_session_complete",
                                            SessionComplete(
                                                reason="loop_detected"
                                            ),
                                            session,
                                        )
                                        return

                                    # No-op detection: if the agent has run multiple
                                    # consecutive turns without making any file changes,
                                    # it is likely stuck (e.g. the issue deliverables
                                    # already exist in the workspace). Force-complete
                                    # instead of wasting API calls and retry loops.
                                    workspace_path = str(session.workspace.path)
                                    dirty = bool(get_file_status(workspace_path))
                                    if dirty:
                                        consecutive_clean_turns = 0
                                    else:
                                        consecutive_clean_turns += 1
                                        if consecutive_clean_turns >= _NOOP_DETECTION_MAX_TURNS:
                                            logger.warning(
                                                "No-op detection triggered issue_id=%s — "
                                                "agent performed %d consecutive turns with "
                                                "zero file changes, force-completing",
                                                issue.id,
                                                consecutive_clean_turns,
                                            )
                                            session.status = "completed"
                                            session.session_end_reason = (
                                                "noop_completed"
                                            )
                                            session.session_end_summary = (
                                                f"{consecutive_clean_turns} "
                                                "consecutive clean turns"
                                            )
                                            # F-40: surface the
                                            # no-op completion to the
                                            # sink as a synthetic
                                            # SessionComplete so
                                            # downstream consumers
                                            # always see a terminal
                                            # event.
                                            self._dispatch_sink(
                                                sink,
                                                "on_session_complete",
                                                SessionComplete(
                                                    reason="noop_completed"
                                                ),
                                                session,
                                            )
                                            return
                                    continue  # Go to next turn
                                session.issue = refreshed_issue or session.issue

                            # F-?? root-cause fix: pre-existing bug
                            # that conflated "issue is no longer
                            # active" with "we ran out of turns".  When
                            # the issue is still active but
                            # ``turn_number`` has reached
                            # ``max_turns``, the right status is
                            # ``max_turns_exceeded`` and
                            # ``session_end_reason`` is
                            # ``budget_exhausted`` — the F-09 budget
                            # test depends on this distinction.
                            if turn_number >= self.max_turns:
                                session.status = "max_turns_exceeded"
                                session.session_end_reason = (
                                    "budget_exhausted"
                                )
                                session.session_end_summary = (
                                    f"reached max_turns="
                                    f"{self.max_turns} after "
                                    f"{turn_number} turns"
                                )
                                logger.info(
                                    "Agent run reached max_turns "
                                    "issue_id=%s turns=%s/%s tools=%s",
                                    issue.id,
                                    turn_number,
                                    self.max_turns,
                                    tool_count,
                                )
                            else:
                                # F-54 root-cause fix: distinguish real
                                # completions from "LLM gave up without
                                # doing work".  When the session ends
                                # but the agent never emitted a single
                                # modifying tool call (Write/Edit), mark
                                # as failed with reason "llm_gave_up"
                                # so the orchestrator can retry rather
                                # than treating it as a clean completion.
                                if getattr(session, "has_made_progress", False):
                                    session.status = "completed"
                                    if session.session_end_reason is None:
                                        session.session_end_reason = (
                                            "task_complete"
                                        )
                                        session.session_end_summary = (
                                            "issue no longer active"
                                        )
                                else:
                                    # F-54 root-cause fix: before
                                    # declaring ``llm_gave_up``, run
                                    # the workflow's ``test_command``
                                    # to check if the work was already
                                    # implemented in a previous session.
                                    # If verification passes, treat
                                    # this as a clean completion.
                                    if await self._run_verification(session):
                                        session.status = "completed"
                                        session.session_end_reason = (
                                            "already_completed"
                                        )
                                        session.session_end_summary = (
                                            "work already implemented "
                                            "(verification passed)"
                                        )
                                        logger.info(
                                            "Issue %s: work already done "
                                            "(verification passed) — "
                                            "marking completed",
                                            issue.id,
                                        )
                                    else:
                                        session.status = "failed"
                                        session.session_end_reason = "llm_gave_up"
                                        session.session_end_summary = (
                                            f"LLM returned SessionComplete(success) "
                                            f"after {tool_count} read-only tool calls "
                                            f"with no code changes"
                                        )
                                        logger.warning(
                                            "LLM gave up without writing code "
                                            "issue_id=%s turns=%s tools=%s "
                                            "has_made_progress=False",
                                            issue.id,
                                            turn_number,
                                            tool_count,
                                        )
                                logger.info(
                                    "Agent run completed issue_id=%s "
                                    "turns=%s/%s tools=%s",
                                    issue.id,
                                    turn_number,
                                    self.max_turns,
                                    tool_count,
                                )
                        else:
                            session.status = "failed"
                            if session.session_end_reason is None:
                                # F-40: capture a per-reason end reason
                                # so downstream sinks can distinguish
                                # ``exit_code=N`` style failures from
                                # clean termination paths.
                                session.session_end_reason = (
                                    f"exit_code={event.reason}"
                                )
                                session.session_end_summary = (
                                    f"QueryRunner ended with reason={event.reason}"
                                )
                            logger.warning(
                                "Agent run failed issue_id=%s reason=%s",
                                issue.id,
                                event.reason,
                            )
                        # F-40: terminal SessionComplete is the only
                        # event the F-38 design never dispatched. The
                        # reason we record on the wire is
                        # ``session_end_reason`` (set by the success /
                        # noop / max_turns / failure paths above) so
                        # the dashboard sees a uniform
                        # ``session_{reason}`` stage.
                        if sink is not None:
                            self._dispatch_sink(
                                sink,
                                "on_session_complete",
                                SessionComplete(
                                    reason=session.session_end_reason
                                    or event.reason
                                ),
                                session,
                            )
                        # F-49 Phase 0: final flush before returning
                        if session._transcript_storage is not None:
                            try:
                                if session._transcript_asst_text:
                                    from src.types.messages import (
                                        create_assistant_message,
                                    )
                                    from src.types.content_blocks import (
                                        TextBlock,
                                    )
                                    session._transcript_storage.write_message(
                                        create_assistant_message(
                                            content=[TextBlock(
                                                text=session._transcript_asst_text,
                                            )],
                                            model=self.agent_config.model,
                                        ),
                                    )
                                session._transcript_storage.flush()
                            except Exception:
                                logger.exception(
                                    "Failed to final-flush transcript "
                                    "run_id=%s", session.run_id,
                                )
                        return
            except RateLimitError as exc:
                # Typed fallback: if the headless runner ever propagates
                # a RateLimitError directly (e.g. via ``await future``
                # at extensions/api/query.py:173), treat it the same as
                # a 429 detected in the text stream.
                if is_rate_limit_error(exc):
                    # Synthesize a minimal turn_output so the standard
                    # detection helper recognizes the case.
                    synthetic_output = turn_output or (
                        f"Error code: 429 - {exc!s}"
                    )
                    new_status = await self._handle_rate_limit(
                        session,
                        synthetic_output,
                        turn_number,
                        status_dashboard,
                    )
                    if new_status == "rate_limit_circuit_open":
                        return
                    turn_output = ""
                    turn_has_tool_calls = False
                    continue
                # Not a 429 — re-raise to preserve existing behavior.
                raise

            # If we consumed all events without SessionComplete (shouldn't
            # happen with current QueryRunner, but be defensive), count the
            # turn anyway
            if not turn_has_tool_calls and turn_output:
                turn_number += 1
                session.turn_count = turn_number

        # Reached max_turns
        session.status = "max_turns_exceeded"
        session.session_end_reason = "budget_exhausted"
        session.session_end_summary = (
            f"reached max_turns={self.max_turns} after {turn_number} turns"
        )
        logger.info(
            "Agent run reached max_turns issue_id=%s turns=%s/%s tools=%s",
            issue.id,
            turn_number,
            self.max_turns,
            tool_count,
        )

        # Emit PhaseComplete event for progress reporting (max_turns path)
        phase_event = PhaseComplete(
            phase=turn_number,
            turn_count=turn_number,
        )
        self._write_event_log(session.workspace.path, issue.id, phase_event, turn=turn_number)
        if sink is not None:
            # F-40: max_turns path now dispatches BOTH PhaseComplete
            # (so the trailing phase is recorded with its progress)
            # AND SessionComplete(reason="budget_exhausted") so
            # downstream consumers always see a terminal event. The
            # ``on_session_complete`` call uses the runner's
            # ``session_end_reason`` (set above) as the wire reason.
            self._dispatch_sink(
                sink, "on_phase_complete", phase_event, session
            )
            self._dispatch_sink(
                sink,
                "on_turn_complete",
                TurnComplete(turn=turn_number),
                session,
            )
            self._dispatch_sink(
                sink,
                "on_session_complete",
                SessionComplete(
                    reason=session.session_end_reason or "budget_exhausted"
                ),
                session,
            )

        session.tool_count = tool_count
        # F-49 Phase 0: final flush before max_turns exit
        if session._transcript_storage is not None:
            try:
                if session._transcript_asst_text:
                    from src.types.messages import create_assistant_message
                    from src.types.content_blocks import TextBlock
                    session._transcript_storage.write_message(
                        create_assistant_message(
                            content=[TextBlock(
                                text=session._transcript_asst_text,
                            )],
                            model=self.agent_config.model,
                        ),
                    )
                session._transcript_storage.flush()
            except Exception:
                logger.exception(
                    "Failed to final-flush transcript run_id=%s",
                    session.run_id,
                )
        append_debug_event(
            session.debug_log_path,
            "agent_runner.max_turns_exceeded",
            issue_id=issue.id,
            run_id=session.run_id,
            turn_count=session.turn_count,
            tool_count=session.tool_count,
            output_len=len(session.output_text),
            last_event_type=session.last_agent_event,
            last_tool=session.last_tool_name,
        )

    def _build_run_id(self, session: AgentSession) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        attempt = getattr(session, "attempt", 1)
        if session.run_kind == "review_followup":
            issue_attempt = getattr(session, "issue_attempt", attempt)
            followup_attempt = getattr(session, "followup_attempt", 1)
            return f"run-{issue_attempt}-followup-{followup_attempt}-{timestamp}"
        return f"run-{attempt:02d}-{timestamp}"

    async def _post_summary_placeholder(
        self,
        session: AgentSession,
        comment_tracker: Any,
    ) -> None:
        body = "## ClawCodex Run Summary\n\n⏳ Run in progress."
        try:
            created = await comment_tracker.create_comment(session.issue.id, body)
        except Exception as exc:
            logger.warning(
                "Failed to post summary placeholder issue_id=%s: %s",
                session.issue.id,
                exc,
            )
            return
        if created is not None and getattr(created, "id", None):
            session.summary_comment_id = created.id

    async def _should_continue(
        self,
        issue: Issue,
        tracker: Any,
        session: AgentSession | None = None,
    ) -> tuple[bool, Issue]:
        """Check if the issue is still in an active state.

        F-54 root-cause fix: even when the tracker reports the issue
        as active, return False (stop) if the workspace already has
        uncommitted or committed changes that satisfy the issue, so
        the agent does not keep spinning in continuation loops after
        completing its work.
        """
        if not issue.id:
            return False, issue

        refreshed = await tracker.fetch_issue_states_by_ids([issue.id])
        refreshed_issue = refreshed.get(issue.id)
        if refreshed_issue is None:
            return False, issue

        active_states = [
            s.strip().lower()
            for s in (getattr(tracker, "active_states", None) or [])
        ]
        is_active = (
            refreshed_issue.state is not None
            and refreshed_issue.state.strip().lower() in active_states
        )
        if not is_active:
            return False, refreshed_issue

        # F-54 root-cause fix: if the tracker still says active but
        # the workspace already has uncommitted changes AND the
        # session already completed production work (turn > 0 with
        # modifying tools used), consider the work done and stop.
        if (
            session is not None
            and getattr(session, "turn_count", 0) > 0
            and getattr(session, "has_made_progress", False)
        ):
            ws = getattr(session, "workspace", None)
            if ws is not None:
                ws_path = getattr(ws, "path", None)
                if ws_path is not None:
                    try:
                        import subprocess
                        proc = subprocess.run(
                            ["git", "status", "--porcelain"],
                            cwd=str(ws_path),
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        has_uncommitted = bool(proc.stdout.strip())
                        # If there are uncommitted changes or the agent
                        # already finished its work, stop the loop.
                        if has_uncommitted or session.status in (
                            "completed", "task_complete"
                        ):
                            logger.info(
                                "Issue %s work appears done in workspace "
                                "(turn_count=%d, has_uncommitted=%s) — "
                                "stopping continuation loop",
                                issue.id,
                                session.turn_count,
                                has_uncommitted,
                            )
                            return False, refreshed_issue
                    except Exception:
                        pass  # Fail-open: allow continue if git check fails

        # F-54 root-cause fix: detect "fake progress" — the agent made
        # read-only tool calls and empty commits but never wrote a
        # single line of code.  When ``has_made_progress`` is False
        # AND the session has consumed multiple turns with only
        # read-only tools, stop the continuation loop so the session
        # terminates and the ``llm_gave_up`` check fires.
        tool_count = getattr(session, "tool_count", 0)
        if (
            not getattr(session, "has_made_progress", False)
            and getattr(session, "turn_count", 0) >= 2
            and tool_count > 0
        ):
            ws = getattr(session, "workspace", None)
            if ws is not None:
                ws_path = getattr(ws, "path", None)
                if ws_path is not None:
                    try:
                        import subprocess
                        # Check if recent commits have actual file
                        # changes.  If the agent made 3+ commits but
                        # ``git diff --stat`` shows nothing changed,
                        # all commits were ``--allow-empty`` — the
                        # agent is faking progress.
                        proc = subprocess.run(
                            ["git", "diff", "--stat", "HEAD~3..HEAD"],
                            cwd=str(ws_path),
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        diff_empty = not proc.stdout.strip()
                        if diff_empty:
                            logger.info(
                                "Issue %s: all recent commits are empty "
                                "(%d turns, %d tools, has_made_progress=%s) — "
                                "stopping fake-progress loop",
                                issue.id,
                                getattr(session, "turn_count", 0),
                                tool_count,
                                getattr(session, "has_made_progress", False),
                            )
                            return False, refreshed_issue
                    except Exception:
                        pass  # Fail-open

        return is_active, refreshed_issue

    def _inject_operator_hints(self, workspace: Any) -> None:
        """Check for operator hints in workspace and inject into context.

        Reads .operator_hints.md in the workspace directory and returns
        its contents if present. The caller should prepend this to the
        tool context for the next LLM call.
        """
        hints_file = workspace.path / ".operator_hints.md"
        if not hints_file.exists():
            return None

        try:
            content = hints_file.read_text(encoding="utf-8").strip()
            if content:
                logger.debug(
                    "Operator hints found for workspace %s: %d chars",
                    workspace.path,
                    len(content),
                )
                return content
        except Exception as exc:
            logger.warning("Failed to read operator hints: %s", exc)
        return None

    async def _run_verification(self, session: AgentSession) -> bool:
        """Run ``agent.test_command`` in the workspace to verify the
        issue deliverables are correctly implemented.

        Returns ``True`` when the command succeeds (exit code 0) or
        when no test command is configured.  ``False`` on failure.

        F-54 root-cause fix: before marking a session as
        ``llm_gave_up``, run this check.  If the test command passes,
        the work was already done in a previous session and the
        current session is correctly detecting completion — not
        "giving up".
        """
        import asyncio

        test_cmd = getattr(self.agent_config, "test_command", None)
        if not test_cmd:
            return True  # No test command = skip verification

        ws = getattr(session, "workspace", None)
        ws_path = getattr(ws, "path", None) if ws else None
        if not ws_path:
            return False

        timeout_ms = getattr(
            getattr(self.agent_config, "verification", None),
            "timeout_ms",
            600_000,
        )
        try:
            proc = await asyncio.create_subprocess_shell(
                test_cmd,
                cwd=str(ws_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_ms / 1000.0,
            )
            if proc.returncode == 0:
                logger.info(
                    "Verification passed for issue_id=%s — "
                    "work is already implemented",
                    session.issue.id,
                )
                return True
            logger.info(
                "Verification failed for issue_id=%s "
                "(exit=%d) — work not yet done",
                session.issue.id,
                proc.returncode,
            )
            return False
        except asyncio.TimeoutError:
            logger.warning(
                "Verification timed out for issue_id=%s",
                session.issue.id,
            )
            return False
        except Exception as exc:
            logger.warning(
                "Verification error for issue_id=%s: %s",
                session.issue.id,
                exc,
            )
            return False

    def _write_event_log(
        self,
        workspace_path: Any,
        issue_id: str | None,
        event: Any,
        turn: int | None = None,
    ) -> None:
        """Write structured event to event log file for CLI tail."""
        import json
        import time

        if issue_id is None:
            return

        log_dir = workspace_path / ".event_logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"{issue_id}.ndjson"

        try:
            if hasattr(event, "tool_name"):
                entry = {
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "tool_call",
                    "tool_name": event.tool_name,
                    "params": event.params,
                    "turn": turn,
                }
            elif hasattr(event, "result"):
                entry = {
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "tool_result",
                    "tool_name": event.tool_name,
                    "is_error": event.result.get("is_error", False),
                    "turn": turn,
                }
            elif hasattr(event, "content"):
                entry = {
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "text_delta",
                    "content": event.content,
                    "turn": turn,
                }
            elif isinstance(event, PhaseComplete):
                entry = {
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "phase_complete",
                    "phase": event.phase,
                    "turn_count": event.turn_count,
                    "turn": turn,
                }
            else:
                entry = {
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": str(type(event).__name__),
                    "turn": turn,
                }

            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
