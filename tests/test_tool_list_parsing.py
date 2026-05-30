"""Tests for the slash-command allowed-tools parsers (Phase 1.5).

Covers ``parse_tool_list_from_cli`` (paren-aware comma/space splitter) and
``parse_slash_command_tools_from_frontmatter`` (frontmatter-value normalizer with the
``*`` wildcard short-circuit). Both live in ``src/utils/markdown_config_loader.py`` —
ports of ``permissionSetup.ts:parseToolListFromCLI`` and
``markdownConfigLoader.ts:parseSlashCommandToolsFromFrontmatter``.
"""
from __future__ import annotations

from src.utils.markdown_config_loader import (
    parse_slash_command_tools_from_frontmatter,
    parse_tool_list_from_cli,
)

# The exact allowed-tools string shipped in security-review.ts frontmatter.
SECURITY_REVIEW_ALLOWED_TOOLS = (
    "Bash(git diff:*), Bash(git status:*), Bash(git log:*), "
    "Bash(git show:*), Bash(git remote show:*), Read, Glob, Grep, LS, Task"
)

SECURITY_REVIEW_EXPECTED = [
    "Bash(git diff:*)",
    "Bash(git status:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(git remote show:*)",
    "Read",
    "Glob",
    "Grep",
    "LS",
    "Task",
]


def test_empty_inputs_return_empty_list():
    assert parse_slash_command_tools_from_frontmatter(None) == []
    assert parse_slash_command_tools_from_frontmatter("") == []
    assert parse_slash_command_tools_from_frontmatter([]) == []


def test_security_review_value_parses_exactly():
    parsed = parse_slash_command_tools_from_frontmatter(SECURITY_REVIEW_ALLOWED_TOOLS)
    assert parsed == SECURITY_REVIEW_EXPECTED


def test_comma_inside_parens_is_preserved():
    # The outer comma splits; the comma inside ``(...)`` is kept verbatim.
    assert parse_tool_list_from_cli(["Bash(a, b:*), Read"]) == ["Bash(a, b:*)", "Read"]


def test_space_outside_parens_splits():
    assert parse_tool_list_from_cli(["Read Glob"]) == ["Read", "Glob"]


def test_space_inside_parens_is_preserved():
    # ``git remote show`` has spaces inside the parens — must survive as one token.
    assert parse_tool_list_from_cli(["Bash(git remote show:*)"]) == [
        "Bash(git remote show:*)"
    ]


def test_wildcard_short_circuits():
    assert parse_slash_command_tools_from_frontmatter("Read, *") == ["*"]


def test_list_input_drops_non_strings():
    assert parse_slash_command_tools_from_frontmatter(["Read", 3, "Glob"]) == [
        "Read",
        "Glob",
    ]


def test_single_scalar_string():
    assert parse_slash_command_tools_from_frontmatter("Read") == ["Read"]


def test_unsupported_type_returns_empty():
    # A dict (or any non-None/str/list) is not a valid tools value.
    assert parse_slash_command_tools_from_frontmatter({"a": 1}) == []
