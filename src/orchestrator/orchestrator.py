"""Polling engine — GenServer equivalent in Python.

Port of Symphony's Orchestrator.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .agent_runner import AgentRunner, AgentSession, RetryItem
from .config.schema import WorkflowConfig
from .git_sync import GitSyncService
from .issue import Issue
from .status_dashboard import SessionStatus, StatusDashboard
from .tracker import TrackerAdapter
from .workspace import WorkspaceManager

logger = logging.getLogger(__name__)

_CONTINUATION_RETRY_DELAY_MS = 1_000
_FAILURE_RETRY_BASE_MS = 10_000


@dataclass
class OrchestratorState:
    """Runtime state for the orchestrator polling loop."""

    poll_interval_ms: int = 30_000
    max_concurrent_agents: int = 10
    next_poll_due_at_ms: float | None = None
    poll_check_in_progress: bool = False
    running: dict[str, AgentSession] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)
    claimed: set[str] = field(default_factory=set)
    retry_queue: list[RetryItem] = field(default_factory=list)
    retry_attempts: dict[str, int] = field(default_factory=dict)
    codex_totals: dict[str, int] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "seconds_running": 0,
        }
    )


class Orchestrator:
    """Polling engine — GenServer equivalent in Python."""

    def __init__(
        self,
        workflow: WorkflowConfig,
        tracker: TrackerAdapter,
        workspace: WorkspaceManager,
        agent_runner: AgentRunner,
        status_dashboard: StatusDashboard | None = None,
    ) -> None:
        self.workflow = workflow
        self.tracker = tracker
        self.workspace = workspace
        self.agent_runner = agent_runner
        self.status_dashboard = status_dashboard or StatusDashboard()
        self.git_sync = GitSyncService(tracker)
        self._state = OrchestratorState(
            poll_interval_ms=workflow.polling.interval_ms,
            max_concurrent_agents=workflow.agent.max_concurrent_agents,
        )
        self._semaphore = asyncio.Semaphore(workflow.agent.max_concurrent_agents)
        self._shutdown_event = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()

    async def run(self) -> None:
        """Main polling loop. Runs until cancelled."""
        logger.info("Orchestrator starting: interval=%sms max_concurrent=%s",
                    self._state.poll_interval_ms,
                    self._state.max_concurrent_agents)

        # Clean up terminal workspaces on startup
        await self.workspace.run_terminal_workspace_cleanup()

        while not self._shutdown_event.is_set():
            await self._poll_and_dispatch()
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._state.poll_interval_ms / 1000.0,
                )
            except asyncio.TimeoutError:
                pass

        logger.info("Orchestrator shutting down")
        await self._cancel_all_tasks()

    async def shutdown(self) -> None:
        """Signal graceful shutdown."""
        self._shutdown_event.set()

    async def _poll_and_dispatch(self) -> None:
        """Fetch candidates, respect concurrency limit, launch runs."""
        self.status_dashboard.on_poll_start()
        self._state.poll_check_in_progress = True

        try:
            # Process retry queue first
            await self._process_retry_queue()

            # Fetch new candidate issues
            try:
                issues = await self.tracker.fetch_candidate_issues()
            except Exception as exc:
                logger.error("Failed to fetch candidate issues: %s", exc)
                return

            available_slots = (
                self._state.max_concurrent_agents - len(self._state.running)
            )

            for issue in issues[:available_slots]:
                if issue.id in self._state.running or issue.id in self._state.completed:
                    continue
                if issue.id in self._state.claimed:
                    continue
                self._state.claimed.add(issue.id)
                await self._launch_issue(issue)

        finally:
            self._state.poll_check_in_progress = False
            self.status_dashboard.on_poll_end()

    async def _launch_issue(self, issue: Issue) -> None:
        """Create workspace and run agent for one issue."""
        try:
            workspace = await self.workspace.create_for_issue(issue)
        except Exception as exc:
            logger.error(
                "Workspace creation failed issue_id=%s: %s",
                issue.id,
                exc,
            )
            self._state.claimed.discard(issue.id)
            return

        session = AgentSession(issue=issue, workspace=workspace)
        self._state.running[issue.id] = session

        self.status_dashboard.on_session_start(
            SessionStatus(
                issue_id=issue.id or "",
                issue_identifier=issue.identifier or "",
                max_turns=self.agent_runner.max_turns,
                workspace_path=str(workspace.path),
            )
        )

        task = asyncio.create_task(self._run_issue(session))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_issue(self, session: AgentSession) -> None:
        """Run agent for one issue with concurrency control."""
        async with self._semaphore:
            ran_agent = False
            try:
                await self.workspace.run_before_run_hook(
                    session.workspace,
                    session.issue,
                )
                ran_agent = True
                try:
                    await self.agent_runner.run(
                        session,
                        self.workflow,
                        status_dashboard=self.status_dashboard,
                        tracker=self.tracker,
                        comment_tracker=self.tracker,
                    )
                    if session.status == "completed":
                        await self.git_sync.sync(session)
                finally:
                    await self.workspace.run_after_run_hook(
                        session.workspace,
                        session.issue,
                    )
            except Exception as exc:
                logger.exception(
                    "Agent run failed issue_id=%s: %s",
                    session.issue.id,
                    exc,
                )
                session.status = (
                    "before_run_failed" if not ran_agent else "failed"
                )
            finally:
                if session.issue.id in self._state.running:
                    del self._state.running[session.issue.id]

                if session.status == "completed":
                    self.status_dashboard.on_session_complete(session.issue.id or "")
                    self._state.completed.add(session.issue.id or "")
                else:
                    self.status_dashboard.on_session_failed(
                        session.issue.id or "",
                        str(session.status),
                    )
                    # Schedule retry
                    await self._schedule_retry(session)

                # Cleanup workspace
                try:
                    await self.workspace.cleanup(session.issue)
                except Exception as exc:
                    logger.warning(
                        "Workspace cleanup failed issue_id=%s: %s",
                        session.issue.id,
                        exc,
                    )

                self._state.claimed.discard(session.issue.id or "")

    async def _schedule_retry(self, session: AgentSession) -> None:
        """Schedule a retry for a failed session."""
        issue_id = session.issue.id or ""
        attempt = self._state.retry_attempts.get(issue_id, 0) + 1
        self._state.retry_attempts[issue_id] = attempt

        # Exponential backoff capped at max_retry_backoff_ms
        base_ms = _FAILURE_RETRY_BASE_MS
        max_ms = self.workflow.agent.max_retry_backoff_ms
        delay_ms = min(base_ms * (1 << (attempt - 1)), max_ms)

        retry = RetryItem(
            issue_id=issue_id,
            attempt=attempt,
            delay_seconds=delay_ms / 1000.0,
            identifier=session.issue.identifier or "",
            error=f"agent failed: {session.status}",
        )
        self._state.retry_queue.append(retry)
        logger.info(
            "Scheduled retry issue_id=%s attempt=%s delay=%sms",
            issue_id,
            attempt,
            delay_ms,
        )

    async def _process_retry_queue(self) -> None:
        """Process retry queue with exponential backoff.

        Retries are processed before new candidate issues so that
        previously-failed work gets priority.
        """
        import time

        now = time.time()
        ready: list[Any] = []
        remaining: list[Any] = []

        for retry in self._state.retry_queue:
            if now >= retry.scheduled_at + retry.delay_seconds:
                ready.append(retry)
            else:
                remaining.append(retry)

        self._state.retry_queue = remaining

        for retry in ready:
            # Skip if already running or completed
            if retry.issue_id in self._state.running or retry.issue_id in self._state.completed:
                logger.debug("Retry skipped issue_id=%s already running/completed", retry.issue_id)
                continue

            # Check concurrency slot
            if len(self._state.running) >= self._state.max_concurrent_agents:
                logger.debug("Retry deferred issue_id=%s no concurrency slots", retry.issue_id)
                remaining.append(retry)
                continue

            # Re-fetch issue state from tracker
            try:
                issues = await self.tracker.fetch_issue_states_by_ids([retry.issue_id])
                issue = issues.get(retry.issue_id)
                if issue is None:
                    logger.warning("Retry issue not found issue_id=%s", retry.issue_id)
                    continue
            except Exception as exc:
                logger.error("Failed to fetch retry issue %s: %s", retry.issue_id, exc)
                # Put back at end of queue with extended delay
                retry.delay_seconds = min(retry.delay_seconds * 2, self.workflow.agent.max_retry_backoff_ms / 1000.0)
                retry.scheduled_at = now
                remaining.append(retry)
                continue

            # Check if issue is still in active states
            active_states = [
                s.strip().lower()
                for s in (getattr(self.tracker, "active_states", None) or [])
            ]
            if issue.state and issue.state.strip().lower() not in active_states:
                logger.info(
                    "Retry issue %s no longer active (state=%s), dropping",
                    retry.issue_id,
                    issue.state,
                )
                continue

            self._state.claimed.add(retry.issue_id)
            await self._launch_issue(issue)
            logger.info(
                "Retry launched issue_id=%s attempt=%s",
                retry.issue_id,
                retry.attempt,
            )

    async def _cancel_all_tasks(self) -> None:
        """Cancel all running agent tasks."""
        if self._tasks:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
