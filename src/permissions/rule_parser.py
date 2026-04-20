from __future__ import annotations

from .types import PermissionRuleValue

LEGACY_TOOL_NAME_ALIASES: dict[str, str] = {
    "Task": "Agent",
    "KillShell": "TaskStop",
    "AgentOutputTool": "TaskOutput",
    "BashOutputTool": "TaskOutput",
}


def normalize_legacy_tool_name(name: str) -> str:
    return LEGACY_TOOL_NAME_ALIASES.get(name, name)


def get_legacy_tool_names(canonical_name: str) -> list[str]:
    return [
        legacy
        for legacy, canonical in LEGACY_TOOL_NAME_ALIASES.items()
        if canonical == canonical_name
    ]


def escape_rule_content(content: str) -> str:
    return (
        content
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def unescape_rule_content(content: str) -> str:
    return (
        content
        .replace("\\(", "(")
        .replace("\\)", ")")
        .replace("\\\\", "\\")
    )


def _find_first_unescaped_char(s: str, char: str) -> int:
    for i, c in enumerate(s):
        if c == char:
            backslash_count = 0
            j = i - 1
            while j >= 0 and s[j] == "\\":
                backslash_count += 1
                j -= 1
            if backslash_count % 2 == 0:
                return i
    return -1


def _find_last_unescaped_char(s: str, char: str) -> int:
    for i in range(len(s) - 1, -1, -1):
        if s[i] == char:
            backslash_count = 0
            j = i - 1
            while j >= 0 and s[j] == "\\":
                backslash_count += 1
                j -= 1
            if backslash_count % 2 == 0:
                return i
    return -1


def permission_rule_value_from_string(rule_string: str) -> PermissionRuleValue:
    open_paren = _find_first_unescaped_char(rule_string, "(")
    if open_paren == -1:
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(rule_string))

    close_paren = _find_last_unescaped_char(rule_string, ")")
    if close_paren == -1 or close_paren <= open_paren:
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(rule_string))

    if close_paren != len(rule_string) - 1:
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(rule_string))

    tool_name = rule_string[:open_paren]
    raw_content = rule_string[open_paren + 1 : close_paren]

    if not tool_name:
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(rule_string))

    if raw_content == "" or raw_content == "*":
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(tool_name))

    rule_content = unescape_rule_content(raw_content)
    return PermissionRuleValue(
        tool_name=normalize_legacy_tool_name(tool_name),
        rule_content=rule_content,
    )


def permission_rule_value_to_string(rule_value: PermissionRuleValue) -> str:
    if not rule_value.rule_content:
        return rule_value.tool_name
    escaped_content = escape_rule_content(rule_value.rule_content)
    return f"{rule_value.tool_name}({escaped_content})"
