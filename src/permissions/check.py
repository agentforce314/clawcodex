from __future__ import annotations

import fnmatch
import logging
import os
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

from .bash_security import analyze_bash_command
from .bash_suggestions import contains_unquoted_chaining
from .rules import (
    get_ask_rule_for_tool,
    get_deny_rule_for_tool,
    get_rule_by_contents_for_tool,
    tool_always_allowed_rule,
)
from .types import (
    AsyncAgentDecisionReason,
    ClassifierDecisionReason,
    ModeDecisionReason,
    OtherDecisionReason,
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionDecision,
    PermissionDenyDecision,
    PermissionPassthroughResult,
    PermissionResult,
    RuleDecisionReason,
    SafetyCheckDecisionReason,
    ToolPermissionContext,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


@runtime_checkable
class CheckPermissionsTool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def is_mcp(self) -> bool: ...

    def check_permissions(
        self, tool_input: dict[str, Any], context: Any
    ) -> PermissionResult: ...


@runtime_checkable
class RequiresInteractionTool(Protocol):
    def requires_user_interaction(self) -> bool: ...


@dataclass
class DenialTracker:
    denial_counts: dict[str, int] = field(default_factory=dict)
    escalation_threshold: int = 3

    def record_denial(self, tool_name: str) -> None:
        self.denial_counts[tool_name] = self.denial_counts.get(tool_name, 0) + 1

    def get_denial_count(self, tool_name: str) -> int:
        return self.denial_counts.get(tool_name, 0)

    def should_escalate(self, tool_name: str) -> bool:
        return self.get_denial_count(tool_name) >= self.escalation_threshold

    def reset(self, tool_name: str | None = None) -> None:
        if tool_name:
            self.denial_counts.pop(tool_name, None)
        else:
            self.denial_counts.clear()


_global_denial_tracker = DenialTracker()


def get_denial_tracker() -> DenialTracker:
    return _global_denial_tracker


def reset_denial_tracker() -> None:
    _global_denial_tracker.reset()


def create_permission_request_message(
    tool_name: str,
    decision_reason: Any | None = None,
) -> str:
    return f"Claude wants to use {tool_name}. Allow?"


def _get_updated_input_or_fallback(
    permission_result: PermissionResult,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    if hasattr(permission_result, "updated_input") and permission_result.updated_input is not None:
        return permission_result.updated_input
    return fallback


# File-editing tools whose "allow all edits during this session" option maps to
# acceptEdits mode, and which acceptEdits mode auto-allows inside the working
# roots (parity with typescript/src/utils/permissions/filesystem.ts:1382-1397).
_FILE_EDIT_TOOLS: tuple[str, ...] = ("Write", "Edit", "MultiEdit", "NotebookEdit")

# Tools that are NOT necessarily gated — they never need a permission prompt.
# This port centralizes the always-allow set here (one auditable surface)
# instead of scattering identical ``check_permissions=allow`` across ~20 tool
# files. The decision is mode-independent, matching how TS resolves these tools.
#
# Two membership rationales (kept distinct on purpose):
#   * TS-DEFAULT-ALLOW — the base ``TOOL_DEFAULTS.checkPermissions`` returns
#     ``{behavior:'allow'}`` (typescript/src/Tool.ts:777), so every TS tool with
#     no override auto-allows. The Python base default is ``passthrough → ask``
#     (build_tool.py), so these need to be named explicitly. Covers TodoWrite,
#     ToolSearch, Sleep, Agent (AgentTool.tsx:1309), and the Python-only
#     bookkeeping/coordination tools with no TS analog (Tasks, Team, Cron,
#     worktree, Status, Brief, advisor, Clipboard*).
#   * DELIBERATE UX DIVERGENCE — TS gates these but the project guideline
#     ("allow if it is not necessarily gated; favor UX while keeping safety")
#     says not to: WebSearch (TS WebSearchTool.ts:645 returns passthrough → ask;
#     low-risk read-only, no arbitrary URL unlike WebFetch), AskUserQuestion
#     (TS returns ask+requiresUserInteraction — the questions ARE the gate;
#     Python's ``call`` collects answers via ``context.ask_user`` so a separate
#     permission prompt is redundant), SendUserMessage, StructuredOutput.
#
# The check (in :func:`has_permissions_to_use_tool_inner`) is gated on a
# ``passthrough`` tool result and runs AFTER deny/ask RULES, so a user-configured
# ``deny``/``ask`` rule (and any explicit tool ``ask``) still wins.
#
# DELIBERATELY EXCLUDED (kept gated — these ARE necessarily gated):
#   * file mutation: Write/Edit/MultiEdit/NotebookEdit
#   * code execution: Bash
#   * arbitrary network egress: WebFetch
#   * input/conditional (carry their own check_permissions): Config (write),
#     Glob, Grep, SendMessage (cross-machine bridge:/uds: recipients), and Skill.
#     Skill's own check (``tools/skill.py:_skill_check_permissions``) AUTO-ALLOWS
#     the invocation — in this port a skill grants no ungated capability: its
#     embedded ``!`` shell is permission-checked in ``_make_shell_executor`` and
#     the model's own tool calls are gated normally — while still honoring
#     per-skill ``Skill(<name>)`` deny/ask content rules. (TS instead gates
#     skills that declare ``allowed-tools``; we diverge because that
#     pre-authorization is not wired through here, so there's nothing extra to
#     gate. It lives here rather than in NO_PERMISSION_TOOLS because that
#     content-rule handling is input-conditional, like Config/Grep.)
#   * MCP server access: MCP / ListMcpResourcesTool / ReadMcpResourceTool and
#     dynamic ``mcp__*`` (external/untrusted boundary).
#   * plan-mode meta tools: EnterPlanMode / ExitPlanMode (ExitPlanMode is the
#     plan-confirmation gate).
NO_PERMISSION_TOOLS: frozenset[str] = frozenset({
    # Interactive / output (the interaction or output IS the action)
    "AskUserQuestion", "SendUserMessage", "StructuredOutput",
    # Read-only / introspection
    "WebSearch", "ToolSearch", "LSP", "advisor", "Brief", "Status",
    "ClipboardRead",
    # Bookkeeping / harness state (no external side effects)
    "TodoWrite", "ClipboardWrite", "Sleep",
    "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TaskOutput", "TaskStop",
    # Orchestration / coordination — every sub-action they spawn is itself
    # permission-checked, so the spawn is not the gate.
    "Agent", "Workflow", "TeamCreate", "TeamDelete",
    # Scheduling — the scheduled run is permission-checked when it fires.
    "CronCreate", "CronList", "CronDelete",
    # Local, reversible git-worktree management.
    "EnterWorktree", "ExitWorktree",
})


def _allowed_roots_for_check(
    context: ToolPermissionContext, tool_use_context: Any | None
) -> list[str]:
    """Working-directory roots used for acceptEdits path checks.

    Prefers the live ``ToolContext.allowed_roots()`` (workspace + additional
    dirs + internal paths); falls back to the cwd, and always folds in the
    session-granted ``additional_working_directories`` from the permission
    context so a just-accepted directory grant is honored.
    """
    roots: list[str] = []
    if tool_use_context is not None:
        try:
            roots.extend(str(r) for r in tool_use_context.allowed_roots())
        except Exception:
            pass
    if not roots:
        roots.append(os.getcwd())
    try:
        roots.extend(context.additional_working_directories.keys())
    except Exception:
        pass
    return roots


def _path_in_working_roots(
    file_path: str,
    context: ToolPermissionContext,
    tool_use_context: Any | None,
) -> bool:
    from pathlib import Path

    abs_path = os.path.abspath(os.path.expanduser(file_path))
    try:
        target = Path(abs_path).resolve()
    except OSError:
        target = Path(abs_path)
    for root in _allowed_roots_for_check(context, tool_use_context):
        try:
            target.relative_to(Path(root).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def has_permissions_to_use_tool(
    tool: CheckPermissionsTool,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
    *,
    tool_use_context: Any | None = None,
) -> PermissionDecision:
    """Resolve permission for ``tool`` against ``context``.

    Mirrors ``hasPermissionsToUseTool`` in
    ``typescript/src/utils/permissions/permissions.ts:473-956``. The flow:

    1. Run :func:`has_permissions_to_use_tool_inner` for rule + tool +
       passthrough → ask resolution.
    2. Apply ``dontAsk`` transform: ask → deny.
    3. Apply ``auto`` mode: dispatch to :func:`auto_mode_classify` and
       convert the result to allow/deny. Skipped when the inner result is a
       non-classifier-approvable safety check (parity with TS lines 530-548).
    4. Apply ``bubble`` mode: ask escalates with an ``asyncAgent`` reason.
       The real cross-process escalation lives in the coordinator; for now
       we surface a structured deny so callers can recognize the path.
    5. Apply ``should_avoid_permission_prompts`` headless guard last so it
       can't be bypassed by an earlier ``ask`` short-circuit.
    """
    result = has_permissions_to_use_tool_inner(
        tool, tool_input, context, tool_use_context=tool_use_context,
    )

    if result.behavior != "ask":
        return result

    if context.mode == "dontAsk":
        return PermissionDenyDecision(
            behavior="deny",
            message="Permission denied: dontAsk mode is active.",
            decision_reason=ModeDecisionReason(mode="dontAsk"),
        )

    if context.mode == "auto":
        # Non-classifier-approvable safety checks are immune to auto-allow:
        # parity with typescript/src/utils/permissions/permissions.ts:530-548.
        ask_reason = getattr(result, "decision_reason", None)
        if (
            ask_reason is not None
            and ask_reason.type == "safetyCheck"
            and not getattr(ask_reason, "classifier_approvable", False)
        ):
            if context.should_avoid_permission_prompts:
                return PermissionDenyDecision(
                    behavior="deny",
                    message=getattr(result, "message", ""),
                    decision_reason=AsyncAgentDecisionReason(
                        reason=(
                            "Safety check requires interactive approval and "
                            "permission prompts are not available in this context"
                        ),
                    ),
                )
            return result

        decision = auto_mode_classify(tool.name, tool_input, context)
        if decision.allow:
            return PermissionAllowDecision(
                behavior="allow",
                updated_input=getattr(result, "updated_input", None) or tool_input,
                decision_reason=ClassifierDecisionReason(
                    classifier="auto-mode",
                    reason=decision.reason,
                ),
            )
        return PermissionDenyDecision(
            behavior="deny",
            message=f"Auto-mode classifier blocked {tool.name}: {decision.reason}",
            decision_reason=ClassifierDecisionReason(
                classifier="auto-mode",
                reason=decision.reason,
            ),
        )

    if context.mode == "bubble":
        # Stub for the parent-escalation path described in
        # book/ch01-architecture.md line 126 + ch06 lines 211-213. A future
        # change replaces this with a real cross-process request to the
        # parent agent; the structured reason makes that swap observable.
        return PermissionDenyDecision(
            behavior="deny",
            message=(
                f"Permission required for {tool.name} in bubble mode; "
                "request must be escalated to the parent agent."
            ),
            decision_reason=AsyncAgentDecisionReason(
                reason="bubble: sub-agent escalates permission to parent",
            ),
        )

    if context.should_avoid_permission_prompts:
        return PermissionDenyDecision(
            behavior="deny",
            message=f"Permission denied: tool {tool.name} requires approval but prompts are not available.",
            decision_reason=OtherDecisionReason(reason="Permission prompts not available"),
        )

    return result


def has_permissions_to_use_tool_inner(
    tool: CheckPermissionsTool,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
    *,
    tool_use_context: Any | None = None,
) -> PermissionDecision:
    deny_rule = get_deny_rule_for_tool(context, tool)
    if deny_rule:
        return PermissionDenyDecision(
            behavior="deny",
            decision_reason=RuleDecisionReason(rule=deny_rule),
            message=f"Permission to use {tool.name} has been denied.",
        )

    ask_rule = get_ask_rule_for_tool(context, tool)
    if ask_rule:
        return PermissionAskDecision(
            behavior="ask",
            decision_reason=RuleDecisionReason(rule=ask_rule),
            message=create_permission_request_message(tool.name),
        )

    tool_permission_result: PermissionResult = PermissionPassthroughResult(
        behavior="passthrough",
        message=create_permission_request_message(tool.name),
    )
    try:
        tool_permission_result = tool.check_permissions(tool_input, tool_use_context)
    except Exception:
        log.exception("Error in tool.check_permissions for %s", tool.name)

    if tool_permission_result.behavior == "deny":
        if isinstance(tool_permission_result, PermissionDenyDecision):
            return tool_permission_result
        return PermissionDenyDecision(
            behavior="deny",
            message=getattr(tool_permission_result, "message", f"Permission denied for {tool.name}"),
            decision_reason=getattr(tool_permission_result, "decision_reason", None),
        )

    # Not-necessarily-gated tools auto-allow (see NO_PERMISSION_TOOLS). Gated on
    # ``passthrough`` so a tool that explicitly returns ``ask``/``deny`` is still
    # honored, and placed AFTER the deny/ask RULE checks above so configured
    # rules win. Mode-independent — matching TS, where these tools' own
    # checkPermissions returns allow regardless of permission mode.
    if (
        tool_permission_result.behavior == "passthrough"
        and tool.name in NO_PERMISSION_TOOLS
    ):
        return PermissionAllowDecision(
            behavior="allow",
            updated_input=_get_updated_input_or_fallback(tool_permission_result, tool_input),
            decision_reason=OtherDecisionReason(
                reason="auto-allow: tool is not necessarily gated (NO_PERMISSION_TOOLS)",
            ),
        )

    if (
        isinstance(tool, RequiresInteractionTool)
        and tool.requires_user_interaction()
        and tool_permission_result.behavior == "ask"
    ):
        return _coerce_to_ask_decision(
            tool_permission_result,
            tool.name,
            context=context,
            tool_use_context=tool_use_context,
        )

    if (
        tool_permission_result.behavior == "ask"
        and hasattr(tool_permission_result, "decision_reason")
        and tool_permission_result.decision_reason is not None
        and tool_permission_result.decision_reason.type == "rule"
        and hasattr(tool_permission_result.decision_reason, "rule")
        and tool_permission_result.decision_reason.rule.rule_behavior == "ask"
    ):
        return _coerce_to_ask_decision(
            tool_permission_result,
            tool.name,
            context=context,
            tool_use_context=tool_use_context,
        )

    if (
        tool_permission_result.behavior == "ask"
        and hasattr(tool_permission_result, "decision_reason")
        and tool_permission_result.decision_reason is not None
        and tool_permission_result.decision_reason.type == "safetyCheck"
    ):
        return _coerce_to_ask_decision(
            tool_permission_result,
            tool.name,
            context=context,
            tool_use_context=tool_use_context,
        )

    should_bypass = (
        context.mode == "bypassPermissions"
        or (
            context.mode == "plan"
            and context.is_bypass_permissions_mode_available
        )
    )
    if should_bypass:
        return PermissionAllowDecision(
            behavior="allow",
            updated_input=_get_updated_input_or_fallback(tool_permission_result, tool_input),
            decision_reason=ModeDecisionReason(mode=context.mode),
        )

    # acceptEdits mode: auto-allow file edits whose target is inside the working
    # roots and is not a protected/dangerous path (parity with
    # typescript/src/utils/permissions/filesystem.ts:1382-1397). This is what
    # makes the file-edit "allow all edits during this session" option
    # (setMode:acceptEdits) and the shift+tab "Accept edits" mode actually
    # suppress later edit prompts. Gated on a passthrough tool result so an
    # explicit tool ask (e.g. the docs gate) is still respected.
    if (
        context.mode == "acceptEdits"
        and tool_permission_result.behavior == "passthrough"
        and tool.name in _FILE_EDIT_TOOLS
    ):
        edit_path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if (
            isinstance(edit_path, str)
            and edit_path
            and _path_in_working_roots(edit_path, context, tool_use_context)
        ):
            from .filesystem import check_path_safety_for_auto_edit

            if check_path_safety_for_auto_edit(edit_path) is None:
                return PermissionAllowDecision(
                    behavior="allow",
                    updated_input=_get_updated_input_or_fallback(
                        tool_permission_result, tool_input
                    ),
                    decision_reason=ModeDecisionReason(mode="acceptEdits"),
                )

    always_allowed = tool_always_allowed_rule(context, tool)
    if always_allowed:
        return PermissionAllowDecision(
            behavior="allow",
            updated_input=_get_updated_input_or_fallback(tool_permission_result, tool_input),
            decision_reason=RuleDecisionReason(rule=always_allowed),
        )

    content_rules = get_rule_by_contents_for_tool(context, tool.name, "allow")
    if content_rules and tool.name == "Bash":
        command = tool_input.get("command", "")
        if command:
            for rule_content, rule in content_rules.items():
                matcher = prepare_permission_matcher(rule_content)
                if matcher(command):
                    return PermissionAllowDecision(
                        behavior="allow",
                        updated_input=tool_input,
                        decision_reason=RuleDecisionReason(rule=rule),
                    )

    if tool_permission_result.behavior == "passthrough":
        return _with_default_suggestions(
            PermissionAskDecision(
                behavior="ask",
                message=create_permission_request_message(
                    tool.name,
                    getattr(tool_permission_result, "decision_reason", None),
                ),
                decision_reason=getattr(tool_permission_result, "decision_reason", None),
                suggestions=getattr(tool_permission_result, "suggestions", None),
            ),
            tool.name,
            tool_input,
            context=context,
            tool_use_context=tool_use_context,
            from_passthrough=True,
        )

    if tool_permission_result.behavior == "allow":
        if isinstance(tool_permission_result, PermissionAllowDecision):
            return tool_permission_result
        return PermissionAllowDecision(
            behavior="allow",
            updated_input=getattr(tool_permission_result, "updated_input", None),
            decision_reason=getattr(tool_permission_result, "decision_reason", None),
        )

    return _coerce_to_ask_decision(
        tool_permission_result,
        tool.name,
        tool_input,
        context=context,
        tool_use_context=tool_use_context,
    )


def _coerce_to_ask_decision(
    result: PermissionResult,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
    *,
    context: ToolPermissionContext | None = None,
    tool_use_context: Any | None = None,
) -> PermissionAskDecision:
    if isinstance(result, PermissionAskDecision):
        return _with_default_suggestions(
            result, tool_name, tool_input, context, tool_use_context
        )
    return _with_default_suggestions(
        PermissionAskDecision(
            behavior="ask",
            message=getattr(result, "message", create_permission_request_message(tool_name)),
            decision_reason=getattr(result, "decision_reason", None),
            suggestions=getattr(result, "suggestions", None),
        ),
        tool_name,
        tool_input,
        context,
        tool_use_context,
    )


def _with_default_suggestions(
    ask: PermissionAskDecision,
    tool_name: str,
    tool_input: dict[str, Any] | None,
    context: ToolPermissionContext | None = None,
    tool_use_context: Any | None = None,
    *,
    from_passthrough: bool = False,
) -> PermissionAskDecision:
    """Return ``ask`` with the "allow for the whole session" updates filled in.

    These updates drive the middle "Yes, …" option every interactive surface
    renders. The per-tool shape (Bash command-prefix rule, file-edit
    ``setMode:acceptEdits``, content-less rule for other tools, plus a
    directory grant for out-of-roots paths) lives in
    :func:`src.permissions.updates.default_session_suggestions`, so both the
    console and TUI prompts stay in sync from one source.

    Three deliberate exclusions are preserved:

    * safety-flagged asks keep an empty list — TS ("Don't suggest saving a
      potentially dangerous command");
    * asks that already carry suggestions (a tool supplied its own) are left
      untouched;
    * only an ask the matcher manufactured from a ``passthrough`` tool result
      (``from_passthrough``) gets a session option. An ask a tool raised
      explicitly — e.g. the docs gate (``write.py``/``edit.py`` block ``.md``
      edits unless ``allow_docs``) — or a configured ask-rule owns its own
      gating, which a mode flip / blanket rule would not satisfy: the
      acceptEdits auto-allow is itself ``passthrough``-gated, so "allow all
      edits this session" would re-prompt the very file it was offered on
      while silently widening session scope. So those asks keep Yes/No only.

    Returns a copy (``dataclasses.replace``) rather than mutating — the input
    can be a tool-owned decision object.
    """

    if ask.suggestions:
        return ask
    if isinstance(ask.decision_reason, SafetyCheckDecisionReason):
        return ask
    if not from_passthrough:
        return ask

    from .updates import default_session_suggestions

    allowed_roots: tuple[str, ...] | None = None
    if tool_use_context is not None:
        try:
            allowed_roots = tuple(str(r) for r in tool_use_context.allowed_roots())
        except Exception:
            allowed_roots = None

    suggestions = default_session_suggestions(
        tool_name, tool_input, context, allowed_roots=allowed_roots
    )
    if suggestions:
        return replace(ask, suggestions=tuple(suggestions))
    return ask


def check_rule_based_permissions(
    tool: CheckPermissionsTool,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
    *,
    tool_use_context: Any | None = None,
) -> PermissionAskDecision | PermissionDenyDecision | None:
    deny_rule = get_deny_rule_for_tool(context, tool)
    if deny_rule:
        return PermissionDenyDecision(
            behavior="deny",
            decision_reason=RuleDecisionReason(rule=deny_rule),
            message=f"Permission to use {tool.name} has been denied.",
        )

    ask_rule = get_ask_rule_for_tool(context, tool)
    if ask_rule:
        return PermissionAskDecision(
            behavior="ask",
            decision_reason=RuleDecisionReason(rule=ask_rule),
            message=create_permission_request_message(tool.name),
        )

    tool_permission_result: PermissionResult = PermissionPassthroughResult(
        behavior="passthrough",
        message=create_permission_request_message(tool.name),
    )
    try:
        tool_permission_result = tool.check_permissions(tool_input, tool_use_context)
    except Exception:
        log.exception("Error in tool.check_permissions for %s", tool.name)

    if tool_permission_result.behavior == "deny":
        if isinstance(tool_permission_result, PermissionDenyDecision):
            return tool_permission_result
        return PermissionDenyDecision(
            behavior="deny",
            message=getattr(tool_permission_result, "message", f"Permission denied for {tool.name}"),
            decision_reason=getattr(tool_permission_result, "decision_reason", None),
        )

    if (
        tool_permission_result.behavior == "ask"
        and hasattr(tool_permission_result, "decision_reason")
        and tool_permission_result.decision_reason is not None
        and tool_permission_result.decision_reason.type == "rule"
        and hasattr(tool_permission_result.decision_reason, "rule")
        and tool_permission_result.decision_reason.rule.rule_behavior == "ask"
    ):
        return _coerce_to_ask_decision(
            tool_permission_result,
            tool.name,
            context=context,
            tool_use_context=tool_use_context,
        )

    if (
        tool_permission_result.behavior == "ask"
        and hasattr(tool_permission_result, "decision_reason")
        and tool_permission_result.decision_reason is not None
        and tool_permission_result.decision_reason.type == "safetyCheck"
    ):
        return _coerce_to_ask_decision(
            tool_permission_result,
            tool.name,
            context=context,
            tool_use_context=tool_use_context,
        )

    return None


def prepare_permission_matcher(rule_content: str) -> Callable[[str], bool]:
    # Chaining guard (C1): every non-explicit-wildcard rule refuses to
    # auto-allow a command that chains further commands (&&, ||, ;, |,
    # newline outside quotes). Without this, a rule like "git diff:*" (or
    # the legacy single-word "git:*", or even an exact "ls -la" rule via
    # the startswith fallback) would blanket-allow "<match> && anything"
    # whenever the trailing command happens to rate benign in the safety
    # screen. Deliberately stricter than TS, whose matcher runs
    # per-AST-sub-command; Python matches whole strings, so chained
    # commands must simply re-prompt. ("" and "*" rules are explicit
    # allow-alls and stay unguarded.)
    if not rule_content:
        return lambda _: True

    if rule_content == "*":
        return lambda _: True

    if ":" in rule_content:
        prefix = rule_content.split(":", 1)[0]
        suffix = rule_content.split(":", 1)[1]
        if suffix == "*":
            def _prefix_matcher(command: str) -> bool:
                # TS semantics (bashPermissions.ts:879-882): exact match or
                # prefix followed by a space — which makes MULTI-WORD
                # prefixes ("git diff:*") work. The previous version
                # compared only the command's first token against the whole
                # prefix, so multi-word prefix rules could never match
                # (latent until C1 un-starved the rule engine). The
                # path-basename normalization of the first token
                # (`/usr/bin/git` → `git`) is a deliberate Python nicety.
                if contains_unquoted_chaining(command):
                    return False
                parts = command.strip().split(None, 1)
                if not parts:
                    return False
                head = parts[0].rsplit("/", 1)[-1]
                normalized = (
                    head if len(parts) == 1 else f"{head} {parts[1]}"
                )
                return normalized == prefix or normalized.startswith(prefix + " ")
            return _prefix_matcher
        else:
            def _exact_prefix_matcher(command: str) -> bool:
                # Known limitation: like the pre-C1 code, this branch only
                # matches single-word rule prefixes (the first token is
                # compared to the whole prefix), so a user-written
                # "git diff:--stat*" rule cannot match. No producer mints
                # such rules today; fix alongside a real consumer.
                if contains_unquoted_chaining(command):
                    return False
                parts = command.strip().split(None, 1)
                if not parts:
                    return False
                cmd_name = parts[0].rsplit("/", 1)[-1]
                if cmd_name != prefix:
                    return False
                rest = parts[1] if len(parts) > 1 else ""
                return fnmatch.fnmatch(rest, suffix)
            return _exact_prefix_matcher

    if "*" in rule_content or "?" in rule_content:
        return lambda command: (
            not contains_unquoted_chaining(command)
            and fnmatch.fnmatch(command.strip(), rule_content)
        )

    def _exact_or_word_prefix_matcher(command: str) -> bool:
        # Rules without ":*" or wildcards: exact match, or the rule
        # followed by a SPACE (word boundary). Bare startswith would let
        # a stored rule match last-token elongations (a rule for one
        # flag cluster matching a longer one) — meaningful since C1's
        # suggestion layer started minting exact-command rules into this
        # branch. The +space prefix form (vs TS pure equality) is kept
        # for the pre-existing locked behavior of word-prefix rules like
        # "npm run".
        if contains_unquoted_chaining(command):
            return False
        cmd = command.strip()
        return cmd == rule_content or cmd.startswith(rule_content + " ")

    return _exact_or_word_prefix_matcher


@dataclass(frozen=True)
class AutoModeDecision:
    allow: bool
    reason: str = ""


def auto_mode_classify(
    tool_name: str,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
) -> AutoModeDecision:
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if not command:
            return AutoModeDecision(allow=False, reason="empty command")

        analysis = analyze_bash_command(command)

        if analysis.is_complex:
            return AutoModeDecision(allow=False, reason="complex command structure")

        if analysis.safety in ("safe", "read_only"):
            return AutoModeDecision(allow=True, reason=f"command is {analysis.safety}")

        return AutoModeDecision(allow=False, reason=f"command is {analysis.safety}")

    if tool_name in ("Read", "Glob", "Grep", "LS"):
        return AutoModeDecision(allow=True, reason="read-only tool")

    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        file_path = tool_input.get("file_path", "")
        if file_path:
            from .filesystem import check_path_safety_for_auto_edit
            safety = check_path_safety_for_auto_edit(file_path)
            if safety is not None:
                return AutoModeDecision(allow=False, reason="protected path")
        return AutoModeDecision(allow=True, reason="file edit in safe location")

    if tool_name == "Agent":
        return AutoModeDecision(allow=True, reason="agent tool")

    if tool_name == "Workflow":
        # Workflow orchestration is safe to auto-allow — each subagent it spawns
        # goes through canUseTool individually.
        return AutoModeDecision(allow=True, reason="workflow orchestrator")

    if tool_name.startswith("mcp__"):
        return AutoModeDecision(allow=False, reason="MCP tools require explicit approval")

    return AutoModeDecision(allow=False, reason=f"unknown tool: {tool_name}")
