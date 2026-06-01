"""Generic repository issue client for GitHub/Gitee/GitCode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

import httpx

from ..issue import Issue
from ..tracker import PullRequestFeedback, PullRequestRef

_PAGE_SIZE = 100


@dataclass(frozen=True)
class RepositoryPlatform:
    """Static per-platform behavior for repository-backed trackers."""

    name: str
    default_endpoint: str
    auth_mode: str
    open_state: str
    closed_state: str
    # API parameter name for filtering issues by state (all platforms use "state")
    state_param: str = "state"
    accept_header: str | None = None


_PLATFORMS: dict[str, RepositoryPlatform] = {
    "github": RepositoryPlatform(
        name="github",
        default_endpoint="https://api.github.com",
        auth_mode="bearer",
        open_state="open",
        closed_state="closed",
        accept_header="application/vnd.github+json",
    ),
    "gitee": RepositoryPlatform(
        name="gitee",
        default_endpoint="https://gitee.com/api/v5",
        auth_mode="access_token",
        open_state="open",
        closed_state="closed",
        accept_header="application/json",
    ),
    "gitcode": RepositoryPlatform(
        name="gitcode",
        default_endpoint="https://api.gitcode.com/api/v5",
        auth_mode="access_token",
        open_state="open",
        closed_state="closed",
        accept_header="application/json",
    ),
}

_OPEN_STATE_ALIASES = {"open", "opened", "reopen", "reopened"}
_TERMINAL_STATE_ALIASES = {
    "closed",
    "close",
    "done",
    "completed",
    "cancelled",
    "canceled",
    "duplicate",
}


class RepositoryIssueClient:
    """Issue API wrapper for repository-backed trackers."""

    def __init__(
        self,
        *,
        platform: str,
        owner: str,
        repo: str,
        api_key: str | None,
        endpoint: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        try:
            self.platform = _PLATFORMS[platform]
        except KeyError as exc:
            raise RepositoryTrackerError(f"unsupported platform: {platform}") from exc
        self.owner = owner
        self.repo = repo
        self.api_key = api_key or ""
        self.endpoint = (endpoint or self.platform.default_endpoint).rstrip("/")
        self._http_client = http_client

    async def fetch_candidate_issues(
        self,
        *,
        active_states: list[str],
        assignee: str | None = None,
    ) -> list[Issue]:
        page = 1
        issues: list[Issue] = []
        labels = _repository_label_filter(active_states)

        while True:
            params = {
                "state": self.platform.open_state,
                "per_page": _PAGE_SIZE,
                "page": page,
            }
            if labels:
                params["labels"] = ",".join(labels)

            payload = await self._request_json(
                "GET",
                f"/repos/{self.owner}/{self.repo}/issues",
                params=params,
            )
            if not isinstance(payload, list):
                raise RepositoryTrackerError("invalid_issue_list_response")

            batch = [
                issue
                for issue in (
                    _normalize_issue(item, active_states=active_states)
                    for item in payload
                )
                if issue is not None and _matches_assignee(issue, assignee)
            ]
            issues.extend(batch)

            if len(payload) < _PAGE_SIZE:
                break
            page += 1

        return issues

    async def fetch_issue_states_by_ids(
        self,
        issue_ids: list[str],
        *,
        active_states: list[str],
        assignee: str | None = None,
    ) -> list[Issue]:
        issues: list[Issue] = []
        for issue_id in dict.fromkeys(issue_ids):
            payload = await self._request_json(
                "GET",
                f"/repos/{self.owner}/{self.repo}/issues/{issue_id}",
            )
            issue = _normalize_issue(payload, active_states=active_states)
            if issue is not None and _matches_assignee(issue, assignee):
                issues.append(issue)
        return issues

    async def create_comment(self, issue_id: str, body: str) -> dict[str, Any] | None:
        data: dict[str, Any] = {"body": body}
        result = await self._request_json(
            "POST",
            f"/repos/{self.owner}/{self.repo}/issues/{issue_id}/comments",
            json=data if self.platform.auth_mode == "bearer" else None,
            data=data if self.platform.auth_mode != "bearer" else None,
        )
        return result if isinstance(result, dict) else None

    async def update_comment(self, comment_id: str, body: str) -> dict[str, Any] | None:
        data: dict[str, Any] = {"body": body}
        result = await self._request_json(
            "PATCH",
            f"/repos/{self.owner}/{self.repo}/issues/comments/{comment_id}",
            json=data if self.platform.auth_mode == "bearer" else None,
            data=data if self.platform.auth_mode != "bearer" else None,
        )
        return result if isinstance(result, dict) else None

    async def fetch_comments(self, issue_id: str) -> list[dict[str, Any]]:
        """Fetch all comments on an issue."""
        page = 1
        comments: list[dict[str, Any]] = []
        while True:
            params = {"per_page": _PAGE_SIZE, "page": page}
            payload = await self._request_json(
                "GET",
                f"/repos/{self.owner}/{self.repo}/issues/{issue_id}/comments",
                params=params,
            )
            if not isinstance(payload, list):
                break
            comments.extend(payload)
            if len(payload) < _PAGE_SIZE:
                break
            page += 1
        return comments

    async def fetch_comments_since(
        self,
        issue_id: str,
        since_comment_id: str | None,
    ) -> list[dict[str, Any]]:
        """Fetch comments newer than since_comment_id for incremental polling."""
        if since_comment_id is None:
            return await self.fetch_comments(issue_id)

        all_comments = await self.fetch_comments(issue_id)

        # GitHub returns comments in chronological order (oldest first)
        # Find the comment with since_comment_id and return newer ones
        newer: list[dict[str, Any]] = []
        found = since_comment_id is None  # if None, return all
        for comment in all_comments:
            if found:
                newer.append(comment)
            elif str(comment.get("id")) == str(since_comment_id):
                found = True
        return newer

    async def update_issue(
        self,
        issue_id: str,
        *,
        state: str | None = None,
        labels: list[str] | None = None,
    ) -> None:
        payload = _build_issue_update_payload(
            state=state,
            labels=labels,
            platform=self.platform,
        )
        if not payload:
            return
        await self._request_json(
            "PATCH",
            f"/repos/{self.owner}/{self.repo}/issues/{issue_id}",
            json=payload if self.platform.auth_mode == "bearer" else None,
            data=payload if self.platform.auth_mode != "bearer" else None,
        )

    async def find_pull_request(
        self,
        *,
        head_branch: str,
        base_branch: str,
    ) -> PullRequestRef | None:
        params: dict[str, Any] = {
            "state": self.platform.open_state,
            "base": base_branch,
        }
        if self.platform.name == "github":
            params["head"] = f"{self.owner}:{head_branch}"
        else:
            params["head"] = head_branch

        payload = await self._request_json(
            "GET",
            f"/repos/{self.owner}/{self.repo}/pulls",
            params=params,
        )
        if not isinstance(payload, list):
            return None
        for item in payload:
            pr = _normalize_pull_request(item)
            if pr is not None:
                return pr
        return None

    async def fetch_pull_request_feedback(
        self,
        *,
        pull_request: PullRequestRef,
        include_ci_failures: bool = True,
        max_log_chars_per_check: int = 12_000,
    ) -> list[PullRequestFeedback]:
        if pull_request.number is None:
            return []

        feedback: list[PullRequestFeedback] = []
        feedback.extend(await self._fetch_pull_request_conversation_feedback(pull_request.number))
        feedback.extend(await self._fetch_pull_request_inline_feedback(pull_request.number))
        feedback.extend(await self._fetch_pull_request_review_feedback(pull_request.number))
        if include_ci_failures:
            feedback.extend(
                await self._fetch_pull_request_ci_feedback(
                    pull_request.number,
                    max_log_chars_per_check=max_log_chars_per_check,
                )
            )
        return feedback

    async def reply_to_pull_request_feedback(
        self,
        *,
        pull_request: PullRequestRef,
        feedback: PullRequestFeedback,
        body: str,
    ) -> dict[str, Any] | None:
        if pull_request.number is None:
            return None
        if feedback.source == "inline_review" and feedback.id:
            comment_id = feedback.id.split(":", 1)[1] if ":" in feedback.id else feedback.id
            endpoint = f"/repos/{self.owner}/{self.repo}/pulls/{pull_request.number}/comments/{comment_id}/replies"
        else:
            endpoint = f"/repos/{self.owner}/{self.repo}/issues/{pull_request.number}/comments"
        payload = {"body": body}
        result = await self._request_json(
            "POST",
            endpoint,
            json=payload if self.platform.auth_mode == "bearer" else None,
            data=payload if self.platform.auth_mode != "bearer" else None,
        )
        return result if isinstance(result, dict) else None

    async def update_pull_request(
        self,
        *,
        pull_request: PullRequestRef,
        title: str | None = None,
        body: str | None = None,
    ) -> PullRequestRef | None:
        if pull_request.number is None:
            return None
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if not payload:
            return pull_request
        result = await self._request_json(
            "PATCH",
            f"/repos/{self.owner}/{self.repo}/pulls/{pull_request.number}",
            json=payload if self.platform.auth_mode == "bearer" else None,
            data=payload if self.platform.auth_mode != "bearer" else None,
        )
        return _normalize_pull_request(result)

    async def close_pull_request(
        self,
        pull_request: PullRequestRef,
    ) -> bool:
        """F-39 Sub-B: close a remote PR so a fresh one can be opened.

        Uses `PATCH /repos/{owner}/{repo}/pulls/{number}` with
        `{"state": "closed"}`. Compatible with GitHub, Gitee, GitCode —
        all three expose the same endpoint shape and accept the
        `state=closed` payload.

        Returns True if the API call succeeded, False on transport /
        authorization error. The caller (orchestrator) decides whether
        to surface a comment to the issue; the registry will still
        be reset even if the remote close fails (best-effort).
        """
        if pull_request.number is None:
            return False
        payload: dict[str, Any] = {"state": "closed"}
        try:
            await self._request_json(
                "PATCH",
                f"/repos/{self.owner}/{self.repo}/pulls/{pull_request.number}",
                json=payload if self.platform.auth_mode == "bearer" else None,
                data=payload if self.platform.auth_mode != "bearer" else None,
            )
            return True
        except RepositoryTrackerError as exc:
            # 422 (merged PRs cannot be closed) is acceptable: the
            # operator's intent was honored, the registry just needs
            # to be reset locally. We only treat 4xx/5xx other than
            # 422 as a hard failure.
            message = str(exc)
            if "status=422" in message:
                return True
            return False

    async def create_pull_request(
        self,
        *,
        title: str,
        head_branch: str,
        base_branch: str,
        body: str,
    ) -> PullRequestRef:
        payload = {
            "title": title,
            "head": head_branch,
            "base": base_branch,
            "body": body,
        }
        body_resp = await self._request_json(
            "POST",
            f"/repos/{self.owner}/{self.repo}/pulls",
            json=payload if self.platform.auth_mode == "bearer" else None,
            data=payload if self.platform.auth_mode != "bearer" else None,
        )
        pr = _normalize_pull_request(body_resp)
        if pr is None:
            raise RepositoryTrackerError("invalid_pull_request_response")
        return pr

    async def _fetch_pull_request_conversation_feedback(
        self,
        pr_number: str,
    ) -> list[PullRequestFeedback]:
        comments = await self.fetch_comments(pr_number)
        return [
            feedback
            for feedback in (
                _normalize_conversation_feedback(comment) for comment in comments
            )
            if feedback is not None
        ]

    async def _fetch_pull_request_inline_feedback(
        self,
        pr_number: str,
    ) -> list[PullRequestFeedback]:
        payload = await self._fetch_paginated(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/comments"
        )
        return [
            feedback
            for feedback in (_normalize_inline_feedback(item) for item in payload)
            if feedback is not None
        ]

    async def _fetch_pull_request_review_feedback(
        self,
        pr_number: str,
    ) -> list[PullRequestFeedback]:
        payload = await self._fetch_paginated(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/reviews"
        )
        return [
            feedback
            for feedback in (_normalize_review_feedback(item) for item in payload)
            if feedback is not None
        ]

    async def _fetch_pull_request_ci_feedback(
        self,
        pr_number: str,
        *,
        max_log_chars_per_check: int,
    ) -> list[PullRequestFeedback]:
        payload = await self._request_json(
            "GET",
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}",
        )
        head = payload.get("head") if isinstance(payload, dict) else None
        sha = head.get("sha") if isinstance(head, dict) else None
        if not isinstance(sha, str) or not sha:
            return []

        checks = await self._fetch_ci_checks(sha)
        return [
            feedback
            for feedback in (
                _normalize_ci_feedback(
                    item,
                    commit_sha=sha,
                    max_log_chars_per_check=max_log_chars_per_check,
                )
                for item in checks
            )
            if feedback is not None
        ]

    async def _fetch_ci_checks(self, sha: str) -> list[dict[str, Any]]:
        if self.platform.name == "github":
            payload = await self._request_json(
                "GET",
                f"/repos/{self.owner}/{self.repo}/commits/{sha}/check-runs",
            )
            check_runs = payload.get("check_runs") if isinstance(payload, dict) else None
            return check_runs if isinstance(check_runs, list) else []
        return await self._fetch_paginated(
            f"/repos/{self.owner}/{self.repo}/commits/{sha}/statuses"
        )

    async def _fetch_paginated(self, path: str) -> list[dict[str, Any]]:
        page = 1
        items: list[dict[str, Any]] = []
        while True:
            payload = await self._request_json(
                "GET",
                path,
                params={"per_page": _PAGE_SIZE, "page": page},
            )
            if not isinstance(payload, list):
                break
            items.extend(item for item in payload if isinstance(item, dict))
            if len(payload) < _PAGE_SIZE:
                break
            page += 1
        return items

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"User-Agent": "clawcodex-orchestrator"}
        if self.platform.accept_header:
            headers["Accept"] = self.platform.accept_header

        merged_params = dict(params or {})
        if self.platform.auth_mode == "bearer":
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.api_key:
            merged_params["access_token"] = self.api_key

        response = await self._request(
            method,
            f"{self.endpoint}{path}",
            headers=headers,
            params=merged_params,
            json=json,
            data=data,
        )
        try:
            return response.json()
        except ValueError as exc:
            raise RepositoryTrackerError(
                f"invalid_json_response status={response.status_code}"
            ) from exc

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        client = self._http_client
        should_close = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise RepositoryTrackerError(f"request_failed: {exc}") from exc
        finally:
            if should_close:
                await client.aclose()

        if response.status_code >= 400:
            raise RepositoryTrackerError(
                f"request_failed status={response.status_code} body={_summarize_body(response)}"
            )
        return response


def _normalize_issue(
    payload: Any,
    *,
    active_states: list[str],
) -> Issue | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("pull_request"):
        return None

    labels = _extract_labels(payload)
    issue_number = payload.get("number")
    raw_state = payload.get("state")
    normalized_state = _choose_issue_state(raw_state, labels, active_states)
    assignee = payload.get("assignee") or {}

    return Issue(
        id=str(issue_number) if issue_number is not None else None,
        identifier=_build_identifier(payload, issue_number),
        title=payload.get("title"),
        description=payload.get("body") or payload.get("description"),
        state=normalized_state,
        branch_name=_extract_branch_name(payload),
        url=payload.get("html_url") or payload.get("url"),
        assignee_id=_assignee_value(assignee),
        labels=labels,
        created_at=_parse_datetime(payload.get("created_at") or payload.get("createdAt")),
        updated_at=_parse_datetime(payload.get("updated_at") or payload.get("updatedAt")),
    )


def _normalize_pull_request(payload: Any) -> PullRequestRef | None:
    if not isinstance(payload, dict):
        return None
    number = payload.get("number") or payload.get("iid") or payload.get("id")
    url = payload.get("html_url") or payload.get("url")
    title = payload.get("title")
    return PullRequestRef(
        number=str(number) if number is not None else None,
        url=url if isinstance(url, str) else None,
        title=title if isinstance(title, str) else None,
    )


def _normalize_conversation_feedback(payload: dict[str, Any]) -> PullRequestFeedback | None:
    body = payload.get("body")
    feedback_id = payload.get("id")
    if not isinstance(body, str) or not body.strip() or feedback_id is None:
        return None
    return PullRequestFeedback(
        id=f"conversation:{feedback_id}",
        source="conversation",
        body=body,
        author_login=_extract_comment_author(payload),
        status="open",
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
        url=_string_value(payload.get("html_url") or payload.get("url")),
    )


def _normalize_inline_feedback(payload: dict[str, Any]) -> PullRequestFeedback | None:
    body = payload.get("body")
    feedback_id = payload.get("id")
    if not isinstance(body, str) or not body.strip() or feedback_id is None:
        return None
    return PullRequestFeedback(
        id=f"inline_review:{feedback_id}",
        source="inline_review",
        body=body,
        author_login=_extract_comment_author(payload),
        file_path=_string_value(payload.get("path") or payload.get("file_path")),
        line=_int_value(payload.get("line") or payload.get("new_line") or payload.get("position")),
        diff_hunk=_string_value(payload.get("diff_hunk")),
        severity="warning",
        status=_normalize_feedback_status(payload),
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
        commit_sha=_string_value(payload.get("commit_id") or payload.get("commit_sha")),
        url=_string_value(payload.get("html_url") or payload.get("url")),
    )


def _normalize_review_feedback(payload: dict[str, Any]) -> PullRequestFeedback | None:
    body = payload.get("body")
    feedback_id = payload.get("id")
    if not isinstance(body, str) or not body.strip() or feedback_id is None:
        return None
    state = str(payload.get("state") or "").strip().lower()
    severity = "error" if state in {"changes_requested", "request_changes"} else "info"
    return PullRequestFeedback(
        id=f"review_summary:{feedback_id}",
        source="review_summary",
        body=body,
        author_login=_extract_comment_author(payload),
        severity=severity,
        status="open",
        created_at=payload.get("submitted_at") or payload.get("created_at"),
        updated_at=payload.get("updated_at"),
        commit_sha=_string_value(payload.get("commit_id") or payload.get("commit_sha")),
        url=_string_value(payload.get("html_url") or payload.get("url")),
    )


def _normalize_ci_feedback(
    payload: dict[str, Any],
    *,
    commit_sha: str,
    max_log_chars_per_check: int,
) -> PullRequestFeedback | None:
    state = str(payload.get("conclusion") or payload.get("state") or "").strip().lower()
    if state not in {"failure", "failed", "error", "cancelled", "timed_out"}:
        return None
    name = _string_value(payload.get("name") or payload.get("context")) or "CI check"
    summary = _string_value(payload.get("output", {}).get("summary") if isinstance(payload.get("output"), dict) else None)
    description = _string_value(payload.get("description"))
    details_url = _string_value(payload.get("details_url") or payload.get("html_url") or payload.get("target_url"))
    parts = [f"{name} reported {state}."]
    if description:
        parts.append(description)
    if summary:
        parts.append(summary)
    body = "\n\n".join(parts)
    if len(body) > max_log_chars_per_check:
        body = body[:max_log_chars_per_check] + "\n...<truncated>"
    feedback_id = payload.get("id") or payload.get("context") or name
    return PullRequestFeedback(
        id=f"ci:{commit_sha}:{feedback_id}",
        source="ci",
        body=body,
        severity="error",
        status="open",
        created_at=payload.get("started_at") or payload.get("created_at"),
        updated_at=payload.get("completed_at") or payload.get("updated_at"),
        commit_sha=commit_sha,
        url=details_url,
    )


def _normalize_feedback_status(payload: dict[str, Any]) -> str:
    if payload.get("resolved") is True:
        return "resolved"
    if payload.get("outdated") is True:
        return "outdated"
    return "open"


def _string_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _int_value(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _build_identifier(payload: dict[str, Any], issue_number: Any) -> str | None:
    if issue_number is None:
        return None
    repo_name = (
        payload.get("repository")
        or payload.get("repo")
        or payload.get("repository_name")
    )
    if isinstance(repo_name, str) and repo_name.strip():
        return f"{repo_name}#{issue_number}"
    return f"#{issue_number}"


def _choose_issue_state(
    raw_state: Any,
    labels: list[str],
    active_states: list[str],
) -> str | None:
    normalized_active = [state.strip().lower() for state in active_states if state.strip()]
    label_set = {label.lower() for label in labels}
    for state_name in normalized_active:
        if state_name in label_set:
            return state_name
    if isinstance(raw_state, str):
        return raw_state.strip().lower()
    return None


def _extract_labels(payload: dict[str, Any]) -> list[str]:
    labels = payload.get("labels", [])
    result: list[str] = []
    if not isinstance(labels, list):
        return result
    for item in labels:
        if isinstance(item, dict):
            name = item.get("name")
        else:
            name = item
        if isinstance(name, str) and name.strip():
            result.append(name.strip().lower())
    return result


def _extract_branch_name(payload: dict[str, Any]) -> str | None:
    body = payload.get("body") or payload.get("description")
    if not isinstance(body, str) or not body.strip():
        return None

    patterns = (
        r"(?im)^\s*branch(?:_name)?\s*[:=]\s*`?([A-Za-z0-9._/\-]+)`?\s*$",
        r"(?im)^\s*git\s+branch\s*[:=]\s*`?([A-Za-z0-9._/\-]+)`?\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, body)
        if match:
            return match.group(1).strip()
    return None


def _matches_assignee(issue: Issue, assignee: str | None) -> bool:
    if not assignee:
        return True
    normalized = assignee.strip().lower()
    if not normalized:
        return True
    return (issue.assignee_id or "").strip().lower() == normalized


def _assignee_value(assignee: Any) -> str | None:
    if isinstance(assignee, dict):
        for key in ("login", "name", "username", "id"):
            value = assignee.get(key)
            if isinstance(value, str) and value.strip():
                return value
    elif isinstance(assignee, str) and assignee.strip():
        return assignee
    return None


def _repository_label_filter(active_states: list[str]) -> list[str]:
    labels: list[str] = []
    for state_name in active_states:
        normalized = state_name.strip().lower()
        if not normalized:
            continue
        if normalized in _OPEN_STATE_ALIASES or normalized in _TERMINAL_STATE_ALIASES:
            continue
        labels.append(state_name)
    return labels


def _build_issue_update_payload(
    *,
    state: str | None,
    labels: list[str] | None,
    platform: RepositoryPlatform,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    normalized = (state or "").strip().lower()
    if normalized:
        if normalized in _TERMINAL_STATE_ALIASES:
            payload["state"] = platform.closed_state
        elif normalized in _OPEN_STATE_ALIASES:
            payload["state"] = platform.open_state
    if labels:
        if platform.auth_mode == "bearer":
            payload["labels"] = labels
        else:
            payload["labels"] = ",".join(labels)
    return payload


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _summarize_body(response: httpx.Response) -> str:
    text = " ".join(response.text.split())
    if len(text) > 500:
        return text[:500] + "...<truncated>"
    return text


class RepositoryTrackerError(Exception):
    """Raised when repository issue tracker operations fail."""


def _extract_comment_author(comment: dict[str, Any]) -> str | None:
    """Extract author login from a comment payload."""
    user = comment.get("user") or comment.get("author")
    if isinstance(user, dict):
        return user.get("login") or user.get("username") or user.get("name")
    if isinstance(user, str) and user.strip():
        return user
    return None
