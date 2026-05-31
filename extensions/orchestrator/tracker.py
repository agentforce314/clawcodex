"""Tracker adapter protocol for issue tracker backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .issue import Issue

if TYPE_CHECKING:
    import httpx


@dataclass(frozen=True)
class Comment:
    """Normalized issue comment."""

    id: str | None = None
    body: str | None = None
    author_login: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    in_reply_to_id: str | None = None  # for threading


SUPPORTED_TRACKERS = frozenset({"linear", "github", "gitee", "gitcode", "local"})


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

    async def ensure_pull_request(
        self,
        *,
        issue: Issue,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> "PullRequestRef | None":
        """Ensure a pull request exists for the branch."""
        return None

    async def find_pull_request(
        self,
        *,
        head_branch: str,
        base_branch: str,
    ) -> "PullRequestRef | None":
        """Check if a pull request already exists for the given branch.

        Used as a guard to skip already-handled issues before launching a new agent run.
        """
        return None

    async def fetch_issue_comments(self, issue_id: str) -> list["Comment"]:
        """Fetch all comments on an issue for clarification polling."""
        return []

    async def fetch_new_comments_since(
        self,
        issue_id: str,
        since_comment_id: str | None,
    ) -> list["Comment"]:
        """Fetch comments newer than a given comment ID (for incremental polling).

        Returns comments sorted oldest-first so the caller can process them in order.
        """
        return []

    async def create_clarification_comment(
        self,
        issue_id: str,
        body: str,
        mentions: list[str] | None = None,
    ) -> "Comment | None":
        """Post a clarification request comment with @mention notifications.

        Args:
            issue_id: the issue to comment on
            body: comment body (should include @mention for authors)
            mentions: list of usernames to @mention

        Returns:
            The created comment, or None if not supported.
        """
        return None


@dataclass(frozen=True)
class PullRequestRef:
    """Normalized pull request reference."""

    number: str | None = None
    url: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class TrackerKindInfo:
    """Static metadata used by config validation and adapter creation."""

    kind: str
    label: str
    default_endpoint: str
    default_clone_base_url: str | None
    api_key_env_vars: tuple[str, ...]
    owner_env_vars: tuple[str, ...] = ()
    repo_env_vars: tuple[str, ...] = ()
    assignee_env_vars: tuple[str, ...] = ()
    requires_project_slug: bool = False
    requires_repository: bool = False


_TRACKER_KIND_INFO: dict[str, TrackerKindInfo] = {
    "linear": TrackerKindInfo(
        kind="linear",
        label="Linear",
        default_endpoint="https://api.linear.app/graphql",
        default_clone_base_url=None,
        api_key_env_vars=("LINEAR_API_KEY",),
        assignee_env_vars=("LINEAR_ASSIGNEE",),
        requires_project_slug=True,
    ),
    "github": TrackerKindInfo(
        kind="github",
        label="GitHub",
        default_endpoint="https://api.github.com",
        default_clone_base_url="https://github.com",
        api_key_env_vars=("GITHUB_TOKEN", "GITHUB_API_KEY"),
        owner_env_vars=("GITHUB_OWNER", "TRACKER_OWNER"),
        repo_env_vars=("GITHUB_REPO", "TRACKER_REPO"),
        assignee_env_vars=("GITHUB_ASSIGNEE", "TRACKER_ASSIGNEE"),
        requires_repository=True,
    ),
    "gitee": TrackerKindInfo(
        kind="gitee",
        label="Gitee",
        default_endpoint="https://gitee.com/api/v5",
        default_clone_base_url="https://gitee.com",
        api_key_env_vars=("GITEE_TOKEN", "GITEE_API_KEY"),
        owner_env_vars=("GITEE_OWNER", "TRACKER_OWNER"),
        repo_env_vars=("GITEE_REPO", "TRACKER_REPO"),
        assignee_env_vars=("GITEE_ASSIGNEE", "TRACKER_ASSIGNEE"),
        requires_repository=True,
    ),
    "gitcode": TrackerKindInfo(
        kind="gitcode",
        label="GitCode",
        default_endpoint="https://api.gitcode.com/api/v5",
        default_clone_base_url="https://gitcode.com",
        api_key_env_vars=("GITCODE_TOKEN", "GITCODE_API_KEY"),
        owner_env_vars=("GITCODE_OWNER", "TRACKER_OWNER"),
        repo_env_vars=("GITCODE_REPO", "TRACKER_REPO"),
        assignee_env_vars=("GITCODE_ASSIGNEE", "TRACKER_ASSIGNEE"),
        requires_repository=True,
    ),
    "local": TrackerKindInfo(
        kind="local",
        label="Local",
        default_endpoint="",
        default_clone_base_url=None,
        api_key_env_vars=(),
    ),
}


def tracker_kind_info(kind: str) -> TrackerKindInfo:
    """Return static metadata for a tracker kind."""
    normalized = normalize_tracker_kind(kind)
    try:
        return _TRACKER_KIND_INFO[normalized]
    except KeyError as exc:
        raise TrackerConfigError(
            f"Unsupported tracker kind: {kind!r}. "
            f"Supported values: {', '.join(sorted(SUPPORTED_TRACKERS))}"
        ) from exc


def normalize_tracker_kind(kind: str | None) -> str:
    """Normalize user-provided tracker kind values."""
    normalized = (kind or "linear").strip().lower()
    if normalized not in SUPPORTED_TRACKERS:
        raise TrackerConfigError(
            f"Unsupported tracker kind: {kind!r}. "
            f"Supported values: {', '.join(sorted(SUPPORTED_TRACKERS))}"
        )
    return normalized


def default_active_states_for_kind(kind: str) -> list[str]:
    """Return sane active-state defaults per tracker."""
    normalized = normalize_tracker_kind(kind)
    if normalized == "linear":
        return ["Todo", "In Progress"]
    if normalized == "local":
        return ["open", "ready"]
    if normalized == "gitcode":
        return ["opened"]
    return ["open"]


def default_terminal_states_for_kind(kind: str) -> list[str]:
    """Return sane terminal-state defaults per tracker."""
    normalized = normalize_tracker_kind(kind)
    if normalized == "linear":
        return ["Closed", "Cancelled", "Canceled", "Duplicate", "Done"]
    if normalized == "local":
        return ["completed", "closed", "cancelled", "failed", "abandoned"]
    return ["closed"]


def create_tracker_adapter(
    config: Any,
    *,
    http_client: "httpx.AsyncClient | None" = None,
) -> TrackerAdapter:
    """Create a tracker adapter from workflow tracker config."""
    kind = normalize_tracker_kind(getattr(config, "kind", None))
    validate_tracker_config(config)
    if kind == "linear":
        from .linear.adapter import LinearAdapter

        return LinearAdapter(
            api_key=getattr(config, "api_key", "") or "",
            project_slug=getattr(config, "project_slug", None),
            endpoint=getattr(config, "endpoint", None)
            or tracker_kind_info("linear").default_endpoint,
            active_states=list(getattr(config, "active_states", []) or []),
            assignee=getattr(config, "assignee", None),
        )
    if kind == "local":
        from .local_tracker.adapter import LocalTrackerAdapter

        return LocalTrackerAdapter(
            issues_path=getattr(config, "issues_path", None) or "",
            active_states=list(getattr(config, "active_states", []) or []),
            terminal_states=list(getattr(config, "terminal_states", []) or []),
        )

    from .repo_tracker.adapter import RepositoryTrackerAdapter

    return RepositoryTrackerAdapter(
        platform=kind,
        owner=getattr(config, "owner", None) or "",
        repo=getattr(config, "repo", None) or "",
        api_key=getattr(config, "api_key", None),
        endpoint=getattr(config, "endpoint", None)
        or tracker_kind_info(kind).default_endpoint,
        active_states=list(getattr(config, "active_states", []) or []),
        terminal_states=list(getattr(config, "terminal_states", []) or []),
        assignee=getattr(config, "assignee", None),
        http_client=http_client,
    )


class TrackerConfigError(ValueError):
    """Raised when tracker configuration is invalid."""


def validate_tracker_config(config: Any) -> None:
    """Validate tracker configuration before adapter creation."""
    info = tracker_kind_info(getattr(config, "kind", None))
    if info.kind == "local":
        if not getattr(config, "issues_path", None):
            raise TrackerConfigError(
                "Local issues path not configured. "
                "Set tracker.issues_path in WORKFLOW.md"
            )
        return
    if not getattr(config, "api_key", None):
        env_hint = " or ".join(info.api_key_env_vars)
        raise TrackerConfigError(
            f"{info.label} API key not configured. "
            f"Set {env_hint} or tracker.api_key in WORKFLOW.md"
        )
    if info.requires_project_slug and not getattr(config, "project_slug", None):
        raise TrackerConfigError(
            f"{info.label} project slug not configured. "
            "Set tracker.project_slug in WORKFLOW.md"
        )
    if info.requires_repository:
        owner = getattr(config, "owner", None)
        repo = getattr(config, "repo", None)
        if not owner or not repo:
            raise TrackerConfigError(
                f"{info.label} repository not configured. "
                "Set tracker.owner and tracker.repo in WORKFLOW.md"
            )


def repository_clone_url_for_tracker(config: Any) -> str | None:
    """Resolve clone URL for repository-backed trackers."""
    clone_url = getattr(config, "clone_url", None)
    if clone_url:
        return clone_url

    info = tracker_kind_info(getattr(config, "kind", None))
    if not info.requires_repository or not info.default_clone_base_url:
        return None

    owner = getattr(config, "owner", None)
    repo = getattr(config, "repo", None)
    if not owner or not repo:
        return None
    return f"{info.default_clone_base_url}/{owner}/{repo}.git"
