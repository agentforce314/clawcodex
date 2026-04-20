from __future__ import annotations

from .rule_parser import permission_rule_value_from_string, permission_rule_value_to_string
from .types import (
    PermissionBehavior,
    PermissionRule,
    PermissionRuleSource,
    PermissionRuleValue,
    PermissionUpdateDestination,
    ToolPermissionContext,
)


def settings_to_rules(
    permissions_data: dict | None,
    source: PermissionRuleSource,
) -> list[PermissionRule]:
    if not permissions_data:
        return []

    rules: list[PermissionRule] = []
    for behavior in ("allow", "deny", "ask"):
        behavior_list = permissions_data.get(behavior, [])
        for rule_string in behavior_list:
            rules.append(PermissionRule(
                source=source,
                rule_behavior=behavior,  # type: ignore[arg-type]
                rule_value=permission_rule_value_from_string(rule_string),
            ))
    return rules


def apply_rules_to_context(
    context: ToolPermissionContext,
    rules: list[PermissionRule],
) -> ToolPermissionContext:
    allow_rules = dict(context.always_allow_rules)
    deny_rules = dict(context.always_deny_rules)
    ask_rules = dict(context.always_ask_rules)

    for rule in rules:
        rule_string = permission_rule_value_to_string(rule.rule_value)
        if rule.rule_behavior == "allow":
            allow_rules.setdefault(rule.source, []).append(rule_string)
        elif rule.rule_behavior == "deny":
            deny_rules.setdefault(rule.source, []).append(rule_string)
        elif rule.rule_behavior == "ask":
            ask_rules.setdefault(rule.source, []).append(rule_string)

    return ToolPermissionContext(
        mode=context.mode,
        additional_working_directories=dict(context.additional_working_directories),
        always_allow_rules=allow_rules,
        always_deny_rules=deny_rules,
        always_ask_rules=ask_rules,
        is_bypass_permissions_mode_available=context.is_bypass_permissions_mode_available,
        should_avoid_permission_prompts=context.should_avoid_permission_prompts,
    )
