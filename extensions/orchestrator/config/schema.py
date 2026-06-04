"""Workflow configuration schema and validation.

Port of Symphony's Config.Schema (Ecto) to plain Python dataclasses.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..tracker import (
    default_active_states_for_kind,
    default_terminal_states_for_kind,
    normalize_tracker_kind,
    tracker_kind_info,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_env_value(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("$"):
        env_name = value[1:]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", env_name):
            env_value = os.environ.get(env_name)
            if env_value is None or env_value == "":
                return None
            return env_value
    return value


def _normalize_secret_value(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def _expand_path(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    resolved = _resolve_env_value(value)
    if resolved is None or resolved == "":
        return fallback
    return os.path.expanduser(resolved)


def _normalize_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k).lower(): _normalize_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_keys(v) for v in value]
    return value


def _drop_nil_values(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for k, v in value.items():
            cleaned = _drop_nil_values(v)
            if cleaned is not None:
                result[k] = cleaned
        return result
    if isinstance(value, list):
        return [_drop_nil_values(v) for v in value]
    return value


def _normalize_state_limits(limits: dict[str, Any] | None) -> dict[str, int]:
    if not limits:
        return {}
    result: dict[str, int] = {}
    for state_name, limit in limits.items():
        key = str(state_name).strip().lower()
        if key and isinstance(limit, int) and limit > 0:
            result[key] = limit
    return result


def _normalize_string_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return list(default)


def _normalize_workspace_strategy(value: Any) -> str:
    strategy = str(value or "isolated").strip().lower()
    if strategy not in {"isolated", "shared", "sequential"}:
        raise ValueError(
            "workspace.strategy must be one of: isolated, shared, sequential"
        )
    return strategy


def _resolve_orchestrator_permission_mode(
    raw_value: Any,
    *,
    is_orchestrator: bool,
) -> str:
    """Resolve permission_mode with headless auto-override.

    When a workflow.md is being loaded for the orchestrator (detected by the
    presence of a ``tracker`` section), a ``dontAsk`` value — whether explicit
    or default — is auto-promoted to ``bypassPermissions``. This ensures
    fully unattended execution, since ``dontAsk`` may still trigger
    ``ApprovalPolicy`` checks that can block tool calls in headless mode.

    Explicit non-default values are preserved so users can opt back into a
    more restrictive mode if needed.
    """
    normalized = str(raw_value).strip().lower() if raw_value else "dontask"
    if is_orchestrator and normalized == "dontask":
        return "bypassPermissions"
    return normalized or "dontAsk"


def _default_tmp_workspace() -> str:
    return os.path.join(os.environ.get("TMPDIR", "/tmp"), "symphony_workspaces")


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


@dataclass
class TrackerConfig:
    kind: str = "linear"
    endpoint: str = "https://api.linear.app/graphql"
    api_key: str | None = None
    project_slug: str | None = None
    owner: str | None = None
    repo: str | None = None
    clone_url: str | None = None
    assignee: str | None = None
    branch_prefix: str | None = None
    issues_path: str | None = None
    active_states: list[str] = field(
        default_factory=lambda: ["Todo", "In Progress"]
    )
    terminal_states: list[str] = field(
        default_factory=lambda: [
            "Closed",
            "Cancelled",
            "Canceled",
            "Duplicate",
            "Done",
        ]
    )


@dataclass
class PollingConfig:
    interval_ms: int = 30_000


@dataclass
class WorkspaceConfig:
    root: str = field(default_factory=_default_tmp_workspace)
    hooks: dict[str, Any] = field(default_factory=dict)
    repo_clone_url: str | None = None
    clone_depth: int | None = 1
    checkout_issue_branch: bool = True
    git_username: str | None = None
    git_token: str | None = None
    gitignore_patterns: list[str] = field(default_factory=list)
    strategy: str = "isolated"
    base_branch: str | None = None
    integration_branch: str | None = None
    require_clean_start: bool = True
    require_clean_between_issues: bool = True
    preserve_on_terminal: bool = True
    sequential_lock: bool = True


@dataclass
class WorkerConfig:
    ssh_hosts: list[str] = field(default_factory=list)
    max_concurrent_agents_per_host: int | None = None


@dataclass
class VerificationConfig:
    timeout_ms: int = 600_000


@dataclass
class AgentConfig:
    max_concurrent_agents: int = 10
    max_turns: int = 600
    max_retry_backoff_ms: int = 300_000
    max_retry_attempts: int = 5
    # Base delay (ms) for retries triggered by max_turns being exhausted.
    # Shared retry budget; capped at max_retry_backoff_ms via exponential backoff.
    max_turns_retry_delay_ms: int = 30_000
    max_concurrent_agents_by_state: dict[str, int] = field(default_factory=dict)
    # NEW: ClawCodex-specific fields
    provider: str = "anthropic"
    permission_mode: str = "dontAsk"
    test_command: str = ""
    build_command: str = ""
    lint_command: str = ""
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    # F-39 Sub-F: rate limit on operator-driven retries. When an
    # issue's `IssueRecord.retry_count` reaches this value, the
    # orchestrator refuses to honor further `agent:retry` labels /
    # `/agent retry` comment commands, even with a force flag from
    # the CLI (which is logged as a high-priority audit entry).
    max_retries_per_issue: int = 3
    # F-39 Sub-F: allow `agent:retry` / `agent:follow-up` /
    # `/agent retry` to be triggered by any GitHub-style user, not
    # just the issue author. By default we enforce the strict
    # "author or maintainer only" rule. Setting this to True
    # disables the role check (e.g. for trusted-team scenarios).
    allow_anyone_to_retry: bool = False
    # 429-aware in-turn backoff. When the upstream provider returns
    # HTTP 429 (rate limit) inside a single QueryRunner turn, the
    # AgentRunner sleeps for an exponentially-growing delay and
    # re-issues the same prompt instead of failing immediately. After
    # ``rate_limit_max_retries`` consecutive 429s the circuit breaker
    # opens (``status="rate_limit_circuit_open"``) and the run is
    # handed back to the orchestrator's inter-run retry queue.
    #
    # Distinct from ``max_turns_retry_delay_ms``: that field governs
    # the inter-run retry queue between separate AgentRunner.run()
    # invocations; these fields govern backoff WITHIN a single run.
    rate_limit_base_delay_ms: int = 30_000
    rate_limit_max_backoff_ms: int = 600_000
    rate_limit_exponential_factor: float = 2.0
    rate_limit_max_retries: int = 40
    # Minimum interval (ms) between successive provider API requests within
    # a single agent run. When non-zero, the agent sleeps for the remaining
    # time before issuing each new request. Default 1000ms (1s delay) to avoid
    # rate limits on providers with tight per-minute quotas (e.g. MiniMax
    # personal plan). Set to 0 for unlimited request rate.
    delay_between_requests_ms: int = 2000
    # F-44: Human review gating. When True, the orchestrator marks each
    # completed issue as PENDING_REVIEW instead of COMPLETED after sync,
    # requiring a human to run `orchestrator issue review --id <id> --approve`
    # before the issue transitions to COMPLETED.
    # Works with all tracker kinds (local, GitHub, Gitee, GitCode, Linear).
    review_required: bool = False


@dataclass
class CodexConfig:
    command: str = ""
    approval_policy: str | dict[str, Any] = field(
        default_factory=lambda: {
            "reject": {
                "sandbox_approval": True,
                "rules": True,
                "mcp_elicitations": True,
            }
        }
    )
    thread_sandbox: str = "workspace-write"
    turn_sandbox_policy: dict[str, Any] | None = None
    turn_timeout_ms: int = 3_600_000
    read_timeout_ms: int = 5_000
    stall_timeout_ms: int = 300_000


@dataclass
class HooksConfig:
    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    before_remove: str | None = None
    pre_commit: str | None = None
    pre_push: str | None = None
    post_sync: str | None = None
    timeout_ms: int = 60_000


@dataclass
class ReviewFeedbackConfig:
    enabled: bool = False
    mode: str = "manual"
    poll_interval_ms: int = 60_000
    max_feedback_items_per_run: int = 20
    include_ci_failures: bool = True
    reply_to_comments: bool = True
    ignore_authors: list[str] = field(default_factory=list)
    max_log_chars_per_check: int = 12_000
    max_followup_attempts_per_pr: int = 5


@dataclass
class ObservabilityConfig:
    dashboard_enabled: bool = True
    refresh_ms: int = 1_000
    render_interval_ms: int = 16


@dataclass
class ServerConfig:
    port: int | None = None
    host: str = "127.0.0.1"


# ---------------------------------------------------------------------------
# Top-level WorkflowConfig
# ---------------------------------------------------------------------------


@dataclass
class WorkflowConfig:
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    codex: CodexConfig = field(default_factory=CodexConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    review_feedback: ReviewFeedbackConfig = field(default_factory=ReviewFeedbackConfig)
    observability: ObservabilityConfig = field(
        default_factory=ObservabilityConfig
    )
    server: ServerConfig = field(default_factory=ServerConfig)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorkflowConfig":
        """Build from a raw dict (already parsed YAML front matter)."""
        raw = _normalize_keys(_drop_nil_values(raw))

        tracker_raw = raw.get("tracker", {})
        polling_raw = raw.get("polling", {})
        workspace_raw = raw.get("workspace", {})
        worker_raw = raw.get("worker", {})
        agent_raw = raw.get("agent", {})
        codex_raw = raw.get("codex", {})
        hooks_raw = raw.get("hooks", {})
        review_feedback_raw = raw.get("review_feedback", {})
        observability_raw = raw.get("observability", {})
        server_raw = raw.get("server", {})

        tracker_kind = normalize_tracker_kind(tracker_raw.get("kind", "linear"))
        tracker_info = tracker_kind_info(tracker_kind)
        tracker_active_states = _normalize_string_list(
            tracker_raw.get("active_states"),
            default_active_states_for_kind(tracker_kind),
        )
        tracker_terminal_states = _normalize_string_list(
            tracker_raw.get("terminal_states"),
            default_terminal_states_for_kind(tracker_kind),
        )

        tracker = TrackerConfig(
            kind=tracker_kind,
            endpoint=_resolve_env_value(tracker_raw.get("endpoint"))
            or tracker_info.default_endpoint,
            api_key=_normalize_secret_value(
                _resolve_env_value(tracker_raw.get("api_key"))
            )
            or _resolve_first_env(tracker_info.api_key_env_vars),
            project_slug=tracker_raw.get("project_slug"),
            owner=_resolve_env_value(tracker_raw.get("owner"))
            or _resolve_first_env(tracker_info.owner_env_vars),
            repo=_resolve_env_value(tracker_raw.get("repo"))
            or _resolve_first_env(tracker_info.repo_env_vars),
            clone_url=_resolve_env_value(tracker_raw.get("clone_url")),
            assignee=_resolve_env_value(tracker_raw.get("assignee"))
            or _resolve_first_env(tracker_info.assignee_env_vars),
            branch_prefix=_resolve_env_value(tracker_raw.get("branch_prefix")),
            issues_path=_normalize_secret_value(
                _expand_path(tracker_raw.get("issues_path"), "")
            ),
            active_states=tracker_active_states,
            terminal_states=tracker_terminal_states,
        )

        workspace_root = _expand_path(
            workspace_raw.get("root"), _default_tmp_workspace()
        )
        workspace_strategy = _normalize_workspace_strategy(
            workspace_raw.get("strategy")
        )
        workspace = WorkspaceConfig(
            root=workspace_root,
            hooks=workspace_raw.get("hooks", {}),
            repo_clone_url=_resolve_env_value(
                workspace_raw.get("repo_clone_url")
            ),
            clone_depth=workspace_raw.get("clone_depth", 1),
            checkout_issue_branch=workspace_raw.get(
                "checkout_issue_branch", True
            ),
            git_username=_resolve_env_value(workspace_raw.get("git_username")),
            git_token=_normalize_secret_value(
                _resolve_env_value(workspace_raw.get("git_token"))
            ),
            gitignore_patterns=workspace_raw.get(
                "gitignore_patterns",
                # F-45: ".reports" added so the orchestrator's per-workspace
                # report markdown/json files don't show up as "dirty" in
                # the pre-commit hook. The per-tool audit log lives in
                # ~/.clawcodex/, not the workspace, so it doesn't need
                # gitignore treatment here.
                ["event_logs", "*.pyc", "__pycache__", "*.egg-info", ".pytest_cache", ".reports"],
            ),
            strategy=workspace_strategy,
            base_branch=_resolve_env_value(workspace_raw.get("base_branch")),
            integration_branch=_resolve_env_value(
                workspace_raw.get("integration_branch")
            ),
            require_clean_start=bool(
                workspace_raw.get("require_clean_start", True)
            ),
            require_clean_between_issues=bool(
                workspace_raw.get("require_clean_between_issues", True)
            ),
            preserve_on_terminal=bool(
                workspace_raw.get("preserve_on_terminal", True)
            ),
            sequential_lock=bool(workspace_raw.get("sequential_lock", True)),
        )

        verification_raw = agent_raw.get("verification", {})
        agent = AgentConfig(
            max_concurrent_agents=agent_raw.get("max_concurrent_agents", 10),
            max_turns=agent_raw.get("max_turns", 600),
            max_retry_backoff_ms=agent_raw.get(
                "max_retry_backoff_ms", 300_000
            ),
            max_retry_attempts=agent_raw.get("max_retry_attempts", 5),
            max_turns_retry_delay_ms=agent_raw.get(
                "max_turns_retry_delay_ms", 30_000
            ),
            max_concurrent_agents_by_state=_normalize_state_limits(
                agent_raw.get("max_concurrent_agents_by_state")
            ),
            provider=agent_raw.get("provider", "anthropic"),
            permission_mode=_resolve_orchestrator_permission_mode(
                agent_raw.get("permission_mode"),
                is_orchestrator=bool(tracker_raw),
            ),
            test_command=_resolve_env_value(agent_raw.get("test_command")) or "",
            build_command=_resolve_env_value(agent_raw.get("build_command")) or "",
            lint_command=_resolve_env_value(agent_raw.get("lint_command")) or "",
            verification=VerificationConfig(
                timeout_ms=verification_raw.get("timeout_ms", 600_000)
            ),
            # F-39 Sub-F
            max_retries_per_issue=agent_raw.get("max_retries_per_issue", 3),
            allow_anyone_to_retry=bool(
                agent_raw.get("allow_anyone_to_retry", False)
            ),
            # 429-aware in-turn backoff (see AgentConfig docstring above)
            rate_limit_base_delay_ms=agent_raw.get(
                "rate_limit_base_delay_ms", 30_000
            ),
            rate_limit_max_backoff_ms=agent_raw.get(
                "rate_limit_max_backoff_ms", 600_000
            ),
            rate_limit_exponential_factor=float(
                agent_raw.get("rate_limit_exponential_factor", 2.0)
            ),
            rate_limit_max_retries=agent_raw.get(
                "rate_limit_max_retries", 40
            ),
            # F-44: review gate — when True, sync ends at PENDING_REVIEW
            # instead of COMPLETED, requiring human approve CLI command.
            review_required=bool(agent_raw.get("review_required", False)),
        )
        if workspace.strategy == "sequential":
            if agent.max_concurrent_agents != 1:
                raise ValueError(
                    "workspace.strategy=sequential requires agent.max_concurrent_agents=1"
                )
            over_limit_states = [
                state
                for state, limit in agent.max_concurrent_agents_by_state.items()
                if limit > 1
            ]
            if over_limit_states:
                raise ValueError(
                    "workspace.strategy=sequential requires all "
                    "agent.max_concurrent_agents_by_state values to be <= 1"
                )

        codex = CodexConfig(
            command=codex_raw.get("command", ""),
            approval_policy=codex_raw.get("approval_policy", CodexConfig().approval_policy),
            thread_sandbox=codex_raw.get("thread_sandbox", "workspace-write"),
            turn_sandbox_policy=codex_raw.get("turn_sandbox_policy"),
            turn_timeout_ms=codex_raw.get("turn_timeout_ms", 3_600_000),
            read_timeout_ms=codex_raw.get("read_timeout_ms", 5_000),
            stall_timeout_ms=codex_raw.get("stall_timeout_ms", 300_000),
        )

        hooks = HooksConfig(
            after_create=_resolve_env_value(hooks_raw.get("after_create")),
            before_run=_resolve_env_value(hooks_raw.get("before_run")),
            after_run=_resolve_env_value(hooks_raw.get("after_run")),
            before_remove=_resolve_env_value(
                hooks_raw.get("before_remove")
            ),
            pre_commit=_resolve_env_value(hooks_raw.get("pre_commit")),
            pre_push=_resolve_env_value(hooks_raw.get("pre_push")),
            post_sync=_resolve_env_value(hooks_raw.get("post_sync")),
            timeout_ms=hooks_raw.get("timeout_ms", 60_000),
        )

        return cls(
            tracker=tracker,
            polling=PollingConfig(
                interval_ms=polling_raw.get("interval_ms", 30_000)
            ),
            workspace=workspace,
            worker=WorkerConfig(
                ssh_hosts=worker_raw.get("ssh_hosts", []),
                max_concurrent_agents_per_host=worker_raw.get(
                    "max_concurrent_agents_per_host"
                ),
            ),
            agent=agent,
            codex=codex,
            hooks=hooks,
            review_feedback=ReviewFeedbackConfig(
                enabled=bool(review_feedback_raw.get("enabled", False)),
                mode=str(review_feedback_raw.get("mode", "manual")).strip().lower() or "manual",
                poll_interval_ms=review_feedback_raw.get("poll_interval_ms", 60_000),
                max_feedback_items_per_run=review_feedback_raw.get(
                    "max_feedback_items_per_run", 20
                ),
                include_ci_failures=bool(
                    review_feedback_raw.get("include_ci_failures", True)
                ),
                reply_to_comments=bool(review_feedback_raw.get("reply_to_comments", True)),
                ignore_authors=_normalize_string_list(
                    review_feedback_raw.get("ignore_authors"), []
                ),
                max_log_chars_per_check=review_feedback_raw.get(
                    "max_log_chars_per_check", 12_000
                ),
                max_followup_attempts_per_pr=review_feedback_raw.get(
                    "max_followup_attempts_per_pr", 5
                ),
            ),
            observability=ObservabilityConfig(
                dashboard_enabled=observability_raw.get(
                    "dashboard_enabled", True
                ),
                refresh_ms=observability_raw.get("refresh_ms", 1_000),
                render_interval_ms=observability_raw.get(
                    "render_interval_ms", 16
                ),
            ),
            server=ServerConfig(
                port=server_raw.get("port"),
                host=server_raw.get("host", "127.0.0.1"),
            ),
        )

    def resolve_turn_sandbox_policy(
        self, workspace_path: str | None = None
    ) -> dict[str, Any]:
        if self.codex.turn_sandbox_policy:
            return self.codex.turn_sandbox_policy
        root = workspace_path or self.workspace.root
        return {
            "type": "workspaceWrite",
            "writableRoots": [root],
            "readOnlyAccess": {"type": "fullAccess"},
            "networkAccess": False,
            "excludeTmpdirEnvVar": False,
            "excludeSlashTmp": False,
        }


def _resolve_first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = _normalize_secret_value(os.environ.get(name))
        if value:
            return value
    return None
