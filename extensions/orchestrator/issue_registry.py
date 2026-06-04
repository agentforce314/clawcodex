"""Local issue→commit→PR mapping registry.

Persists the mapping so that after an orchestrator restart, previously
handled issues can still be identified even if they are no longer in
memory and the tracker API does not reflect the latest state (e.g.
issue still open on the tracker, but a PR already exists on the branch
that was created and abandoned by a previous run).

File format: JSON, stored at `{workspace.root}/.clawcodex_issue_registry.json`
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from pathlib import Path

from .tracker import Intent

logger = logging.getLogger(__name__)


class IssueStatus(str, Enum):
    """Lifecycle stages of a tracked issue."""

    QUEUED = "queued"             # in candidate queue, awaiting dispatch
    PENDING = "pending"           # claimed, workspace created, not yet synced
    RUNNING = "running"          # agent session actively processing
    SYNCED = "synced"             # git sync completed (commit + push + PR)
    PENDING_REVIEW = "pending_review"  # awaiting human review (LocalTracker only)
    COMPLETED = "completed"       # session finished successfully
    FAILED = "failed"            # session ended with a non-success status
    ABANDONED = "abandoned"      # retry limit reached, gave up
    VERIFICATION_FAILED = "verification_failed"


TERMINAL_STATUSES = frozenset(
    {
        IssueStatus.COMPLETED,
        IssueStatus.FAILED,
        IssueStatus.ABANDONED,
        IssueStatus.VERIFICATION_FAILED,
    }
)


@dataclass
class IssueRecord:
    """One entry in the issue registry."""

    issue_id: str
    issue_identifier: str
    branch_name: str | None = None
    commit_sha: str | None = None
    pr_number: str | None = None
    pr_url: str | None = None
    base_branch: str = "main"
    workspace_strategy: str | None = None
    workspace_path: str | None = None
    base_commit_sha: str | None = None
    start_commit_sha: str | None = None
    previous_issue_id: str | None = None
    sequence_index: int | None = None
    status: IssueStatus = IssueStatus.PENDING
    report_path: str | None = None
    verification_status: str | None = None
    verification_output: str | None = None
    last_hook_error: str | None = None
    summary_comment_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    attempt_count: int = 0
    # Clarification-related fields (for three-channel clarification flow)
    clarification_status: str | None = None   # ClarificationStatus value
    question_history: list[str] = field(default_factory=list)
    author_login: str | None = None
    local_answer: str | None = None
    local_answer_source: str | None = None    # "dashboard" | "clarification_queue"
    first_response_source: str | None = None  # "local" | "author"
    stale_answers: list[str] = field(default_factory=list)
    processed_feedback_ids: list[str] = field(default_factory=list)
    pending_feedback_ids: list[str] = field(default_factory=list)
    feedback_cursor: str | None = None
    followup_attempt_count: int = 0
    last_followup_commit_sha: str | None = None
    last_feedback_checked_at: float | None = None
    # F-39: operator intent + retry bookkeeping. These fields are
    # absent from registry.json files written before F-39, so they
    # default to NONE / 0 / None and the existing _load() filter
    # (known_fields) handles back-compat transparently.
    intent: Intent = Intent.NONE
    retry_count: int = 0
    last_command: str | None = None
    intent_source: str | None = None  # "label" | "command" | "cli"
    # F-39 Sub-D: comment-command incremental-scan cursor. Set to
    # the bot's confirmation-comment ID after a command is honored;
    # the next poll uses it as `since_comment_id` so the same
    # command isn't re-processed.
    command_cursor: str | None = None
    run_id: str | None = None
    debug_log_path: str | None = None
    run_turn_count: int = 0
    run_tool_count: int = 0
    run_last_event: str | None = None
    run_last_tool: str | None = None
    run_output_len: int = 0
    run_timeout_deadline_at: float | None = None
    run_workspace_dirty: bool | None = None

    def touch(self) -> None:
        self.updated_at = time.time()


class IssueRegistry:
    """Persistent issue→commit→PR mapping, stored as JSON."""

    def __init__(self, storage_path: Path) -> None:
        self._path = storage_path
        self._records: dict[str, IssueRecord] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._records = {}
            for k, v in data.items():
                # Convert status string to IssueStatus enum
                if isinstance(v.get("status"), str):
                    v = dict(v)
                    try:
                        v["status"] = IssueStatus(v["status"])
                    except ValueError:
                        v["status"] = IssueStatus.PENDING
                # Convert intent string to Intent enum (F-39 back-compat:
                # records written before F-39 have no `intent` field, so
                # the dict-comprehension filter below drops it and the
                # dataclass default Intent.NONE kicks in).
                if isinstance(v.get("intent"), str):
                    v = dict(v)
                    try:
                        v["intent"] = Intent(v["intent"])
                    except ValueError:
                        v["intent"] = Intent.NONE
                known_fields = {field.name for field in fields(IssueRecord)}
                self._records[k] = IssueRecord(
                    **{field_name: value for field_name, value in v.items() if field_name in known_fields}
                )
        except Exception as exc:
            logger.warning("Failed to load issue registry: %s — starting fresh", exc)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(
                    {k: asdict(v) for k, v in self._records.items()},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to save issue registry: %s", exc)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, issue_id: str) -> IssueRecord | None:
        return self._records.get(issue_id)

    def get_by_branch(self, branch_name: str) -> IssueRecord | None:
        for record in self._records.values():
            if record.branch_name == branch_name:
                return record
        return None

    def has_pr(self, issue_id: str) -> bool:
        record = self._records.get(issue_id)
        return record is not None and record.pr_number is not None

    def is_completed(self, issue_id: str) -> bool:
        record = self._records.get(issue_id)
        return record is not None and record.status == IssueStatus.COMPLETED

    def is_terminal(self, issue_id: str) -> bool:
        record = self._records.get(issue_id)
        return record is not None and record.status in TERMINAL_STATUSES

    def iter_records_with_pr(self) -> list[IssueRecord]:
        return [
            record
            for record in self._records.values()
            if record.pr_number and record.branch_name
        ]

    def latest_sequential_record(self) -> IssueRecord | None:
        sequential_records = (
            record
            for record in self._records.values()
            if record.workspace_strategy == "sequential"
            and record.sequence_index is not None
        )
        return max(
            sequential_records,
            key=lambda record: record.sequence_index or 0,
            default=None,
        )

    def running_records(self) -> list[IssueRecord]:
        return [
            record
            for record in self._records.values()
            if record.status == IssueStatus.RUNNING
        ]

    def has_processed_feedback(self, issue_id: str, feedback_id: str) -> bool:
        record = self._records.get(issue_id)
        return record is not None and feedback_id in record.processed_feedback_ids

    def can_follow_up(self, issue_id: str, max_attempts: int) -> bool:
        record = self._records.get(issue_id)
        if record is None:
            return False
        return record.followup_attempt_count < max_attempts

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def register(
        self,
        issue_id: str,
        issue_identifier: str,
        branch_name: str | None = None,
        base_branch: str = "main",
        workspace_strategy: str | None = None,
        workspace_path: str | None = None,
        base_commit_sha: str | None = None,
        start_commit_sha: str | None = None,
        previous_issue_id: str | None = None,
        sequence_index: int | None = None,
        status: IssueStatus | None = None,
    ) -> IssueRecord:
        """Create a pending record for a newly claimed issue."""
        record = IssueRecord(
            issue_id=issue_id,
            issue_identifier=issue_identifier,
            branch_name=branch_name,
            base_branch=base_branch,
            workspace_strategy=workspace_strategy,
            workspace_path=workspace_path,
            base_commit_sha=base_commit_sha,
            start_commit_sha=start_commit_sha,
            previous_issue_id=previous_issue_id,
            sequence_index=sequence_index,
            status=status or IssueStatus.PENDING,
        )
        self._records[issue_id] = record
        self._save()
        return record

    def mark_synced(
        self,
        issue_id: str,
        *,
        branch_name: str | None = None,
        commit_sha: str | None = None,
        pr_number: str | None = None,
        pr_url: str | None = None,
    ) -> IssueRecord | None:
        """Update record after git sync has run."""
        record = self._records.get(issue_id)
        if record is None:
            return None
        if branch_name is not None:
            record.branch_name = branch_name
        if commit_sha is not None:
            record.commit_sha = commit_sha
        if pr_number is not None:
            record.pr_number = pr_number
        if pr_url is not None:
            record.pr_url = pr_url
        record.status = IssueStatus.SYNCED
        record.touch()
        self._save()
        return record

    def mark_running(self, issue_id: str) -> IssueRecord | None:
        """Mark an issue as actively running by an agent session."""
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.status = IssueStatus.RUNNING
        record.touch()
        self._save()
        return record

    def update_run_diagnostics(
        self,
        issue_id: str,
        *,
        run_id: str | None = None,
        debug_log_path: str | None = None,
        turn_count: int | None = None,
        tool_count: int | None = None,
        last_event: str | None = None,
        last_tool: str | None = None,
        output_len: int | None = None,
        timeout_deadline_at: float | None = None,
        workspace_dirty: bool | None = None,
    ) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        if run_id is not None:
            record.run_id = run_id
        if debug_log_path is not None:
            record.debug_log_path = debug_log_path
        if turn_count is not None:
            record.run_turn_count = turn_count
        if tool_count is not None:
            record.run_tool_count = tool_count
        if last_event is not None:
            record.run_last_event = last_event
        if last_tool is not None:
            record.run_last_tool = last_tool
        if output_len is not None:
            record.run_output_len = output_len
        if timeout_deadline_at is not None:
            record.run_timeout_deadline_at = timeout_deadline_at
        if workspace_dirty is not None:
            record.run_workspace_dirty = workspace_dirty
        record.touch()
        self._save()
        return record

    def mark_pending_review(self, issue_id: str) -> IssueRecord | None:
        """Mark an issue as awaiting human review (LocalTracker git commit done)."""
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.status = IssueStatus.PENDING_REVIEW
        record.touch()
        self._save()
        return record

    def mark_completed(self, issue_id: str) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.status = IssueStatus.COMPLETED
        record.touch()
        self._save()
        return record

    def mark_failed(self, issue_id: str) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.status = IssueStatus.FAILED
        record.attempt_count += 1
        record.touch()
        self._save()
        return record

    def mark_failed_with_reason(
        self,
        issue_id: str,
        reason: str,
    ) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.status = IssueStatus.FAILED
        record.verification_status = "failed"
        record.verification_output = reason
        record.last_hook_error = reason
        record.attempt_count += 1
        record.touch()
        self._save()
        return record

    def mark_verification_failed(
        self,
        issue_id: str,
        *,
        output: str | None = None,
        hook_error: str | None = None,
    ) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.status = IssueStatus.VERIFICATION_FAILED
        record.verification_status = "failed"
        record.verification_output = output
        record.last_hook_error = hook_error
        record.attempt_count += 1
        record.touch()
        self._save()
        return record

    def mark_abandoned(self, issue_id: str) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.status = IssueStatus.ABANDONED
        record.touch()
        self._save()
        return record

    def update_branch(self, issue_id: str, branch_name: str) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.branch_name = branch_name
        record.touch()
        self._save()
        return record

    def update_report(
        self,
        issue_id: str,
        *,
        report_path: str | None = None,
        verification_status: str | None = None,
        verification_output: str | None = None,
        summary_comment_id: str | None = None,
    ) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        if report_path is not None:
            record.report_path = report_path
        if verification_status is not None:
            record.verification_status = verification_status
        if verification_output is not None:
            record.verification_output = verification_output
        if summary_comment_id is not None:
            record.summary_comment_id = summary_comment_id
        record.touch()
        self._save()
        return record

    def mark_feedback_pending(
        self,
        issue_id: str,
        feedback_ids: list[str],
        *,
        cursor: str | None = None,
    ) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        seen = set(record.pending_feedback_ids)
        for feedback_id in feedback_ids:
            if feedback_id not in seen and feedback_id not in record.processed_feedback_ids:
                record.pending_feedback_ids.append(feedback_id)
                seen.add(feedback_id)
        if cursor is not None:
            record.feedback_cursor = cursor
        record.last_feedback_checked_at = time.time()
        record.touch()
        self._save()
        return record

    def mark_feedback_processed(
        self,
        issue_id: str,
        feedback_ids: list[str],
        *,
        commit_sha: str | None = None,
        cursor: str | None = None,
    ) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        processed = set(record.processed_feedback_ids)
        for feedback_id in feedback_ids:
            if feedback_id not in processed:
                record.processed_feedback_ids.append(feedback_id)
                processed.add(feedback_id)
        record.pending_feedback_ids = [
            feedback_id
            for feedback_id in record.pending_feedback_ids
            if feedback_id not in processed
        ]
        if commit_sha is not None:
            record.last_followup_commit_sha = commit_sha
        if cursor is not None:
            record.feedback_cursor = cursor
        record.last_feedback_checked_at = time.time()
        record.touch()
        self._save()
        return record

    def increment_followup_attempt(self, issue_id: str) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.followup_attempt_count += 1
        record.touch()
        self._save()
        return record

    def mark_feedback_checked(self, issue_id: str) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.last_feedback_checked_at = time.time()
        record.touch()
        self._save()
        return record

    # ------------------------------------------------------------------
    # Clarification field mutations (for three-channel flow)
    # ------------------------------------------------------------------

    def update_clarification(
        self,
        issue_id: str,
        *,
        clarification_status: str | None = None,
        question: str | None = None,
        author_login: str | None = None,
        local_answer: str | None = None,
        local_answer_source: str | None = None,
        first_response_source: str | None = None,
    ) -> IssueRecord | None:
        """Update clarification-related fields on an issue record."""
        record = self._records.get(issue_id)
        if record is None:
            return None
        if clarification_status is not None:
            record.clarification_status = clarification_status
        if question is not None:
            record.question_history.append(question)
        if author_login is not None:
            record.author_login = author_login
        if local_answer is not None:
            record.local_answer = local_answer
        if local_answer_source is not None:
            record.local_answer_source = local_answer_source
        if first_response_source is not None:
            record.first_response_source = first_response_source
        record.touch()
        self._save()
        return record

    def add_stale_answer(self, issue_id: str, stale_answer: str) -> IssueRecord | None:
        """Record a stale (rejected) answer for notification."""
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.stale_answers.append(stale_answer)
        record.touch()
        self._save()
        return record

    # ------------------------------------------------------------------
    # F-39 intent + retry bookkeeping
    # ------------------------------------------------------------------

    def mark_intent(
        self,
        issue_id: str,
        intent: Intent,
        *,
        source: str | None = None,
        command: str | None = None,
    ) -> IssueRecord | None:
        """Record an operator intent (F-39 Sub-A) on an existing record.

        If the record does not exist yet, this is a no-op — the orchestrator
        creates the record on first claim via `register()`. Callers that need
        to capture intent on a brand-new issue should call `register()` first.

        `source` is informational ("label" | "command" | "cli") and is
        persisted on the record for audit purposes.
        """
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.intent = intent
        if source is not None:
            record.intent_source = source
        if command is not None:
            record.last_command = command
        record.touch()
        self._save()
        return record

    def clear_intent(
        self,
        issue_id: str,
        *,
        record_intent_history: bool = False,
    ) -> IssueRecord | None:
        """Reset intent back to NONE (F-39 Sub-A).

        Used by Sub-B / Sub-C after the intent has been honored (reset
        succeeded / follow-up commit landed). If the record doesn't
        exist, returns None.
        """
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.intent = Intent.NONE
        if not record_intent_history:
            record.intent_source = None
        record.touch()
        self._save()
        return record

    def increment_retry_count(self, issue_id: str) -> IssueRecord | None:
        """Bump retry_count by one (F-39 Sub-A → Sub-F rate limiting)."""
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.retry_count += 1
        record.touch()
        self._save()
        return record

    def reset_for_retry(
        self,
        issue_id: str,
        *,
        increment_retry: bool = True,
    ) -> IssueRecord | None:
        """F-39 Sub-B: clear transient PR / commit state for a retry.

        Per the design doc: "对本地 IssueRecord ... 清空 status → pending,
        删 commit_sha / pr_number / pr_url / report_path".

        `retry_count` is incremented (unless caller passes
        `increment_retry=False` — useful for tests / CLI dry-runs).
        The intent field is preserved so audit trails can still
        answer "why was this re-run?" after the new run completes.
        """
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.status = IssueStatus.PENDING
        record.commit_sha = None
        record.pr_number = None
        record.pr_url = None
        record.report_path = None
        record.summary_comment_id = None
        record.verification_status = None
        record.verification_output = None
        record.last_hook_error = None
        if increment_retry:
            record.retry_count += 1
        record.touch()
        self._save()
        return record

    def unblock(self, issue_id: str) -> IssueRecord | None:
        """F-39 Sub-E: roll an ABANDONED issue back to PENDING.

        Used by the CLI ``issue retry --mode unblock`` fallback and
        by the orchestrator's UNBLOCK comment-command handler. Per
        the design doc: "IssueRegistry 增 unblock(issue_id) 方法
        (把 abandoned 状态回滚)".

        Behaviour:
          * If the record doesn't exist, returns None.
          * If the record exists and is in ABANDONED, flip status
            back to PENDING and clear intent.
          * For any other status this is a no-op (intentionally
            idempotent — calling unblock on a healthy issue is fine).

        Note: `retry_count` is NOT touched, so the rate limit
        still applies to the next retry attempt after unblock.
        """
        record = self._records.get(issue_id)
        if record is None:
            return None
        if record.status is IssueStatus.ABANDONED:
            record.status = IssueStatus.PENDING
        record.intent = Intent.NONE
        record.intent_source = None
        record.last_command = None
        record.touch()
        self._save()
        return record