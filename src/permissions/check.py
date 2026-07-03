from __future__ import annotations

import fnmatch
import logging
import os
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

from .bash_security import analyze_bash_command
from .bash_suggestions import (
    SAFE_ENV_VARS,
    contains_executable_substitution,
    contains_unquoted_chaining,
    split_chained_command,
)
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

        decision = auto_mode_classify(
            tool.name, tool_input, context,
            tool=tool, tool_use_context=tool_use_context,
        )
        if decision.unavailable:
            # critic M1 — classifier outage + iron-gate-OPEN: TS returns the
            # original ask (permissions.ts:871-876), which PROMPTS
            # interactively and hits the headless-deny guard below.
            # NEVER a silent auto-allow.
            if context.should_avoid_permission_prompts:
                return PermissionDenyDecision(
                    behavior="deny",
                    message=(
                        f"Auto-mode classifier unavailable for {tool.name} "
                        "and prompts are not available in this context"
                    ),
                    decision_reason=ClassifierDecisionReason(
                        classifier="auto-mode", reason=decision.reason,
                    ),
                )
            return result  # the original ask → prompt the user
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

    # ch08 round-4 WI-3 — bubble mode is NOT a resolver case (TS has no
    # 'bubble' branch in permissions.ts; facet Q4). Bubble is purely an
    # upstream setting: run_agent._build_permission_context sets
    # should_avoid_permission_prompts=False + await_automated=True for
    # bubble, so the ask "bubbles" to the parent's handler by falling
    # through here (return result). The previous deny-stub CONTRADICTED
    # that cascade — it denied every ask in bubble mode, which would have
    # made every subagent (and any --permission-mode bubble session)
    # uniformly denied the moment bubble became reachable. A headless
    # bubble agent (should_avoid=True, no handler) still fails closed at
    # the guard just below. Removing the stub makes bubble ready for the
    # deferred async-fork escalation without shipping the deny-everything
    # trap.

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

    # Bash content deny rules (compound-aware) — see _bash_content_rule_decision.
    deny_content = _bash_content_rule_decision(tool, tool_input, context, "deny")
    if deny_content is not None:
        return deny_content

    ask_rule = get_ask_rule_for_tool(context, tool)
    if ask_rule:
        return PermissionAskDecision(
            behavior="ask",
            decision_reason=RuleDecisionReason(rule=ask_rule),
            message=create_permission_request_message(tool.name),
        )

    # Bash content ask rules (after all deny sources; before anything allows).
    ask_content = _bash_content_rule_decision(tool, tool_input, context, "ask")
    if ask_content is not None:
        return ask_content

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
        # A content ALLOW rule (e.g. Bash(echo:*)) must never auto-allow a
        # command that hides an executable substitution — `echo "$(rm -rf /)"`
        # runs the rm, which the safety analyzer tokenizes away and the string
        # matcher can't see. Refuse to auto-allow → the command prompts instead
        # (the compound path is already covered: split_chained_command refuses
        # substitution, so per-sub allow never fires on it). Content-less
        # "allow all Bash" is intentionally NOT gated here — that user allowed
        # everything.
        if command and contains_executable_substitution(command):
            command = ""
        if command:
            # Try the command AND — like the suggestion ladder, which skips
            # SAFE_ENV_VARS — a copy with those safe assignments stripped, so
            # `NODE_ENV=test npm run lint` matches the `npm run:*` rule it was
            # suggested for (TS stripSafeWrappers strips safe env for allow).
            # Only the curated safe-list is stripped; an unsafe env prefix still
            # prompts.
            allow_candidates = [command]
            safe_stripped = _strip_env_assignments(command, safe_only=True)
            if safe_stripped and safe_stripped != command:
                allow_candidates.append(safe_stripped)
            for cand in allow_candidates:
                for rule_content, rule in content_rules.items():
                    matcher = prepare_permission_matcher(rule_content)
                    if matcher(cand):
                        return PermissionAllowDecision(
                            behavior="allow",
                            updated_input=tool_input,
                            decision_reason=RuleDecisionReason(rule=rule),
                        )
            # Compound command: allow iff EVERY sub-command matches some
            # allow content rule (TS bashPermissions.ts:2383/2470). Runs
            # after the whole-command loop (which refuses chained commands
            # by design) and after the tool safety screen above — a
            # safety-flagged compound never reaches here. Each sub faces
            # the SAME matcher a simple command would, so this never widens
            # what one rule can match; it only lets several rules agree.
            witness = _all_subcommands_allowed_by_content_rules(context, command)
            if witness is not None:
                return PermissionAllowDecision(
                    behavior="allow",
                    updated_input=tool_input,
                    decision_reason=RuleDecisionReason(rule=witness),
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


def _bash_content_rule_decision(
    tool: CheckPermissionsTool,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
    behavior: str,
) -> PermissionAskDecision | PermissionDenyDecision | None:
    """Bash CONTENT deny/ask rules — e.g. ``Bash(rm:*)`` — matched against
    the whole command AND every sub-command of a compound (TS
    bashPermissions.ts:842: deny/ask rules must match compound commands so
    they can't be bypassed by wrapping). Previously content deny/ask rules
    were consulted NOWHERE — only content-less rules (rules.py
    ``_tool_matches_rule`` rejects ``rule_content``) — so a configured
    ``Bash(rm:*)`` deny was silently unenforced."""

    if tool.name != "Bash":
        return None
    rule = _match_bash_content_rules(
        context, str(tool_input.get("command", "") or ""), behavior
    )
    if rule is None:
        return None
    if behavior == "deny":
        return PermissionDenyDecision(
            behavior="deny",
            decision_reason=RuleDecisionReason(rule=rule),
            message=f"Permission to use {tool.name} has been denied.",
        )
    return PermissionAskDecision(
        behavior="ask",
        decision_reason=RuleDecisionReason(rule=rule),
        message=create_permission_request_message(tool.name),
    )


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

    deny_content = _bash_content_rule_decision(tool, tool_input, context, "deny")
    if deny_content is not None:
        return deny_content

    ask_rule = get_ask_rule_for_tool(context, tool)
    if ask_rule:
        return PermissionAskDecision(
            behavior="ask",
            decision_reason=RuleDecisionReason(rule=ask_rule),
            message=create_permission_request_message(tool.name),
        )

    ask_content = _bash_content_rule_decision(tool, tool_input, context, "ask")
    if ask_content is not None:
        return ask_content

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


# A leading ``NAME=value`` assignment where value may be single-quoted,
# double-quoted (both may contain spaces), or a bare word. Matching the quote
# form is what stops ``FOO="a b" rm x`` from splitting mid-value and leaving the
# ``rm`` unstripped — which would let a quoted-space env prefix bypass a
# ``Bash(rm:*)`` deny/ask rule. The bare value class excludes shell metachars
# (``;|&()<>$`` + backtick) so it can't over-consume an operator into the value
# (TS's class does the same); the splitter separates real operators first, but
# this keeps the helper correct if reused outside that sandwich.
_ENV_ASSIGNMENT_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|[^\s'\";|&()<>$`]*)"
)

# Safe command wrappers TS strips before deny/ask matching (stripSafeWrappers,
# bashPermissions.ts:501-540) so `timeout 5 rm -rf x` / `nohup rm x` can't slip
# past a Bash(rm:*) deny. Only wrappers that don't change WHICH program runs
# (sudo/env/xargs are deliberately excluded — they can redirect execution).
_SAFE_WRAPPER_RES = [
    re.compile(
        r"^timeout[ \t]+(?:(?:--\S+|-[A-Za-z][ \t]+\S+|-[A-Za-z]\S*)[ \t]+)*"
        r"(?:--[ \t]+)?\d+(?:\.\d+)?[smhd]?[ \t]+"
    ),
    re.compile(r"^time[ \t]+(?:--[ \t]+)?"),
    re.compile(r"^nice(?:[ \t]+-n[ \t]+-?\d+|[ \t]+-\d+)?[ \t]+(?:--[ \t]+)?"),
    re.compile(r"^stdbuf(?:[ \t]+-[ioe]\S+)+[ \t]+(?:--[ \t]+)?"),
    re.compile(r"^nohup[ \t]+(?:--[ \t]+)?"),
]


def _strip_safe_wrappers(command: str) -> str:
    rest = command.lstrip()
    changed = True
    while changed:
        changed = False
        for pat in _SAFE_WRAPPER_RES:
            m = pat.match(rest)
            if m:
                rest = rest[m.end():].lstrip()
                changed = True
                break
    return rest


def _normalize_for_deny_ask(command: str) -> str:
    """Reduce a command to the program it will actually run, for deny/ask
    matching: strip ALL env assignments and safe wrappers, interleaved to a
    fixed point (``nohup FOO=1 timeout 5 rm`` → ``rm``), then unescape a leading
    ``\\`` on the command word (bash runs ``\\rm`` as ``rm``)."""

    prev = None
    cur = command.lstrip()
    while cur != prev:
        prev = cur
        cur = _strip_all_env_assignments(cur)
        cur = _strip_safe_wrappers(cur)
    if cur.startswith("\\") and len(cur) > 1 and cur[1] not in " \t\\":
        cur = cur[1:]
    return cur


def _strip_env_assignments(command: str, *, safe_only: bool) -> str:
    """Drop leading ``NAME=value`` assignments (values may be single/double
    quoted or bare). ``safe_only`` limits stripping to the curated
    :data:`SAFE_ENV_VARS` allow-list; otherwise ALL assignments are dropped.
    The remainder is returned as its original substring (no re-tokenization)."""

    rest = command.lstrip()
    while rest:
        m = _ENV_ASSIGNMENT_RE.match(rest)
        # Must consume a whole shell word: the char after the assignment has to
        # be whitespace or end-of-string, else this is part of a larger token.
        if not m or (m.end() < len(rest) and not rest[m.end()].isspace()):
            break
        if safe_only and m.group(0).split("=", 1)[0] not in SAFE_ENV_VARS:
            break
        rest = rest[m.end():].lstrip()
    return rest


def _strip_all_env_assignments(command: str) -> str:
    """ALL leading ``NAME=value`` (TS ``stripAllEnvVars`` for deny/ask —
    bashPermissions.ts:942-947) so ``FOO=1 rm x`` / ``FOO="a b" rm x`` cannot
    slip past a ``Bash(rm:*)`` deny/ask rule."""

    return _strip_env_assignments(command, safe_only=False)


def _match_bash_content_rules(
    context: ToolPermissionContext,
    command: str,
    behavior: str,
) -> "PermissionRule | None":
    """First Bash content rule of ``behavior`` matching ``command``, checking
    the whole command AND — for a chained command — every sub-command.

    TS parity (bashPermissions.ts:842-843): deny/ask rules MUST be able to
    match compound commands so ``echo hi && rm -rf /`` can't bypass a
    ``Bash(rm:*)`` deny by wrapping. Three layers, most-certain first:

    1. exact string equality against the raw command (TS matchMode 'exact'
       bypasses the compound guard — the only way an exact rule naming a
       compound can ever match, since the generic matcher refuses chaining);
    2. the standard whole-command matcher (refuses chained commands);
    3. per-sub-command matching via :func:`split_chained_command` — a rule
       matching ANY sub-command matches the compound. Deny/ask matching also
       tries each sub with ALL env assignments stripped (TS stripAllEnvVars).

    Splitter refusal (exotic syntax) degrades to layers 1-2 — i.e. today's
    behavior — never to a wider match.
    """

    content_rules = get_rule_by_contents_for_tool(context, "Bash", behavior)
    if not content_rules or not command:
        return None

    stripped = command.strip()
    matchers: list[tuple[Callable[[str], bool], Any]] = []
    for rule_content, rule in content_rules.items():
        if stripped and stripped == str(rule_content).strip():
            return rule  # exact match bypasses the compound guard (TS 'exact')
        matchers.append((prepare_permission_matcher(rule_content), rule))

    # For deny/ask, ALSO try the command normalized to the program it actually
    # runs — env assignments + safe wrappers stripped, leading `\` unescaped —
    # so `FOO=1 rm x`, `timeout 5 rm x`, `nohup rm x`, `\rm x` can't slip past
    # Bash(rm:*) (TS stripAllEnvVars + stripSafeWrappers). Allow matching
    # deliberately does NOT normalize — a wrapped/prefixed command is a
    # different command and should re-prompt.
    normalize = behavior in ("deny", "ask")

    def _match_one(cmd: str) -> "PermissionRule | None":
        variants = [cmd]
        if normalize:
            norm = _normalize_for_deny_ask(cmd)
            if norm and norm != cmd:
                variants.append(norm)
        for cand in variants:
            for matcher, rule in matchers:
                if matcher(cand):
                    return rule
        return None

    whole = _match_one(command)
    if whole is not None:
        return whole

    if contains_unquoted_chaining(command):
        subs = split_chained_command(command)
        if subs:
            for sub in subs:
                sub_match = _match_one(sub)
                if sub_match is not None:
                    return sub_match
    return None


def _all_subcommands_allowed_by_content_rules(
    context: ToolPermissionContext,
    command: str,
) -> "PermissionRule | None":
    """When EVERY sub-command of a chained ``command`` matches some allow
    content rule, return a witness rule (the last sub's match); else None.

    TS parity (bashPermissions.ts:2383/2470): a compound command is allowed
    iff all of its sub-commands are individually allowed. The whole-command
    matcher (chaining guard) stays authoritative for simple commands; this
    runs only for chained ones, each sub matched by the SAME matcher a simple
    command would face — so splitting never widens what a single rule can
    match, it only requires more rules to agree. Splitter refusal → None
    (today's behavior: prompt).
    """

    if not contains_unquoted_chaining(command):
        return None
    content_rules = get_rule_by_contents_for_tool(context, "Bash", "allow")
    if not content_rules:
        return None
    subs = split_chained_command(command)
    if not subs:
        return None

    matchers = [
        (prepare_permission_matcher(rule_content), rule)
        for rule_content, rule in content_rules.items()
    ]
    witness = None
    for sub in subs:
        # Strip SAFE_ENV_VARS only (as the suggestion ladder does) so an
        # accepted rule actually matches the sub it came from.
        candidates = [sub]
        safe_stripped = _strip_env_assignments(sub, safe_only=True)
        if safe_stripped and safe_stripped != sub:
            candidates.append(safe_stripped)
        for cand in candidates:
            for matcher, rule in matchers:
                if matcher(cand):
                    witness = rule
                    break
            else:
                continue
            break
        else:
            return None
    return witness


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
    # ch06 round-4 PR-B (critic M1): the classifier was UNAVAILABLE
    # (error/timeout/abort) and the iron gate is OPEN. TS's fail-open
    # returns the original ASK (prompts / headless-denies), it never
    # auto-allows on a classifier outage. When this is True the auto
    # branch surfaces the ask instead of trusting ``allow``.
    unavailable: bool = False


def auto_mode_classify(
    tool_name: str,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
    *,
    tool: Any = None,
    tool_use_context: Any = None,
) -> AutoModeDecision:
    """Auto-mode classification.

    ch06 round-4: when the transcript-classifier flag is ON and the
    caller supplies ``tool`` + ``tool_use_context`` (present on the live
    query-loop path), the STATIC heuristic below is the fast-path
    pre-filter — a heuristic ``allow`` short-circuits with ZERO LLM cost
    (the static allow-set is a subset of what's safe). Only a heuristic
    ``deny`` escalates to the LLM classifier, which sees the transcript +
    the pending action and can override the deny (or confirm it). Flag OFF
    → pure static behavior (today's zero-extra-cost path)."""
    static = _auto_mode_classify_static(tool_name, tool_input, context)

    if static.allow:
        return static
    # Residual (heuristic would deny): escalate to the LLM classifier when
    # enabled and the live inputs are available.
    if tool is not None and tool_use_context is not None:
        try:
            from .yolo_classifier import (
                classify_action_llm,
                is_transcript_classifier_enabled,
            )

            if is_transcript_classifier_enabled():
                decision = classify_action_llm(tool, tool_input, tool_use_context)
                # Record denial tracking around the verdict (critic M2).
                _record_classifier_outcome(tool_use_context, decision.allow)
                # Over-limit (3 consecutive / 20 total denials) → stop
                # classifying and surface the ask so a human breaks the
                # loop (interactive) or the headless agent aborts via the
                # prompts-unavailable deny. TS handleDenialLimitExceeded
                # (permissions.ts:985-1059).
                if not decision.allow and _classifier_denial_fallback(
                    tool_use_context
                ):
                    return AutoModeDecision(
                        allow=False,
                        reason=(
                            "classifier denial limit reached — human "
                            "confirmation required"
                        ),
                        unavailable=True,  # route through the ask/headless-deny path
                    )
                return AutoModeDecision(
                    allow=decision.allow,
                    reason=f"classifier: {decision.reason}",
                    # unavailable+allow only happens on iron-gate-OPEN; the
                    # auto branch surfaces the ask instead of allowing.
                    unavailable=decision.unavailable and decision.allow,
                )
        except Exception:  # noqa: BLE001 — classifier failure keeps the static deny
            import logging

            logging.getLogger(__name__).debug(
                "auto-mode classifier escalation failed; using static deny",
                exc_info=True,
            )
    return static


def _record_classifier_outcome(tool_use_context: Any, allowed: bool) -> None:
    """ch06 round-4 PR-B (critic M2) — denial tracking on the session.

    A DenialState lives on the tool_use_context (per session). On a block
    we record a denial; on an allow we reset the consecutive streak. When
    the limit trips (3 consecutive / 20 total) the NEXT block should fall
    back to a manual prompt — surfaced via the DenialState the caller can
    inspect. Best-effort; a tracking failure never affects the verdict."""
    try:
        from .yolo_classifier import DenialState

        state = getattr(tool_use_context, "_classifier_denials", None)
        if not isinstance(state, DenialState):
            state = DenialState()
            try:
                tool_use_context._classifier_denials = state
            except Exception:  # noqa: BLE001 — read-only stub context
                return
        if allowed:
            state.record_success()
        else:
            state.record_denial()
    except Exception:  # noqa: BLE001
        pass


def _classifier_denial_fallback(tool_use_context: Any) -> bool:
    """True when the denial limit has tripped — the caller should fall back
    to a manual prompt (interactive) or deny (headless)."""
    try:
        from .yolo_classifier import DenialState

        state = getattr(tool_use_context, "_classifier_denials", None)
        return isinstance(state, DenialState) and state.should_fallback_to_prompt()
    except Exception:  # noqa: BLE001
        return False


def _auto_mode_classify_static(
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
