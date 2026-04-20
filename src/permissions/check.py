from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

from .bash_security import analyze_bash_command
from .rules import (
    get_ask_rule_for_tool,
    get_deny_rule_for_tool,
    get_rule_by_contents_for_tool,
    tool_always_allowed_rule,
)
from .types import (
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
    result = has_permissions_to_use_tool_inner(
        tool, tool_input, context, tool_use_context=tool_use_context,
    )

    if context.mode == "dontAsk" and result.behavior == "ask":
        return PermissionDenyDecision(
            behavior="deny",
            message="Permission denied: dontAsk mode is active.",
            decision_reason=ModeDecisionReason(mode="dontAsk"),
        )

    if (
        context.should_avoid_permission_prompts
        and result.behavior == "ask"
    ):
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
        return PermissionAskDecision(
            behavior="ask",
            message=create_permission_request_message(
                tool.name,
                getattr(tool_permission_result, "decision_reason", None),
            ),
            decision_reason=getattr(tool_permission_result, "decision_reason", None),
            suggestions=getattr(tool_permission_result, "suggestions", None),
        )

    if tool_permission_result.behavior == "allow":
        if isinstance(tool_permission_result, PermissionAllowDecision):
            return tool_permission_result
        return PermissionAllowDecision(
            behavior="allow",
            updated_input=getattr(tool_permission_result, "updated_input", None),
            decision_reason=getattr(tool_permission_result, "decision_reason", None),
        )

    return _coerce_to_ask_decision(tool_permission_result, tool.name)


def _coerce_to_ask_decision(
    result: PermissionResult,
    tool_name: str,
) -> PermissionAskDecision:
    if isinstance(result, PermissionAskDecision):
        return result
    return PermissionAskDecision(
        behavior="ask",
        message=getattr(result, "message", create_permission_request_message(tool_name)),
        decision_reason=getattr(result, "decision_reason", None),
        suggestions=getattr(result, "suggestions", None),
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
    if not rule_content:
        return lambda _: True

    if rule_content == "*":
        return lambda _: True

    if ":" in rule_content:
        prefix = rule_content.split(":", 1)[0]
        suffix = rule_content.split(":", 1)[1]
        if suffix == "*":
            def _prefix_matcher(command: str) -> bool:
                parts = command.strip().split(None, 1)
                if not parts:
                    return False
                cmd_name = parts[0].rsplit("/", 1)[-1]
                return cmd_name == prefix
            return _prefix_matcher
        else:
            def _exact_prefix_matcher(command: str) -> bool:
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
        return lambda command: fnmatch.fnmatch(command.strip(), rule_content)

    return lambda command: command.strip().startswith(rule_content)


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

    if tool_name.startswith("mcp__"):
        return AutoModeDecision(allow=False, reason="MCP tools require explicit approval")

    return AutoModeDecision(allow=False, reason=f"unknown tool: {tool_name}")
