"""Run a single issue through ClawCodex query engine.

Port of Symphony's AgentRunner, replacing Codex JSON-RPC with QueryRunner.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..api.query import PhaseComplete, QueryConfig, QueryRunner
from ..api.query import SessionComplete, TextDelta, ToolCallEvent, ToolResultEvent
from .approval_policy import ApprovalPolicy, get_approval_policy, ToolCallEvent as PolicyToolCallEvent
from .config.schema import AgentConfig, CodexConfig, WorkflowConfig
from .issue import Issue
from .prompt_builder import PromptBuilder
from .workspace import Workspace

if TYPE_CHECKING:
    from .progress_reporter import ProgressReporter

logger = logging.getLogger(__name__)


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
        }

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

            async for event in runner.stream():
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

                    # In headless (orchestrator) mode the permission system
                    # is bypassed via ToolContext.approval_policy =
                    # "bypassPermissions" + permission_handler = None, so all
                    # tool calls are auto-approved.  The orchestrator's
                    # ApprovalPolicy (ToolCallEvent) is not consulted here —
                    # decisions live in the headless agent loop, not in
                    # QueryRunner.stream() output.
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
                    turn_number += 1
                    session.turn_count = turn_number

                    # Emit PhaseComplete event for progress reporting
                    if progress_reporter is not None:
                        phase_event = PhaseComplete(
                            phase=turn_number,
                            turn_count=turn_number,
                        )
                        progress_reporter.on_event(phase_event, session)

                    if event.reason == "success":
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

            # If we consumed all events without SessionComplete (shouldn't
            # happen with current QueryRunner, but be defensive), count the
            # turn anyway
            if not turn_has_tool_calls and turn_output:
                turn_number += 1
                session.turn_count = turn_number

        # Reached max_turns
        session.status = "completed"
        logger.info(
            "Agent run reached max_turns issue_id=%s turns=%s/%s tools=%s",
            issue.id,
            turn_number,
            self.max_turns,
            tool_count,
        )

        # Emit PhaseComplete event for progress reporting (max_turns path)
        if progress_reporter is not None:
            phase_event = PhaseComplete(
                phase=turn_number,
                turn_count=turn_number,
            )
            progress_reporter.on_event(phase_event, session)

        # Post completion summary to Linear
        await self._post_run_comment(
            session, tool_count, comment_tracker, logger
        )

    async def _post_run_comment(
        self,
        session: AgentSession,
        tool_count: int,
        comment_tracker: Any,
        logger: Any,
    ) -> None:
        """Post a run summary comment to the configured tracker."""
        if comment_tracker is None or not session.issue.id:
            return

        identifier = session.issue.identifier or "unknown"
        status = session.status
        turns = session.turn_count
        output = session.output_text

        # Truncate output excerpt to avoid exceeding tracker comment limits
        excerpt = output[-1500:] if len(output) > 1500 else output
        if excerpt and len(output) > 1500:
            excerpt = f"... (truncated from {len(output)} chars)\n{excerpt}"

        body = (
            f"## ClawCodex Run Complete\n\n"
            f"**Status:** {status}\n"
            f"**Turns:** {turns}\n"
            f"**Tool calls:** {tool_count}\n\n"
            f"**Output excerpt:**\n"
            f"```\n{excerpt}\n```\n"
        )

        try:
            await comment_tracker.create_comment(session.issue.id, body)
            logger.info(
                "Posted completion comment for issue_id=%s",
                session.issue.id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to post completion comment for issue_id=%s: %s",
                session.issue.id,
                exc,
            )

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
            else:
                entry = {
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": str(type(event).__name__),
                }

            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
