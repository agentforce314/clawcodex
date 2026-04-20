from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .errors import ToolPermissionError
from .task_manager import TaskManager
from src.permissions.types import ToolPermissionContext


def _resolve_path(p: str | Path) -> Path:
    return Path(p).expanduser().resolve()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


@dataclass
class ToolUseOptions:
    commands: list[Any] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)
    debug: bool = False
    main_loop_model: str = ""
    verbose: bool = False
    thinking_config: dict[str, Any] | None = None
    mcp_clients: list[Any] = field(default_factory=list)
    mcp_resources: dict[str, list[Any]] = field(default_factory=dict)
    is_non_interactive_session: bool = False
    agent_definitions: dict[str, Any] = field(default_factory=dict)
    max_budget_usd: float | None = None
    custom_system_prompt: str | None = None
    append_system_prompt: str | None = None
    query_source: str | None = None
    refresh_tools: Callable[[], list[Any]] | None = None
    provider_override: dict[str, str] | None = None
    hooks: dict[str, list[Any]] | None = None


@dataclass
class QueryChainTracking:
    chain_id: str = ""
    depth: int = 0


@dataclass
class FileReadingLimits:
    max_tokens: int | None = None
    max_size_bytes: int | None = None


@dataclass
class GlobLimits:
    max_results: int | None = None


@dataclass
class ToolContext:
    workspace_root: Path
    permission_context: ToolPermissionContext = field(
        default_factory=lambda: ToolPermissionContext(mode="bypassPermissions")
    )
    cwd: Path | None = None
    read_file_fingerprints: dict[Path, tuple[int, int] | tuple[int, int, bool]] = field(default_factory=dict)
    task_manager: TaskManager = field(default_factory=TaskManager)
    mcp_clients: dict[str, Any] = field(default_factory=dict)
    lsp_client: Any | None = None
    todos: list[dict[str, Any]] = field(default_factory=list)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Background Bash commands spawned via ``run_in_background: true``.
    # Keyed by background-task id. Each entry holds the ``Popen`` handle,
    # the on-disk output path, and book-keeping fields such as ``command``,
    # ``started_at``, and ``exit_code`` (populated once the process exits).
    background_bash_tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    plan_mode: bool = False
    worktree_root: Path | None = None
    outbox: list[dict[str, Any]] = field(default_factory=list)
    ask_user: Callable[[list[dict[str, Any]]], dict[str, str]] | None = None
    crons: dict[str, dict[str, Any]] = field(default_factory=dict)
    team: dict[str, Any] | None = None
    output_style_name: str | None = None
    output_style_dir: Path | None = None
    additional_working_directories: tuple[Path, ...] = ()
    allow_docs: bool = False

    permission_handler: Callable[[str, str, Optional[str]], tuple[bool, bool]] | None = None

    options: ToolUseOptions = field(default_factory=ToolUseOptions)
    abort_controller: Any | None = None
    messages: list[Any] = field(default_factory=list)
    set_response_length: Callable[[Callable[[int], int]], None] | None = None
    set_in_progress_tool_use_ids: Callable[[Callable[[set[str]], set[str]]], None] | None = None
    query_tracking: QueryChainTracking | None = None
    file_reading_limits: FileReadingLimits | None = None
    glob_limits: GlobLimits | None = None
    content_replacement_state: Any | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    tool_use_id: str | None = None
    user_modified: bool = False

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).resolve()
        if self.cwd is None:
            self.cwd = self.workspace_root
        else:
            self.cwd = Path(self.cwd).resolve()

    def mark_file_read(self, path: Path, *, partial: bool = False) -> None:
        stat = path.stat()
        self.read_file_fingerprints[path.resolve()] = (int(stat.st_mtime), int(stat.st_size), partial)

    def was_file_read_and_unchanged(self, path: Path) -> bool:
        resolved = path.resolve()
        fingerprint = self.read_file_fingerprints.get(resolved)
        if fingerprint is None:
            return False
        mtime, size = fingerprint[0], fingerprint[1]
        stat = resolved.stat()
        return (mtime, size) == (int(stat.st_mtime), int(stat.st_size))

    def file_read_status(self, path: Path) -> str:
        """Return the read status of a file for write/edit staleness checks.

        Returns one of:
        - ``"not_read"`` -- no prior read recorded
        - ``"partial"`` -- file was read with offset/limit (partial view)
        - ``"modified"`` -- file changed on disk since last read
        - ``"ok"`` -- file was fully read and unchanged
        """
        resolved = path.resolve()
        fingerprint = self.read_file_fingerprints.get(resolved)
        if fingerprint is None:
            return "not_read"
        mtime, size = fingerprint[0], fingerprint[1]
        is_partial = fingerprint[2] if len(fingerprint) > 2 else False
        if is_partial:
            return "partial"
        stat = resolved.stat()
        if (mtime, size) != (int(stat.st_mtime), int(stat.st_size)):
            return "modified"
        return "ok"

    def allowed_roots(self) -> tuple[Path, ...]:
        roots: list[Path] = [self.workspace_root]
        roots.extend(self.additional_working_directories)
        return tuple(roots)

    def ensure_allowed_path(self, path: str | Path) -> Path:
        p = Path(path).expanduser() if isinstance(path, str) else path.expanduser()
        if not p.is_absolute():
            base = self.cwd or self.workspace_root
            p = (base / p).resolve()
        else:
            p = p.resolve()
        roots = self.allowed_roots()
        if any(_is_within(p, root) for root in roots):
            return p
        roots_str = ", ".join(str(r) for r in roots)
        raise ToolPermissionError(f"path is outside allowed working directories: {p} (allowed: {roots_str})")

    def ensure_tool_allowed(self, tool_name: str) -> None:
        if self.permission_context.blocks(tool_name):
            raise ToolPermissionError(f"tool is blocked by permission context: {tool_name}")
