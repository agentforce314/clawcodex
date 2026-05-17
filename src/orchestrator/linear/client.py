"""Thin Linear GraphQL client for polling candidate issues."""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from .issue import Issue

logger = logging.getLogger(__name__)

_ISSUE_PAGE_SIZE = 50
_MAX_ERROR_BODY_LOG_BYTES = 1_000

_CANDIDATE_QUERY = """
query SymphonyLinearPoll($projectSlug: String!, $stateNames: [String!]!, $first: Int!, $relationFirst: Int!, $after: String) {
  issues(filter: {project: {slugId: {eq: $projectSlug}}, state: {name: {in: $stateNames}}}, first: $first, after: $after) {
    nodes {
      id
      identifier
      title
      description
      priority
      state { name }
      branchName
      url
      assignee { id }
      labels { nodes { name } }
      inverseRelations(first: $relationFirst) {
        nodes {
          type
          issue { id identifier state { name } }
        }
      }
      createdAt
      updatedAt
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_QUERY_BY_IDS = """
query SymphonyLinearIssuesById($ids: [ID!]!, $first: Int!, $relationFirst: Int!) {
  issues(filter: {id: {in: $ids}}, first: $first) {
    nodes {
      id
      identifier
      title
      description
      priority
      state { name }
      branchName
      url
      assignee { id }
      labels { nodes { name } }
      inverseRelations(first: $relationFirst) {
        nodes {
          type
          issue { id identifier state { name } }
        }
      }
      createdAt
      updatedAt
    }
  }
}
"""

_VIEWER_QUERY = """
query SymphonyLinearViewer {
  viewer { id }
}
"""


class LinearGraphQLClient:
    """Async Linear GraphQL client."""

    def __init__(self, api_key: str, endpoint: str = "https://api.linear.app/graphql") -> None:
        self.api_key = api_key
        self.endpoint = endpoint

    async def graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query, "variables": variables or {}}
        if operation_name:
            payload["operationName"] = operation_name

        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    body = await resp.json()
                    if resp.status != 200:
                        logger.error(
                            "Linear GraphQL request failed status=%s %s",
                            resp.status,
                            _summarize_error_body(body),
                        )
                        raise LinearAPIError(
                            f"Linear API returned status {resp.status}"
                        )
                    return body
            except aiohttp.ClientError as exc:
                logger.error("Linear GraphQL request failed: %s", exc)
                raise LinearAPIError(f"Linear API request failed: {exc}") from exc

    async def fetch_candidate_issues(
        self,
        project_slug: str,
        active_states: list[str],
        assignee_filter: dict[str, Any] | None = None,
    ) -> list[Issue]:
        if not self.api_key:
            raise LinearAPIError("Missing Linear API token")
        if not project_slug:
            raise LinearAPIError("Missing Linear project slug")

        all_issues: list[Issue] = []
        after_cursor: str | None = None
        while True:
            body = await self.graphql(
                _CANDIDATE_QUERY,
                {
                    "projectSlug": project_slug,
                    "stateNames": active_states,
                    "first": _ISSUE_PAGE_SIZE,
                    "relationFirst": _ISSUE_PAGE_SIZE,
                    "after": after_cursor,
                },
            )
            issues, page_info = _decode_page(body, assignee_filter)
            all_issues.extend(issues)
            if page_info.get("has_next_page") and page_info.get("end_cursor"):
                after_cursor = page_info["end_cursor"]
            else:
                break
        return all_issues

    async def fetch_issue_states_by_ids(
        self,
        issue_ids: list[str],
        assignee_filter: dict[str, Any] | None = None,
    ) -> list[Issue]:
        if not issue_ids:
            return []

        all_issues: list[Issue] = []
        ids = list(dict.fromkeys(issue_ids))  # preserve order, dedupe
        for i in range(0, len(ids), _ISSUE_PAGE_SIZE):
            batch = ids[i : i + _ISSUE_PAGE_SIZE]
            body = await self.graphql(
                _QUERY_BY_IDS,
                {
                    "ids": batch,
                    "first": len(batch),
                    "relationFirst": _ISSUE_PAGE_SIZE,
                },
            )
            issues, _ = _decode_page(body, assignee_filter)
            all_issues.extend(issues)

        # Sort by original requested order
        order = {issue_id: idx for idx, issue_id in enumerate(ids)}
        all_issues.sort(key=lambda i: order.get(i.id or "", len(ids)))
        return all_issues

    async def resolve_viewer_id(self) -> str | None:
        body = await self.graphql(_VIEWER_QUERY, {})
        viewer = body.get("data", {}).get("viewer")
        if viewer and isinstance(viewer, dict):
            return viewer.get("id")
        return None


class LinearAPIError(Exception):
    """Raised when a Linear API request fails."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_page(
    body: dict[str, Any],
    assignee_filter: dict[str, Any] | None,
) -> tuple[list[Issue], dict[str, Any]]:
    data = body.get("data", {}).get("issues", {})
    nodes = data.get("nodes", [])
    page_info = data.get("pageInfo", {})

    issues = []
    for node in nodes:
        issue = _normalize_issue(node, assignee_filter)
        if issue is not None:
            issues.append(issue)

    return issues, {
        "has_next_page": page_info.get("hasNextPage", False),
        "end_cursor": page_info.get("endCursor"),
    }


def _normalize_issue(
    issue: dict[str, Any],
    assignee_filter: dict[str, Any] | None,
) -> Issue | None:
    assignee = issue.get("assignee")
    return Issue(
        id=issue.get("id"),
        identifier=issue.get("identifier"),
        title=issue.get("title"),
        description=issue.get("description"),
        priority=_parse_priority(issue.get("priority")),
        state=issue.get("state", {}).get("name") if issue.get("state") else None,
        branch_name=issue.get("branchName"),
        url=issue.get("url"),
        assignee_id=assignee.get("id") if isinstance(assignee, dict) else None,
        blocked_by=_extract_blockers(issue),
        labels=_extract_labels(issue),
        assigned_to_worker=_assigned_to_worker(assignee, assignee_filter),
        created_at=_parse_datetime(issue.get("createdAt")),
        updated_at=_parse_datetime(issue.get("updatedAt")),
    )


def _extract_labels(issue: dict[str, Any]) -> list[str]:
    labels = issue.get("labels", {}).get("nodes", [])
    return [
        str(label["name"]).lower()
        for label in labels
        if isinstance(label, dict) and label.get("name")
    ]


def _extract_blockers(issue: dict[str, Any]) -> list[dict[str, Any]]:
    relations = issue.get("inverseRelations", {}).get("nodes", [])
    blockers = []
    for rel in relations:
        if (
            isinstance(rel, dict)
            and str(rel.get("type", "")).strip().lower() == "blocks"
        ):
            blocker_issue = rel.get("issue")
            if isinstance(blocker_issue, dict):
                blockers.append(
                    {
                        "id": blocker_issue.get("id"),
                        "identifier": blocker_issue.get("identifier"),
                        "state": blocker_issue.get("state", {}).get("name")
                        if blocker_issue.get("state")
                        else None,
                    }
                )
    return blockers


def _assigned_to_worker(
    assignee: dict[str, Any] | None,
    assignee_filter: dict[str, Any] | None,
) -> bool:
    if assignee_filter is None:
        return True
    match_values = assignee_filter.get("match_values")
    if not match_values:
        return False
    aid = assignee.get("id") if isinstance(assignee, dict) else None
    return aid in match_values


def _parse_priority(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _parse_datetime(value: str | None) -> Any:
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _summarize_error_body(body: Any) -> str:
    text = json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else str(body)
    text = " ".join(text.split())
    if len(text) > _MAX_ERROR_BODY_LOG_BYTES:
        text = text[:_MAX_ERROR_BODY_LOG_BYTES] + "...<truncated>"
    return text
