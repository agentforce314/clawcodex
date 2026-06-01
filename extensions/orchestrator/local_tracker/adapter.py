"""Filesystem-backed tracker adapter for local issue documents."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from ..issue import Issue

logger = logging.getLogger(__name__)
from ..tracker import (
    Comment,
    DEFAULT_INTENT_LABELS,
    Intent,
    PullRequestRef,
    TrackerAdapter,
    intent_from_label_set,
)
from .parser import (
    LocalIssueDocument,
    parse_markdown_issue,
    utc_now_iso,
    write_markdown_frontmatter,
)


class LocalTrackerAdapter(TrackerAdapter):
    """Tracker adapter that stores issues and comments in local files."""

    def __init__(
        self,
        issues_path: str | Path,
        active_states: list[str] | None = None,
        terminal_states: list[str] | None = None,
        intent_labels: dict[str, str] | None = None,
    ) -> None:
        self.issues_path = Path(issues_path).expanduser()
        self._active_states = tuple(
            active_states if active_states is not None else ["open", "ready"]
        )
        self._terminal_states = tuple(
            terminal_states
            if terminal_states is not None
            else ["completed", "closed", "cancelled", "failed", "abandoned"]
        )
        self._active_state_set = _normalize_states(self._active_states)
        # F-39: same label conventions as the repository-backed adapters.
        self.intent_labels: dict[str, str] = (
            dict(intent_labels) if intent_labels else dict(DEFAULT_INTENT_LABELS)
        )

    @property
    def active_states(self) -> list[str]:
        return list(self._active_states)

    @property
    def terminal_states(self) -> list[str]:
        return list(self._terminal_states)

    async def fetch_candidate_issues(self) -> list[Issue]:
        documents = self._load_documents()
        issues = [
            document.issue
            for document in documents
            if _normalize_state(document.issue.state) in self._active_state_set
        ]
        return sorted(
            issues,
            key=lambda issue: (
                issue.priority is None,
                issue.priority if issue.priority is not None else 0,
                issue.identifier or issue.id or "",
            ),
        )

    async def fetch_issue_states_by_ids(
        self,
        issue_ids: list[str],
    ) -> dict[str, Issue]:
        requested = set(issue_ids)
        issues: dict[str, Issue] = {}
        for document in self._load_documents():
            issue = document.issue
            if issue.id in requested:
                issues[issue.id or ""] = issue
        return issues

    async def create_comment(self, issue_id: str, body: str) -> Comment | None:
        return self._append_comment(issue_id, body)

    async def update_comment(
        self,
        issue_id: str,
        comment_id: str,
        body: str,
    ) -> Comment | None:
        comment_path = self._comments_path(issue_id)
        if not comment_path.exists():
            return None
        now = utc_now_iso()
        updated_comment: Comment | None = None
        lines: list[str] = []
        for line in comment_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if str(payload.get("id")) == str(comment_id):
                payload["body"] = body
                payload["updated_at"] = now
                updated_comment = Comment(
                    id=_string_or_none(payload.get("id")),
                    body=_string_or_none(payload.get("body")),
                    author_login=_string_or_none(payload.get("author_login")),
                    created_at=_string_or_none(payload.get("created_at")),
                    updated_at=_string_or_none(payload.get("updated_at")),
                    in_reply_to_id=_string_or_none(payload.get("in_reply_to_id")),
                )
            lines.append(json.dumps(payload, ensure_ascii=False))
        if updated_comment is None:
            return None
        tmp_path = comment_path.with_suffix(comment_path.suffix + ".tmp")
        tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp_path, comment_path)
        return updated_comment

    async def update_issue_state(self, issue_id: str, state: str) -> None:
        document = self._document_for_issue(issue_id)
        write_markdown_frontmatter(
            document.path,
            {
                "state": state,
                "updated_at": utc_now_iso(),
            },
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
        issue_id = issue.id or issue.identifier
        if issue_id:
            document = self._document_for_issue(issue_id)
            write_markdown_frontmatter(
                document.path,
                {
                    "branch_name": head_branch,
                    "base_branch": base_branch,
                    "pr_title": title,
                },
            )
        return await self.find_pull_request(
            head_branch=head_branch,
            base_branch=base_branch,
        )

    async def find_pull_request(
        self,
        *,
        head_branch: str,
        base_branch: str,
    ) -> PullRequestRef | None:
        for document in self._load_documents():
            issue = document.issue
            if issue.branch_name != head_branch:
                continue
            if document.base_branch and document.base_branch != base_branch:
                continue
            if not document.pr_url:
                continue
            return PullRequestRef(
                number=document.pr_number,
                url=document.pr_url,
                title=_string_or_none(document.metadata.get("pr_title")),
            )
        return None

    async def fetch_issue_comments(self, issue_id: str) -> list[Comment]:
        comment_path = self._comments_path(issue_id)
        if not comment_path.exists():
            return []

        comments: list[Comment] = []
        for line in comment_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            comments.append(
                Comment(
                    id=_string_or_none(payload.get("id")),
                    body=_string_or_none(payload.get("body")),
                    author_login=_string_or_none(payload.get("author_login")),
                    created_at=_string_or_none(payload.get("created_at")),
                    updated_at=_string_or_none(payload.get("updated_at")),
                    in_reply_to_id=_string_or_none(payload.get("in_reply_to_id")),
                )
            )
        return comments

    async def fetch_new_comments_since(
        self,
        issue_id: str,
        since_comment_id: str | None,
    ) -> list[Comment]:
        comments = await self.fetch_issue_comments(issue_id)
        if since_comment_id is None:
            return comments
        for index, comment in enumerate(comments):
            if comment.id == since_comment_id:
                return comments[index + 1 :]
        return comments

    async def create_clarification_comment(
        self,
        issue_id: str,
        body: str,
        mentions: list[str] | None = None,
    ) -> Comment | None:
        prefix = " ".join(f"@{mention}" for mention in mentions or [])
        comment_body = f"{prefix}\n\n{body}".strip() if prefix else body
        return self._append_comment(issue_id, comment_body)

    async def extract_intent_from_labels(
        self,
        labels: list[str] | None,
    ) -> Intent:
        return intent_from_label_set(labels, self.intent_labels)

    async def close_pull_request(
        self,
        pull_request: PullRequestRef,
    ) -> bool:
        # LocalTracker has no remote PR — issue_registry tracks the
        # "PR" via frontmatter (pr_number / pr_url). Closing a local
        # PR is a no-op; the orchestrator's reset_for_retry will clear
        # the frontmatter fields directly.
        logger.info(
            "LocalTrackerAdapter.close_pull_request: no-op (pr_number=%s)",
            pull_request.number,
        )
        return True

    async def fetch_issue_command_intent(
        self,
        issue_id: str,
        since_comment_id: str | None,
    ) -> "CommandIntent | None":
        from ..tracker import CommandIntent, parse_agent_command
        comments = await self.fetch_new_comments_since(
            issue_id, since_comment_id
        )
        for comment in comments:
            body = comment.body or ""
            command = parse_agent_command(body)
            if command is not None:
                return CommandIntent(
                    command=command,
                    author_login=comment.author_login,
                    comment_id=comment.id,
                    comment_body=body,
                )
        return None

    def _load_documents(self) -> list[LocalIssueDocument]:
        if not self.issues_path.exists():
            return []
        documents: list[LocalIssueDocument] = []
        for path in sorted(self.issues_path.glob("*.md")):
            if _is_ignored_issue_path(path):
                continue
            documents.append(parse_markdown_issue(path))
        return documents

    def _document_for_issue(self, issue_id: str) -> LocalIssueDocument:
        for document in self._load_documents():
            issue = document.issue
            if issue.id == issue_id or issue.identifier == issue_id:
                return document
        raise FileNotFoundError(f"Local issue not found: {issue_id}")

    def _append_comment(self, issue_id: str, body: str) -> Comment:
        self.issues_path.mkdir(parents=True, exist_ok=True)
        now = utc_now_iso()
        comment = Comment(
            id=str(uuid.uuid4()),
            body=body,
            author_login="clawcodex",
            created_at=now,
            updated_at=now,
        )
        payload = {
            "id": comment.id,
            "body": comment.body,
            "author_login": comment.author_login,
            "created_at": comment.created_at,
            "updated_at": comment.updated_at,
        }
        with self._comments_path(issue_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return comment

    def _comments_path(self, issue_id: str) -> Path:
        return self.issues_path / f"{_safe_file_stem(issue_id)}.comments.ndjson"


def _normalize_states(states: tuple[str, ...]) -> set[str]:
    return {_normalize_state(state) for state in states if _normalize_state(state)}


def _normalize_state(state: str | None) -> str:
    return (state or "").strip().lower()


def _is_ignored_issue_path(path: Path) -> bool:
    name = path.name
    return (
        name.startswith(".")
        or name.endswith(".tmp")
        or name.endswith(".comments.md")
        or ".comments." in name
    )


def _safe_file_stem(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    stem = safe.strip("-._") or "issue"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{stem}-{digest}"


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
