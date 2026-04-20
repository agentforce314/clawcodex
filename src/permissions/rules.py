from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .rule_parser import permission_rule_value_from_string
from .types import (
    PERMISSION_RULE_SOURCES,
    PermissionBehavior,
    PermissionRule,
    ToolPermissionContext,
)

if TYPE_CHECKING:
    pass


@runtime_checkable
class ToolLike(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def is_mcp(self) -> bool: ...


def get_allow_rules(context: ToolPermissionContext) -> list[PermissionRule]:
    rules: list[PermissionRule] = []
    for source in PERMISSION_RULE_SOURCES:
        for rule_string in context.always_allow_rules.get(source, []):
            rules.append(PermissionRule(
                source=source,
                rule_behavior="allow",
                rule_value=permission_rule_value_from_string(rule_string),
            ))
    return rules


def get_deny_rules(context: ToolPermissionContext) -> list[PermissionRule]:
    rules: list[PermissionRule] = []
    for source in PERMISSION_RULE_SOURCES:
        for rule_string in context.always_deny_rules.get(source, []):
            rules.append(PermissionRule(
                source=source,
                rule_behavior="deny",
                rule_value=permission_rule_value_from_string(rule_string),
            ))
    return rules


def get_ask_rules(context: ToolPermissionContext) -> list[PermissionRule]:
    rules: list[PermissionRule] = []
    for source in PERMISSION_RULE_SOURCES:
        for rule_string in context.always_ask_rules.get(source, []):
            rules.append(PermissionRule(
                source=source,
                rule_behavior="ask",
                rule_value=permission_rule_value_from_string(rule_string),
            ))
    return rules


def _get_tool_name_for_permission_check(tool: ToolLike) -> str:
    return tool.name


def _tool_matches_rule(tool: ToolLike, rule: PermissionRule) -> bool:
    if rule.rule_value.rule_content is not None:
        return False

    name_for_check = _get_tool_name_for_permission_check(tool)
    if rule.rule_value.tool_name == name_for_check:
        return True

    if name_for_check.startswith("mcp__"):
        parts = name_for_check.split("__", 2)
        rule_parts = rule.rule_value.tool_name.split("__", 2)
        if (
            len(rule_parts) >= 2
            and len(parts) >= 2
            and rule_parts[0] == "mcp"
            and parts[0] == "mcp"
            and rule_parts[1] == parts[1]
            and (len(rule_parts) < 3 or rule_parts[2] == "*")
        ):
            return True

    return False


def get_deny_rule_for_tool(
    context: ToolPermissionContext,
    tool: ToolLike,
) -> PermissionRule | None:
    for rule in get_deny_rules(context):
        if _tool_matches_rule(tool, rule):
            return rule
    return None


def get_ask_rule_for_tool(
    context: ToolPermissionContext,
    tool: ToolLike,
) -> PermissionRule | None:
    for rule in get_ask_rules(context):
        if _tool_matches_rule(tool, rule):
            return rule
    return None


def tool_always_allowed_rule(
    context: ToolPermissionContext,
    tool: ToolLike,
) -> PermissionRule | None:
    for rule in get_allow_rules(context):
        if _tool_matches_rule(tool, rule):
            return rule
    return None


def get_rule_by_contents_for_tool(
    context: ToolPermissionContext,
    tool_name: str,
    behavior: PermissionBehavior,
) -> dict[str, PermissionRule]:
    if behavior == "allow":
        rules = get_allow_rules(context)
    elif behavior == "deny":
        rules = get_deny_rules(context)
    else:
        rules = get_ask_rules(context)

    result: dict[str, PermissionRule] = {}
    for rule in rules:
        if (
            rule.rule_value.tool_name == tool_name
            and rule.rule_value.rule_content is not None
            and rule.rule_behavior == behavior
        ):
            result[rule.rule_value.rule_content] = rule
    return result


def filter_denied_agents(
    agents: list[dict[str, str]],
    context: ToolPermissionContext,
    agent_tool_name: str,
) -> list[dict[str, str]]:
    denied_types: set[str] = set()
    for rule in get_deny_rules(context):
        if (
            rule.rule_value.tool_name == agent_tool_name
            and rule.rule_value.rule_content is not None
        ):
            denied_types.add(rule.rule_value.rule_content)
    return [a for a in agents if a.get("agentType", "") not in denied_types]
