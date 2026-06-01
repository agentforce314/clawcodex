"""Tracker adapter protocol for issue tracker backends."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from .issue import Issue

if TYPE_CHECKING:
    import httpx


class Intent(str, Enum):
    """Operator intent expressed via issue labels or comment commands.

    F-39: each issue may carry an intent that overrides the default
    4-layer "already handled" defense in the orchestrator.

      - NONE: no operator intent recorded
      - RETRY: reset local registry entry + close remote PR + new run
      - FOLLOWUP: keep PR, append commit on same branch
      - BLOCKED: permanently skip the issue
    """

    NONE = "none"
    RETRY = "retry"
    FOLLOWUP = "followup"
    BLOCKED = "blocked"


# Default label conventions for the three retry intents. Adapters accept an
# override at construction time; the keys map Intent values to label names.
DEFAULT_INTENT_LABELS: dict[str, str] = {
    "retry": "agent:retry",
    "followup": "agent:follow-up",
    "blocked": "agent:blocked",
}


def _normalize_label(value: str) -> str:
    return value.strip().lower()


def intent_from_label_set(
    labels: list[str] | None,
    intent_labels: dict[str, str] | None = None,
) -> Intent:
    """Resolve an Intent from a list of issue labels.

    Priority rules (per F-39 design):
      - `agent:blocked` wins over any other intent (permanent skip).
      - `agent:retry` + `agent:follow-up` together → FOLLOWUP is more
        conservative (keeps PR evidence), so it wins.
      - Otherwise return whichever single intent label is present, or NONE.
    """
    if not labels:
        return Intent.NONE
    mapping = intent_labels or DEFAULT_INTENT_LABELS
    retry_label = _normalize_label(mapping.get("retry", ""))
    followup_label = _normalize_label(mapping.get("followup", ""))
    blocked_label = _normalize_label(mapping.get("blocked", ""))
    normalized = {_normalize_label(label) for label in labels if label}
    if blocked_label and blocked_label in normalized:
        return Intent.BLOCKED
    if followup_label and followup_label in normalized:
        return Intent.FOLLOWUP
    if retry_label and retry_label in normalized:
        return Intent.RETRY
    return Intent.NONE


# ---------------------------------------------------------------------------
# F-39 Sub-D: comment command parsing
# ---------------------------------------------------------------------------


class Command(str, Enum):
    """Operator command expressed via an issue comment.

    Distinct from `Intent` because commands may carry side effects
    (e.g. UNBLOCK clears an abandoned status) and because not every
    command maps to a run-mode intent.
    """

    RETRY = "retry"
    FOLLOWUP = "followup"
    UNBLOCK = "unblock"


# Regex for `/agent <subcommand> [args]` at the start of a line / body.
# Permissive trailing text: any args / reason after the subcommand.
_AGENT_COMMAND_RE = re.compile(
    r"^/agent\s+(retry|follow-up|unblock)\b[^\n]*",
    re.IGNORECASE | re.MULTILINE,
)


def parse_agent_command(body: str | None) -> Command | None:
    """Extract a ClawCodex operator command from a comment body.

    Recognized forms (case-insensitive, anywhere in the body):
      - `/agent retry [reason...]`
      - `/agent follow-up [note...]`
      - `/agent unblock`

    Returns the matched `Command` or `None` if no recognized command
    is present. Only the first match is returned — operators that
    pile commands into one comment will get the first one honored.
    """
    if not body:
        return None
    match = _AGENT_COMMAND_RE.search(body)
    if not match:
        return None
    raw = match.group(1).lower()
    if raw == "retry":
        return Command.RETRY
    if raw == "follow-up":
        return Command.FOLLOWUP
    if raw == "unblock":
        return Command.UNBLOCK
    return None


def command_to_intent(command: Command) -> Intent:
    """Map a Command to the Intent the orchestrator should run with.

    `UNBLOCK` is a state-clearing meta-command and has no direct
    run-mode intent; it returns Intent.NONE so the next poll re-
    applies the label-based intent (or stays NONE if the operator
    removed the agent:blocked label too).
    """
    if command is Command.RETRY:
        return Intent.RETRY
    if command is Command.FOLLOWUP:
        return Intent.FOLLOWUP
    return Intent.NONE


# Priority merge: a comment command can override a label intent, but
# BLOCKED is sticky (per F-39 design: blocked is a permanent skip and
# only the unblock command / CLI override can lift it).
#
# Conservative rule between RETRY and FOLLOWUP: FOLLOWUP wins (preserves
# PR evidence). This mirrors the label-only priority in
# `intent_from_label_set`.
def merge_intents(label_intent: Intent, command_intent: Intent) -> Intent:
    """Merge a label-derived Intent with a command-derived Intent.

    Precedence (high → low):
      1. Intent.BLOCKED — sticky permanent skip.
      2. The more conservative of {RETRY, FOLLOWUP} = FOLLOWUP.
      3. Otherwise: command_intent wins over label_intent.
      4. Otherwise: whichever is non-NONE; else NONE.
    """
    if label_intent is Intent.BLOCKED or command_intent is Intent.BLOCKED:
        return Intent.BLOCKED
    if label_intent is Intent.FOLLOWUP or command_intent is Intent.FOLLOWUP:
        return Intent.FOLLOWUP
    if command_intent is not Intent.NONE:
        return command_intent
    if label_intent is not Intent.NONE:
        return label_intent
    return Intent.NONE


@dataclass(frozen=True)
class Comment:
    """Normalized issue comment."""

    id: str | None = None
    body: str | None = None
    author_login: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    in_reply_to_id: str | None = None  # for threading


@dataclass(frozen=True)
class CommandIntent:
    """F-39 Sub-F: a parsed `/agent ...` command plus provenance.

    The orchestrator needs the author login to perform the F-39 Sub-F
    role check ("only the issue author or a maintainer may trigger
    `/agent retry`"). Older callers that only need the command value
    should use ``intent.command``.
    """

    command: Command
    author_login: str | None = None
    comment_id: str | None = None
    comment_body: str | None = None


@dataclass(frozen=True)
class PullRequestFeedback:
    """Normalized pull request review feedback."""

    id: str
    source: Literal["conversation", "inline_review", "review_summary", "ci"]
    body: str
    author_login: str | None = None
    file_path: str | None = None
    line: int | None = None
    diff_hunk: str | None = None
    severity: Literal["info", "warning", "error"] | None = None
    status: Literal["open", "resolved", "outdated"] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    commit_sha: str | None = None
    url: str | None = None


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
    async def create_comment(self, issue_id: str, body: str) -> "Comment | None":
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

    async def update_pull_request(
        self,
        *,
        pull_request: "PullRequestRef",
        title: str | None = None,
        body: str | None = None,
    ) -> "PullRequestRef | None":
        """Update pull request metadata when supported."""
        return None

    async def update_comment(
        self,
        issue_id: str,
        comment_id: str,
        body: str,
    ) -> "Comment | None":
        """Update an existing issue comment when supported."""
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

    async def fetch_pull_request_feedback(
        self,
        *,
        pull_request: "PullRequestRef",
        include_ci_failures: bool = True,
        max_log_chars_per_check: int = 12_000,
    ) -> list[PullRequestFeedback]:
        """Fetch review feedback and CI failures for a pull request."""
        return []

    async def reply_to_pull_request_feedback(
        self,
        *,
        pull_request: "PullRequestRef",
        feedback: PullRequestFeedback,
        body: str,
    ) -> "Comment | None":
        """Reply to a pull request feedback item after a follow-up run."""
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

    async def extract_intent_from_labels(
        self,
        labels: list[str] | None,
    ) -> Intent:
        """Resolve an operator Intent from the issue's label set (F-39).

        Default implementation is a no-op (returns Intent.NONE) — it has
        no platform-specific label conventions. Subclasses that ship
        labels through `Issue.labels` should override this to apply
        platform-specific intent label resolution.

        See `intent_from_label_set` for the priority rules.
        """
        return Intent.NONE

    async def close_pull_request(
        self,
        pull_request: "PullRequestRef",
    ) -> bool:
        """Close a remote pull request (F-39 Sub-B reset path).

        Default implementation is a no-op (returns False). Subclasses
        for platforms that support closing a PR (GitHub, Gitee, GitCode
        via `PATCH /repos/{owner}/{repo}/pulls/{number}` with
        `{"state": "closed"}`) should override and return True on
        success.

        Returns True if the PR was closed (or was already closed).
        Returns False if the platform does not support PR closure.
        """
        return False

    async def fetch_issue_command_intent(
        self,
        issue_id: str,
        since_comment_id: str | None,
    ) -> "CommandIntent | None":
        """F-39 Sub-D + Sub-F: scan recent issue comments for a `/agent ...` command.

        Default implementation returns None (no command found). Subclasses
        that can fetch issue comments should override this to call
        `fetch_new_comments_since(issue_id, since_comment_id)`, iterate
        the results oldest-first, and return the first `Command` returned
        by `parse_agent_command(body)`. The returned `CommandIntent`
        MUST include the comment's `author_login` (F-39 Sub-F role
        check) and `comment_id` (for the `command_cursor`).

        Back-compat note: F-39 Sub-D callers that only need the
        `Command` value should read `intent.command`.

        Operators can pass `since_comment_id=None` to scan the full
        comment history; the orchestrator will typically pass the
        most recent `IssueRecord.command_cursor` so already-
        processed commands are skipped.

        Returns the first `CommandIntent` found, or `None` if no
        command is present in the unscanned portion of the comment
        stream.
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
