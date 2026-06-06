"""Polling engine — GenServer equivalent in Python.

Port of Symphony's Orchestrator.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_runner import AgentRunner, AgentSession, RetryItem
from .config.schema import WorkflowConfig
from .debug_log import append_debug_event
from .git_sync import (
    GitSyncPostCommitError,
    GitSyncService,
    HookFailedError,
    VerificationFailed,
)
from .issue import Issue
from .issue_registry import IssueRegistry, IssueStatus
from .prompt_builder import PromptBuilder
from .progress_reporter import ProgressReporter
from .review_feedback import ReviewFeedbackService, ReviewFollowup
from .status_dashboard import SessionStatus, StatusDashboard
from src.tool_system.context import ToolContext
from src.utils.git import get_file_status
from .tracker import (
    Command,
    Intent,
    PullRequestRef,
    TrackerAdapter,
    command_to_intent,
    merge_intents,
)
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
    pending_review: set[str] = field(default_factory=set)  # awaiting human review
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
        self._validate_workspace_strategy()
        self.git_sync = GitSyncService(
            tracker,
            workflow.tracker.branch_prefix,
            workflow.workspace.gitignore_patterns,
            workflow.agent,
            workflow.hooks,
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
        workspace_root = Path(workspace.config.root)
        self._workspace_root = workspace_root
        # Persistent issue→commit→PR mapping (persists across restarts)
        registry_path = workspace_root / ".clawcodex_issue_registry.json"
        self._registry = IssueRegistry(registry_path)

        # Write orchestrator metadata for CLI discovery
        self._metadata_started_at = time.time()
        from .workspace_locator import write_orchestrator_metadata
        write_orchestrator_metadata(
            workspace_root=workspace_root,
            workflow_path=self._workflow_path,
            started_at=self._metadata_started_at,
        )

        # Clarification handling (three-channel flow)
        clarification_queue_path = workspace_root / ".clawcodex_clarification_queue.json"
        from .clarification_queue import ClarificationQueue
        self._clarification_queue = ClarificationQueue(clarification_queue_path)

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
        self._progress_context = ToolContext(workspace_root=workspace_root)
        self._progress_reporter = ProgressReporter(self._progress_context)

    def _validate_workspace_strategy(self) -> None:
        if self.workflow.workspace.strategy != "sequential":
            return
        if self.workflow.agent.max_concurrent_agents != 1:
            raise ValueError(
                "workspace.strategy=sequential requires agent.max_concurrent_agents=1"
            )
        over_limit_states = [
            state
            for state, limit in self.workflow.agent.max_concurrent_agents_by_state.items()
            if limit > 1
        ]
        if over_limit_states:
            raise ValueError(
                "workspace.strategy=sequential requires all "
                "agent.max_concurrent_agents_by_state values to be <= 1"
            )

    def _sync_gitignore_to_workspace(self, workspace: Any) -> None:
        """Write ignore patterns for orchestrator-managed workspace files."""
        workspace_path = Path(workspace.path)
        if self.workflow.workspace.strategy == "sequential":
            ignore_path = workspace_path / ".git" / "info" / "exclude"
        else:
            ignore_path = workspace_path / ".gitignore"
        if not ignore_path.parent.exists():
            return

        patterns = self.git_sync._gitignore_patterns
        existing: set[str] = set()
        if ignore_path.exists():
            existing = {
                line.strip()
                for line in ignore_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            }

        new_patterns = [p for p in patterns if p not in existing]
        if not new_patterns:
            return

        with ignore_path.open("a", encoding="utf-8") as f:
            if ignore_path.exists() and ignore_path.stat().st_size > 0:
                f.write("\n")
            f.write("# ClawCodeX managed — do not edit manually\n")
            for p in new_patterns:
                f.write(f"{p}\n")
        logger.debug("Updated %s with %d patterns", ignore_path, len(new_patterns))

    async def run(self) -> None:
        """Main polling loop. Runs until cancelled."""
        logger.info("Orchestrator starting: interval=%sms max_concurrent=%s",
                    self._state.poll_interval_ms,
                    self._state.max_concurrent_agents)

        # Clean up terminal workspaces on startup
        await self.workspace.run_terminal_workspace_cleanup()
        await self._recover_stale_running_records()

        # Start metadata heartbeat for CLI discovery
        heartbeat_task = asyncio.create_task(self._metadata_heartbeat_loop())
        self._tasks.add(heartbeat_task)

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

    async def _recover_stale_running_records(self) -> None:
        reason = "Recovered stale running issue on orchestrator startup"
        stale_records = self._registry.running_records()
        for record in stale_records:
            self._registry.mark_failed_with_reason(record.issue_id, reason)
            await self._sync_tracker_issue_state(record.issue_id, "failed")
            logger.warning(
                "Recovered stale running issue_id=%s on orchestrator startup",
                record.issue_id,
            )

    async def _metadata_heartbeat_loop(self) -> None:
        """Periodically rewrite metadata so CLI can always discover the orchestrator.

        If metadata.json is accidentally deleted, this recreates it within
        the heartbeat interval (30s), preventing the ``server start`` PID
        guard from being bypassed for a running instance.
        """
        from .workspace_locator import write_orchestrator_metadata

        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=30.0,
                )
                break  # shutdown requested
            except asyncio.TimeoutError:
                pass

            write_orchestrator_metadata(
                workspace_root=self._workspace_root,
                workflow_path=self._workflow_path,
                started_at=self._metadata_started_at,
            )

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

            await self._process_review_feedback()

            # Fetch new candidate issues
            try:
                issues = await self.tracker.fetch_candidate_issues()
            except Exception as exc:
                logger.error("Failed to fetch candidate issues: %s", exc)
                return

            available_slots = (
                self._state.max_concurrent_agents - len(self._state.running)
            )

            # Pre-register all unregistered candidates with QUEUED status
            # so the dashboard / registry reflects the full backlog.
            for issue in issues:
                if not self._registry.get(issue.id or ""):
                    base_branch = (
                        getattr(issue, "base_branch", None)
                        or self.workflow.workspace.base_branch
                        or "main"
                    )
                    self._registry.register(
                        issue_id=issue.id or "",
                        issue_identifier=issue.identifier or "",
                        branch_name=issue.branch_name,
                        base_branch=base_branch,
                        status=IssueStatus.QUEUED,
                    )

            if self.workflow.workspace.strategy == "sequential" and self._state.running:
                return

            launched_this_poll = 0
            for issue in issues:
                if launched_this_poll >= available_slots:
                    break
                if issue.id in self._state.running or issue.id in self._state.completed:
                    continue
                if issue.id in self._state.claimed:
                    continue

                # F-39 Sub-A + Sub-D: intent pre-check happens BEFORE
                # the `has_pr` / `is_completed` skip. Operators can
                # trigger an intent either via labels (Sub-A) or via
                # comment commands (Sub-D). The merged intent here
                # already applies the priority rules from
                # `merge_intents`.
                intent, command_intent_obj = await self._resolve_intent(issue)
                # `command_intent_obj` may carry the comment author
                # for F-39 Sub-F role checks; the bare `Command` value
                # is in `command_intent_obj.command`.
                command = (
                    command_intent_obj.command
                    if command_intent_obj is not None
                    else None
                )
                command_author = (
                    command_intent_obj.author_login
                    if command_intent_obj is not None
                    else None
                )

                # F-39 Sub-F: role check. If a comment command is
                # what triggered the intent, only the issue author or
                # a maintainer (or `allow_anyone_to_retry=True`) is
                # allowed to fire it. The check happens BEFORE the
                # acknowledgement comment is posted, so a rejected
                # command never advances the cursor.
                if (
                    command_intent_obj is not None
                    and intent in (Intent.RETRY, Intent.FOLLOWUP)
                    and not self._is_command_author_eligible(
                        issue, command_author
                    )
                ):
                    await self._reject_unauthorized_command(
                        issue, command_intent_obj
                    )
                    continue

                # F-39 Sub-F: rate limit on RETRY intent. If the issue
                # has hit `max_retries_per_issue`, refuse the reset
                # (even with `--force`; only the label-based retry
                # honors force in the daemon path).
                if intent is Intent.RETRY:
                    if not self._check_retry_rate_limit(issue, force=False):
                        continue

                # F-39 Sub-D: when a comment command is honored, post
                # a bot acknowledgement so the operator sees the
                # intent was received, and record the command on the
                # registry for audit.
                if command is not None:
                    await self._post_command_acknowledgement(issue, command)
                    record = self._registry.get(issue.id or "")
                    if record is not None:
                        record.last_command = f"/agent {command.value}"
                        record.touch()
                        self._registry._save()
                    logger.info(
                        "Issue %s command received: /agent %s",
                        issue.id,
                        command.value,
                    )

                    # UNBLOCK is a meta-command: clear any BLOCKED
                    # state so the next poll re-applies the (now
                    # possibly cleared) label-based intent.
                    if command is Command.UNBLOCK:
                        record = self._registry.get(issue.id or "")
                        if (
                            record is not None
                            and record.status is IssueStatus.ABANDONED
                        ):
                            logger.info(
                                "Issue %s unblocked, status reset to pending",
                                issue.id,
                            )
                            record.status = IssueStatus.PENDING
                            record.intent = Intent.NONE
                            record.intent_source = None
                            self._registry._save()

                if intent is Intent.BLOCKED:
                    logger.info(
                        "Issue %s blocked intent detected, marking abandoned",
                        issue.id,
                    )
                    record = self._registry.get(issue.id or "")
                    if record is None:
                        self._registry.register(
                            issue_id=issue.id or "",
                            issue_identifier=issue.identifier or "",
                            branch_name=getattr(issue, "branch_name", None) or "main",
                        )
                    self._registry.mark_intent(
                        issue.id or "", intent,
                        source=("command" if command is not None else "label"),
                        command=(f"/agent {command.value}" if command is not None else None),
                    )
                    self._registry.mark_abandoned(issue.id or "")
                    await self._sync_tracker_issue_state(issue.id or "", "abandoned")
                    self._state.completed.add(issue.id or "")
                    continue

                if intent is Intent.RETRY:
                    logger.info(
                        "Issue %s retry intent detected, will reset on launch",
                        issue.id,
                    )
                    self._registry.mark_intent(
                        issue.id or "", intent,
                        source=("command" if command is not None else "label"),
                        command=(f"/agent {command.value}" if command is not None else None),
                    )
                    # F-39 Sub-B will perform the actual reset+close.
                elif intent is Intent.FOLLOWUP:
                    logger.info(
                        "Issue %s follow-up intent detected, will reuse branch",
                        issue.id,
                    )
                    self._registry.mark_intent(
                        issue.id or "", intent,
                        source=("command" if command is not None else "label"),
                        command=(f"/agent {command.value}" if command is not None else None),
                    )
                    # F-39 Sub-C will perform the actual follow-up.

                # Skip terminal registry records even if the tracker still
                # exposes the issue in an active state. Explicit retry/follow-up
                # intents are the only daemon path that may reopen handled work.
                if intent is Intent.NONE and (
                    self._registry.is_terminal(issue.id or "")
                    or self._registry.has_pr(issue.id or "")
                ):
                    logger.info("Issue %s already handled (registry), skipping", issue.id)
                    continue
                if not await self._dependencies_satisfied(issue):
                    continue
                self._state.claimed.add(issue.id)
                await self._launch_issue(issue)
                if issue.id in self._state.running:
                    launched_this_poll += 1

        finally:
            self._state.poll_check_in_progress = False
            self.status_dashboard.on_poll_end()

    async def _dependencies_satisfied(self, issue: Issue) -> bool:
        dependencies = [dep for dep in getattr(issue, "depends_on", []) if dep]
        if not dependencies:
            return True

        unresolved = [
            dependency
            for dependency in dependencies
            if not (
                self._registry.is_completed(dependency)
                or self._registry.has_pr(dependency)
            )
        ]
        if unresolved:
            logger.info(
                "Issue %s waiting for dependencies: %s",
                issue.id,
                ", ".join(unresolved),
            )
            return False
        return True

    async def _resolve_intent(
        self, issue: Issue,
    ) -> tuple[Intent, "CommandIntent | None"]:
        """Resolve the current operator intent for an issue.

        Merges two sources (F-39 Sub-A + Sub-D):
          1. Label-based intent (Sub-A: `agent:retry` / `agent:follow-up`
             / `agent:blocked`).
          2. Comment-based command (Sub-D: `/agent retry` / `/agent
             follow-up` / `/agent unblock`).

        Returns the merged `Intent` plus the raw `CommandIntent` (with
        the comment's author login for the F-39 Sub-F role check) if a
        comment command was honored. Priority: BLOCKED is sticky; the
        more conservative of {RETRY, FOLLOWUP} wins; command beats
        label otherwise.
        """
        labels = list(getattr(issue, "labels", None) or [])
        label_intent = Intent.NONE
        if labels:
            try:
                label_intent = await self.tracker.extract_intent_from_labels(labels)
            except Exception as exc:
                logger.warning(
                    "Failed to extract intent from labels for issue %s: %s",
                    issue.id,
                    exc,
                )

        # F-39 Sub-D: comment command intent.
        command_intent_obj = await self._resolve_command_intent(issue)
        command = command_intent_obj.command if command_intent_obj is not None else None
        command_intent = command_to_intent(command) if command is not None else Intent.NONE
        merged = merge_intents(label_intent, command_intent)
        return merged, command_intent_obj

    async def _resolve_command_intent(self, issue: Issue) -> "CommandIntent | None":
        """F-39 Sub-D: fetch and parse the most recent /agent command.

        F-39 Sub-F: the returned `CommandIntent` carries the comment
        author so the caller can perform the role check. Adapters that
        don't expose author info will return `author_login=None`, in
        which case `_is_command_author_eligible` will reject the
        command (fail-closed) to avoid the LLM-self-trigger risk.
        """
        issue_id = issue.id or ""
        if not issue_id:
            return None
        record = self._registry.get(issue_id)
        cursor = record.command_cursor if record is not None else None
        try:
            return await self.tracker.fetch_issue_command_intent(
                issue_id, cursor
            )
        except Exception as exc:
            logger.warning(
                "Failed to fetch issue command intent for %s: %s",
                issue_id,
                exc,
            )
            return None

    async def _post_command_acknowledgement(
        self,
        issue: Issue,
        command: "Command",
    ) -> str | None:
        """F-39 Sub-D: post a bot confirmation comment and update cursor.

        The confirmation comment includes a metadata HTML comment
        with `command_cursor` so the next poll knows where to resume
        scanning. Returns the created comment ID, or None on
        failure.
        """
        issue_id = issue.id or ""
        body = (
            f"## ClawCodex: 已受理 /agent {command.value}\n\n"
            f"下一轮 poll 开始执行。\n"
        )
        try:
            comment = await self.tracker.create_comment(issue_id, body)
        except Exception as exc:
            logger.warning(
                "Failed to post command acknowledgement for %s: %s",
                issue_id,
                exc,
            )
            return None
        comment_id = getattr(comment, "id", None) if comment is not None else None
        if comment_id:
            record = self._registry.get(issue_id)
            if record is not None:
                record.command_cursor = comment_id
                self._registry._save()
        return comment_id

    # ------------------------------------------------------------------
    # F-39 Sub-F: role check + rate-limit guard
    # ------------------------------------------------------------------

    def _is_command_author_eligible(
        self,
        issue: Issue,
        author_login: str | None,
    ) -> bool:
        """Return True if `author_login` may trigger a retry/follow-up.

        Per the F-39 Sub-F design doc: "comment 命令默认要求「issue
        作者」或「仓库 maintainer」才能触发". The check has three
        short-circuits:

          1. `workflow.agent.allow_anyone_to_retry` — disables the
             role check entirely (trusted-team mode).
          2. `author_login` is None — fail-closed. Adapters that
             don't expose author info cannot pass the check; this
             prevents the LLM-self-trigger risk where a bot
             accidentally writes `/agent retry` in its own reply
             and the daemon can't tell it wasn't a human.
          3. The bot itself (`clawcodex`) is always allowed so the
             CLI fallback (`/agent retry` from a local operator
             routed through the bot) isn't rejected. NOTE: the CLI
             path doesn't actually go through this code path; this
             branch is only here to be lenient on platform quirks
             where the bot appears as the author of its own ack
             comment.

        Otherwise, the author must equal the issue author login
        (kept in `IssueRecord.author_login`, populated by the
        clarification flow) or a maintainer login (platform
        metadata; we fall back to None for now and rely on the
        author check).
        """
        if getattr(
            self.workflow.agent, "allow_anyone_to_retry", False
        ):
            return True
        if not author_login:
            # Fail-closed: if we don't know who wrote the command,
            # we cannot certify they are not the LLM itself.
            return False
        if author_login == "clawcodex":
            return True
        record = self._registry.get(issue.id or "")
        issue_author = getattr(record, "author_login", None) if record else None
        return bool(issue_author and author_login == issue_author)

    async def _reject_unauthorized_command(
        self,
        issue: Issue,
        command_intent: "CommandIntent",
    ) -> None:
        """F-39 Sub-F: post a comment rejecting an unauthorized command.

        Per the design acceptance criteria: "用户在 issue comment 发
        `/agent retry`,且非原作者时,**daemon 拒绝执行**并发评论
        `## ClawCodex: 仅 issue 作者或 maintainer 可触发 /agent retry`".
        """
        issue_id = issue.id or ""
        body = (
            f"## ClawCodex: 仅 issue 作者或 maintainer 可触发 "
            f"/agent {command_intent.command.value}\n\n"
            f"author=`{command_intent.author_login or '<unknown>'}` "
            f"not authorized; ignored.\n"
        )
        try:
            await self.tracker.create_comment(issue_id, body)
        except Exception as exc:
            logger.warning(
                "Failed to post unauthorized-command rejection for %s: %s",
                issue_id, exc,
            )
        logger.info(
            "Issue %s command rejected: /agent %s by %s (not authorized)",
            issue_id,
            command_intent.command.value,
            command_intent.author_login,
        )
        self._log_audit_event(
            issue_id=issue_id,
            event="unauthorized_command",
            mode=f"command:{command_intent.command.value}",
            reason="role_check_failed",
            author=command_intent.author_login or "unknown",
        )

    def _check_retry_rate_limit(
        self,
        issue: Issue,
        *,
        force: bool = False,
    ) -> bool:
        """F-39 Sub-F: refuse a RETRY when retry_count >= max_retries_per_issue.

        Returns True if the retry is allowed (and bumps
        `retry_count` for the record), or False if the rate limit
        was hit. The caller is responsible for the actual reset
        work; this helper is a guard.

        On a hit, this method:
          * Logs the rejection.
          * Appends an `agent:retry-rejected` label to the issue
            (best-effort).
          * Posts a comment explaining the rejection.
          * Records a high-priority audit.jsonl entry.
        """
        issue_id = issue.id or ""
        max_retries = getattr(
            self.workflow.agent, "max_retries_per_issue", 3
        )
        record = self._registry.get(issue_id)
        current = record.retry_count if record else 0
        if current < max_retries:
            return True
        if force:
            # `force=True` is reserved for the CLI path, which
            # logs its own audit entry. The daemon path passes
            # `force=False` and is therefore rejected on the
            # `current >= max_retries` branch.
            return True
        # Rate limit hit; do the side-effects.
        logger.warning(
            "Issue %s retry rate limit hit: %d >= %d",
            issue_id, current, max_retries,
        )
        self._log_audit_event(
            issue_id=issue_id,
            event="retry_rejected",
            mode="label:agent:retry",
            reason=f"retry_count={current} >= max_retries_per_issue={max_retries}",
            author="daemon",
        )
        # Best-effort: add the agent:retry-rejected label and
        # post a comment. Failures here are logged but do not
        # change the verdict (False = reject).
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            asyncio.create_task(
                self._post_retry_rejection(issue_id, current, max_retries)
            )
        else:
            asyncio.run(
                self._post_retry_rejection(issue_id, current, max_retries)
            )
        return False

    async def _post_retry_rejection(
        self,
        issue_id: str,
        current: int,
        max_retries: int,
    ) -> None:
        """F-39 Sub-F: best-effort label + comment for rate-limit hits."""
        body = (
            f"## ClawCodex: retry rate limit reached\n\n"
            f"This issue has been retried {current} times "
            f"(limit: {max_retries}). The `agent:retry` label "
            f"is being ignored. Please review manually and "
            f"either remove the label or use "
            f"`clawcodex orchestrator issue retry --id {issue_id} "
            f"--mode reset --force` to bypass.\n"
        )
        try:
            await self.tracker.create_comment(issue_id, body)
        except Exception as exc:
            logger.warning(
                "Failed to post retry-rejection comment for %s: %s",
                issue_id, exc,
            )
        # Adding the rejection label is platform-specific. We use
        # `update_issue_state` as a no-op state-setter and try to
        # pass the label through the same channel; the adapter
        # implementations that support labels will route it.
        try:
            update_labels = getattr(
                self.tracker, "add_label", None
            )
            if callable(update_labels):
                result = update_labels(issue_id, "agent:retry-rejected")
                if hasattr(result, "__await__"):
                    await result
        except Exception as exc:
            logger.warning(
                "Failed to add agent:retry-rejected label to %s: %s",
                issue_id, exc,
            )

    def _log_audit_event(
        self,
        *,
        issue_id: str,
        event: str,
        mode: str,
        reason: str,
        author: str,
    ) -> None:
        """F-39 Sub-F: write a daemon-side audit log entry.

        Best-effort: writes to `~/.clawcodex/orchestrator/audit.jsonl`
        (the same file the CLI uses). Failure to write is logged
        but does not affect the orchestrator's main loop.
        """
        try:
            import json
            import time
            from pathlib import Path
            log_path = Path.home() / ".clawcodex" / "orchestrator" / "audit.jsonl"
            payload = {
                "ts": time.time(),
                "ts_iso": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
                "operator": author,
                "issue_id": issue_id,
                "mode": mode,
                "reason": reason,
                "event": event,
                "force": False,
                "priority": "high",
            }
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(
                "Failed to write daemon audit log: %s", exc,
            )

    async def _prepare_intent_reset(self, issue: Issue) -> None:
        """F-39 Sub-B: apply registry-side reset before launching an issue.

        Reads the persisted intent from the registry (set in
        `_poll_and_dispatch`) and, when intent == RETRY:
          1. Closes the existing remote PR (best-effort; failure is
             logged but does not block the reset).
          2. Calls `reset_for_retry(issue_id)` to clear local
             commit_sha / pr_number / pr_url / report_path / status.

        For Intent.FOLLOWUP, no reset is performed here — Sub-C will
        handle the follow-up commit path inside git_sync.sync().

        For Intent.NONE / Intent.BLOCKED, this is a no-op. The
        BLOCKED case never reaches `_launch_issue` because
        `_poll_and_dispatch` skips it.
        """
        issue_id = issue.id or ""
        if not issue_id:
            return
        record = self._registry.get(issue_id)
        if record is None:
            return
        intent = record.intent
        if intent is not Intent.RETRY:
            return

        # 1. Close the existing PR (best-effort).
        pr_number = record.pr_number
        pr_url = record.pr_url
        if pr_number:
            pr_ref = PullRequestRef(number=pr_number, url=pr_url)
            try:
                closed = await self.tracker.close_pull_request(pr_ref)
                if closed:
                    logger.info(
                        "Issue %s retry: closed remote PR %s",
                        issue_id,
                        pr_number,
                    )
                else:
                    logger.warning(
                        "Issue %s retry: tracker could not close PR %s; "
                        "continuing with local reset",
                        issue_id,
                        pr_number,
                    )
            except Exception as exc:
                logger.warning(
                    "Issue %s retry: close_pull_request raised %s; "
                    "continuing with local reset",
                    issue_id,
                    exc,
                )

        # 2. Reset the local registry entry. retry_count is bumped
        # inside reset_for_retry by default.
        self._registry.reset_for_retry(issue_id)
        logger.info(
            "Issue %s retry: registry reset (attempt %d)",
            issue_id,
            (self._registry.get(issue_id) or record).retry_count,
        )

    def _prepare_intent_session(self, session: AgentSession) -> None:
        """F-39 Sub-C: wire the session for an intent-driven run.

        Called from `_launch_issue` immediately after the AgentSession
        is constructed. Reads the registry's intent field and:

          - Intent.FOLLOWUP → set `run_kind = "agent_followup"`, copy
            the existing PR (number + url) and base_branch onto the
            session, and pin `issue.branch_name` to the registry
            branch so `_ensure_work_branch` reuses it.
          - Intent.RETRY → the registry was already reset by
            `_prepare_intent_reset`; nothing more to do here. The
            session is a fresh issue-style run.
          - Intent.NONE / Intent.BLOCKED → no-op.

        Sub-C mirrors the F-37 review_followup pattern (see
        `_launch_review_followup`): we reuse the same branch + PR
        and append a commit via git_sync(mode="followup").
        """
        issue_id = session.issue.id or ""
        if not issue_id:
            return
        record = self._registry.get(issue_id)
        if record is None or record.intent is not Intent.FOLLOWUP:
            return

        session.run_kind = "agent_followup"

        # Wire the existing PR so git_sync reuses it instead of
        # creating a new one.
        if record.pr_number:
            session.pull_request = PullRequestRef(
                number=record.pr_number,
                url=record.pr_url,
            )

        # Pin base_branch so git_sync.push targets the right base.
        if record.base_branch:
            session.base_branch = record.base_branch

        # Pin issue.branch_name so _ensure_work_branch reuses the
        # existing feature branch (otherwise it would fall back to
        # the default name and create a new one).
        if record.branch_name and hasattr(session.issue, "branch_name"):
            try:
                session.issue.branch_name = record.branch_name
            except Exception:
                # Issue is a frozen dataclass in some contexts; in
                # that case the registry's branch_name still wins
                # because git_sync.sync also reads from the
                # registry-aware session.base_branch.
                logger.debug(
                    "Could not pin issue.branch_name for followup "
                    "issue %s; relying on session.base_branch",
                    issue_id,
                )

        logger.info(
            "Issue %s followup: session wired (branch=%s pr=%s base=%s)",
            issue_id,
            getattr(session.issue, "branch_name", None),
            getattr(getattr(session, "pull_request", None), "number", None),
            session.base_branch,
        )

    async def _process_review_feedback(self) -> None:
        config = self.workflow.review_feedback
        if not config.enabled:
            return
        available_slots = self._state.max_concurrent_agents - len(self._state.running)
        if available_slots <= 0:
            return

        service = ReviewFeedbackService(
            tracker=self.tracker,
            registry=self._registry,
            config=config,
        )
        try:
            followups = await service.collect_followups(available_slots)
        except Exception as exc:
            logger.error("Failed to collect PR review feedback: %s", exc)
            return

        for followup in followups:
            issue_id = followup.issue.id or ""
            if issue_id in self._state.running or issue_id in self._state.claimed:
                continue
            if config.mode != "auto":
                self._registry.mark_feedback_pending(
                    issue_id,
                    [item.id for item in followup.feedback],
                )
                logger.info(
                    "PR feedback pending manual follow-up issue_id=%s feedback_count=%d",
                    issue_id,
                    len(followup.feedback),
                )
                continue
            self._state.claimed.add(issue_id)
            await self._launch_review_followup(followup)

    async def _launch_review_followup(self, followup: ReviewFollowup) -> None:
        issue = followup.issue
        issue.branch_name = followup.record.branch_name
        prompt = PromptBuilder.render_review_feedback(
            issue=issue,
            pull_request=followup.pull_request,
            branch_name=followup.record.branch_name or "",
            feedback=followup.feedback,
        )
        try:
            workspace = await self.workspace.create_for_issue(issue)
        except Exception as exc:
            logger.error("Workspace creation failed for PR follow-up issue_id=%s: %s", issue.id, exc)
            self._state.claimed.discard(issue.id or "")
            return

        session = AgentSession(
            issue=issue,
            workspace=workspace,
            pause_resume_event=asyncio.Event(),
            event_queue=asyncio.Queue(),
            prompt_override=prompt,
            run_kind="review_followup",
        )
        session.pull_request = followup.pull_request
        session.base_branch = followup.record.base_branch
        session.feedback_ids = [item.id for item in followup.feedback]
        self._state.running[issue.id or ""] = session
        if self._registry.mark_running(issue.id or "") is None:
            logger.warning(
                "Review follow-up started without registry record issue_id=%s",
                issue.id,
            )
        followup_record = self._registry.increment_followup_attempt(issue.id or "")
        session.issue_attempt = max(1, getattr(followup.record, "attempt_count", 0) + 1)
        session.followup_attempt = (
            followup_record.followup_attempt_count if followup_record is not None else 1
        )
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

    async def _launch_issue(self, issue: Issue) -> None:
        """Create workspace and run agent for one issue."""
        if not await self._dependencies_satisfied(issue):
            self._state.claimed.discard(issue.id)
            return

        # F-39 Sub-B: if the registry carries a RETRY intent for this
        # issue, close the existing remote PR (best-effort) and reset
        # the local record so the new run starts from a clean slate.
        # This must happen BEFORE workspace creation so the new run
        # does not try to push a follow-up commit to a closed PR.
        await self._prepare_intent_reset(issue)

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
        workspace_strategy = self.workflow.workspace.strategy
        branch_name = getattr(issue, "branch_name", None) or "main"
        base_branch = getattr(issue, "base_branch", None) or self.workflow.workspace.base_branch or "main"
        integration_branch = self.workflow.workspace.integration_branch
        if workspace_strategy == "sequential" and integration_branch:
            branch_name = integration_branch
        start_commit_sha = await self.workspace.current_head(workspace.path)
        base_commit_sha = start_commit_sha if workspace_strategy == "sequential" else None
        previous_issue_id = None
        sequence_index = None
        if workspace_strategy == "sequential":
            previous_record = self._registry.latest_sequential_record()
            previous_issue_id = previous_record.issue_id if previous_record else None
            sequence_index = (previous_record.sequence_index or 0) + 1 if previous_record else 1
        # F-42: in sequential mode the registry's workspace_path must
        # record the configured root (not whatever WorkspaceManager
        # happened to return for the current issue), so that subsequent
        # issues can resolve the previous commit chain against the same
        # path. In isolated / shared modes the per-issue workspace.path
        # is already the canonical location, so keep that.
        recorded_workspace_path = (
            str(self._workspace_root)
            if workspace_strategy == "sequential"
            else str(workspace.path)
        )
        self._registry.register(
            issue_id=issue.id or "",
            issue_identifier=issue.identifier or "",
            branch_name=branch_name,
            base_branch=base_branch,
            workspace_strategy=workspace_strategy,
            workspace_path=recorded_workspace_path,
            base_commit_sha=base_commit_sha,
            start_commit_sha=start_commit_sha,
            previous_issue_id=previous_issue_id,
            sequence_index=sequence_index,
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
        retry_attempt = self._state.retry_attempts.get(issue.id or "", 0)
        session.attempt = retry_attempt + 1
        session.issue_attempt = session.attempt
        session.workspace_strategy = workspace_strategy
        session.workspace_path = str(workspace.path)
        session.start_commit_sha = start_commit_sha
        session.base_commit_sha = base_commit_sha
        session.previous_issue_id = previous_issue_id
        session.sequence_index = sequence_index
        session.integration_branch = integration_branch
        # F-39 Sub-C: if the registry intent is FOLLOWUP, wire the
        # session so the agent + git_sync know to reuse the existing
        # branch / PR rather than create a new run.
        self._prepare_intent_session(session)
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

    async def _sync_tracker_issue_state(self, issue_id: str, state: str) -> None:
        if not issue_id:
            return
        try:
            await self.tracker.update_issue_state(issue_id, state)
        except Exception as exc:
            logger.warning(
                "Failed to sync tracker state issue_id=%s state=%s: %s",
                issue_id,
                state,
                exc,
            )

    def _update_run_diagnostics(self, session: AgentSession) -> None:
        issue_id = session.issue.id or ""
        record = self._registry.update_run_diagnostics(
            issue_id,
            run_id=getattr(session, "run_id", None),
            debug_log_path=getattr(session, "debug_log_path", None),
            turn_count=getattr(session, "turn_count", 0),
            tool_count=getattr(session, "tool_count", 0),
            last_event=getattr(session, "last_agent_event", None),
            last_tool=getattr(session, "last_tool_name", None),
            output_len=len(getattr(session, "output_text", "") or ""),
            timeout_deadline_at=getattr(session, "timeout_deadline_at", None),
            workspace_dirty=getattr(session, "run_workspace_dirty", None),
        )
        if record is None:
            logger.warning(
                "Skipped run diagnostics update because registry record is missing issue_id=%s run_id=%s status=%s",
                issue_id,
                getattr(session, "run_id", None),
                getattr(session, "status", None),
            )

    async def _run_issue(self, session: AgentSession) -> None:
        """Run agent for one issue with concurrency control."""
        async with self._semaphore:
            ran_agent = False
            workspace_dirty: bool | None = None
            try:
                await self.workspace.run_before_run_hook(
                    session.workspace,
                    session.issue,
                )
                ran_agent = True
                try:
                    self._progress_reporter.set_task_id(session.issue.id)
                    run_timeout_seconds = self.workflow.agent.run_timeout_ms / 1000.0
                    session.timeout_deadline_at = time.time() + run_timeout_seconds
                    await asyncio.wait_for(
                        self.agent_runner.run(
                            session,
                            self.workflow,
                            status_dashboard=self.status_dashboard,
                            tracker=self.tracker,
                            comment_tracker=self.tracker,
                            clarification_resolver=self._clarification_resolver,
                            progress_reporter=self._progress_reporter,
                            diagnostics_callback=self._update_run_diagnostics,
                        ),
                        timeout=run_timeout_seconds,
                    )
                    if session.status == "completed":
                        # F-39 Sub-C: a followup run passes mode="followup"
                        # to git_sync so it reuses the existing branch + PR
                        # instead of creating a new one.
                        sync_mode = (
                            "followup"
                            if session.run_kind == "agent_followup"
                            else "default"
                        )
                        sync_result = await self.git_sync.sync(
                            session, mode=sync_mode
                        )
                        if sync_result is not None:
                            self._registry.update_report(
                                session.issue.id or "",
                                report_path=getattr(session, "report_path", None),
                                verification_status=getattr(session, "verification_status", None),
                                verification_output=getattr(session, "verification_output", None),
                                summary_comment_id=getattr(session, "summary_comment_id", None),
                                # F-?? root-cause fix: persist
                                # explicit session-end reason so the
                                # dashboard / verification can
                                # distinguish stagnation / loop from
                                # a clean success path.
                                session_end_reason=getattr(
                                    session, "session_end_reason", None
                                ),
                                session_end_summary=getattr(
                                    session, "session_end_summary", ""
                                ),
                            )
                            if session.run_kind == "review_followup":
                                self._registry.mark_feedback_processed(
                                    session.issue.id or "",
                                    list(getattr(session, "feedback_ids", [])),
                                    commit_sha=sync_result.commit_sha,
                                )
                                await self._reply_to_processed_feedback(session)
                            elif session.run_kind == "agent_followup":
                                # F-39 Sub-C: a follow-up keeps the
                                # existing pr_number / pr_url / status;
                                # only the followup_attempt_count and
                                # last_followup_commit_sha change.
                                self._registry.increment_followup_attempt(
                                    session.issue.id or ""
                                )
                                if sync_result.commit_sha:
                                    record = self._registry.get(
                                        session.issue.id or ""
                                    )
                                    if record is not None:
                                        record.last_followup_commit_sha = (
                                            sync_result.commit_sha
                                        )
                                        self._registry._save()
                                logger.info(
                                    "Issue %s followup committed: %s on %s",
                                    session.issue.id,
                                    sync_result.commit_sha,
                                    sync_result.branch_name,
                                )
                            else:
                                self._registry.mark_synced(
                                    session.issue.id or "",
                                    branch_name=sync_result.branch_name,
                                    commit_sha=sync_result.commit_sha,
                                    pr_number=sync_result.pull_request.number if sync_result.pull_request else None,
                                    pr_url=sync_result.pull_request.url if sync_result.pull_request else None,
                                )
                            # F-44 review gate: after commit, await human review before completion.
                            # Triggered when GitSyncResult.pending_review is True (LocalTracker
                            # by default, or any tracker when agent.review_required=True in workflow).
                            if sync_result.pending_review:
                                if self.workflow.agent.auto_approve:
                                    logger.info(
                                        "Issue %s auto-approved (auto_approve=True) — "
                                        "skipping pending_review gate",
                                        session.issue.id,
                                    )
                                else:
                                    self._registry.mark_pending_review(session.issue.id or "")
                                    await self._sync_tracker_issue_state(
                                        session.issue.id or "", "pending_review"
                                    )
                                    self.status_dashboard.on_session_complete(session.issue.id or "")
                                    self._state.completed.add(session.issue.id or "")
                                    self._state.pending_review.add(session.issue.id or "")
                                    # Do NOT cleanup workspace — human needs to review it
                                    return
                finally:
                    await self.workspace.run_after_run_hook(
                        session.workspace,
                        session.issue,
                    )
            except GitSyncPostCommitError as exc:
                sync_result = exc.result
                self._registry.update_report(
                    session.issue.id or "",
                    report_path=getattr(session, "report_path", None),
                    verification_status=getattr(session, "verification_status", None),
                    verification_output=getattr(session, "verification_output", None),
                    summary_comment_id=getattr(session, "summary_comment_id", None),
                    session_end_reason=getattr(
                        session, "session_end_reason", None
                    ),
                    session_end_summary=getattr(
                        session, "session_end_summary", ""
                    ),
                )
                if session.run_kind == "agent_followup":
                    record = self._registry.get(session.issue.id or "")
                    if record is not None and sync_result.commit_sha:
                        record.last_followup_commit_sha = sync_result.commit_sha
                        self._registry._save()
                elif session.run_kind != "review_followup":
                    self._registry.mark_synced(
                        session.issue.id or "",
                        branch_name=sync_result.branch_name,
                        commit_sha=sync_result.commit_sha,
                        pr_number=(
                            sync_result.pull_request.number
                            if sync_result.pull_request
                            else None
                        ),
                        pr_url=(
                            sync_result.pull_request.url
                            if sync_result.pull_request
                            else None
                        ),
                    )
                logger.warning(
                    "Post-commit sync failed issue_id=%s commit=%s: %s",
                    session.issue.id,
                    sync_result.commit_sha,
                    exc,
                )
                session.status = "verification_failed"
                session.verification_status = "failed"
                session.verification_output = exc.output
                if exc.hook_name:
                    session.last_hook_error = str(exc.cause)
            except VerificationFailed as exc:
                logger.warning(
                    "Verification failed issue_id=%s: %s",
                    session.issue.id,
                    exc,
                )
                session.status = "verification_failed"
                session.verification_status = "failed"
                session.verification_output = exc.output
            except HookFailedError as exc:
                logger.warning(
                    "Hook failed issue_id=%s hook=%s: %s",
                    session.issue.id,
                    exc.hook_name,
                    exc,
                )
                session.status = "verification_failed"
                session.verification_status = "failed"
                session.verification_output = exc.output
                session.last_hook_error = str(exc)
            except asyncio.TimeoutError:
                reason = (
                    "Agent run exceeded configured timeout "
                    f"({self.workflow.agent.run_timeout_ms}ms)"
                )
                logger.warning(
                    "Agent run timed out issue_id=%s timeout_ms=%s",
                    session.issue.id,
                    self.workflow.agent.run_timeout_ms,
                )
                session.status = "agent_timeout"
                session.verification_status = "failed"
                session.verification_output = reason
                session.last_hook_error = reason
                workspace_dirty = bool(get_file_status(str(session.workspace.path)))
                append_debug_event(
                    session.debug_log_path,
                    "orchestrator.timeout",
                    issue_id=session.issue.id,
                    run_id=session.run_id,
                    turn_count=session.turn_count,
                    tool_count=session.tool_count,
                    last_event_type=session.last_agent_event,
                    last_tool=session.last_tool_name,
                    output_len=len(session.output_text),
                    timeout_deadline_at=session.timeout_deadline_at,
                    workspace_dirty=workspace_dirty,
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
                if workspace_dirty is not None:
                    session.run_workspace_dirty = workspace_dirty
                self._update_run_diagnostics(session)

                if session.issue.id in self._state.running:
                    del self._state.running[session.issue.id]

                # F-44 review gate: if the issue is already in pending_review
                # (set by the early return above), skip the final status
                # transition so the outer finally does NOT overwrite it with
                # COMPLETED. The human must run `orchestrator issue review
                # --id ... --approve` to move it to COMPLETED.
                if session.issue.id in self._state.pending_review:
                    # Issue is waiting for human review — do nothing further.
                    # Workspace preservation is handled by the early return.
                    logger.info(
                        "Issue %s left in pending_review state — human review required",
                        session.issue.id,
                    )
                elif session.status == "completed":
                    self.status_dashboard.on_session_complete(session.issue.id or "")
                    self._state.completed.add(session.issue.id or "")
                    self._registry.mark_completed(session.issue.id or "")
                    await self._sync_tracker_issue_state(
                        session.issue.id or "", "completed"
                    )
                elif session.status == "verification_failed":
                    self.status_dashboard.on_session_failed(
                        session.issue.id or "",
                        str(session.status),
                    )
                    self._registry.mark_verification_failed(
                        session.issue.id or "",
                        output=getattr(session, "verification_output", None),
                        hook_error=getattr(session, "last_hook_error", None),
                    )
                    await self._schedule_retry(session)
                elif session.status == "agent_timeout":
                    self.status_dashboard.on_session_failed(
                        session.issue.id or "",
                        str(session.status),
                    )
                    self._registry.mark_failed_with_reason(
                        session.issue.id or "",
                        getattr(session, "last_hook_error", None)
                        or getattr(session, "verification_output", None)
                        or "Agent run timed out",
                    )
                    await self._schedule_retry(session)
                elif session.status == "max_turns_exceeded":
                    self.status_dashboard.on_session_failed(
                        session.issue.id or "",
                        str(session.status),
                    )
                    self._registry.mark_failed(session.issue.id or "")
                    await self._schedule_retry(
                        session,
                        delay_base_ms=self.workflow.agent.max_turns_retry_delay_ms,
                    )
                elif session.status == "rate_limit_circuit_open":
                    # The AgentRunner's 429 backoff circuit breaker tripped
                    # after ``rate_limit_max_retries`` consecutive rate
                    # limit hits. Surface it on the dashboard and hand it
                    # off to the inter-run retry queue with the longest
                    # configured base delay so the provider's rate window
                    # has a chance to reset before the next attempt.
                    backoff_s = (
                        self.workflow.agent.rate_limit_max_backoff_ms
                    )
                    logger.warning(
                        "Rate limit circuit open issue_id=%s — scheduling "
                        "inter-run retry with base delay %dms (session "
                        "spent %.1fs in in-turn backoff across %d hits)",
                        session.issue.id or "",
                        backoff_s,
                        getattr(session, "total_429_backoff_seconds", 0.0),
                        getattr(session, "consecutive_429_count", 0),
                    )
                    self.status_dashboard.on_session_failed(
                        session.issue.id or "",
                        "rate_limit_circuit_open",
                    )
                    self._registry.mark_failed(session.issue.id or "")
                    await self._schedule_retry(
                        session,
                        delay_base_ms=backoff_s,
                    )
                elif session.status in (
                    "stagnation",
                    "loop_detected",
                ):
                    # F-?? root-cause fix: the agent loop detected it
                    # was no longer making progress (stagnation =
                    # consecutive no-op turns; loop_detected = same
                    # tool-call signature repeated within window).
                    # Mark the issue failed with the explicit
                    # session_end_reason so the dashboard / cron tick
                    # can distinguish these from ordinary crashes.
                    logger.warning(
                        "Agent %s issue_id=%s — %s: %s",
                        session.status,
                        session.issue.id or "",
                        getattr(session, "session_end_summary", ""),
                    )
                    self.status_dashboard.on_session_failed(
                        session.issue.id or "",
                        str(session.status),
                    )
                    self._registry.mark_failed(session.issue.id or "")
                    # No retry — same agent will likely repeat the
                    # same loop on retry without human intervention.
                    # The cron tick will mark the issue abandoned on
                    # the next pass and the operator can either
                    # adjust the issue / workflow or skip it.
                else:
                    self.status_dashboard.on_session_failed(
                        session.issue.id or "",
                        str(session.status),
                    )
                    self._registry.mark_failed(session.issue.id or "")
                    # Schedule retry
                    await self._schedule_retry(session)

                # Update summary comment for non-completed paths
                if session.issue.id not in self._state.pending_review:
                    await self._update_issue_summary(session)

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

    async def _update_issue_summary(self, session: AgentSession) -> None:
        """Update the issue summary comment with final status for failure paths."""
        comment_id = getattr(session, "summary_comment_id", None)
        if comment_id is None:
            return
        body_lines = [
            "## ClawCodex Run Summary",
            "",
            f'- Run: `{getattr(session, "run_id", "unknown")}`',
            f'- Status: `{getattr(session, "status", "unknown")}`',
            f'- Turns: {getattr(session, "turn_count", 0)}',
            f'- Tool calls: {getattr(session, "tool_count", 0)}',
        ]
        if getattr(session, "last_hook_error", None):
            body_lines.append(f"- Error: `{session.last_hook_error}`")
        body = "\n".join(body_lines)
        try:
            await self.tracker.update_comment(session.issue.id, comment_id, body)
        except Exception as exc:
            logger.warning("Failed to update summary comment issue_id=%s: %s", session.issue.id, exc)

    async def _reply_to_processed_feedback(self, session: AgentSession) -> None:
        if not self.workflow.review_feedback.reply_to_comments:
            return
        pull_request = getattr(session, "pull_request", None)
        feedback_ids = set(getattr(session, "feedback_ids", []))
        if pull_request is None or not feedback_ids:
            return
        try:
            feedback = await self.tracker.fetch_pull_request_feedback(
                pull_request=pull_request,
                include_ci_failures=False,
            )
        except Exception as exc:
            logger.warning("Failed to refresh feedback for replies issue_id=%s: %s", session.issue.id, exc)
            return
        body = "Handled in the latest ClawCodex follow-up commit."
        for item in feedback:
            if item.id not in feedback_ids:
                continue
            try:
                await self.tracker.reply_to_pull_request_feedback(
                    pull_request=pull_request,
                    feedback=item,
                    body=body,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to reply to PR feedback issue_id=%s feedback_id=%s: %s",
                    session.issue.id,
                    item.id,
                    exc,
                )

    async def _schedule_retry(
        self,
        session: AgentSession,
        *,
        delay_base_ms: int | None = None,
    ) -> None:
        """Schedule a retry for a failed session.

        ``delay_base_ms`` overrides the base delay for the exponential backoff
        curve. When ``None`` the default ``_FAILURE_RETRY_BASE_MS`` is used
        (10s). The orchestrator passes ``workflow.agent.max_turns_retry_delay_ms``
        for ``max_turns_exceeded`` sessions so the longer wait default kicks in
        without forcing all retries to share it.
        """
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
            await self._sync_tracker_issue_state(issue_id, "abandoned")
            return

        # Exponential backoff capped at max_retry_backoff_ms
        base_ms = (
            delay_base_ms
            if delay_base_ms is not None
            else _FAILURE_RETRY_BASE_MS
        )
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
                await self._sync_tracker_issue_state(issue_id, "failed")
                self._state.completed.add(issue_id)
            elif policy == "notify":
                self._registry.mark_failed(issue_id)
                await self._sync_tracker_issue_state(issue_id, "failed")
                self._state.completed.add(issue_id)
                logger.warning("Escalation notify for issue %s", issue_id)
            else:  # skip → mark as abandoned
                self._registry.mark_abandoned(issue_id)
                await self._sync_tracker_issue_state(issue_id, "abandoned")
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
        elif cmd == "retry":
            # Reset pending_review issue for retry with feedback
            logger.info("Retry requested for issue %s", issue_id)
            self._state.pending_review.discard(issue_id)
            self._state.completed.discard(issue_id)
            self._state.claimed.discard(issue_id)
            record = self._registry._records.get(issue_id)
            if record:
                record.status = IssueStatus.PENDING
                record.attempt_count += 1
                self._registry._save()
            logger.info("Issue %s queued for retry (attempt %d)", issue_id, record.attempt_count if record else 1)

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
