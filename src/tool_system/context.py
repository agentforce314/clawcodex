from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .errors import ToolPermissionError
from .task_manager import TaskManager
from src.permissions.types import PermissionAskHandler, ToolPermissionContext
from src.services.swarm.agent_name_registry import AgentNameRegistry
from src.task_registry import RuntimeTaskRegistry
from src.utils.abort_controller import AbortController


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
    # Chapter-10 / Chunk B / WI-1.3 — typed runtime-task registry. Houses
    # ``LocalShellTaskState`` / ``LocalAgentTaskState`` / etc. as
    # ``TaskStateBase`` subclasses. Replaces the un-typed
    # ``background_bash_tasks`` and ``_internal=True`` agent entries that
    # used to live on ``tasks``. ``runtime_tasks`` is the source of truth
    # for the chapter-10 task state machine; ``tasks`` continues to host
    # ``tasks_v2``/todo entries for the unrelated TaskCreate system.
    runtime_tasks: RuntimeTaskRegistry = field(default_factory=RuntimeTaskRegistry)
    # WI-5.1: per-message tool-result aggregate counter. The execution
    # pipeline (Step 11) reads + increments this each time a tool result
    # is mapped to its API form; when the running total exceeds
    # ``MAX_TOOL_RESULTS_PER_MESSAGE_CHARS`` (default 200K) the next
    # result is persisted to disk regardless of its individual size.
    # Reset to 0 between messages by the turn-loop dispatcher.
    #
    # ``_aggregate_lock`` synchronizes the read-decide-write across
    # concurrent tool dispatches (critic B6). The query loop's concurrent dispatch
    # uses ``asyncio.to_thread`` to fan out concurrency-safe tools (Read,
    # Grep, Glob) — without this lock, N threads would all read 0, all
    # decide their block is under the cap, and the per-message budget
    # would be silently bypassed. The full read+decide+write runs
    # serialized so the persistence decision uses the LIVE counter and
    # the cap is strictly enforced. Cost: the rare persist-to-disk path
    # serializes against the lock, but persists are O(1) per turn in
    # typical workloads (the common case under-threshold returns the
    # block without I/O).
    tool_result_chars_so_far: int = 0
    _aggregate_lock: threading.Lock = field(default_factory=threading.Lock)
    # Session-cumulative tokens spent on client-side advisor calls.
    # ``src/tool_system/tools/advisor.py`` accumulates here on every
    # consultation; the status surface reads them to display
    # ``advisor: <in>/<out>`` next to the worker's
    # token counts. Distinct from ``tool_result_chars_so_far`` (which
    # is a per-message budget tied to API-result persistence) — these
    # are per-session totals for UI display.
    advisor_input_tokens: int = 0
    advisor_output_tokens: int = 0
    # Chapter-10 / Chunk F / WI-6.1 — agent-name registry. Maps the
    # human-readable ``name`` (passed via Agent({name: "researcher"}))
    # to the random ``agent_id`` returned by the spawn. SendMessage
    # consults this registry first when resolving a ``to:`` field;
    # falling back to "treat ``to`` as a raw agent_id" when the name
    # isn't registered preserves the legacy code path.
    #
    # Per Chunk-F-Phase-6 critic concern C1 (Phase-7 fix): the registry
    # is a typed ``AgentNameRegistry`` (not a bare dict) so the
    # collision check + claim is atomic under its own RLock. Two
    # concurrent same-name spawns can't both succeed.
    #
    # Collision policy (gap analysis ambiguity #2 + critic C2):
    # * spawn-name-collision-with-running task → AgentNameAlreadyClaimedError
    #   (translated to ToolInputError at the agent-tool boundary).
    # * spawn-name-collision-with-terminal task → silent overwrite;
    #   old terminal holders remain reachable by raw task_id + auto-
    #   resume (WI-7.4).
    agent_name_registry: AgentNameRegistry = field(default_factory=AgentNameRegistry)
    # Background Bash commands spawned via ``run_in_background: true``.
    # Kept as a deprecated dict-of-dicts compatibility view during the
    # Chunk-B migration cycle; the bash spawn writer now populates
    # ``runtime_tasks`` as the source of truth and mirrors the legacy dict
    # shape here so any external test fixtures or readers that haven't
    # migrated yet continue to work. Removed in a follow-up phase.
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

    # C1 (components parity): request/reply protocol — the surface gets the
    # full PermissionAskRequest (tool_input → previews, suggestions →
    # "always allow") and answers with a PermissionAskReply (chosen_updates,
    # deny feedback). Replaced the legacy (tool_name, message, suggestion)
    # -> (allowed, enable) shape end-to-end; no shim.
    permission_handler: PermissionAskHandler | None = None

    options: ToolUseOptions = field(default_factory=ToolUseOptions)
    # Always present; callers that own the per-run cancellation lifecycle
    # (TUI bridge, REPL engine) overwrite this with their own controller
    # in ``submit()`` / ``__init__`` so tools, hooks, and subagents see
    # the same signal the UI trips. The default factory keeps the field
    # non-``None`` for unit tests and SDK callers that never explicitly
    # set it — readers can drop the historical ``if ctrl and …`` /
    # ``or AbortController()`` defensive checks that masked the "field
    # is None" hazard class.
    abort_controller: AbortController = field(default_factory=AbortController)
    messages: list[Any] = field(default_factory=list)
    set_response_length: Callable[[Callable[[int], int]], None] | None = None
    set_in_progress_tool_use_ids: Callable[[Callable[[set[str]], set[str]]], None] | None = None
    # Optional hook to stream a spawned subagent's live progress to the UI
    # (the Agent tool sets run_params.on_message → this). Wired only by the
    # agent-server (forwards an ``agent_progress`` message to the client); SDK /
    # REPL / unit-test paths leave it ``None`` and emit nothing.
    agent_progress_emit: Callable[[dict[str, Any]], None] | None = None
    # Mirrors TS Tool.ts:231
    # ``setHasInterruptibleToolInProgress?: (v: boolean) => void``.
    # Optional callback wired only in interactive (REPL/TUI) contexts; SDK
    # and unit-test paths leave it ``None`` and
    # ``StreamingToolExecutor._update_interruptible_state`` will skip the
    # call. The flag drives the UI's "press ESC to interrupt" indicator:
    # ``True`` only when at least one tool is currently executing AND every
    # executing tool's ``interrupt_behavior()`` returns ``"cancel"``. Fired
    # from ``StreamingToolExecutor._execute_tool`` on every transition into
    # or out of the ``executing`` status, matching TS lines 270, 290, 386.
    set_has_interruptible_tool_in_progress: Callable[[bool], None] | None = None
    query_tracking: QueryChainTracking | None = None
    file_reading_limits: FileReadingLimits | None = None
    glob_limits: GlobLimits | None = None
    content_replacement_state: Any | None = None
    agent_id: str | None = None
    # QUERY-1 — teammate identity (TS teammate.ts:125: a teammate requires
    # BOTH). Threaded by the Agent tool's NAMED spawn (run_agent →
    # create_subagent_context) when the parent carries a TeamCreate'd team;
    # None for plain subagents, so the teammate stop-hook block never fires
    # for them.
    teammate_name: str | None = None
    team_name: str | None = None
    agent_type: str | None = None
    tool_use_id: str | None = None
    user_modified: bool = False
    # Identifier of the active query/session. Surfaced to skills (SKILL.md
    # bodies may reference ``${CLAUDE_SESSION_ID}``) and any other tool
    # that needs to correlate with persisted session state. ``None`` is
    # interpreted as "unknown" by callers; substitutions yield an empty
    # string in that case.
    session_id: str | None = None

    # Chapter-12 / Phase 0 / WI-0.1 — frozen snapshot of hook config.
    # The snapshot is built once at startup by ``HookConfigManager.load()``
    # and updated only via explicit channels (the ``/hooks`` command or
    # an explicit ``reload_if_changed()`` call). Hook execution reads from
    # ``hook_config_manager.snapshot`` instead of ``options.hooks`` so a
    # malicious post-trust mutation of ``settings.json`` cannot affect
    # in-flight tool calls.
    #
    # ``options.hooks`` survives as a deprecated fallback for one release
    # cycle (see ``_get_hooks_from_snapshot`` in ``src/hooks/hook_executor.py``):
    # callers that still pass hooks via options get a ``DeprecationWarning``
    # but their behavior is preserved.
    hook_config_manager: Any | None = None
    # Chapter-12 / Phase 0 / WI-0.2 — workspace-trust gate. Bootstrap flips
    # this to ``True`` after the user accepts the trust dialog. Hooks (other
    # than ``HookSource.POLICY_SETTINGS``) are skipped while the workspace is
    # untrusted, mirroring TS' ``shouldSkipHookDueToTrust`` gate.
    workspace_trusted: bool = False

    # Chapter-9 / Fork Agents — captured bytes of the system prompt used on
    # the parent's most recent API call. Threaded into fork children so the
    # API request prefix is byte-identical across all parallel children
    # (chapter 9 §"The Byte-Identical Prefix Trick", Layer 1). Mirrors
    # ``toolUseContext.renderedSystemPrompt`` from
    # ``typescript/src/tools/AgentTool/AgentTool.tsx:496``.
    #
    # When ``None``, the fork path falls back to recomputing the parent
    # system prompt from ``options.custom_system_prompt`` or the active
    # agent definition. That fallback can diverge under feature-flag
    # transitions (GrowthBook cold→warm) and bust the prompt cache; the
    # main loop should populate this field whenever it has the rendered
    # bytes on hand for the parent's last turn.
    #
    # ch09 round-4 WI-1 — now POPULATED by query() at turn entry
    # (query.py, after the options.tools sync). Accepts the parent's actual
    # ``system_prompt`` shape: a ``list[dict]`` on the live agent-server /
    # headless path (build_effective_system_prompt) or a ``str`` on
    # string-prompt callers. The fork threads it verbatim into the child's
    # QueryParams.system_prompt so both sides run the identical
    # _call_model_sync assembly → byte-identical wire prefix.
    rendered_system_prompt: "str | list[dict[str, Any]] | None" = None

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
        # Session-granted directories from accepted permission updates.
        # `PermissionUpdateAddDirectories` (e.g. the user choosing "allow all
        # edits in <dir>/ during this session" for an out-of-cwd file) writes
        # `ToolPermissionContext.additional_working_directories`; without folding
        # those in here the grant never reaches `ensure_allowed_path` and the
        # next edit/read in that directory still fails. Resolved so the
        # /tmp → /private/tmp (macOS) match holds.
        try:
            for dir_path in self.permission_context.additional_working_directories.keys():
                try:
                    roots.append(Path(dir_path).resolve())
                except OSError:
                    roots.append(Path(dir_path))
        except Exception:
            pass
        # The session's tool-results spill dir is an internal path the runtime
        # writes large tool results to and then points the model back at (e.g. a
        # workflow subagent told to Read the offloaded result). Reading it back
        # must be allowed even though it sits outside workspace_root. Resolved so
        # the /tmp → /private/tmp (macOS) match holds against the resolved path.
        try:
            from src.services.tool_execution.tool_result_persistence import (
                resolve_tool_results_dir,
            )

            roots.append(resolve_tool_results_dir(self).resolve())
        except Exception:
            pass
        return tuple(roots)

    def ensure_allowed_path(self, path: str | Path) -> Path:
        p = Path(path).expanduser() if isinstance(path, str) else path.expanduser()
        if not p.is_absolute():
            base = self.cwd or self.workspace_root
            p = (base / p).resolve()
        else:
            p = p.resolve()
        # Mirror TS ``shouldBypassPermissions`` at
        # ``typescript/src/utils/permissions/permissions.ts:1268-1281``:
        # bypassPermissions mode (set by --dangerously-skip-permissions),
        # or plan mode when the user started with bypass available,
        # short-circuits the working-directory allowlist so the tool can
        # operate outside ``workspace_root``.
        mode = self.permission_context.mode
        if mode == "bypassPermissions" or (
            mode == "plan"
            and self.permission_context.is_bypass_permissions_mode_available
        ):
            return p
        roots = self.allowed_roots()
        if any(_is_within(p, root) for root in roots):
            return p
        roots_str = ", ".join(str(r) for r in roots)
        raise ToolPermissionError(f"path is outside allowed working directories: {p} (allowed: {roots_str})")

    def ensure_readable_path(self, path: str | Path) -> Path:
        """Like :meth:`ensure_allowed_path` but also permits harness-internal
        readable paths (tool-results / budget spill / scratchpad / memdir).

        The Read tool uses this so reading back the runtime's own spilled tool
        results — which sit outside ``workspace_root`` — does not raise. Mirrors
        TS keeping ``checkReadableInternalPath`` separate from the write/cwd
        allowlist; writes still go through :meth:`ensure_allowed_path`, so this
        never widens write or ``cd`` scope.
        """
        p = Path(path).expanduser() if isinstance(path, str) else path.expanduser()
        if not p.is_absolute():
            base = self.cwd or self.workspace_root
            p = (base / p).resolve()
        else:
            p = p.resolve()
        mode = self.permission_context.mode
        if mode == "bypassPermissions" or (
            mode == "plan"
            and self.permission_context.is_bypass_permissions_mode_available
        ):
            return p
        roots = self.allowed_roots()
        if any(_is_within(p, root) for root in roots):
            return p
        # Harness-internal readable paths (spilled tool results, scratchpad,
        # memory) are readable even though they sit outside the working roots.
        from src.permissions.filesystem import check_readable_internal_path

        if check_readable_internal_path(str(p), self):
            return p
        roots_str = ", ".join(str(r) for r in roots)
        raise ToolPermissionError(
            f"path is outside allowed working directories: {p} (allowed: {roots_str})"
        )

    def ensure_tool_allowed(self, tool_name: str) -> None:
        if self.permission_context.blocks(tool_name):
            raise ToolPermissionError(f"tool is blocked by permission context: {tool_name}")
