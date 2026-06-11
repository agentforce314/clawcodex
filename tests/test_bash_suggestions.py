"""Tests for src/permissions/bash_suggestions.py (C1).

Mirrors the TS behavior of tools/BashTool/bashPermissions.ts
``suggestionForExactCommand`` / ``getSimpleCommandPrefix`` and the shared
builders in utils/permissions/shellRuleMatching.ts.
"""

from __future__ import annotations

import unittest

from src.permissions.bash_suggestions import (
    get_simple_command_prefix,
    suggestions_for_bash_command,
)
from src.permissions.types import PermissionUpdateAddRules


def _only_rule(updates):
    assert len(updates) == 1, updates
    update = updates[0]
    assert isinstance(update, PermissionUpdateAddRules)
    assert update.behavior == "allow"
    assert update.destination == "localSettings"
    assert len(update.rules) == 1
    return update.rules[0]


class TestGetSimpleCommandPrefix(unittest.TestCase):
    def test_two_word_prefix(self) -> None:
        self.assertEqual(
            get_simple_command_prefix('git commit -m "fix typo"'), "git commit"
        )

    def test_safe_env_var_skipped(self) -> None:
        self.assertEqual(
            get_simple_command_prefix("NODE_ENV=prod npm run build"), "npm run"
        )

    def test_unsafe_env_var_returns_none(self) -> None:
        self.assertIsNone(get_simple_command_prefix("MY_VAR=val npm run build"))

    def test_flag_second_token_returns_none(self) -> None:
        self.assertIsNone(get_simple_command_prefix("ls -la"))

    def test_filename_second_token_returns_none(self) -> None:
        self.assertIsNone(get_simple_command_prefix("cat file.txt"))

    def test_number_second_token_returns_none(self) -> None:
        self.assertIsNone(get_simple_command_prefix("chmod 755 file"))

    def test_single_word_returns_none(self) -> None:
        self.assertIsNone(get_simple_command_prefix("ls"))


class TestSuggestionsForBashCommand(unittest.TestCase):
    def test_prefix_rule_for_subcommand(self) -> None:
        rule = _only_rule(suggestions_for_bash_command("git diff --stat"))
        self.assertEqual(rule.tool_name, "Bash")
        self.assertEqual(rule.rule_content, "git diff:*")

    def test_exact_rule_when_no_prefix(self) -> None:
        rule = _only_rule(suggestions_for_bash_command("ls -la"))
        self.assertEqual(rule.rule_content, "ls -la")

    def test_heredoc_uses_prefix_before_operator(self) -> None:
        cmd = 'git commit -m "$(cat <<\'EOF\'\nmsg\nEOF\n)"'
        rule = _only_rule(suggestions_for_bash_command(cmd))
        self.assertEqual(rule.rule_content, "git commit:*")

    def test_heredoc_bare_shell_yields_nothing(self) -> None:
        # Deliberate divergence from TS: Bash(bash:*) ≈ Bash(*).
        self.assertEqual(suggestions_for_bash_command("bash <<EOF\nevil\nEOF"), [])

    def test_multiline_uses_first_line_prefix(self) -> None:
        cmd = "git status\ngit diff"
        rule = _only_rule(suggestions_for_bash_command(cmd))
        self.assertEqual(rule.rule_content, "git status:*")

    def test_empty_command_yields_nothing(self) -> None:
        self.assertEqual(suggestions_for_bash_command("   "), [])

    def test_compound_command_still_gets_first_prefix(self) -> None:
        # Safe because the matcher refuses chained commands at match time.
        rule = _only_rule(suggestions_for_bash_command("git diff && echo hi"))
        self.assertEqual(rule.rule_content, "git diff:*")


class TestContainsUnquotedChaining(unittest.TestCase):
    def test_operators_detected(self) -> None:
        from src.permissions.bash_suggestions import contains_unquoted_chaining

        for cmd in (
            "a && b",
            "a || b",
            "a; b",
            "a | b",
            "a\nb",
        ):
            self.assertTrue(contains_unquoted_chaining(cmd), cmd)

    def test_quoted_operators_ignored(self) -> None:
        from src.permissions.bash_suggestions import contains_unquoted_chaining

        for cmd in (
            'echo "a && b"',
            "echo 'a; b'",
            'grep "x|y" file',
            "git commit -m 'one; two'",
        ):
            self.assertFalse(contains_unquoted_chaining(cmd), cmd)

    def test_simple_commands_clean(self) -> None:
        from src.permissions.bash_suggestions import contains_unquoted_chaining

        self.assertFalse(contains_unquoted_chaining("git diff --stat"))
        self.assertFalse(contains_unquoted_chaining("ls -la /tmp"))


class TestMatcherChainingGuard(unittest.TestCase):
    def test_prefix_rule_rejects_chained_commands(self) -> None:
        from src.permissions.check import prepare_permission_matcher

        matcher = prepare_permission_matcher("git diff:*")
        self.assertTrue(matcher("git diff --cached"))
        self.assertFalse(matcher("git diff && echo hi"))
        self.assertFalse(matcher("git diff | tee out.txt"))
        self.assertFalse(matcher("git diff; echo hi"))

    def test_single_word_prefix_rule_rejects_chained(self) -> None:
        from src.permissions.check import prepare_permission_matcher

        matcher = prepare_permission_matcher("git:*")
        self.assertTrue(matcher("git status"))
        self.assertFalse(matcher("git status && git push"))

    def test_plain_prefix_rule_rejects_chained(self) -> None:
        from src.permissions.check import prepare_permission_matcher

        matcher = prepare_permission_matcher("npm run")
        self.assertTrue(matcher("npm run build"))
        self.assertFalse(matcher("npm run build && npm publish"))

    def test_quoted_operator_still_matches(self) -> None:
        from src.permissions.check import prepare_permission_matcher

        matcher = prepare_permission_matcher("git commit:*")
        self.assertTrue(matcher("git commit -m 'one; two'"))

    def test_explicit_allow_all_unguarded(self) -> None:
        from src.permissions.check import prepare_permission_matcher

        self.assertTrue(prepare_permission_matcher("*")("a && b"))
        self.assertTrue(prepare_permission_matcher("")("a && b"))


if __name__ == "__main__":
    unittest.main()
