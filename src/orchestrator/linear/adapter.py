"""Linear-backed tracker adapter."""

from __future__ import annotations

import logging
from typing import Any

from ..tracker import TrackerAdapter
from .client import LinearGraphQLClient
from .issue import Issue

logger = logging.getLogger(__name__)

_CREATE_COMMENT_MUTATION = """
mutation SymphonyCreateComment($issueId: String!, $body: String!) {
  commentCreate(input: {issueId: $issueId, body: $body}) {
    success
  }
}
"""

_UPDATE_STATE_MUTATION = """
mutation SymphonyUpdateIssueState($issueId: String!, $stateId: String!) {
  issueUpdate(id: $issueId, input: {stateId: $stateId}) {
    success
  }
}
"""

_STATE_LOOKUP_QUERY = """
query SymphonyResolveStateId($issueId: String!, $stateName: String!) {
  issue(id: $issueId) {
    team {
      states(filter: {name: {eq: $stateName}}, first: 1) {
        nodes { id }
      }
    }
  }
}
"""


class LinearAdapter(TrackerAdapter):
    """Linear-backed issue tracker via GraphQL API."""

    def __init__(
        self,
        api_key: str,
        project_slug: str | None = None,
        endpoint: str = "https://api.linear.app/graphql",
        active_states: list[str] | None = None,
        assignee: str | None = None,
    ) -> None:
        self.client = LinearGraphQLClient(api_key=api_key, endpoint=endpoint)
        self.project_slug = project_slug
        self.active_states = active_states or ["Todo", "In Progress"]
        self.assignee = assignee
        self._assignee_filter: dict[str, Any] | None = None

    async def _resolve_assignee_filter(self) -> dict[str, Any] | None:
        if self._assignee_filter is not None:
            return self._assignee_filter

        if not self.assignee:
            self._assignee_filter = None
            return None

        normalized = self.assignee.strip()
        if normalized.lower() == "me":
            viewer_id = await self.client.resolve_viewer_id()
            if viewer_id:
                self._assignee_filter = {
                    "configured_assignee": "me",
                    "match_values": {viewer_id},
                }
            else:
                logger.warning("Could not resolve Linear viewer for assignee='me'")
                self._assignee_filter = None
        else:
            self._assignee_filter = {
                "configured_assignee": normalized,
                "match_values": {normalized},
            }
        return self._assignee_filter

    async def fetch_candidate_issues(self) -> list[Issue]:
        assignee_filter = await self._resolve_assignee_filter()
        return await self.client.fetch_candidate_issues(
            project_slug=self.project_slug or "",
            active_states=self.active_states,
            assignee_filter=assignee_filter,
        )

    async def fetch_issue_states_by_ids(
        self, issue_ids: list[str]
    ) -> dict[str, Issue]:
        assignee_filter = await self._resolve_assignee_filter()
        issues = await self.client.fetch_issue_states_by_ids(
            issue_ids, assignee_filter=assignee_filter
        )
        return {issue.id: issue for issue in issues if issue.id}

    async def create_comment(self, issue_id: str, body: str) -> None:
        body_resp = await self.client.graphql(
            _CREATE_COMMENT_MUTATION,
            {"issueId": issue_id, "body": body},
        )
        success = (
            body_resp.get("data", {}).get("commentCreate", {}).get("success")
            is True
        )
        if not success:
            raise LinearAdapterError("comment_create_failed")

    async def update_issue_state(self, issue_id: str, state: str) -> None:
        state_id = await self._resolve_state_id(issue_id, state)
        body = await self.client.graphql(
            _UPDATE_STATE_MUTATION,
            {"issueId": issue_id, "stateId": state_id},
        )
        success = (
            body.get("data", {}).get("issueUpdate", {}).get("success") is True
        )
        if not success:
            raise LinearAdapterError("issue_update_failed")

    async def _resolve_state_id(self, issue_id: str, state_name: str) -> str:
        body = await self.client.graphql(
            _STATE_LOOKUP_QUERY,
            {"issueId": issue_id, "stateName": state_name},
        )
        states = (
            body.get("data", {}).get("issue", {}).get("team", {}).get("states", {}).get("nodes", [])
        )
        if states and isinstance(states, list):
            state_id = states[0].get("id")
            if state_id:
                return state_id
        raise LinearAdapterError(f"state_not_found: {state_name}")


class LinearAdapterError(Exception):
    """Raised when a Linear adapter operation fails."""
