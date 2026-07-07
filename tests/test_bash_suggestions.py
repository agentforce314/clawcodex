"""Tests for src/permissions/bash_suggestions.py (C1).

Mirrors the TS behavior of tools/BashTool/bashPermissions.ts
``suggestionForExactCommand`` / ``getSimpleCommandPrefix`` and the shared
builders in utils/permissions/shellRuleMatching.ts.
"""

from __future__ import annotations

import unittest

from src.permissions.bash_suggestions import (
    BARE_SHELL_PREFIXES,
    EVAL_LIKE_BUILTINS,
    NEVER_PREFIX_COMMANDS,
    get_safe_first_word_prefix,
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

    def test_first_word_generalizes_when_no_two_word_prefix(self) -> None:
        # `mv old.txt new.txt` has no 2-word prefix (2nd token is a path), so
        # the first-word rung now generalizes to `mv:*` (TS getFirstWordPrefix
        # parity; the rule-allow path gate keeps `mv:*` workspace-contained).
        rule = _only_rule(suggestions_for_bash_command("mv old.txt new.txt"))
        self.assertEqual(rule.rule_content, "mv:*")

    def test_exact_rule_for_ungeneralizable_first_word(self) -> None:
        # A path-form first token can't generalize (not a bare command name)
        # → exact rule fallback.
        rule = _only_rule(suggestions_for_bash_command("./run.sh --once"))
        self.assertEqual(rule.rule_content, "./run.sh --once")

    def test_substitution_heredoc_yields_no_suggestion(self) -> None:
        # A command with executable substitution ($(...)) is injection-suspect;
        # like TS's too-complex path it carries NO savable suggestion (was: the
        # port's heredoc special-case minted `git commit:*`).
        cmd = 'git commit -m "$(cat <<\'EOF\'\nmsg\nEOF\n)"'
        self.assertEqual(suggestions_for_bash_command(cmd), [])

    def test_heredoc_bare_shell_yields_nothing(self) -> None:
        # Deliberate divergence from TS: Bash(bash:*) ≈ Bash(*).
        self.assertEqual(suggestions_for_bash_command("bash <<EOF\nevil\nEOF"), [])

    def test_multiline_gets_per_sub_rules(self) -> None:
        # R6 compound parity: each line contributes a rule (was: first line only).
        cmd = "git status\ngit diff"
        updates = suggestions_for_bash_command(cmd)
        self.assertEqual(len(updates), 1)
        self.assertEqual([r.rule_content for r in updates[0].rules],
                         ["git status:*", "git diff:*"])

    def test_empty_command_yields_nothing(self) -> None:
        self.assertEqual(suggestions_for_bash_command("   "), [])

    def test_compound_command_gets_per_sub_rules(self) -> None:
        # R6 compound parity: one bundled addRules update covering every sub
        # (was: first sub's prefix only). Match-time requires ALL subs to
        # match, so the bundle is what stops the re-prompt.
        updates = suggestions_for_bash_command("git diff && echo hi")
        self.assertEqual(len(updates), 1)
        self.assertEqual([r.rule_content for r in updates[0].rules],
                         ["git diff:*", "echo hi:*"])

    def test_compound_of_safe_words_gets_per_sub_rules(self) -> None:
        # R6 compound parity: was [] (nothing suggestible for "ls && pwd").
        updates = suggestions_for_bash_command("ls && pwd")
        self.assertEqual([r.rule_content for r in updates[0].rules],
                         ["ls:*", "pwd:*"])

    def test_multiline_compound_gets_per_sub_rules(self) -> None:
        # R6 compound parity: all three subs contribute (was: first only).
        updates = suggestions_for_bash_command("git fetch && git rebase\ngit push")
        self.assertEqual([r.rule_content for r in updates[0].rules],
                         ["git fetch:*", "git rebase:*", "git push:*"])

    def test_multiline_bare_shell_sub_contributes_nothing(self) -> None:
        # D1 guard survives the R6 rework: the bare-shell sub yields no rule
        # (an exact "bash" rule would word-prefix-match "bash anything").
        updates = suggestions_for_bash_command("bash\necho hi")
        self.assertEqual([r.rule_content for r in updates[0].rules], ["echo hi:*"])

    def test_heredoc_at_index_zero_yields_nothing(self) -> None:
        self.assertEqual(suggestions_for_bash_command("<<EOF\nhi\nEOF"), [])

    def test_heredoc_with_chained_before_segment_yields_nothing(self) -> None:
        self.assertEqual(
            suggestions_for_bash_command("true && cat <<EOF\nhi\nEOF"), []
        )

    def test_env_assignment_then_compound(self) -> None:
        # R6 compound parity: both subs contribute; the safe env assignment
        # is still skipped for prefix derivation.
        updates = suggestions_for_bash_command("NODE_ENV=test npm run lint && npm test")
        self.assertEqual([r.rule_content for r in updates[0].rules],
                         ["npm run:*", "npm test:*"])


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

    def test_lone_ampersand_detected_redirections_skipped(self) -> None:
        from src.permissions.bash_suggestions import contains_unquoted_chaining

        self.assertTrue(contains_unquoted_chaining("a & b"))
        self.assertTrue(contains_unquoted_chaining("a&b"))
        self.assertTrue(contains_unquoted_chaining("sleep 5 &"))
        self.assertFalse(contains_unquoted_chaining("cmd 2>&1"))
        self.assertFalse(contains_unquoted_chaining("cmd <&3"))
        self.assertFalse(contains_unquoted_chaining("cmd &> out.log"))

    def test_escaped_operators(self) -> None:
        from src.permissions.bash_suggestions import contains_unquoted_chaining

        self.assertFalse(contains_unquoted_chaining(r"echo a\;b"))
        self.assertFalse(contains_unquoted_chaining(r"echo a\|b"))
        # Double backslash = literal backslash, then a REAL separator.
        self.assertTrue(contains_unquoted_chaining("echo a\\\\; rm x"))


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

    def test_prefix_rule_word_boundary(self) -> None:
        from src.permissions.check import prepare_permission_matcher

        matcher = prepare_permission_matcher("git diff:*")
        self.assertTrue(matcher("git diff"))
        self.assertFalse(matcher("git diffx"))

    def test_exact_rule_rejects_elongation(self) -> None:
        from src.permissions.check import prepare_permission_matcher

        matcher = prepare_permission_matcher("ls -la")
        self.assertTrue(matcher("ls -la"))
        self.assertTrue(matcher("ls -la /tmp"))
        self.assertFalse(matcher("ls -lah"))
        run = prepare_permission_matcher("npm run")
        self.assertFalse(run("npm runabc"))

    def test_basename_normalization_both_directions(self) -> None:
        from src.permissions.check import prepare_permission_matcher

        matcher = prepare_permission_matcher("git status:*")
        # Intended direction: path-qualified executable matches.
        self.assertTrue(matcher("/usr/bin/git status"))
        # Accepted trade-off (locked deliberately): a same-named
        # executable at ANY path also matches — the safety screen runs
        # first on every command, which bounds the exposure.
        self.assertTrue(matcher("/somewhere/else/git status"))

    def test_exact_prefix_suffix_branch(self) -> None:
        from src.permissions.check import prepare_permission_matcher

        matcher = prepare_permission_matcher("git:status*")
        self.assertTrue(matcher("git status --short"))
        self.assertFalse(matcher("git push"))
        self.assertFalse(matcher("git status && git push"))


class TestGetSafeFirstWordPrefix(unittest.TestCase):
    # Behavior CHANGED (loosen-permissions): the first-word rung is now a
    # faithful port of TS getFirstWordPrefix — it generalizes ANY bare command
    # name except bare shells/wrappers and eval-like builtins, because the TUI
    # approval box has an editable rule field (round 6). The prior read-only
    # allowlist (SAFE_PREFIX_COMMANDS) was deleted; it forced everyday dev
    # tools (pytest, ruff, find) to re-prompt on every arg.
    def test_generalizes_bare_command_names(self) -> None:
        for cmd, want in (
            ("ls demos/", "ls"),
            ("cat a/b/c.txt", "cat"),
            ("grep -r foo .", "grep"),
            ("pytest -q", "pytest"),
            ("ruff check .", "ruff"),
            ("find . -name x", "find"),
            ("sort -o out f", "sort"),
        ):
            self.assertEqual(get_safe_first_word_prefix(cmd), want, cmd)

    def test_bare_shells_and_eval_like_return_none(self) -> None:
        for cmd in ("bash -c ls", "sh x", "env ls", "xargs rm", "sudo ls",
                    "eval x", "source setup.sh", "trap 'x' EXIT",
                    "command ls", "let y=1"):
            self.assertIsNone(get_safe_first_word_prefix(cmd), cmd)

    def test_path_or_flag_first_token_returns_none(self) -> None:
        self.assertIsNone(get_safe_first_word_prefix("./script.sh"))
        self.assertIsNone(get_safe_first_word_prefix("/usr/bin/ls x"))
        self.assertIsNone(get_safe_first_word_prefix("-rf x"))

    def test_unsafe_env_var_returns_none(self) -> None:
        self.assertIsNone(get_safe_first_word_prefix("LD_PRELOAD=x ls /"))

    def test_safe_env_var_skipped(self) -> None:
        self.assertEqual(get_safe_first_word_prefix("NO_COLOR=1 ls /"), "ls")

    def test_never_prefix_covers_shells_and_eval_like(self) -> None:
        # The refusal set = bare shells/wrappers ∪ eval-like builtins.
        self.assertTrue(BARE_SHELL_PREFIXES <= NEVER_PREFIX_COMMANDS)
        self.assertTrue(EVAL_LIKE_BUILTINS <= NEVER_PREFIX_COMMANDS)
        for bad in ("bash", "sh", "env", "xargs", "sudo", "eval", "source",
                    "command", "builtin", "trap", "let", "hash"):
            self.assertIn(bad, NEVER_PREFIX_COMMANDS, bad)


class TestFirstWordPrefixSuggestion(unittest.TestCase):
    def _rule(self, command: str):
        updates = suggestions_for_bash_command(command)
        return _only_rule(updates).rule_content if updates else None

    def test_ls_path_generalizes_to_prefix(self) -> None:
        self.assertEqual(self._rule("ls demos/"), "ls:*")
        self.assertEqual(self._rule("cat foo.txt"), "cat:*")
        self.assertEqual(self._rule("grep -r foo ."), "grep:*")

    def test_reported_bug_one_grant_covers_sibling_paths(self) -> None:
        # Approving `ls .../demos/` must cover `ls .../demos/elon-blog/`.
        from src.permissions.check import prepare_permission_matcher

        rule = self._rule("ls /Users/x/workspace/demos/")
        self.assertEqual(rule, "ls:*")
        matcher = prepare_permission_matcher(rule)
        self.assertTrue(matcher("ls /Users/x/workspace/demos/elon-blog/"))

    def test_everyday_dev_tools_generalize(self) -> None:
        # The core UX win: one grant covers all future args (TS parity).
        self.assertEqual(self._rule("pytest -k foo"), "pytest:*")
        # `ruff check src` HAS a 2-word prefix (check is a subcommand) → the
        # tighter `ruff check:*` wins over the first-word rung.
        self.assertEqual(self._rule("ruff check src"), "ruff check:*")
        self.assertEqual(self._rule("find . -name x"), "find:*")

    def test_eval_like_and_substitution_never_suggested(self) -> None:
        # Guardrail: eval-like builtins and substitution-bearing commands get
        # NO savable suggestion (they ask through the structural path).
        for cmd in ('eval "x"', "source s.sh", "trap 'rm -rf /' EXIT",
                    'echo "$(date)"', "cat `whoami`"):
            self.assertEqual(suggestions_for_bash_command(cmd), [], cmd)

    def test_two_word_prefix_still_wins(self) -> None:
        # The 2-word prefix path is unchanged (takes precedence).
        self.assertEqual(self._rule("git status"), "git status:*")


if __name__ == "__main__":
    unittest.main()
