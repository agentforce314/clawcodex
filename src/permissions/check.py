from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
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

    if (
        isinstance(tool, RequiresInteractionTool)
        and tool.requires_user_interaction()
        and tool_permission_result.behavior == "ask"
    ):
        return _coerce_to_ask_decision(tool_permission_result, tool.name)

    if (
        tool_permission_result.behavior == "ask"
        and hasattr(tool_permission_result, "decision_reason")
        and tool_permission_result.decision_reason is not None
        and tool_permission_result.decision_reason.type == "rule"
        and hasattr(tool_permission_result.decision_reason, "rule")
        and tool_permission_result.decision_reason.rule.rule_behavior == "ask"
    ):
        return _coerce_to_ask_decision(tool_permission_result, tool.name)

    if (
        tool_permission_result.behavior == "ask"
        and hasattr(tool_permission_result, "decision_reason")
        and tool_permission_result.decision_reason is not None
        and tool_permission_result.decision_reason.type == "safetyCheck"
    ):
        return _coerce_to_ask_decision(tool_permission_result, tool.name)

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
        )

    if tool_permission_result.behavior == "allow":
        if isinstance(tool_permission_result, PermissionAllowDecision):
            return tool_permission_result
        return PermissionAllowDecision(
            behavior="allow",
            updated_input=getattr(tool_permission_result, "updated_input", None),
            decision_reason=getattr(tool_permission_result, "decision_reason", None),
        )

    return _coerce_to_ask_decision(tool_permission_result, tool.name, tool_input)


def _coerce_to_ask_decision(
    result: PermissionResult,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
) -> PermissionAskDecision:
    if isinstance(result, PermissionAskDecision):
        return _with_default_suggestions(result, tool_name, tool_input)
    return _with_default_suggestions(
        PermissionAskDecision(
            behavior="ask",
            message=getattr(result, "message", create_permission_request_message(tool_name)),
            decision_reason=getattr(result, "decision_reason", None),
            suggestions=getattr(result, "suggestions", None),
        ),
        tool_name,
        tool_input,
    )


def _with_default_suggestions(
    ask: PermissionAskDecision,
    tool_name: str,
    tool_input: dict[str, Any] | None,
) -> PermissionAskDecision:
    """Fill ``ask.suggestions`` with derived "don't ask again" rules.

    Mirrors TS bashPermissions.ts:1234-1236 (step 5: "Suggest prefix if
    available, otherwise exact command"). Two deliberate exclusions:

    * safety-flagged asks keep an empty list — TS :1219 ("Don't suggest
      saving a potentially dangerous command");
    * asks that already carry suggestions (a tool supplied its own) are
      left untouched.

    Read asks get the ``Read(<dir>/**)`` rule via the previously-orphaned
    :func:`create_read_rule_suggestion`.
    """

    if ask.suggestions:
        return ask
    if isinstance(ask.decision_reason, SafetyCheckDecisionReason):
        return ask
    if not tool_input:
        return ask

    suggestions: list[Any] | None = None
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if isinstance(command, str) and command.strip():
            from .bash_suggestions import suggestions_for_bash_command

            suggestions = suggestions_for_bash_command(command)
    elif tool_name == "Read":
        file_path = tool_input.get("file_path") or tool_input.get("path")
        if isinstance(file_path, str) and file_path:
            import os as _os

            from .updates import create_read_rule_suggestion

            suggestion = create_read_rule_suggestion(
                _os.path.dirname(file_path) or file_path,
                destination="localSettings",
            )
            suggestions = [suggestion] if suggestion else None

    if not suggestions:
        return ask
    ask.suggestions = suggestions
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
        return _coerce_to_ask_decision(tool_permission_result, tool.name)

    if (
        tool_permission_result.behavior == "ask"
        and hasattr(tool_permission_result, "decision_reason")
        and tool_permission_result.decision_reason is not None
        and tool_permission_result.decision_reason.type == "safetyCheck"
    ):
        return _coerce_to_ask_decision(tool_permission_result, tool.name)

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
