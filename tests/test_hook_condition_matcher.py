"""Phase-4 / WI-4.2 — matches_hook_condition tests.

Covers parsing + matching for the ``if_condition`` permission-rule
grammar. Composes ``permission_rule_value_from_string`` (parse) +
``prepare_permission_matcher`` (content matcher) + tool-specific input
field extraction.
"""

from __future__ import annotations

import pytest

from src.hooks.condition_matcher import matches_hook_condition
from src.hooks.hook_types import HookConfig


def _hook(*, matcher: str | None = None, if_condition: str | None = None) -> HookConfig:
    return HookConfig(
        type="command", command="x",
        matcher=matcher, if_condition=if_condition,
    )


class TestMatcherOnly:
    """Sanity: existing matcher behavior preserved when ``if_condition``
    is unset.
    """

    def test_no_matcher_no_condition_matches_all(self):
        assert matches_hook_condition(_hook(), "Bash", {}) is True

    def test_exact_matcher_match(self):
        assert matches_hook_condition(_hook(matcher="Bash"), "Bash", {}) is True

    def test_exact_matcher_miss(self):
        assert matches_hook_condition(_hook(matcher="Bash"), "Read", {}) is False

    def test_prefix_wildcard(self):
        assert matches_hook_condition(_hook(matcher="mcp__*"), "mcp__server_tool", {}) is True
        assert matches_hook_condition(_hook(matcher="mcp__*"), "Bash", {}) is False


class TestIfConditionToolNameOnly:
    """``if_condition`` with no parens — just a tool name."""

    def test_bare_tool_name_matches(self):
        assert matches_hook_condition(
            _hook(if_condition="Bash"), "Bash", {"command": "ls"},
        ) is True

    def test_bare_tool_name_misses(self):
        assert matches_hook_condition(
            _hook(if_condition="Bash"), "Read", {"file_path": "x"},
        ) is False


class TestIfConditionWithContent:
    """The chapter's worked example #1 territory:
    ``Bash(git commit*)`` matches ``git commit -m "msg"`` but NOT ``ls``.
    """

    def test_bash_git_commit_matches(self):
        assert matches_hook_condition(
            _hook(if_condition="Bash(git commit*)"),
            "Bash",
            {"command": "git commit -m 'test'"},
        ) is True

    def test_bash_git_commit_does_not_match_ls(self):
        # Headline regression test for the chapter's worked example #1.
        # Pre-Phase-4 this was inert: the matcher would fire on every
        # Bash call.
        assert matches_hook_condition(
            _hook(if_condition="Bash(git commit*)"),
            "Bash",
            {"command": "ls"},
        ) is False

    def test_bash_git_commit_does_not_match_other_git_subcommand(self):
        assert matches_hook_condition(
            _hook(if_condition="Bash(git commit*)"),
            "Bash",
            {"command": "git push"},
        ) is False

    def test_bash_wildcard_content_matches_anything(self):
        # ``Bash(*)`` and ``Bash()`` both reduce to "any Bash" per the
        # grammar (rule_parser strips empty/star content).
        assert matches_hook_condition(
            _hook(if_condition="Bash(*)"),
            "Bash",
            {"command": "anything"},
        ) is True

    def test_read_file_path_pattern(self):
        # Read tool's match target is ``file_path`` — verifies the
        # tool-input field mapping.
        assert matches_hook_condition(
            _hook(if_condition="Read(/etc/*)"),
            "Read",
            {"file_path": "/etc/passwd"},
        ) is True
        assert matches_hook_condition(
            _hook(if_condition="Read(/etc/*)"),
            "Read",
            {"file_path": "/home/user/file"},
        ) is False


class TestUnknownToolWithContent:
    """An ``if_condition`` with content for a tool we don't have an input
    field mapping for can't fire — conservative default.
    """

    def test_unknown_tool_with_content_returns_false(self):
        assert matches_hook_condition(
            _hook(if_condition="UnknownTool(foo*)"),
            "UnknownTool",
            {"some_field": "foo bar"},
        ) is False

    def test_unknown_tool_without_content_still_matches(self):
        # Tool-name-only condition: works regardless of mapping.
        assert matches_hook_condition(
            _hook(if_condition="UnknownTool"),
            "UnknownTool",
            {"some_field": "foo bar"},
        ) is True


class TestMatcherAndIfConditionAnded:
    """Both ``matcher`` and ``if_condition`` set: BOTH must pass."""

    def test_matcher_matches_but_if_does_not(self):
        # Matcher OK, if_condition fails → hook does NOT fire.
        assert matches_hook_condition(
            _hook(matcher="Bash", if_condition="Bash(git commit*)"),
            "Bash",
            {"command": "ls"},  # matcher would fire; if_condition won't
        ) is False

    def test_if_matches_but_matcher_does_not(self):
        # Hypothetical: matcher targets Read but if_condition targets Bash.
        # Tool is Bash → matcher fails, hook doesn't fire.
        assert matches_hook_condition(
            _hook(matcher="Read", if_condition="Bash(git*)"),
            "Bash",
            {"command": "git status"},
        ) is False

    def test_both_match(self):
        assert matches_hook_condition(
            _hook(matcher="Bash", if_condition="Bash(git commit*)"),
            "Bash",
            {"command": "git commit -am 'fix'"},
        ) is True


class TestMissingInput:
    """Edge: tool_input lacks the expected field."""

    def test_bash_without_command_field(self):
        assert matches_hook_condition(
            _hook(if_condition="Bash(git*)"),
            "Bash",
            {},  # no "command" key
        ) is False

    def test_bash_with_non_string_command(self):
        # Defensive: command field present but not a string.
        assert matches_hook_condition(
            _hook(if_condition="Bash(git*)"),
            "Bash",
            {"command": 42},
        ) is False
