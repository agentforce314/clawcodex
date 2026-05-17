"""Tracker adapter protocol for issue tracker backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .linear.issue import Issue


class TrackerAdapter(ABC):
    """Adapter boundary for issue tracker reads and writes."""

    @abstractmethod
    async def fetch_candidate_issues(self) -> list[Issue]:
        """Poll for issues in active states."""

    @abstractmethod
    async def fetch_issue_states_by_ids(
        self, issue_ids: list[str]
    ) -> dict[str, Issue]:
        """Refresh current state for running issues.

        Returns a mapping from issue_id to Issue.
        """

    @abstractmethod
    async def create_comment(self, issue_id: str, body: str) -> None:
        """Post comment to issue (used by agent to report progress)."""

    @abstractmethod
    async def update_issue_state(self, issue_id: str, state: str) -> None:
        """Transition issue to a new state."""
