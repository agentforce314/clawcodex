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


@dataclass
class WorkerConfig:
    ssh_hosts: list[str] = field(default_factory=list)
    max_concurrent_agents_per_host: int | None = None


@dataclass
class AgentConfig:
    max_concurrent_agents: int = 10
    max_turns: int = 20
    max_retry_backoff_ms: int = 300_000
    max_retry_attempts: int = 5
    max_concurrent_agents_by_state: dict[str, int] = field(default_factory=dict)
    # NEW: ClawCodex-specific fields
    provider: str = "anthropic"
    permission_mode: str = "dontAsk"


@dataclass
class CodexConfig:
    command: str = "codex app-server"
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
    timeout_ms: int = 60_000


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
        observability_raw = raw.get("observability", {})
        server_raw = raw.get("server", {})

        tracker_kind = normalize_tracker_kind(tracker_raw.get("kind", "linear"))
        tracker_info = tracker_kind_info(tracker_kind)
        tracker_active_states = tracker_raw.get(
            "active_states", default_active_states_for_kind(tracker_kind)
        )
        tracker_terminal_states = tracker_raw.get(
            "terminal_states",
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
            active_states=tracker_active_states,
            terminal_states=tracker_terminal_states,
        )

        workspace_root = _expand_path(
            workspace_raw.get("root"), _default_tmp_workspace()
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
                ["event_logs", "*.pyc", "__pycache__", "*.egg-info", ".pytest_cache"],
            ),
        )

        agent = AgentConfig(
            max_concurrent_agents=agent_raw.get("max_concurrent_agents", 10),
            max_turns=agent_raw.get("max_turns", 20),
            max_retry_backoff_ms=agent_raw.get(
                "max_retry_backoff_ms", 300_000
            ),
            max_concurrent_agents_by_state=_normalize_state_limits(
                agent_raw.get("max_concurrent_agents_by_state")
            ),
            provider=agent_raw.get("provider", "anthropic"),
            permission_mode=agent_raw.get("permission_mode", "dontAsk"),
        )

        codex = CodexConfig(
            command=codex_raw.get("command", "codex app-server"),
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
