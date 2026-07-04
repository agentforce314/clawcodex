from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from enum import Enum, EnumMeta
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Hook events — Chapter-12 / Phase-1 / WI-1.1
# ---------------------------------------------------------------------------
#
# 28-event taxonomy promoted from the legacy 10-event Literal. Mirrors
# typescript/src/utils/hooks/hooksConfigManager.ts:26-267 plus the chapter's
# reference table (``ch12-extensibility.md`` §"Five Most Important Lifecycle
# Events" + §"Reference table — remaining events").
#
# Per assumption A1 (resolved-by-critic in §19 of the plan):
# ``SessionStart``, ``SessionEnd``, ``PreCompact``, ``PostCompact`` are
# **first-class** events — no longer routed through ``Notification`` with a
# magic matcher string. The legacy form is supported by a back-compat
# translator in ``config_manager.load_hooks_from_settings`` for one CHANGELOG
# cycle (with DeprecationWarning).
HookEvent = Literal[
    # --- Tool lifecycle ---
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    # --- Permission ---
    "PermissionDenied",
    "PermissionRequest",
    # --- Session ---
    "SessionStart",
    "SessionEnd",
    "Setup",
    # --- Subagent ---
    "SubagentStart",
    "SubagentStop",
    # --- Stop / continuation ---
    "Stop",
    "StopFailure",
    # --- Compaction ---
    "PreCompact",
    "PostCompact",
    # --- Notification (general-purpose; no longer a routing target for
    # SessionStart etc. after Phase 1) ---
    "Notification",
    # --- User input ---
    "UserPromptSubmit",
    # --- Sampling lifecycle ---
    "PostSampling",
    # --- Configuration ---
    "ConfigChange",
    "InstructionsLoaded",
    "CwdChanged",
    "FileChanged",
    # --- Workspace ---
    "WorktreeCreate",
    "WorktreeRemove",
    # --- Task lifecycle ---
    "TaskCreated",
    "TaskCompleted",
    "TeammateIdle",
    # --- Elicitation (MCP-related) ---
    "Elicitation",
    "ElicitationResult",
]

ALL_HOOK_EVENTS: list[HookEvent] = [
    # Tool lifecycle
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    # Permission
    "PermissionDenied",
    "PermissionRequest",
    # Session
    "SessionStart",
    "SessionEnd",
    "Setup",
    # Subagent
    "SubagentStart",
    "SubagentStop",
    # Stop
    "Stop",
    "StopFailure",
    # Compaction
    "PreCompact",
    "PostCompact",
    # Notification
    "Notification",
    # User input
    "UserPromptSubmit",
    # Sampling
    "PostSampling",
    # Configuration
    "ConfigChange",
    "InstructionsLoaded",
    "CwdChanged",
    "FileChanged",
    # Workspace
    "WorktreeCreate",
    "WorktreeRemove",
    # Task lifecycle
    "TaskCreated",
    "TaskCompleted",
    "TeammateIdle",
    # Elicitation
    "Elicitation",
    "ElicitationResult",
]


# Hook *type* — the kind of executor that runs the hook. Phase-1 keeps the
# four config-driven types; ``callback`` lands in Phase 9 (gap #12).
HookType = Literal["command", "agent", "http", "prompt"]


# Re-exported here so callers can ``from src.hooks.hook_types import ShellType``
# without pulling in ``shell_invocation`` (which would import ``shutil``).
# The canonical definition lives in ``src/hooks/shell_invocation.py``.
ShellType = Literal["bash", "powershell"]


# ---------------------------------------------------------------------------
# Hook source — Chapter-12 / Phase-1 / WI-1.2
# ---------------------------------------------------------------------------
#
# Six-source priority scheme matching TS ``hooksSettings.ts:103-107``. Priority
# is an integer attribute (not the enum's ordinal) so PLUGIN_HOOK can carry the
# 999 sentinel meaning "always last," consistent with the chapter table.
#
# **Renamed from Phase 0:**
#   - ``SETTINGS`` → ``USER_SETTINGS`` (split from ``PROJECT_SETTINGS`` +
#     ``LOCAL_SETTINGS``).
#   - ``POLICY`` → ``POLICY_SETTINGS``.
#   - ``PLUGINS`` → ``PLUGIN_HOOK``.
#
# **Removed:** ``FRONTMATTER`` and ``SKILLS`` had no producer in Phase 0
# (gap analysis §5). Removed outright.
#
# **Deprecation aliases:** the ``EnumMeta``-level ``__getattr__`` on the
# metaclass below intercepts the old names, emits a ``DeprecationWarning``,
# and returns the new value. Callers see continuity for one CHANGELOG cycle.


class _HookSourceMeta(EnumMeta):
    """Intercept legacy enum-name access (``HookSource.SETTINGS`` etc.) at the
    metaclass level so the back-compat alias path can emit ``DeprecationWarning``.

    Module-level ``__getattr__`` (PEP 562) does not apply here: ``HookSource.X``
    is a class-attribute access on the enum, resolved via the metaclass.
    """

    _DEPRECATED_ALIASES: dict[str, str] = {
        "SETTINGS": "USER_SETTINGS",
        "POLICY": "POLICY_SETTINGS",
        "PLUGINS": "PLUGIN_HOOK",
    }

    def __getattr__(cls, name: str):
        # Dunder names: defer to default lookup (raises AttributeError as
        # EnumMeta does); avoids spurious DeprecationWarning paths and lets
        # ``hasattr(HookSource, "__name__")`` etc. work normally.
        if name.startswith("_"):
            return EnumMeta.__getattr__(cls, name)

        if name in cls._DEPRECATED_ALIASES:
            new_name = cls._DEPRECATED_ALIASES[name]
            warnings.warn(
                f"HookSource.{name} is deprecated; use HookSource.{new_name}. "
                "Will be removed two CHANGELOG entries after the rename.",
                DeprecationWarning,
                stacklevel=2,
            )
            # Resolve the canonical member via the enum's member map. We index
            # into ``_member_map_`` rather than recurse through ``__getattr__``
            # to keep the warning fire site exact (one warning per alias use,
            # not two).
            return cls._member_map_[new_name]

        return EnumMeta.__getattr__(cls, name)


class HookSource(str, Enum, metaclass=_HookSourceMeta):
    USER_SETTINGS = "userSettings"
    PROJECT_SETTINGS = "projectSettings"
    LOCAL_SETTINGS = "localSettings"
    POLICY_SETTINGS = "policySettings"
    SESSION_HOOK = "sessionHook"
    PLUGIN_HOOK = "pluginHook"

    @property
    def priority(self) -> int:
        """Sort key for snapshot ordering. Lower = sorts first.

        The 999 sentinel for ``PLUGIN_HOOK`` matches TS' pattern ("always
        last") so insertions of new tiers between the canonical ones don't
        disturb plugin ordering. Note: priority is an *ordering* attribute
        only — the chapter's "policy cannot be overridden" semantic is
        enforced by ``apply_policy_cascade`` (Phase 2 / WI-2.3), not by
        priority. Don't confuse the two.
        """
        return {
            HookSource.USER_SETTINGS: 0,
            HookSource.PROJECT_SETTINGS: 1,
            HookSource.LOCAL_SETTINGS: 2,
            HookSource.POLICY_SETTINGS: 3,
            HookSource.SESSION_HOOK: 4,
            HookSource.PLUGIN_HOOK: 999,
        }[self]

    @property
    def is_policy(self) -> bool:
        """True iff this source is the enterprise-managed policy tier.

        Used by the trust gate (WI-0.2) and the policy cascade (Phase 2 /
        WI-2.3) to identify hooks that bypass user-side controls.
        """
        return self == HookSource.POLICY_SETTINGS


# ---------------------------------------------------------------------------
# HookConfig — Chapter-12 / Phase-1 / WI-1.3
# ---------------------------------------------------------------------------


@dataclass
class HookConfig:
    type: HookType = "command"
    command: str = ""
    timeout: int | None = None
    matcher: str | None = None
    url: str | None = None
    prompt_text: str | None = None
    agent_instructions: str | None = None
    source: HookSource = HookSource.USER_SETTINGS

    # Phase-1 / WI-1.3 — schema additions:
    #
    # ``if_condition`` — permission-rule grammar string (e.g.,
    # ``"Bash(git commit*)"``). Evaluated by
    # ``hook_executor._matches_if_condition`` (SCHEMAS-1 — the port of TS
    # ``prepareIfConditionMatcher``) against the active tool call before the
    # hook spawns. ``None`` means "no extra filter beyond ``matcher``."
    # Mirrors TS ``schemas/hooks.ts:19-27`` ``if`` field. (Prior to
    # SCHEMAS-1 this comment named ``matches_hook_condition``, an evaluator
    # that was never built — the field parsed but was inert.)
    if_condition: str | None = None

    # ``once`` — if True, the hook is removed from the session registry after
    # its first successful execution. Honored by Phase-3 ``session_hooks``
    # registration only (config-driven hooks are not session-scoped, so the
    # field is parsed-and-ignored for them). Mirrors TS ``HookSettings.once``.
    once: bool = False

    # ``skill_root`` — for skill-declared hooks, the absolute path of the
    # skill directory. Becomes ``CLAUDE_PLUGIN_ROOT`` in subprocess env at
    # hook-fire time (WI-1.5). ``None`` for non-skill hooks. Carries through
    # registration → execution; not parsed from settings.json (only set at
    # skill-hook registration time in Phase 3).
    skill_root: str | None = None

    # Round-2 / Ch12 — per-hook shell selection. ``None`` means "use the
    # platform default" (bash on POSIX via /bin/sh); ``"bash"`` is an
    # explicit alias for that same path; ``"powershell"`` spawns ``pwsh``
    # with the canonical ``-NoProfile -NonInteractive -Command <cmd>`` argv.
    # Mirrors TS ``BashCommandHookSchema.shell`` at
    # ``typescript/src/schemas/hooks.ts:36-41``. Only meaningful for
    # ``type == "command"`` — non-command hooks ignore this field (matches
    # TS where ``shell`` only appears on the command schema).
    shell: ShellType | None = None


# ---------------------------------------------------------------------------
# HookResult / HookProgress / per-event input dataclasses
# ---------------------------------------------------------------------------


@dataclass
class HookResult:
    message: Any | None = None
    blocking_error: str | None = None
    permission_behavior: str | None = None
    hook_permission_decision_reason: str | None = None
    hook_source: str | None = None
    updated_input: dict[str, Any] | None = None
    # PermissionRequest-event extras (HOOKS-1): permission updates a hook may
    # attach to an allow, and the deny-time turn-abort flag. Mirrors the TS
    # PermissionRequest decision shape (utils/hooks.ts:833-840).
    updated_permissions: list[dict[str, Any]] | None = None
    interrupt: bool = False
    prevent_continuation: bool = False
    stop_reason: str | None = None
    additional_contexts: list[str] | None = None
    updated_mcp_tool_output: Any | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    command: str | None = None


@dataclass
class HookProgress:
    command: str = ""
    prompt_text: str | None = None
    tool_use_id: str = ""
    parent_tool_use_id: str = ""


@dataclass
class PreToolUseHookInput:
    tool_name: str = ""
    tool_use_id: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    permission_mode: str | None = None
    request_prompt: str | None = None
    tool_use_summary: str | None = None


@dataclass
class PostToolUseHookInput:
    tool_name: str = ""
    tool_use_id: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_response: Any = None
    permission_mode: str | None = None


@dataclass
class StopHookInput:
    permission_mode: str | None = None
    stop_hook_active: bool = False
    messages: list[Any] = field(default_factory=list)


@dataclass
class NotificationHookInput:
    notification_type: str = ""
    message: str = ""
    tool_name: str | None = None
    tool_use_id: str | None = None


@dataclass
class UserPromptSubmitHookInput:
    # ch14 round-4 — the Claude Code hook contract's stdin field is
    # ``prompt`` (matches the TS executor). ``user_message`` kept as a
    # deprecated alias so any legacy reader still resolves.
    prompt: str = ""
    session_id: str | None = None
    user_message: str = ""


@dataclass
class PostSamplingHookInput:
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    response_content: Any = None


TOOL_HOOK_EXECUTION_TIMEOUT_MS = 60_000
HTTP_HOOK_TIMEOUT_MS = 30_000
AGENT_HOOK_TIMEOUT_MS = 120_000
