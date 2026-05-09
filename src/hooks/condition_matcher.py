"""Hook ``if`` condition matcher.

Phase-4 / WI-4.2. The chapter's worked example #1
(``ch12-extensibility.md``) describes a settings.json hook configured with
``"if": "Bash(git commit*)"`` that fires only when the model issues a Bash
``git commit`` call — never on ``ls`` or any other Bash command.

Pre-Phase-4 the ``if_condition`` field was parsed (Phase 1 / WI-1.3) and
stored on ``HookConfig`` but never consulted. The chapter's worked example
#1 was therefore inert: any matching ``Bash`` matcher would fire
indiscriminately. This module adds the missing consumer.

**Reuses existing permission-rule infrastructure (per A6 + N1):**
  * ``permission_rule_value_from_string`` parses the grammar (e.g.,
    ``Bash(git commit*)`` → ``PermissionRuleValue(tool_name="Bash",
    rule_content="git commit*")``).
  * ``tool_matches_rule`` (promoted from ``_tool_matches_rule`` per N1)
    handles the *name-only* leg.
  * ``prepare_permission_matcher`` builds a callable that evaluates a
    glob-style content pattern against an input string.

**Why the new module rather than extending ``tool_matches_rule``:** the
permissions system *intentionally* returns False on content-bearing rules
in ``tool_matches_rule`` — content matching is a separate scope (granular
permission gates, e.g., the user explicitly allowed ``Bash(git commit*)``).
For hook ``if`` we want the *combined* check (name + optional content).
Extending the existing function would change its semantic contract; a
separate composed helper is cleaner.
"""

from __future__ import annotations

from typing import Any

from src.permissions.check import prepare_permission_matcher
from src.permissions.rule_parser import permission_rule_value_from_string
from src.permissions.types import PermissionRule, PermissionRuleValue

from .hook_types import HookConfig


# Map from tool name → the input-dict field whose string value is the
# matchable target for a content rule. Mirrors TS' tool-specific input
# extraction. Each entry is a deliberate choice; unknown tools fall back
# to "no input target" → content rules can't fire on them.
#
# Bash(git commit*) → matches against tool_input["command"].
# Read(/etc/*)      → matches against tool_input["file_path"].
# Etc.
_TOOL_INPUT_FIELD_FOR_RULE: dict[str, str] = {
    "Bash": "command",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "WebFetch": "url",
}


def _tool_name_matches(rule_tool_name: str, actual_tool_name: str) -> bool:
    """Just the name leg of ``tool_matches_rule``, factored out so we can
    use it with content-bearing rules (where ``tool_matches_rule`` itself
    short-circuits to False).

    Mirrors the MCP-namespace handling at ``permissions/rules.py`` so a
    rule like ``mcp__foo__*`` matches any ``mcp__foo__bar`` call.
    """
    if rule_tool_name == actual_tool_name:
        return True
    if actual_tool_name.startswith("mcp__"):
        parts = actual_tool_name.split("__", 2)
        rule_parts = rule_tool_name.split("__", 2)
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


def _extract_match_target(
    tool_name: str,
    tool_input: dict[str, Any],
) -> str | None:
    """Return the string that a content-rule should match against, or
    ``None`` if this tool has no defined match target.

    Returning ``None`` causes content-bearing rules to evaluate False (the
    matcher can't run). This is the conservative behavior: an unknown
    tool's hook with ``if: SomeTool(*)`` won't fire spuriously — better
    to require explicit support than to silently match.
    """
    field = _TOOL_INPUT_FIELD_FOR_RULE.get(tool_name)
    if field is None:
        return None
    value = tool_input.get(field)
    if isinstance(value, str):
        return value
    return None


def matches_hook_condition(
    hook: HookConfig,
    tool_name: str,
    tool_input: dict[str, Any],
) -> bool:
    """Evaluate a hook's combined matcher + ``if_condition`` against a
    tool call. Returns True iff the hook should fire.

    Logic:
      1. ``matcher`` (existing semantic) — simple tool-name glob; handled
         by the executor's existing ``_matches_tool``. ``None`` matcher
         matches all tools.
      2. ``if_condition`` (Phase-4 addition) — permission-rule grammar.
         If absent, the matcher result alone decides. If present, BOTH
         the matcher and the rule must pass.

    Both checks AND'd: a hook with both ``matcher: "Bash"`` and
    ``if_condition: "Bash(git commit*)"`` fires only on ``Bash`` calls
    whose command starts with ``git commit``. A hook with only
    ``if_condition`` (no ``matcher``) fires when the rule passes.
    """
    # The matcher leg is unchanged from pre-Phase-4 behavior. Re-using the
    # executor's ``_matches_tool`` would create a circular import; the
    # logic is small enough to inline here.
    if hook.matcher is not None and not _matches_tool_simple(hook.matcher, tool_name):
        return False

    if not hook.if_condition:
        # No ``if`` clause — matcher decision wins.
        return True

    rule_value = permission_rule_value_from_string(hook.if_condition)

    # Tool-name leg: mandatory. (A condition like ``*`` parses to
    # ``tool_name="*"``, which matches no real tool — TS' grammar
    # requires either a tool name or a content pattern.)
    if not _tool_name_matches(rule_value.tool_name, tool_name):
        return False

    # No content pattern → name match alone is sufficient.
    if rule_value.rule_content is None:
        return True

    # Content pattern: build matcher and evaluate against the tool's
    # primary input string.
    target = _extract_match_target(tool_name, tool_input)
    if target is None:
        # Unknown tool → no target; content-rule can't fire.
        return False

    matcher = prepare_permission_matcher(rule_value.rule_content)
    return matcher(target)


def _matches_tool_simple(matcher: str | None, tool_name: str) -> bool:
    """Minimal ``matcher: str`` evaluator (ports the executor's existing
    ``_matches_tool``). ``None`` matches all; ``Bash`` exact-matches;
    ``Bash*`` prefix-matches; ``*Bash`` suffix-matches.
    """
    if matcher is None:
        return True
    if matcher == tool_name:
        return True
    if matcher.endswith("*"):
        return tool_name.startswith(matcher[:-1])
    if matcher.startswith("*"):
        return tool_name.endswith(matcher[1:])
    return False
