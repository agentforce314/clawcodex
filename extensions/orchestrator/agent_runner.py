"""Run a single issue through ClawCodex query engine.

Port of Symphony's AgentRunner, replacing Codex JSON-RPC with QueryRunner.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..api.query import PhaseComplete, QueryConfig, QueryRunner
from ..api.query import SessionComplete, TextDelta, ToolCallEvent, ToolResultEvent
from .approval_policy import ApprovalPolicy, get_approval_policy, ToolCallEvent as PolicyToolCallEvent
from .config.schema import AgentConfig, CodexConfig, WorkflowConfig
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

# F-45: tool-event audit log rotation threshold. When events.ndjson
# exceeds this size on next append, rotate to events.ndjson.1 (single
# generation, overwrite). v2.14 will hook a cron for 7-day cleanup.
_TOOL_EVENT_LOG_ROTATE_BYTES = 50 * 1024 * 1024


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
        self._write_event_log(session.workspace.path, issue.id, text_event)

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

        turn_number = 0
        tool_count = 0

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
                )
                logger.info(
                    "Continuation turn %d/%s for issue_id=%s",
                    turn_number,
                    self.max_turns,
                    issue.id,
                )

            query_config = QueryConfig(
                prompt=prompt,
                workspace=workspace.path,
                provider=self.agent_config.provider,
                max_turns=self.max_turns,
                permission_mode=self.agent_config.permission_mode,
            )
            runner = QueryRunner(query_config)

            turn_has_tool_calls = False
            turn_output = ""

            try:
                stream_iter = runner.stream()
                while True:
                    try:
                        event = await stream_iter.__anext__()
                    except StopAsyncIteration:
                        break
                    if isinstance(event, TextDelta):
                        session.output_text += event.content
                        turn_output += event.content
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
                        self._write_event_log(session.workspace.path, issue.id, event)

                    elif isinstance(event, ToolCallEvent):
                        turn_has_tool_calls = True
                        tool_count += 1

                        # Pause support: wait for resume if session is paused
                        if session.pause_resume_event is not None:
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
                        self._write_event_log(session.workspace.path, issue.id, event)

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
                        self._write_event_log(session.workspace.path, issue.id, event)
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

                        # Emit PhaseComplete event for progress reporting
                        phase_event = PhaseComplete(
                            phase=turn_number,
                            turn_count=turn_number,
                        )
                        self._write_event_log(session.workspace.path, issue.id, phase_event)
                        if progress_reporter is not None:
                            progress_reporter.on_event(phase_event, session)

                        session.tool_count = tool_count
                        if event.reason == "success":
                            # A successful turn resets the 429 backoff
                            # counter — a 429 followed by a clean run is
                            # a sign the rate window has passed.
                            session.consecutive_429_count = 0
                            session.rate_limit_pending_turn = None

                            # Check if issue is still active before declaring completion
                            if tracker is not None and issue.id:
                                is_active, refreshed_issue = await self._should_continue(
                                    issue, tracker
                                )
                                if is_active and turn_number < self.max_turns:
                                    logger.info(
                                        "Issue %s still active, continuing turn %d/%d",
                                        issue.id,
                                        turn_number,
                                        self.max_turns,
                                    )
                                    continue  # Go to next turn
                                session.issue = refreshed_issue or session.issue

                            session.status = "completed"
                            logger.info(
                                "Agent run completed issue_id=%s turns=%s/%s tools=%s",
                                issue.id,
                                turn_number,
                                self.max_turns,
                                tool_count,
                            )
                        else:
                            session.status = "failed"
                            logger.warning(
                                "Agent run failed issue_id=%s reason=%s",
                                issue.id,
                                event.reason,
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
        self._write_event_log(session.workspace.path, issue.id, phase_event)
        if progress_reporter is not None:
            progress_reporter.on_event(phase_event, session)

        session.tool_count = tool_count

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
    ) -> tuple[bool, Issue]:
        """Check if the issue is still in an active state."""
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

    def _write_event_log(
        self,
        workspace_path: Any,
        issue_id: str | None,
        event: Any,
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
                }
            elif hasattr(event, "result"):
                entry = {
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "tool_result",
                    "tool_name": event.tool_name,
                    "is_error": event.result.get("is_error", False),
                }
            elif hasattr(event, "content"):
                entry = {
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "text_delta",
                    "content": event.content,
                }
            elif isinstance(event, PhaseComplete):
                entry = {
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "phase_complete",
                    "phase": event.phase,
                    "turn_count": event.turn_count,
                }
            else:
                entry = {
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": str(type(event).__name__),
                }

            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
