"""Tracker adapter for repository-backed issue trackers."""

from __future__ import annotations

import httpx

from ..issue import Issue
from ..tracker import (
    PullRequestRef,
    TrackerAdapter,
    default_active_states_for_kind,
    default_terminal_states_for_kind,
)
from .client import RepositoryIssueClient


class RepositoryTrackerAdapter(TrackerAdapter):
    """Repository-backed issue tracker adapter."""

    def __init__(
        self,
        *,
        platform: str,
        owner: str,
        repo: str,
        api_key: str | None = None,
        endpoint: str | None = None,
        active_states: list[str] | None = None,
        terminal_states: list[str] | None = None,
        assignee: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.platform = platform
        self.owner = owner
        self.repo = repo
        self.assignee = assignee
        self.active_states = (
            active_states or default_active_states_for_kind(platform)
        )
        self.terminal_states = (
            terminal_states or default_terminal_states_for_kind(platform)
        )
        self.client = RepositoryIssueClient(
            platform=platform,
            owner=owner,
            repo=repo,
            api_key=api_key,
            endpoint=endpoint,
            http_client=http_client,
        )

    async def fetch_candidate_issues(self) -> list[Issue]:
        return await self.client.fetch_candidate_issues(
            active_states=self.active_states,
            assignee=self.assignee,
        )

    async def fetch_issue_states_by_ids(
        self, issue_ids: list[str]
    ) -> dict[str, Issue]:
        issues = await self.client.fetch_issue_states_by_ids(
            issue_ids,
            active_states=self.active_states,
            assignee=self.assignee,
        )
        return {issue.id: issue for issue in issues if issue.id}

    async def create_comment(self, issue_id: str, body: str) -> None:
        await self.client.create_comment(issue_id, body)

    async def update_issue_state(self, issue_id: str, state: str) -> None:
        issue = await self.client.fetch_issue_states_by_ids(
            [issue_id],
            active_states=self.active_states,
            assignee=None,
        )
        current = issue[0] if issue else None

        labels = list(current.labels) if current is not None else []
        normalized_state = state.strip().lower()
        known_state_labels = {
            item.strip().lower()
            for item in [*self.active_states, *self.terminal_states]
            if item.strip()
        }
        labels = [
            label
            for label in labels
            if label.strip().lower() not in known_state_labels
        ]
        if normalized_state and normalized_state not in {
            "open",
            "opened",
            "closed",
            "close",
        }:
            labels.append(state)

        await self.client.update_issue(
            issue_id,
            state=state,
            labels=labels or None,
        )

    async def ensure_pull_request(
        self,
        *,
        issue: Issue,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> PullRequestRef | None:
        existing = await self.client.find_pull_request(
            head_branch=head_branch,
            base_branch=base_branch,
        )
        if existing is not None:
            return existing
        return await self.client.create_pull_request(
            title=title,
            head_branch=head_branch,
            base_branch=base_branch,
            body=body,
        )
