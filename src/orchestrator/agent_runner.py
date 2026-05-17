"""Run a single issue through ClawCodex query engine.

Port of Symphony's AgentRunner, replacing Codex JSON-RPC with QueryRunner.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..api.query import QueryConfig, QueryRunner
from ..api.query import SessionComplete, TextDelta, ToolCallEvent, ToolResultEvent
from .approval_policy import ApprovalPolicy, get_approval_policy, ToolCallEvent as PolicyToolCallEvent
from .config.schema import AgentConfig, CodexConfig, WorkflowConfig
from .linear.issue import Issue
from .prompt_builder import PromptBuilder
from .workspace import Workspace

logger = logging.getLogger(__name__)


@dataclass
class AgentSession:
    """One active issue run."""

    issue: Issue
    workspace: Workspace
    turn_count: int = 0
    status: str = "running"  # running, completed, failed
    output_text: str = ""


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

        # Mirror decision back to the caller's event
        if policy_event.is_approved:
            event.allow(policy_event._deny_reason or "")
        else:
            event.deny(policy_event._deny_reason or "policy_denied")
        return event

    async def run(
        self,
        session: AgentSession,
        workflow: WorkflowConfig,
        status_dashboard: Any | None = None,
        tracker: Any = None,
        linear_adapter: Any = None,
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
                prompt = PromptBuilder.render(issue)
            else:
                prompt = PromptBuilder.build_continuation_prompt(
                    turn_number=turn_number,
                    max_turns=self.max_turns,
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
                max_turns=1,  # One turn per QueryRunner call
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

                elif isinstance(event, ToolCallEvent):
                    turn_has_tool_calls = True
                    tool_count += 1
                    # Apply approval policy
                    self._handle_tool_call(event, session_context)
                    logger.debug(
                        "Tool call issue_id=%s tool=%s approved=%s",
                        issue.id,
                        event.tool_name,
                        event.is_approved,
                    )
                    if status_dashboard is not None:
                        try:
                            status_dashboard.on_event(event, session)
                        except Exception:
                            pass

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

                elif isinstance(event, SessionComplete):
                    turn_number += 1
                    session.turn_count = turn_number

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

        # Post completion summary to Linear
        await self._post_run_comment(
            session, tool_count, linear_adapter, logger
        )

    async def _post_run_comment(
        self,
        session: AgentSession,
        tool_count: int,
        linear_adapter: Any,
        logger: Any,
    ) -> None:
        """Post a run summary comment to Linear after session completes."""
        if linear_adapter is None or not session.issue.id:
            return

        identifier = session.issue.identifier or "unknown"
        status = session.status
        turns = session.turn_count
        output = session.output_text

        # Truncate output excerpt to avoid exceeding Linear comment limits
        excerpt = output[-1500:] if len(output) > 1500 else output
        if excerpt and len(output) > 1500:
            excerpt = f"... (truncated from {len(output)} chars)\n{excerpt}"

        body = (
            f"## Symphony Run Complete\n\n"
            f"**Status:** {status}\n"
            f"**Turns:** {turns}\n"
            f"**Tool calls:** {tool_count}\n\n"
            f"**Output excerpt:**\n"
            f"```\n{excerpt}\n```\n"
        )

        try:
            await linear_adapter.create_comment(session.issue.id, body)
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
