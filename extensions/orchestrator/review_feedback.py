"""Pull request review feedback follow-up planning."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .issue import Issue
from .issue_registry import IssueRecord, IssueRegistry
from .tracker import PullRequestFeedback, PullRequestRef, TrackerAdapter


@dataclass(frozen=True)
class ReviewFollowup:
    issue: Issue
    record: IssueRecord
    pull_request: PullRequestRef
    feedback: list[PullRequestFeedback]
    prompt: str


class ReviewFeedbackService:
    """Find PR feedback that should trigger a follow-up agent run."""

    def __init__(
        self,
        *,
        tracker: TrackerAdapter,
        registry: IssueRegistry,
        config: object,
    ) -> None:
        self.tracker = tracker
        self.registry = registry
        self.config = config

    async def collect_followups(self, available_slots: int) -> list[ReviewFollowup]:
        if available_slots <= 0 or not getattr(self.config, "enabled", False):
            return []

        followups: list[ReviewFollowup] = []
        for record in self.registry.iter_records_with_pr():
            if len(followups) >= available_slots:
                break
            if not self.registry.can_follow_up(
                record.issue_id,
                getattr(self.config, "max_followup_attempts_per_pr", 5),
            ):
                continue

            pull_request = PullRequestRef(
                number=record.pr_number,
                url=record.pr_url,
            )
            feedback = await self.tracker.fetch_pull_request_feedback(
                pull_request=pull_request,
                include_ci_failures=getattr(self.config, "include_ci_failures", True),
                max_log_chars_per_check=getattr(self.config, "max_log_chars_per_check", 12_000),
            )
            pending = self._filter_pending(record, feedback)
            if not pending:
                self.registry.mark_feedback_checked(record.issue_id)
                continue

            limit = getattr(self.config, "max_feedback_items_per_run", 20)
            selected = pending[:limit]
            self.registry.mark_feedback_pending(
                record.issue_id,
                [item.id for item in selected],
                cursor=selected[-1].updated_at or selected[-1].created_at or selected[-1].id,
            )
            issue = Issue(
                id=record.issue_id,
                identifier=record.issue_identifier,
                title=record.issue_identifier,
                branch_name=record.branch_name,
            )
            followups.append(
                ReviewFollowup(
                    issue=issue,
                    record=record,
                    pull_request=pull_request,
                    feedback=selected,
                    prompt="",
                )
            )
        return followups

    def _filter_pending(
        self,
        record: IssueRecord,
        feedback: list[PullRequestFeedback],
    ) -> list[PullRequestFeedback]:
        ignored_authors = {
            author.strip().lower()
            for author in getattr(self.config, "ignore_authors", [])
            if author.strip()
        }
        processed = set(record.processed_feedback_ids)
        already_pending = set(record.pending_feedback_ids)
        pending: list[PullRequestFeedback] = []
        for item in feedback:
            if item.id in processed or item.id in already_pending:
                continue
            if item.status in {"resolved", "outdated"}:
                continue
            if item.author_login and item.author_login.strip().lower() in ignored_authors:
                continue
            pending.append(item)
        return pending
