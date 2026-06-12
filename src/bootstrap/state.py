"""Process-wide session state. Mirrors ``typescript/src/bootstrap/state.ts``.

DO NOT ADD MORE STATE HERE — BE JUDICIOUS WITH GLOBAL STATE.

This module is a DAG leaf — it must not import from any feature subsystem
package (``src/tui``, ``src/repl``, ``src/agent``, ``src/services``,
``src/query``, ``src/context_system``, ``src/permissions``,
``src/command_system``, ``src/tool_system``, ``src/coordinator``). The
``import-linter`` contract (see ``.importlinter`` / ``pyproject.toml``)
enforces this when the linter is installed; until then, treat the rule as
review discipline.

Phase 2 of the bootstrap-folder parity sweep (see
``my-docs/get-parity-by-folder/bootstrap-refactoring-plan.md``) brings the
``_BootstrapState`` field count to ~50 and adds:

  * Turn-level metric accumulators (hook / tool / classifier duration + count)
  * Token aggregators over ``model_usage`` (input / output / cache-read /
    cache-creation / web-search)
  * Turn-output-token budget primitives (module-level globals, matching TS
    lines 756-775)
  * ``get_total_duration`` (returns milliseconds for TS parity)
  * ``get_usage_for_model``
  * ``prefer_third_party_authentication`` derivation
  * Allowed-settings-sources allowlist (default = the TS five-source list)
  * Invoked-skills tracker with composite-key dict
  * Permanent no-op stubs for slow-ops and REPL-bridge entries

Class-C symbols from the gap analysis (channels, scheduled tasks,
plugins, hook callbacks SDK, agent color picker, mode-exit one-shots,
model strings, scroll-drain debouncer, statsStore, SDK betas, FD-borne
credentials, plan slug cache, teleport reliability info, deferred-write
interaction time, last API request snapshots, feature-flag toggles)
remain deferred — they land when their consumers do.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator, NewType

from src.utils.signal import Signal, create_signal


# ---------------------------------------------------------------------------
# Type aliases / value types
# ---------------------------------------------------------------------------

# Branded session ID (mirrors TS ``type SessionId = string & {__brand: ...}``).
# Python's NewType is purely a static-analysis hint — it does not enforce
# the brand at runtime — but it prevents accidental crossover with other
# string-typed identifiers in type-checked code.
SessionId = NewType("SessionId", str)


@dataclass
class ModelUsage:
    """Per-model usage accumulator. Mirrors the TS ``ModelUsage`` type used
    by ``addToTotalCostState`` and ``setCostStateForRestore``.

    Phase 1 keeps this minimal; richer fields (webSearchRequests, tool-call
    counts, etc.) land in Phase 2 alongside the cost-tracker consolidation.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0
    web_search_requests: int = 0


@dataclass
class InvokedSkillInfo:
    """One invoked skill, preserved across compaction.

    Mirrors TS ``InvokedSkillInfo`` at ``bootstrap/state.ts:1425-1431``.
    Used by ``services/compact/post_compact_attachments.py`` to re-emit
    skill content after a ``/compact`` rewinds the transcript.
    """

    skill_name: str
    skill_path: str
    content: str
    invoked_at: float  # seconds since epoch (time.time())
    agent_id: str | None


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------


def _resolve_real_cwd() -> str:
    """Resolve ``os.getcwd()`` through ``os.path.realpath`` and NFC-normalize.

    Mirrors TS ``realpathSync(cwd()).normalize('NFC')`` at module init.
    Captures the cwd at process start — this matches the chapter's note:
    "The ``originalCwd`` is resolved through ``realpathSync`` and
    NFC-normalized at process start. It never changes."

    On filesystems where ``realpath`` raises (CloudStorage EPERM on macOS),
    falls back to the raw cwd, still NFC-normalized.
    """
    raw = os.getcwd()
    try:
        return unicodedata.normalize("NFC", os.path.realpath(raw))
    except OSError:
        return unicodedata.normalize("NFC", raw)


def _new_session_id() -> SessionId:
    return SessionId(str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# State dataclass
# ---------------------------------------------------------------------------


@dataclass
class _BootstrapState:
    """The Phase 1 field subset.

    Fields are grouped by domain matching ``bootstrap/state.ts``. New
    fields land here as their consuming subsystem is ported. **Do not**
    split this into multiple dataclasses — the single-file discipline is
    what enforces the DAG-leaf property (one file to lint, one file to
    reason about).
    """

    # --- Identity & paths (TS: lines 46-50, 100-103, 219) -------------------
    original_cwd: str = field(default_factory=_resolve_real_cwd)
    project_root: str = field(default_factory=_resolve_real_cwd)
    cwd: str = field(default_factory=_resolve_real_cwd)
    session_id: SessionId = field(default_factory=_new_session_id)
    parent_session_id: SessionId | None = None
    session_project_dir: str | None = None

    # --- Session flags (TS: lines 71-79, 153-157, etc.) ---------------------
    is_interactive: bool = False
    # Pre-existing default is "claude-code"; TS source uses "cli"
    # (``bootstrap/state.ts:305``). Keep "claude-code" until existing
    # call sites are migrated; tracking as a follow-up.
    client_type: str = "claude-code"
    session_trust_accepted: bool = False
    session_persistence_disabled: bool = False
    is_remote_mode: bool = False
    has_exited_plan_mode: bool = False

    # --- Cost & timing (TS: lines 51-66) -----------------------------------
    total_cost_usd: float = 0.0
    total_api_duration: int = 0
    total_api_duration_without_retries: int = 0
    total_tool_duration: int = 0
    start_time: float = field(default_factory=time.time)
    last_interaction_time: float = field(default_factory=time.time)
    total_lines_added: int = 0
    total_lines_removed: int = 0
    has_unknown_model_cost: bool = False
    model_usage: dict[str, ModelUsage] = field(default_factory=dict)

    # --- Turn-level metric accumulators (TS: lines 47-52, 624-672) ---------
    # Bumped by add_to_turn_*_duration / add_to_tool_duration / ...; reset
    # by reset_turn_*_duration at end of each turn. The chapter's
    # "turn breakdown" telemetry depends on these.
    turn_hook_duration_ms: int = 0
    turn_tool_duration_ms: int = 0
    turn_classifier_duration_ms: int = 0
    turn_hook_count: int = 0
    turn_tool_count: int = 0
    turn_classifier_count: int = 0

    # --- Allowed settings sources (TS: lines 75, 287-292) -------------------
    # Default mirrors TS line 287-292. Bootstrap is a DAG leaf, so we keep
    # this as a plain list[str] rather than importing src/settings/ enums.
    # Translators between this list and the settings package's internal
    # enum live in src/settings/, not here.
    allowed_setting_sources: list[str] = field(
        default_factory=lambda: [
            "userSettings",
            "projectSettings",
            "localSettings",
            "flagSettings",
            "policySettings",
        ]
    )

    # --- Invoked skills (TS: lines 150-160) ---------------------------------
    # Composite key: f"{agent_id or ''}:{skill_name}" — string, NOT a tuple,
    # to keep parity with TS Map<string, ...> for any future serialization.
    invoked_skills: dict[str, InvokedSkillInfo] = field(default_factory=dict)

    # --- Cache optimization (TS: lines 122-123, 202-205, 207, 256) ---------
    cached_claude_md_content: str | None = None
    system_prompt_section_cache: dict[str, str | None] = field(default_factory=dict)
    pending_post_compaction: bool = False
    additional_directories_for_claude_md: list[str] = field(default_factory=list)

    # --- Model (TS: lines 68-70) -------------------------------------------
    main_loop_model_override: str | None = None
    initial_main_loop_model: str | None = None

    # --- API correlation (TS: lines 244-252, 205) --------------------------
    prompt_id: str | None = None
    last_main_request_id: str | None = None
    last_api_completion_timestamp: float | None = None
    last_emitted_date: str | None = None

    # --- Backwards-compat extras (pre-existing Python field) ---------------
    extra: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Module-scope singleton
# ---------------------------------------------------------------------------


_STATE: _BootstrapState = _BootstrapState()


# ---------------------------------------------------------------------------
# WI-4: Turn-output-token snapshot & budget continuation
#
# Module-level globals — mirror TS lines 756-775. Not on ``_BootstrapState``
# because the TS code keeps them module-scoped, and the parity audit
# preserves shape.
#
# RESET ALSO MUST HANDLE THESE: ``reset_state_for_tests()`` rebinds all three
# below. Any future addition to this block needs a matching reset line.
# ---------------------------------------------------------------------------

_output_tokens_at_turn_start: int = 0
_current_turn_token_budget: int | None = None
_budget_continuation_count: int = 0


# ---------------------------------------------------------------------------
# Per-query SDK context (contextvars-based, mirrors TS AsyncLocalStorage)
# ---------------------------------------------------------------------------


@dataclass
class SdkContext:
    """Per-query context that overrides the global ``_STATE`` for the
    duration of an async/sync call stack.

    Mirrors TS ``SdkContext`` (``bootstrap/state.ts:439-445``). When set
    via ``run_with_sdk_context(...)``, reads of ``session_id``,
    ``session_project_dir``, ``cwd``, ``original_cwd``, and
    ``parent_session_id`` return context-scoped values rather than the
    global singleton. Used by the Agent SDK when multiple concurrent
    queries share a process and each needs its own identity view.

    Python implementation note: ``contextvars.ContextVar`` is the
    asyncio-aware equivalent of Node's ``async_hooks.AsyncLocalStorage``.
    Both propagate context across ``await`` points without explicit
    threading; both isolate sibling tasks.

    Sentinel semantics: ``None`` on ``cwd`` / ``original_cwd`` means
    "not set in this context — fall back to the global". Matches TS
    nullish-coalescing (``ctx?.originalCwd ?? STATE.originalCwd``).
    Writing ``set_original_cwd("")`` from inside the context stores the
    empty string explicitly and returns ``""``; only ``None`` triggers
    the global fallback.
    """

    session_id: SessionId
    session_project_dir: str | None = None
    cwd: str | None = None
    original_cwd: str | None = None
    parent_session_id: SessionId | None = None


_sdk_context: contextvars.ContextVar[SdkContext | None] = contextvars.ContextVar(
    "sdk_context", default=None
)


def _get_sdk_context() -> SdkContext | None:
    """Return the current per-query SDK context, or None if not inside one."""
    return _sdk_context.get()


@contextlib.contextmanager
def run_with_sdk_context(context: SdkContext) -> Iterator[None]:
    """Run a block with an SDK-specific context overriding global state.

    Mirrors TS ``runWithSdkContext`` (``bootstrap/state.ts:460-462``).
    Within the ``with`` block, ``get_session_id``, ``get_original_cwd``,
    ``get_cwd_state``, ``get_session_project_dir``, and
    ``get_parent_session_id`` read from ``context``. Mutations via
    ``switch_session`` / ``regenerate_session_id`` / ``set_original_cwd``
    / ``set_cwd_state`` mutate the context (not the global) while inside.

    Example::

        ctx = SdkContext(
            session_id=SessionId("..."),
            original_cwd="/tmp/sdk-workspace",
            cwd="/tmp/sdk-workspace",
        )
        with run_with_sdk_context(ctx):
            run_query()   # reads ctx.session_id, not the global

    Async usage: nested ``await`` calls within the ``with`` block inherit
    the context automatically — contextvars propagate across asyncio
    task boundaries created with ``asyncio.create_task`` (since 3.7).
    """
    token = _sdk_context.set(context)
    try:
        yield
    finally:
        _sdk_context.reset(token)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

# Fires whenever ``switch_session`` mutates ``session_id``. Listeners
# receive the new session id as the single positional argument. Bootstrap
# cannot import the listener modules (DAG-leaf rule), so consumers register
# via ``on_session_switch(cb)`` and are responsible for unsubscribing.
_session_switched: Signal = create_signal()
on_session_switch = _session_switched.subscribe


# ===========================================================================
# Accessors — Identity & paths
# ===========================================================================


def get_session_id() -> SessionId:
    """Current session ID. Mirrors TS ``getSessionId()``.

    Within a ``run_with_sdk_context(...)`` block, returns the context's
    session_id rather than the global. Outside, returns the global.
    """
    ctx = _get_sdk_context()
    return ctx.session_id if ctx is not None else _STATE.session_id


def regenerate_session_id(*, set_current_as_parent: bool = False) -> SessionId:
    """Generate a fresh UUID session ID and install it.

    If ``set_current_as_parent`` is True, the outgoing session ID becomes
    the new ``parent_session_id``. Mirrors TS ``regenerateSessionId``.
    Does NOT emit ``session_switched`` — that is reserved for
    ``switch_session`` (which is the resume/teleport path). This is the
    /clear or new-session-within-same-process path.

    Inside ``run_with_sdk_context``: mutates the context. Outside:
    mutates the global.
    """
    ctx = _get_sdk_context()
    current = ctx.session_id if ctx is not None else _STATE.session_id
    if set_current_as_parent:
        if ctx is not None:
            ctx.parent_session_id = current
        else:
            _STATE.parent_session_id = current
    new_id = _new_session_id()
    if ctx is not None:
        ctx.session_id = new_id
        ctx.session_project_dir = None
    else:
        _STATE.session_id = new_id
        _STATE.session_project_dir = None
    return new_id


def switch_session(session_id: SessionId, project_dir: str | None = None) -> None:
    """Atomically switch the active session.

    ``session_id`` and ``session_project_dir`` always change together; this
    is the **only** mutator for either. Mirrors TS ``switchSession``
    (``bootstrap/state.ts:522``) and the CC-34 single-setter discipline.
    Fires the ``session_switched`` signal after the mutation.

    Inside ``run_with_sdk_context``: mutates the context. Outside:
    mutates the global. The signal fires regardless.
    """
    ctx = _get_sdk_context()
    if ctx is not None:
        ctx.session_id = session_id
        ctx.session_project_dir = project_dir
    else:
        _STATE.session_id = session_id
        _STATE.session_project_dir = project_dir
    _session_switched.emit(session_id)


def get_parent_session_id() -> SessionId | None:
    ctx = _get_sdk_context()
    return ctx.parent_session_id if ctx is not None else _STATE.parent_session_id


def get_session_project_dir() -> str | None:
    ctx = _get_sdk_context()
    return ctx.session_project_dir if ctx is not None else _STATE.session_project_dir


def get_original_cwd() -> str:
    ctx = _get_sdk_context()
    if ctx is not None and ctx.original_cwd is not None:
        return ctx.original_cwd
    return _STATE.original_cwd


def set_original_cwd(path: str) -> None:
    normalized = unicodedata.normalize("NFC", path)
    ctx = _get_sdk_context()
    if ctx is not None:
        ctx.original_cwd = normalized
    else:
        _STATE.original_cwd = normalized


def get_project_root() -> str:
    """Stable project root. Set once at startup (and by ``--worktree``);
    NOT updated by mid-session ``EnterWorktreeTool``. Mirrors TS
    ``getProjectRoot``.

    Always reads from the global — project_root is process-scope, not
    per-query, per the chapter's "project identity" framing."""
    return _STATE.project_root


def set_project_root(path: str) -> None:
    """Only for ``--worktree`` startup flag. Mirrors TS ``setProjectRoot``.
    Always mutates the global — does NOT respect SDK context."""
    _STATE.project_root = unicodedata.normalize("NFC", path)


def get_cwd_state() -> str:
    ctx = _get_sdk_context()
    if ctx is not None and ctx.cwd is not None:
        return ctx.cwd
    return _STATE.cwd


def set_cwd_state(path: str) -> None:
    normalized = unicodedata.normalize("NFC", path)
    ctx = _get_sdk_context()
    if ctx is not None:
        ctx.cwd = normalized
    else:
        _STATE.cwd = normalized


# ===========================================================================
# Accessors — Session flags
# ===========================================================================


def get_is_interactive() -> bool:
    return _STATE.is_interactive


def set_is_interactive(value: bool) -> None:
    _STATE.is_interactive = bool(value)


def get_is_non_interactive_session() -> bool:
    return not _STATE.is_interactive


def get_client_type() -> str:
    return _STATE.client_type


def set_client_type(value: str) -> None:
    _STATE.client_type = str(value)


def get_session_trust_accepted() -> bool:
    return _STATE.session_trust_accepted


def set_session_trust_accepted(value: bool) -> None:
    _STATE.session_trust_accepted = bool(value)


def is_session_persistence_disabled() -> bool:
    return _STATE.session_persistence_disabled


def set_session_persistence_disabled(value: bool) -> None:
    _STATE.session_persistence_disabled = bool(value)


def get_is_remote_mode() -> bool:
    return _STATE.is_remote_mode


def set_is_remote_mode(value: bool) -> None:
    _STATE.is_remote_mode = bool(value)


def has_exited_plan_mode_in_session() -> bool:
    return _STATE.has_exited_plan_mode


def set_has_exited_plan_mode(value: bool) -> None:
    _STATE.has_exited_plan_mode = bool(value)


def prefer_third_party_authentication() -> bool:
    """Mirrors TS ``preferThirdPartyAuthentication`` (state.ts:1157-1160).

    True iff the session is non-interactive AND the client type is not
    ``'claude-vscode'`` (the IDE extension is treated as 1P for auth
    purposes even though it runs non-interactively).
    """
    return get_is_non_interactive_session() and _STATE.client_type != "claude-vscode"


# ===========================================================================
# Accessors — Cost & timing
# ===========================================================================


def get_total_cost_usd() -> float:
    return _STATE.total_cost_usd


def add_to_total_cost_state(
    cost: float,
    model_usage: ModelUsage,
    model: str,
) -> None:
    """Record a cost event. Mirrors TS ``addToTotalCostState``."""
    _STATE.model_usage[model] = model_usage
    _STATE.total_cost_usd += cost


def get_total_api_duration() -> int:
    return _STATE.total_api_duration


def get_total_api_duration_without_retries() -> int:
    return _STATE.total_api_duration_without_retries


def add_to_total_duration_state(duration: int, duration_without_retries: int) -> None:
    _STATE.total_api_duration += duration
    _STATE.total_api_duration_without_retries += duration_without_retries


def get_total_tool_duration() -> int:
    return _STATE.total_tool_duration


def add_to_tool_duration(duration: int) -> None:
    """Record a tool-execution duration.

    Mirrors TS ``addToToolDuration`` (state.ts:618-622): bumps the
    cumulative ``total_tool_duration`` AND the per-turn
    ``turn_tool_duration_ms`` AND the per-turn ``turn_tool_count``.
    All three update atomically.
    """
    _STATE.total_tool_duration += duration
    _STATE.turn_tool_duration_ms += duration
    _STATE.turn_tool_count += 1


def get_total_lines_added() -> int:
    return _STATE.total_lines_added


def get_total_lines_removed() -> int:
    return _STATE.total_lines_removed


def add_to_total_lines_changed(added: int, removed: int) -> None:
    _STATE.total_lines_added += added
    _STATE.total_lines_removed += removed


def has_unknown_model_cost() -> bool:
    return _STATE.has_unknown_model_cost


def set_has_unknown_model_cost() -> None:
    _STATE.has_unknown_model_cost = True


def get_model_usage() -> dict[str, ModelUsage]:
    """Return the per-model usage map. Callers may read but should not
    mutate; use ``add_to_total_cost_state`` to record."""
    return _STATE.model_usage


def get_usage_for_model(model: str) -> ModelUsage | None:
    """Return the ``ModelUsage`` recorded for ``model`` or ``None`` if no
    cost event has been recorded for it. Mirrors TS ``getUsageForModel``
    (state.ts:862-864).
    """
    return _STATE.model_usage.get(model)


def get_total_input_tokens() -> int:
    """Sum of ``input_tokens`` across every model in ``model_usage``.

    Mirrors TS ``getTotalInputTokens`` (state.ts:736-738) — TS uses
    ``sumBy(values, 'inputTokens')``.
    """
    return sum(usage.input_tokens for usage in _STATE.model_usage.values())


def get_total_output_tokens() -> int:
    """Sum of ``output_tokens`` across every model in ``model_usage``.
    Mirrors TS ``getTotalOutputTokens`` (state.ts:740-742)."""
    return sum(usage.output_tokens for usage in _STATE.model_usage.values())


def get_total_cache_read_input_tokens() -> int:
    """Sum of ``cache_read_input_tokens`` across every model in
    ``model_usage``. Mirrors TS ``getTotalCacheReadInputTokens``
    (state.ts:744-746)."""
    return sum(
        usage.cache_read_input_tokens for usage in _STATE.model_usage.values()
    )


def get_total_cache_creation_input_tokens() -> int:
    """Sum of ``cache_creation_input_tokens`` across every model in
    ``model_usage``. Mirrors TS ``getTotalCacheCreationInputTokens``
    (state.ts:748-750)."""
    return sum(
        usage.cache_creation_input_tokens for usage in _STATE.model_usage.values()
    )


def get_total_web_search_requests() -> int:
    """Sum of ``web_search_requests`` across every model in ``model_usage``.
    Mirrors TS ``getTotalWebSearchRequests`` (state.ts:752-754)."""
    return sum(usage.web_search_requests for usage in _STATE.model_usage.values())


def get_start_time() -> float:
    return _STATE.start_time


def get_total_duration() -> int:
    """Wall-clock duration since session start, in MILLISECONDS.

    Mirrors TS ``getTotalDuration`` (state.ts:606-608), which returns
    ``Date.now() - STATE.startTime`` (already milliseconds in JS).
    Python ``start_time`` is in seconds, so cast to int ms here so
    downstream formatters that assume ms match TS exactly.
    """
    return int((time.time() - _STATE.start_time) * 1000)


def get_last_interaction_time() -> float:
    return _STATE.last_interaction_time


def update_last_interaction_time() -> None:
    _STATE.last_interaction_time = time.time()


def reset_cost_state() -> None:
    """Reset accumulators for a fresh session. Mirrors TS ``resetCostState``."""
    _STATE.total_cost_usd = 0.0
    _STATE.total_api_duration = 0
    _STATE.total_api_duration_without_retries = 0
    _STATE.total_tool_duration = 0
    _STATE.start_time = time.time()
    _STATE.total_lines_added = 0
    _STATE.total_lines_removed = 0
    _STATE.has_unknown_model_cost = False
    _STATE.model_usage = {}
    _STATE.prompt_id = None


def set_cost_state_for_restore(
    *,
    total_cost_usd: float,
    total_api_duration: int,
    total_api_duration_without_retries: int,
    total_tool_duration: int,
    total_lines_added: int,
    total_lines_removed: int,
    last_duration: float | None = None,
    model_usage: dict[str, ModelUsage] | None = None,
) -> None:
    """Restore accumulators from a persisted session snapshot.

    Called by the (deferred Phase 2) ``restore_cost_state_for_session``
    orchestrator. Mirrors TS ``setCostStateForRestore``
    (``bootstrap/state.ts:955``).
    """
    _STATE.total_cost_usd = total_cost_usd
    _STATE.total_api_duration = total_api_duration
    _STATE.total_api_duration_without_retries = total_api_duration_without_retries
    _STATE.total_tool_duration = total_tool_duration
    _STATE.total_lines_added = total_lines_added
    _STATE.total_lines_removed = total_lines_removed
    if model_usage is not None:
        _STATE.model_usage = dict(model_usage)
    if last_duration is not None:
        _STATE.start_time = time.time() - last_duration


# ===========================================================================
# Accessors — Cache optimization
# ===========================================================================


def get_cached_claude_md_content() -> str | None:
    return _STATE.cached_claude_md_content


def set_cached_claude_md_content(content: str | None) -> None:
    _STATE.cached_claude_md_content = content


def get_system_prompt_section_cache() -> dict[str, str | None]:
    return _STATE.system_prompt_section_cache


def set_system_prompt_section_cache_entry(name: str, value: str | None) -> None:
    _STATE.system_prompt_section_cache[name] = value


def clear_system_prompt_section_state() -> None:
    _STATE.system_prompt_section_cache.clear()


def mark_post_compaction() -> None:
    """Mark that a compaction just occurred. Consumed once by the next API
    success event, then auto-resets. Mirrors TS ``markPostCompaction``."""
    _STATE.pending_post_compaction = True


def consume_post_compaction() -> bool:
    """Returns True once after compaction, then False on subsequent calls.
    Mirrors TS ``consumePostCompaction``."""
    was = _STATE.pending_post_compaction
    _STATE.pending_post_compaction = False
    return was


def get_additional_directories_for_claude_md() -> list[str]:
    return _STATE.additional_directories_for_claude_md


def set_additional_directories_for_claude_md(directories: list[str]) -> None:
    _STATE.additional_directories_for_claude_md = list(directories)


# ===========================================================================
# Accessors — Model
# ===========================================================================


def get_main_loop_model_override() -> str | None:
    return _STATE.main_loop_model_override


def set_main_loop_model_override(model: str | None) -> None:
    _STATE.main_loop_model_override = model


def get_initial_main_loop_model() -> str | None:
    return _STATE.initial_main_loop_model


def set_initial_main_loop_model(model: str | None) -> None:
    _STATE.initial_main_loop_model = model


# ===========================================================================
# Accessors — API correlation
# ===========================================================================


def get_prompt_id() -> str | None:
    return _STATE.prompt_id


def set_prompt_id(prompt_id: str | None) -> None:
    _STATE.prompt_id = prompt_id


def get_last_main_request_id() -> str | None:
    return _STATE.last_main_request_id


def set_last_main_request_id(request_id: str) -> None:
    _STATE.last_main_request_id = request_id


def get_last_api_completion_timestamp() -> float | None:
    return _STATE.last_api_completion_timestamp


def set_last_api_completion_timestamp(ts: float) -> None:
    _STATE.last_api_completion_timestamp = ts


def get_last_emitted_date() -> str | None:
    return _STATE.last_emitted_date


def set_last_emitted_date(date: str | None) -> None:
    _STATE.last_emitted_date = date


# ===========================================================================
# Accessors — Turn-level metrics (TS: lines 624-672)
# ===========================================================================


def get_turn_hook_duration_ms() -> int:
    return _STATE.turn_hook_duration_ms


def add_to_turn_hook_duration(duration_ms: int) -> None:
    """Bump per-turn hook duration & count. Mirrors TS
    ``addToTurnHookDuration`` (state.ts:628-631)."""
    _STATE.turn_hook_duration_ms += duration_ms
    _STATE.turn_hook_count += 1


def reset_turn_hook_duration() -> None:
    _STATE.turn_hook_duration_ms = 0
    _STATE.turn_hook_count = 0


def get_turn_hook_count() -> int:
    return _STATE.turn_hook_count


def get_turn_tool_duration_ms() -> int:
    return _STATE.turn_tool_duration_ms


def reset_turn_tool_duration() -> None:
    """Reset per-turn tool duration & count. Mirrors TS
    ``resetTurnToolDuration`` (state.ts:646-649). ``add_to_tool_duration``
    is the writer (bumps total + turn duration + turn count atomically)."""
    _STATE.turn_tool_duration_ms = 0
    _STATE.turn_tool_count = 0


def get_turn_tool_count() -> int:
    return _STATE.turn_tool_count


def get_turn_classifier_duration_ms() -> int:
    return _STATE.turn_classifier_duration_ms


def add_to_turn_classifier_duration(duration_ms: int) -> None:
    """Bump per-turn classifier duration & count. Mirrors TS
    ``addToTurnClassifierDuration`` (state.ts:659-662)."""
    _STATE.turn_classifier_duration_ms += duration_ms
    _STATE.turn_classifier_count += 1


def reset_turn_classifier_duration() -> None:
    _STATE.turn_classifier_duration_ms = 0
    _STATE.turn_classifier_count = 0


def get_turn_classifier_count() -> int:
    return _STATE.turn_classifier_count


# ===========================================================================
# Accessors — Turn-output-token budget (TS: lines 756-775)
# ===========================================================================
#
# These read/write the module-level globals declared near the singleton.
# TS keeps them module-scoped (not on STATE); we preserve the shape so the
# parity audit stays clean. ``reset_state_for_tests`` zeros them.


def get_turn_output_tokens() -> int:
    """Output tokens emitted in the current turn.

    Returns ``get_total_output_tokens() - <snapshot at turn start>``.
    Mirrors TS ``getTurnOutputTokens`` (state.ts:758-760).
    """
    return get_total_output_tokens() - _output_tokens_at_turn_start


def get_current_turn_token_budget() -> int | None:
    """The token budget set for the current turn, or ``None`` if no
    budget is in effect. Mirrors TS ``getCurrentTurnTokenBudget``
    (state.ts:761-763)."""
    return _current_turn_token_budget


def snapshot_output_tokens_for_turn(budget: int | None) -> None:
    """Snapshot the output-token counter at turn start and install the
    new turn budget. Resets the budget-continuation counter to 0.

    Mirrors TS ``snapshotOutputTokensForTurn`` (state.ts:765-769).
    ``budget=None`` is a legitimate input meaning "no budget enforced
    this turn"; it still snapshots the counter and zeros continuations.
    """
    global _output_tokens_at_turn_start, _current_turn_token_budget
    global _budget_continuation_count
    _output_tokens_at_turn_start = get_total_output_tokens()
    _current_turn_token_budget = budget
    _budget_continuation_count = 0


def get_budget_continuation_count() -> int:
    return _budget_continuation_count


def increment_budget_continuation_count() -> None:
    """Bump the continuation count. Mirrors TS
    ``incrementBudgetContinuationCount`` (state.ts:773-775)."""
    global _budget_continuation_count
    _budget_continuation_count += 1


# ===========================================================================
# Accessors — Allowed settings sources (TS: lines 75, 287-292, 1149-1155)
# ===========================================================================


def get_allowed_setting_sources() -> list[str]:
    """Return a copy of the allowed-settings-sources list.

    Returning a copy discourages caller mutation; consumers that need
    to mutate must call ``set_allowed_setting_sources`` explicitly.
    """
    return list(_STATE.allowed_setting_sources)


def set_allowed_setting_sources(sources: list[str]) -> None:
    """Replace the allowed-settings-sources list atomically.

    Mirrors TS ``setAllowedSettingSources`` (state.ts:1153-1155). Stores
    a copy so subsequent mutation of the caller's list does not leak in.
    """
    _STATE.allowed_setting_sources = list(sources)


# ===========================================================================
# Accessors — Invoked skills (compaction preservation; TS: lines 1424-1486)
# ===========================================================================


def add_invoked_skill(
    skill_name: str,
    skill_path: str,
    content: str,
    agent_id: str | None = None,
) -> None:
    """Record an invoked skill so the post-compaction attachment path can
    re-emit it after a ``/compact`` rewinds the transcript.

    Composite key: ``f"{agent_id or ''}:{skill_name}"`` — string form,
    NOT a tuple, to match TS ``${agentId ?? ''}:${skillName}`` (state.ts:
    1439). Same-key add overwrites. Mirrors TS ``addInvokedSkill``
    (state.ts:1433-1447).
    """
    key = f"{agent_id or ''}:{skill_name}"
    _STATE.invoked_skills[key] = InvokedSkillInfo(
        skill_name=skill_name,
        skill_path=skill_path,
        content=content,
        invoked_at=time.time(),
        agent_id=agent_id,
    )


def get_invoked_skills() -> dict[str, InvokedSkillInfo]:
    """Return the underlying invoked-skills dict (NOT a copy).

    Mirrors TS ``getInvokedSkills`` which returns the live Map. Callers
    that want a copy must copy explicitly.
    """
    return _STATE.invoked_skills


def get_invoked_skills_for_agent(
    agent_id: str | None,
) -> dict[str, InvokedSkillInfo]:
    """Filter the invoked-skills map to entries whose ``agent_id`` matches.

    ``agent_id=None`` matches only skills recorded with ``agent_id=None``.
    Mirrors TS ``getInvokedSkillsForAgent`` (state.ts:1453-1464); the
    ``normalizedId = agentId ?? null`` step in TS is unnecessary in
    Python because we never pass ``undefined``.
    """
    return {
        key: skill
        for key, skill in _STATE.invoked_skills.items()
        if skill.agent_id == agent_id
    }


def clear_invoked_skills(
    preserved_agent_ids: set[str] | None = None,
) -> None:
    """Clear invoked skills, optionally preserving entries tied to the
    named agents.

    Semantics (mirror TS ``clearInvokedSkills`` state.ts:1466-1478):
      * ``preserved_agent_ids=None`` OR empty set → clear EVERYTHING.
      * Otherwise, keep entries whose ``agent_id`` is non-None AND in
        the set. Entries with ``agent_id is None`` are always removed
        when a preserved set is supplied (matches TS line 1474:
        ``if (skill.agentId === null || !preservedAgentIds.has(skill.agentId))``).
    """
    if not preserved_agent_ids:
        _STATE.invoked_skills.clear()
        return
    for key in list(_STATE.invoked_skills.keys()):
        skill = _STATE.invoked_skills[key]
        if skill.agent_id is None or skill.agent_id not in preserved_agent_ids:
            del _STATE.invoked_skills[key]


def clear_invoked_skills_for_agent(agent_id: str) -> None:
    """Remove every invoked skill tied to ``agent_id``. Mirrors TS
    ``clearInvokedSkillsForAgent`` (state.ts:1480-1486)."""
    for key in list(_STATE.invoked_skills.keys()):
        if _STATE.invoked_skills[key].agent_id == agent_id:
            del _STATE.invoked_skills[key]


# ===========================================================================
# Accessors — Slow operations (permanent no-op stubs; TS: lines 1488-1508)
# ===========================================================================
#
# TS keeps these as documented no-ops (the slow-ops tracker was removed).
# Python mirrors as no-ops so any future caller that depends on the names
# compiles, while the actual behavior remains a permanent no-op. Do NOT
# add backing state for these — that would resurrect a removed feature.


_EMPTY_SLOW_OPERATIONS: tuple[dict, ...] = ()


def add_slow_operation(operation: str, duration_ms: int) -> None:
    """Permanent no-op. Mirrors TS ``addSlowOperation`` (state.ts:1497-1500)."""
    return


def get_slow_operations() -> tuple[dict, ...]:
    """Permanent no-op — always returns the same empty tuple. Mirrors TS
    ``getSlowOperations`` (state.ts:1502-1508)."""
    return _EMPTY_SLOW_OPERATIONS


# ===========================================================================
# Accessors — REPL bridge (closed-source feature stubs; TS: lines 1647-1653)
# ===========================================================================
#
# Feature-gated in TS (returns false / null in the open build). Python
# mirrors as permanent stubs — no backing state, no toggle.


def is_repl_bridge_active() -> bool:
    """Permanent stub. Mirrors TS ``isReplBridgeActive`` (state.ts:1647-1649)."""
    return False


def get_repl_bridge_handle() -> None:
    """Permanent stub. Mirrors TS ``getReplBridgeHandle`` (state.ts:1651-1653)."""
    return None


# ===========================================================================
# Test reset
# ===========================================================================


def reset_state_for_tests() -> None:
    """Wipe state to defaults. Test-only escape hatch.

    Gated by the ``PYTEST_CURRENT_TEST`` environment variable, which pytest
    sets during test execution. Production calls raise ``RuntimeError``.
    Mirrors TS ``resetStateForTests`` (``bootstrap/state.ts:951``).

    Resets:
      * the ``_STATE`` dataclass singleton (all dataclass fields zero out)
      * the session-switched signal listeners
      * the WI-4 turn-budget module-level globals (these live outside
        ``_STATE`` to mirror TS lines 756-775; the dataclass replace does
        not touch them, so we rebind them here explicitly)
    """
    if os.environ.get("PYTEST_CURRENT_TEST") is None:
        raise RuntimeError("reset_state_for_tests can only be called in tests")
    global _STATE
    global _output_tokens_at_turn_start, _current_turn_token_budget
    global _budget_continuation_count
    _STATE = _BootstrapState()
    _session_switched.clear()
    _output_tokens_at_turn_start = 0
    _current_turn_token_budget = None
    _budget_continuation_count = 0


__all__ = [
    # Types
    "SessionId",
    "ModelUsage",
    "InvokedSkillInfo",
    "SdkContext",
    # Per-query context
    "run_with_sdk_context",
    # Signal
    "on_session_switch",
    # Identity & paths
    "get_session_id",
    "regenerate_session_id",
    "switch_session",
    "get_parent_session_id",
    "get_session_project_dir",
    "get_original_cwd",
    "set_original_cwd",
    "get_project_root",
    "set_project_root",
    "get_cwd_state",
    "set_cwd_state",
    # Session flags
    "get_is_interactive",
    "set_is_interactive",
    "get_is_non_interactive_session",
    "get_client_type",
    "set_client_type",
    "get_session_trust_accepted",
    "set_session_trust_accepted",
    "is_session_persistence_disabled",
    "set_session_persistence_disabled",
    "get_is_remote_mode",
    "set_is_remote_mode",
    "has_exited_plan_mode_in_session",
    "set_has_exited_plan_mode",
    "prefer_third_party_authentication",
    # Cost & timing
    "get_total_cost_usd",
    "add_to_total_cost_state",
    "get_total_api_duration",
    "get_total_api_duration_without_retries",
    "add_to_total_duration_state",
    "get_total_tool_duration",
    "add_to_tool_duration",
    "get_total_lines_added",
    "get_total_lines_removed",
    "add_to_total_lines_changed",
    "has_unknown_model_cost",
    "set_has_unknown_model_cost",
    "get_model_usage",
    "get_usage_for_model",
    "get_total_input_tokens",
    "get_total_output_tokens",
    "get_total_cache_read_input_tokens",
    "get_total_cache_creation_input_tokens",
    "get_total_web_search_requests",
    "get_start_time",
    "get_total_duration",
    "get_last_interaction_time",
    "update_last_interaction_time",
    "reset_cost_state",
    "set_cost_state_for_restore",
    # Turn-level metrics
    "get_turn_hook_duration_ms",
    "add_to_turn_hook_duration",
    "reset_turn_hook_duration",
    "get_turn_hook_count",
    "get_turn_tool_duration_ms",
    "reset_turn_tool_duration",
    "get_turn_tool_count",
    "get_turn_classifier_duration_ms",
    "add_to_turn_classifier_duration",
    "reset_turn_classifier_duration",
    "get_turn_classifier_count",
    # Turn-output-token budget
    "get_turn_output_tokens",
    "get_current_turn_token_budget",
    "snapshot_output_tokens_for_turn",
    "get_budget_continuation_count",
    "increment_budget_continuation_count",
    # Allowed settings sources
    "get_allowed_setting_sources",
    "set_allowed_setting_sources",
    # Invoked skills
    "add_invoked_skill",
    "get_invoked_skills",
    "get_invoked_skills_for_agent",
    "clear_invoked_skills",
    "clear_invoked_skills_for_agent",
    # Slow operations (permanent no-op stubs)
    "add_slow_operation",
    "get_slow_operations",
    # REPL bridge stubs
    "is_repl_bridge_active",
    "get_repl_bridge_handle",
    # Cache optimization
    "get_cached_claude_md_content",
    "set_cached_claude_md_content",
    "get_system_prompt_section_cache",
    "set_system_prompt_section_cache_entry",
    "clear_system_prompt_section_state",
    "mark_post_compaction",
    "consume_post_compaction",
    "get_additional_directories_for_claude_md",
    "set_additional_directories_for_claude_md",
    # Model
    "get_main_loop_model_override",
    "set_main_loop_model_override",
    "get_initial_main_loop_model",
    "set_initial_main_loop_model",
    # API correlation
    "get_prompt_id",
    "set_prompt_id",
    "get_last_main_request_id",
    "set_last_main_request_id",
    "get_last_api_completion_timestamp",
    "set_last_api_completion_timestamp",
    "get_last_emitted_date",
    "set_last_emitted_date",
    # Test reset
    "reset_state_for_tests",
]
