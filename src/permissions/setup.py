from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from .bash_security import is_dangerous_bash_permission
from .loader import apply_rules_to_context, settings_to_rules
from .rule_parser import permission_rule_value_from_string, permission_rule_value_to_string
from .types import (
    AdditionalWorkingDirectory,
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    PermissionRuleValue,
    ToolPermissionContext,
    ToolPermissionRulesBySource,
)

logger = logging.getLogger(__name__)

SETTINGS_SOURCES_PRIORITY: list[PermissionRuleSource] = [
    "policySettings",
    "flagSettings",
    "userSettings",
    "projectSettings",
    "localSettings",
    "cliArg",
    "command",
    "session",
]


@dataclass
class DangerousRuleWarning:
    rule: PermissionRule
    tool_name: str
    rule_content: str | None
    source: PermissionRuleSource


@dataclass
class PermissionSetupResult:
    context: ToolPermissionContext
    warnings: list[DangerousRuleWarning] = field(default_factory=list)
    shadowed_rules: list[tuple[PermissionRule, PermissionRule]] = field(default_factory=list)


def _load_settings_file(path: str) -> dict[str, Any] | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _extract_permissions(settings: dict[str, Any] | None) -> dict[str, Any] | None:
    if not settings:
        return None
    perms = settings.get("permissions")
    if isinstance(perms, dict):
        return perms
    return None


def _extract_working_dirs(
    settings: dict[str, Any] | None,
    source: PermissionRuleSource,
) -> dict[str, AdditionalWorkingDirectory]:
    if not settings:
        return {}
    dirs = settings.get("additionalWorkingDirectories", [])
    if not isinstance(dirs, list):
        return {}
    result: dict[str, AdditionalWorkingDirectory] = {}
    for d in dirs:
        if isinstance(d, str) and d:
            resolved = os.path.abspath(os.path.expanduser(d))
            result[resolved] = AdditionalWorkingDirectory(path=resolved, source=source)
    return result


def setup_permissions(
    *,
    cwd: str | None = None,
    cli_allow: list[str] | None = None,
    cli_deny: list[str] | None = None,
    mode: PermissionMode = "default",
    user_settings_path: str | None = None,
    project_settings_path: str | None = None,
    local_settings_path: str | None = None,
    managed_settings_path: str | None = None,
    is_bypass_available: bool = False,
    should_avoid_prompts: bool = False,
) -> PermissionSetupResult:
    effective_cwd = cwd or os.getcwd()

    context = ToolPermissionContext(
        mode=mode,
        is_bypass_permissions_mode_available=is_bypass_available,
        should_avoid_permission_prompts=should_avoid_prompts,
    )

    warnings: list[DangerousRuleWarning] = []
    all_rules: list[PermissionRule] = []

    source_configs: list[tuple[str | None, PermissionRuleSource]] = [
        (managed_settings_path, "policySettings"),
        (user_settings_path, "userSettings"),
        (project_settings_path, "projectSettings"),
        (local_settings_path, "localSettings"),
    ]

    additional_dirs: dict[str, AdditionalWorkingDirectory] = {}

    for path, source in source_configs:
        if not path:
            continue
        settings = _load_settings_file(path)
        perms = _extract_permissions(settings)
        if perms:
            rules = settings_to_rules(perms, source)
            all_rules.extend(rules)
        dirs = _extract_working_dirs(settings, source)
        additional_dirs.update(dirs)

    if cli_allow:
        for rule_str in cli_allow:
            all_rules.append(PermissionRule(
                source="cliArg",
                rule_behavior="allow",
                rule_value=permission_rule_value_from_string(rule_str),
            ))

    if cli_deny:
        for rule_str in cli_deny:
            all_rules.append(PermissionRule(
                source="cliArg",
                rule_behavior="deny",
                rule_value=permission_rule_value_from_string(rule_str),
            ))

    for rule in all_rules:
        if rule.rule_behavior == "allow":
            if is_dangerous_bash_permission(
                rule.rule_value.tool_name,
                rule.rule_value.rule_content,
            ):
                warnings.append(DangerousRuleWarning(
                    rule=rule,
                    tool_name=rule.rule_value.tool_name,
                    rule_content=rule.rule_value.rule_content,
                    source=rule.source,
                ))

    context = apply_rules_to_context(context, all_rules)

    if additional_dirs:
        context = ToolPermissionContext(
            mode=context.mode,
            additional_working_directories={
                **context.additional_working_directories,
                **additional_dirs,
            },
            always_allow_rules=context.always_allow_rules,
            always_deny_rules=context.always_deny_rules,
            always_ask_rules=context.always_ask_rules,
            is_bypass_permissions_mode_available=context.is_bypass_permissions_mode_available,
            should_avoid_permission_prompts=context.should_avoid_permission_prompts,
        )

    shadowed = _detect_shadowed_rules(all_rules)

    return PermissionSetupResult(
        context=context,
        warnings=warnings,
        shadowed_rules=shadowed,
    )


def _detect_shadowed_rules(
    rules: list[PermissionRule],
) -> list[tuple[PermissionRule, PermissionRule]]:
    shadowed: list[tuple[PermissionRule, PermissionRule]] = []

    allow_rules = [r for r in rules if r.rule_behavior == "allow"]
    deny_rules = [r for r in rules if r.rule_behavior == "deny"]

    for allow_rule in allow_rules:
        for deny_rule in deny_rules:
            if allow_rule.rule_value.tool_name == deny_rule.rule_value.tool_name:
                if deny_rule.rule_value.rule_content is None:
                    shadowed.append((allow_rule, deny_rule))
                elif (
                    allow_rule.rule_value.rule_content is not None
                    and deny_rule.rule_value.rule_content is not None
                    and allow_rule.rule_value.rule_content == deny_rule.rule_value.rule_content
                ):
                    shadowed.append((allow_rule, deny_rule))

    return shadowed


def persist_session_rule(
    settings_path: str,
    rule_value: PermissionRuleValue,
    behavior: PermissionBehavior,
) -> bool:
    try:
        settings = _load_settings_file(settings_path) or {}
        perms = settings.setdefault("permissions", {})
        behavior_list = perms.setdefault(behavior, [])
        rule_str = permission_rule_value_to_string(rule_value)
        if rule_str not in behavior_list:
            behavior_list.append(rule_str)
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)
        return True
    except OSError:
        logger.error("Failed to persist rule to %s", settings_path)
        return False


def validate_permission_rules(rules: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    for rule_dict in rules:
        if "tool" not in rule_dict:
            errors.append("Rule missing 'tool' field")
            continue
        behavior = rule_dict.get("behavior", "")
        if behavior not in ("allow", "deny", "ask"):
            errors.append(f"Invalid behavior: {behavior}")
    return errors
