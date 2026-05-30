"""Polling engine — GenServer equivalent in Python.

Port of Symphony's Orchestrator.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_runner import AgentRunner, AgentSession, RetryItem
from .config.schema import WorkflowConfig
from .git_sync import GitSyncService
from .issue import Issue
from .issue_registry import IssueRegistry, IssueStatus
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
        self.git_sync = GitSyncService(
            tracker,
            workflow.tracker.branch_prefix,
            workflow.workspace.gitignore_patterns,
        )
        self._state = OrchestratorState(
            poll_interval_ms=workflow.polling.interval_ms,
            max_concurrent_agents=workflow.agent.max_concurrent_agents,
        )
        self._semaphore = asyncio.Semaphore(workflow.agent.max_concurrent_agents)
        self._shutdown_event = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        # Store workflow path for metadata
        self._workflow_path: str | None = getattr(workflow, "_source_path", None)
        # Workspace root for control command polling
        self._workspace_root = workspace.config.root
        # Persistent issue→commit→PR mapping (persists across restarts)
        registry_path = workspace.config.root / ".clawcodex_issue_registry.json"
        self._registry = IssueRegistry(registry_path)

        # Write orchestrator metadata for CLI discovery
        from .workspace_locator import write_orchestrator_metadata
        write_orchestrator_metadata(
            workspace_root=workspace.config.root,
            workflow_path=self._workflow_path,
        )

        # Clarification handling (three-channel flow)
        clarification_queue_path = workspace.config.root / ".clawcodex_clarification_queue.json"
        from .clarification_queue import ClarificationQueue
        self._clarification_queue = ClarificationQueue(clarification_queue_path)

        # Event stream for CLI tail (shared queue directory)
        self._event_stream_dir = workspace.config.root / ".event_streams"
        self._event_stream_dir.mkdir(parents=True, exist_ok=True)
        from .clarification import ClarificationResolver, ClarificationConfig
        self._clarification_resolver = ClarificationResolver(
            clarification_queue=self._clarification_queue,
            tracker=tracker,
            config=ClarificationConfig(
                enabled=getattr(workflow.agent, "clarification_enabled", True),
                timeout_local_seconds=getattr(workflow.agent, "clarification_timeout_local", 30 * 60),
                timeout_author_seconds=getattr(workflow.agent, "clarification_timeout_author", 72 * 3600),
                max_questions_per_issue=getattr(workflow.agent, "max_questions_per_issue", 3),
                operator_priority=getattr(workflow.agent, "clarification_operator_priority", True),
                simultaneous_grace_ms=getattr(workflow.agent, "clarification_simultaneous_grace_ms", 5000),
                escalation=getattr(workflow.agent, "clarification_escalation", "skip"),
            ),
        )

    def _sync_gitignore_to_workspace(self, workspace: Any) -> None:
        """Write .gitignore to workspace root from configured patterns."""
        gitignore_path = Path(workspace.path) / ".gitignore"
        patterns = self.git_sync._gitignore_patterns

        existing: set[str] = set()
        if gitignore_path.exists():
            existing = {line.strip() for line in gitignore_path.read_text().splitlines() if line.strip() and not line.startswith("#")}

        new_patterns = [p for p in patterns if p not in existing]
        if not new_patterns:
            return

        with open(gitignore_path, "a", encoding="utf-8") as f:
            if existing:
                f.write("\n")
            f.write("# ClawCodeX managed — do not edit manually\n")
            for p in new_patterns:
                f.write(f"{p}\n")
        logger.debug("Updated .gitignore in %s with %d patterns", workspace.path, len(new_patterns))

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
        """Signal graceful shutdown and clean up metadata."""
        self._shutdown_event.set()
        # Clean up orchestrator metadata
        from .workspace_locator import clear_orchestrator_metadata
        clear_orchestrator_metadata(self._workspace_root)

    async def _poll_and_dispatch(self) -> None:
        """Fetch candidates, respect concurrency limit, launch runs."""
        self.status_dashboard.on_poll_start()
        self._state.poll_check_in_progress = True

        try:
            # Process lifecycle control commands (pause/resume/stop/takeover)
            await self._process_control_commands()

            # Poll clarification answers (Channel 2 + Channel 3)
            await self._clarification_resolver.poll_clarification_answers()

            # Process retry queue first
            await self._process_retry_queue()

            # Handle escalated (clarification-exhausted) issues
            await self._process_escalated_issues()

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
                # Skip if registry marks this issue as already completed or has a PR
                if self._registry.is_completed(issue.id) or self._registry.has_pr(issue.id):
                    logger.info("Issue %s already handled (registry), skipping", issue.id)
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

        # Register as pending so restart won't re-launch this issue
        branch_name = getattr(issue, "branch_name", None) or "main"
        base_branch = getattr(issue, "base_branch", "main") or "main"
        self._registry.register(
            issue_id=issue.id or "",
            issue_identifier=issue.identifier or "",
            branch_name=branch_name,
            base_branch=base_branch,
        )

        # Pre-check: verify issue is still in an active state and has no
        # existing PR (which would mean it was already handled) before running agent
        try:
            refreshed = await self.tracker.fetch_issue_states_by_ids([issue.id])
            refreshed_issue = refreshed.get(issue.id)
            if refreshed_issue is None:
                logger.info("Issue %s no longer exists, skipping", issue.id)
                self._state.claimed.discard(issue.id)
                return
            active_states = [
                s.strip().lower()
                for s in (getattr(self.tracker, "active_states", None) or [])
            ]
            is_active = (
                refreshed_issue.state is not None
                and refreshed_issue.state.strip().lower() in active_states
            )
            if not is_active:
                logger.info(
                    "Issue %s is no longer active (state=%r), skipping",
                    issue.id,
                    refreshed_issue.state,
                )
                self._state.claimed.discard(issue.id)
                return
            # Check for existing PR (only for repository-backed trackers)
            branch_name = refreshed_issue.branch_name
            if branch_name and hasattr(self.tracker, "find_pull_request"):
                base_branch = getattr(refreshed_issue, "base_branch", "main") or "main"
                existing_pr = await self.tracker.find_pull_request(
                    head_branch=branch_name,
                    base_branch=base_branch,
                )
                if existing_pr is not None:
                    logger.info(
                        "Issue %s already has PR %s (%s), skipping",
                        issue.id,
                        existing_pr.number,
                        existing_pr.url,
                    )
                    self._state.claimed.discard(issue.id)
                    # Also add to completed so we don't re-process after restart
                    self._state.completed.add(issue.id)
                    return
            # Update issue with latest state
            issue.state = refreshed_issue.state
        except Exception as exc:
            logger.warning(
                "Could not verify issue state for %s: %s — proceeding anyway",
                issue.id,
                exc,
            )

        session = AgentSession(
            issue=issue,
            workspace=workspace,
            pause_resume_event=asyncio.Event(),
            event_queue=asyncio.Queue(),
        )
        self._state.running[issue.id] = session

        # Update persistent registry so `issue list` reflects running state
        self._registry.mark_running(issue.id or "")

        # Sync .gitignore to workspace so unwanted files are excluded from commit
        self._sync_gitignore_to_workspace(session.workspace)

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
                        clarification_resolver=self._clarification_resolver,
                    )
                    if session.status == "completed":
                        sync_result = await self.git_sync.sync(session)
                        if sync_result is not None:
                            self._registry.mark_synced(
                                session.issue.id or "",
                                branch_name=sync_result.branch_name,
                                commit_sha=sync_result.commit_sha,
                                pr_number=sync_result.pull_request.number if sync_result.pull_request else None,
                                pr_url=sync_result.pull_request.url if sync_result.pull_request else None,
                            )
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
                    self._registry.mark_completed(session.issue.id or "")
                else:
                    self.status_dashboard.on_session_failed(
                        session.issue.id or "",
                        str(session.status),
                    )
                    self._registry.mark_failed(session.issue.id or "")
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

        max_attempts = self.workflow.agent.max_retry_attempts
        if max_attempts and attempt > max_attempts:
            logger.warning(
                "Retry limit reached issue_id=%s attempts=%d max=%d — giving up",
                issue_id,
                attempt,
                max_attempts,
            )
            self._state.claimed.discard(issue_id)
            self._registry.mark_abandoned(issue_id)
            return

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

    async def _process_escalated_issues(self) -> None:
        """Check for clarification-exhausted issues and apply escalation policy.

        When a clarification item is marked EXHAUSTED, the escalation policy
        determines what happens next:
          - skip: mark as ABANDONED so orchestrator skips it on next poll
          - mark_failed: mark as FAILED
          - notify: mark as FAILED + send notification
        """
        import json

        sentinel_path = self._workspace_root / ".escalated_issues.json"
        if not sentinel_path.exists():
            return

        try:
            data = json.loads(sentinel_path.read_text())
        except Exception:
            return

        if not data:
            return

        # Collect IDs to remove from sentinel
        to_remove = []

        for issue_id in data:
            if issue_id in self._state.completed or issue_id in self._state.claimed:
                to_remove.append(issue_id)
                continue

            policy = self._clarification_resolver._config.escalation
            if policy == "mark_failed":
                self._registry.mark_failed(issue_id)
                self._state.completed.add(issue_id)
            elif policy == "notify":
                self._registry.mark_failed(issue_id)
                self._state.completed.add(issue_id)
                logger.warning("Escalation notify for issue %s", issue_id)
            else:  # skip → mark as abandoned
                self._registry.mark_abandoned(issue_id)
                self._state.completed.add(issue_id)
                logger.info("Escalation skip for issue %s", issue_id)

            to_remove.append(issue_id)

        # Prune processed entries from sentinel
        if to_remove:
            for issue_id in to_remove:
                data.pop(issue_id, None)
            sentinel_path.write_text(json.dumps(data, indent=2))

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

    async def _process_control_commands(self) -> None:
        """Process lifecycle control commands from CLI.

        Checks the control directory for pause/resume/stop/takeover commands
        written by `clawcodex orchestrator pause/resume/stop/takeover`.
        """
        import os

        control_dir = self._workspace_root / ".orchestrator_control"
        if not control_dir.exists():
            return

        try:
            for control_file in control_dir.iterdir():
                if not control_file.name.endswith(".control"):
                    continue
                parts = control_file.read_text(encoding="utf-8").strip().split("\n")
                if not parts:
                    continue
                cmd = parts[0].strip()
                issue_id = parts[1].strip() if len(parts) > 1 else ""
                extra = parts[2].strip() if len(parts) > 2 else ""

                try:
                    self._apply_control_command(cmd, issue_id, extra)
                finally:
                    # Clean up control file after processing
                    try:
                        control_file.unlink()
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Failed to process control commands: %s", exc)

    def _apply_control_command(self, cmd: str, issue_id: str, extra: str) -> None:
        """Apply a single control command to a running session."""
        if not issue_id or issue_id not in self._state.running:
            logger.debug("Control %s for unknown issue %s", cmd, issue_id)
            return

        session = self._state.running[issue_id]
        if cmd == "pause":
            session.paused = True
            session.pause_reason = extra or "operator requested pause"
            session.pause_resume_event.clear()
            logger.info("Paused issue %s: %s", issue_id, session.pause_reason)
        elif cmd == "resume":
            session.paused = False
            session.pause_resume_event.set()
            logger.info("Resumed issue %s", issue_id)
        elif cmd == "stop":
            # Request cancellation via task cancel
            logger.info("Stop requested for issue %s", issue_id)
            session.status = "failed"
            session.pause_resume_event.set()  # Unblock if paused
        elif cmd == "takeover":
            logger.info("Takeover requested for issue %s", issue_id)
            session.status = "failed"
            session.pause_resume_event.set()  # Unblock if paused
            # Note: REPL takeover requires full session context - handled separately

    def get_event_stream(self, issue_id: str) -> "asyncio.Queue | None":
        """Get the event queue for a running issue session (for CLI tail)."""
        session = self._state.running.get(issue_id)
        if session is None:
            return None
        return session.event_queue

    async def _cancel_all_tasks(self) -> None:
        """Cancel all running agent tasks."""
        if self._tasks:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
