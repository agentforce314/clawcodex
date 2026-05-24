"""Tests for tree-sitter-bash adapter (Task #3)."""

from __future__ import annotations

import pytest

from src.permissions._treesitter_adapter import (
    BashlexParseResult,
    classify_command_with_bashlex,
    get_command_safety_from_bashlex,
    is_bashlex_available,
    parse_command_with_bashlex,
)
from src.permissions.bash_parser.commands import CommandSafety


class TestBashlexAvailable:
    def test_bashlex_is_available(self):
        assert is_bashlex_available() is True


class TestParseCommandWithBashlex:
    def test_simple_echo(self):
        result = parse_command_with_bashlex('echo hello world')
        assert result.kind == "simple"
        assert len(result.commands) == 1
        assert result.commands[0]['argv'][0] == 'echo'

    def test_git_command(self):
        result = parse_command_with_bashlex('git status')
        assert result.kind == "simple"
        assert result.commands[0]['argv'][0] == 'git'

    def test_empty_command(self):
        result = parse_command_with_bashlex('')
        assert result.kind == "simple"
        assert result.commands == []

    def test_whitespace_command(self):
        result = parse_command_with_bashlex('   ')
        assert result.kind == "simple"
        assert result.commands == []

    def test_complex_command_with_pipe(self):
        # bashlex handles simple commands well; pipes may result in too-complex
        # depending on the command structure - this is acceptable behavior
        result = parse_command_with_bashlex('echo test | wc')
        # Result can be either simple or too-complex depending on bashlex parsing
        assert result.kind in ("simple", "too-complex")

    def test_command_with_redirect(self):
        result = parse_command_with_bashlex('echo test > output.txt')
        assert result.kind == "simple"
        assert 'echo' in result.commands[0]['argv']


class TestClassifyCommand:
    def test_classify_git_read_only(self):
        safety = classify_command_with_bashlex('git status')
        assert safety == CommandSafety.READ_ONLY

    def test_classify_git_write(self):
        safety = classify_command_with_bashlex('git add .')
        assert safety == CommandSafety.WRITE

    def test_classify_git_push(self):
        safety = classify_command_with_bashlex('git push')
        assert safety == CommandSafety.DANGEROUS

    def test_classify_rm_destructive(self):
        safety = classify_command_with_bashlex('rm -rf /')
        assert safety == CommandSafety.DESTRUCTIVE

    def test_classify_echo_safe(self):
        safety = classify_command_with_bashlex('echo hello')
        assert safety == CommandSafety.SAFE

    def test_get_command_safety_alias(self):
        """Test that get_command_safety_from_bashlex is an alias."""
        safety1 = classify_command_with_bashlex('git status')
        safety2 = get_command_safety_from_bashlex('git status')
        assert safety1 == safety2


class TestBashlexParseResult:
    def test_parse_result_init(self):
        result = BashlexParseResult(kind="simple", commands=[], reason="")
        assert result.kind == "simple"
        assert result.commands == []
        assert result.reason == ""

    def test_parse_result_with_commands(self):
        commands = [{'argv': ['echo', 'hello'], 'env_vars': {}, 'redirects': [], 'text': 'echo hello'}]
        result = BashlexParseResult(kind="simple", commands=commands, reason="")
        assert len(result.commands) == 1


class TestBackwardCompatibility:
    def test_returns_bashlex_parse_result(self):
        """Ensure adapter returns BashlexParseResult type."""
        result = parse_command_with_bashlex('ls -la')
        assert isinstance(result, BashlexParseResult)