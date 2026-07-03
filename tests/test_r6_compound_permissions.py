"""R6 — compound-command permission parity (TS bashPermissions merge flow).

User report: pipelines like ``grep … | tr … | sort -u`` re-prompted every time
with only Yes/No (no persistable option). Root causes: (1) the suggestion
ladder derived at most the FIRST sub-command's prefix (usually nothing) for a
compound; (2) the matcher refuses chained commands, with no per-sub-command
path, so no rule set could ever auto-allow a compound; (3) Bash CONTENT
deny/ask rules (``Bash(rm:*)``) were consulted NOWHERE — silently unenforced.

TS parity implemented (typescript/src/tools/BashTool/bashPermissions.ts):
- split compound commands (splitCommand port: split_chained_command, refusing
  exotic syntax — refusal degrades to today's prompt, never a wider allow);
- allow iff EVERY sub-command matches an allow content rule (:2383/:2470);
- deny/ask content rules match the whole command AND every sub-command
  (:842 — wrapping a denied command in a compound cannot bypass it), with
  all-env-assignment stripping for deny/ask (stripAllEnvVars);
- per-sub-command "don't ask again" suggestions aggregated into ONE addRules
  update, deduped, capped at 5 (:2487-2547, GH#11380).
"""
from __future__ import annotations

import unittest

from src.permissions.bash_suggestions import (
    split_chained_command,
    suggestions_for_bash_command,
)
from src.permissions.check import has_permissions_to_use_tool_inner
from src.permissions.types import (
    PermissionPassthroughResult,
    ToolPermissionContext,
)

USER_PIPELINE = (
    "grep -ohE '\"tags\": \\[[^]]*\\]' /Users/x/src/data/posts.ts"
    " | tr ',' '\\n' | grep -o '\"[^\"]*\"' | sort -u | tr -d '\"'"
)


class _BashTool:
    name = "Bash"

    def check_permissions(self, tool_input, context):
        return PermissionPassthroughResult()


def _ctx(allow=(), deny=(), ask=()):
    return ToolPermissionContext(
        always_allow_rules={"session": [f"Bash({r})" for r in allow]},
        always_deny_rules={"session": [f"Bash({r})" for r in deny]},
        always_ask_rules={"session": [f"Bash({r})" for r in ask]},
    )


def _decide(command, ctx):
    return has_permissions_to_use_tool_inner(_BashTool(), {"command": command}, ctx)


class TestSplitChainedCommand(unittest.TestCase):
    def test_splits_operators(self):
        self.assertEqual(
            split_chained_command("a | b && c ; d || e & f |& g"),
            ["a", "b", "c", "d", "e", "f", "g"],
        )
        self.assertEqual(split_chained_command("a\nb"), ["a", "b"])

    def test_quotes_protect_operators(self):
        self.assertEqual(split_chained_command("echo 'a|b' | tr x y"),
                         ["echo 'a|b'", "tr x y"])
        self.assertEqual(split_chained_command('echo "a && b"'), ['echo "a && b"'])
        # Escaped operator outside quotes is literal, not a separator.
        self.assertEqual(split_chained_command("echo a\\|b"), ["echo a\\|b"])

    def test_redirections_stay_inside_pieces(self):
        self.assertEqual(split_chained_command("make 2>&1 | tail -5"),
                         ["make 2>&1", "tail -5"])
        self.assertEqual(split_chained_command("cmd &> log | wc -l"),
                         ["cmd &> log", "wc -l"])
        # `>|` is a force-clobber redirect, not a pipe boundary.
        self.assertEqual(split_chained_command("a >| f | b"), ["a >| f", "b"])

    def test_refusals(self):
        for cmd in (
            "echo $(rm -rf /) | cat",       # command substitution (unquoted)
            'echo "$(rm -rf /)" | cat',     # SECURITY: $() executes in double quotes
            'echo "`rm -rf /`" | cat',      # SECURITY: backtick executes in double quotes
            'cat "$(id)" | grep x',         # ditto, mid-pipeline
            "echo `id` | cat",              # backticks (unquoted)
            "diff <(ls a) <(ls b)",         # process substitution / parens
            "(cd /tmp && ls) | cat",        # subshell
            "cat <<EOF | tee\nhi\nEOF",     # heredoc
            "echo a \\\n | rm -rf /",       # backslash-newline continuation
            "echo $'a\\'' | rm -rf /",      # ANSI-C quoting blind spot
            "echo 'unterminated | cat",     # unbalanced quote
        ):
            self.assertIsNone(split_chained_command(cmd), cmd)
        # Cap: >50 pieces refuses.
        self.assertIsNone(split_chained_command(" ; ".join(["echo x"] * 51)))

    def test_bash53_value_substitution_refused(self):
        # bash 5.3 `${ cmd; }` / `${| cmd; }` EXECUTE cmd → refuse (like $()).
        self.assertIsNone(split_chained_command('echo "${ rm -rf /; }" | cat'))
        self.assertIsNone(split_chained_command('echo "${| id; }" | cat'))
        # Plain parameter expansion does NOT execute → still splits.
        self.assertEqual(split_chained_command('echo "${VAR}" | cat'),
                         ['echo "${VAR}"', 'cat'])
        self.assertEqual(split_chained_command('echo "${VAR:-x}" | cat'),
                         ['echo "${VAR:-x}"', 'cat'])

    def test_substitution_literal_in_single_quotes_still_splits(self):
        # Single quotes make $()/backtick LITERAL (bash), so it's safe to split.
        self.assertEqual(
            split_chained_command("echo '$(rm -rf /)' | cat"),
            ["echo '$(rm -rf /)'", "cat"],
        )
        # Backslash-escaped $ in double quotes is literal too.
        self.assertEqual(
            split_chained_command('echo "\\$(rm)" | cat'),
            ['echo "\\$(rm)"', "cat"],
        )

    def test_user_pipeline_splits_correctly(self):
        subs = split_chained_command(USER_PIPELINE)
        self.assertIsNotNone(subs)
        self.assertEqual(len(subs), 5)
        self.assertTrue(subs[0].startswith("grep -ohE"))
        self.assertEqual(subs[3], "sort -u")


class TestCompoundSuggestions(unittest.TestCase):
    def test_user_pipeline_gets_bundled_rules(self):
        updates = suggestions_for_bash_command(USER_PIPELINE)
        self.assertEqual(len(updates), 1)  # ONE addRules update (TS parity)
        contents = [r.rule_content for r in updates[0].rules]
        # grep/tr are read-only-safe first words → prefix rules; sort is
        # excluded from the safe set (sort -o writes) → exact; dedup applies.
        self.assertEqual(contents, ["grep:*", "tr:*", "sort -u"])
        self.assertEqual(updates[0].destination, "localSettings")

    def test_cap_at_five_rules(self):
        cmd = " | ".join(f"cmd{i} arg" for i in range(9))
        updates = suggestions_for_bash_command(cmd)
        self.assertEqual(len(updates), 1)
        self.assertEqual(len(updates[0].rules), 5)

    def test_splitter_refusal_falls_back_to_legacy(self):
        # Command substitution → no split; legacy first-sub 2-word prefix.
        updates = suggestions_for_bash_command("git status && echo $(id)")
        contents = [r.rule_content for u in updates for r in u.rules]
        self.assertEqual(contents, ["git status:*"])


class TestCompoundMatching(unittest.TestCase):
    def test_all_subs_matching_allows_the_pipeline(self):
        ctx = _ctx(allow=("grep:*", "tr:*", "sort -u"))
        self.assertEqual(_decide(USER_PIPELINE, ctx).behavior, "allow")

    def test_one_unmatched_sub_still_asks(self):
        ctx = _ctx(allow=("grep:*", "tr:*"))  # no rule for sort -u
        self.assertEqual(_decide(USER_PIPELINE, ctx).behavior, "ask")

    def test_accepting_the_suggestion_stops_reprompting(self):
        # The full loop: ask → accept the suggested bundle → same command allows.
        from src.permissions.updates import apply_permission_updates

        ctx = ToolPermissionContext()
        first = _decide(USER_PIPELINE, ctx)
        self.assertEqual(first.behavior, "ask")
        self.assertTrue(first.suggestions)
        ctx2 = apply_permission_updates(ctx, list(first.suggestions))
        self.assertEqual(_decide(USER_PIPELINE, ctx2).behavior, "allow")
        # And a VARIANT built from the same commands is covered too.
        variant = "grep -c foo /tmp/f.txt | sort -u | tr -d 'x'"
        self.assertEqual(_decide(variant, ctx2).behavior, "allow")

    def test_simple_commands_unchanged(self):
        ctx = _ctx(allow=("git status:*",))
        self.assertEqual(_decide("git status --short", ctx).behavior, "allow")
        self.assertEqual(_decide("git log", ctx).behavior, "ask")

    def test_splitter_refusal_never_allows(self):
        # Every sub would match, but the $() refusal keeps it at ask.
        ctx = _ctx(allow=("grep:*", "cat:*"))
        self.assertEqual(_decide("grep $(id) x | cat", ctx).behavior, "ask")

    def test_double_quoted_substitution_never_auto_allows(self):
        # SECURITY: $()/backtick execute inside double quotes; even with allow
        # rules for every visible command, the smuggled rm must NOT be allowed.
        ctx = _ctx(allow=("echo:*", "cat:*"))
        self.assertEqual(_decide('echo "$(rm -rf /)" | cat', ctx).behavior, "ask")
        self.assertEqual(_decide('echo "`rm -rf /`" | cat', ctx).behavior, "ask")


class TestContentDenyAskEnforced(unittest.TestCase):
    def test_deny_rule_now_enforced_on_simple_command(self):
        ctx = _ctx(deny=("rm:*",))
        self.assertEqual(_decide("rm -rf /tmp/x", ctx).behavior, "deny")

    def test_deny_cannot_be_bypassed_by_wrapping(self):
        ctx = _ctx(allow=("echo:*", "rm:*"), deny=("rm:*",))
        # Deny wins over allow, whole or wrapped (TS :842).
        self.assertEqual(_decide("echo hi && rm -rf /tmp/x", ctx).behavior, "deny")

    def test_deny_ignores_env_var_prefix(self):
        ctx = _ctx(deny=("rm:*",))
        # Compound AND simple, bare AND quoted-value (spaces) env prefixes.
        for cmd in (
            "echo a && FOO=1 rm -rf /tmp/x",
            "FOO=1 rm -rf /tmp/x",
            'FOO="a b" rm -rf /tmp/x',
            "A=1 B='c d' rm -rf /tmp/x",
        ):
            self.assertEqual(_decide(cmd, ctx).behavior, "deny", cmd)

    def test_allow_strips_only_SAFE_env_prefix(self):
        # SAFE_ENV_VARS (e.g. NODE_ENV) ARE stripped for allow matching so an
        # accepted rule matches the command it was suggested for (TS parity +
        # the user's "re-prompts every time" case). An UNSAFE env prefix is a
        # different command and still prompts.
        ctx = _ctx(allow=("npm run:*", "npm test:*"))
        self.assertEqual(
            _decide("NODE_ENV=test npm run lint", ctx).behavior, "allow")
        self.assertEqual(
            _decide("NODE_ENV=test npm run lint && npm test", ctx).behavior, "allow")
        # Non-safe env var → not stripped → prompt.
        self.assertEqual(
            _decide("EVIL=1 npm run lint", ctx).behavior, "ask")

    def test_value_substitution_never_auto_allows(self):
        # SECURITY: bash 5.3 ${ cmd; } executes; must not auto-allow via echo:*.
        ctx = _ctx(allow=("echo:*", "cat:*"))
        self.assertEqual(_decide('echo "${ rm -rf /; }" | cat', ctx).behavior, "ask")

    def test_exact_deny_matches_the_exact_compound(self):
        ctx = ToolPermissionContext(
            always_deny_rules={"session": ["Bash(git status && git log)"]}
        )
        self.assertEqual(_decide("git status && git log", ctx).behavior, "deny")

    def test_ask_rule_beats_allow_for_a_sub(self):
        ctx = _ctx(allow=("git status:*", "git log:*"), ask=("git log:*",))
        self.assertEqual(_decide("git status && git log", ctx).behavior, "ask")


class TestSubstitutionAllowGuard(unittest.TestCase):
    """A content ALLOW rule must never auto-allow a command hiding an
    executable construct. The guard is SELF-SUFFICIENT — it catches every
    execution form directly, not relying on the safety analyzer's tokenizer
    (see TestSafetyScreenBackstop for the independent belt-and-suspenders)."""

    def test_echo_grant_does_not_run_hidden_execution(self):
        ctx = _ctx(allow=("echo:*", "cat:*", "tee:*"))
        for cmd in (
            'echo "$(rm -rf /)"',        # cmd-sub in double quotes
            'echo "`rm -rf /`"',         # backtick in double quotes
            'echo "${ rm -rf /; }"',     # bash-5.3 value substitution
            'echo $(rm -rf /)',          # cmd-sub unquoted
            'echo \\$(rm -rf /)',        # \$ literal but (rm) is a SUBSHELL
            'cat <(rm -rf /)',           # process substitution
            'tee >(rm -rf /) f',         # process substitution
            "echo $'x'$(rm -rf /)",      # ANSI-C quoting (scanner desync)
            'echo hi; (rm -rf /)',       # bare subshell in a compound
        ):
            self.assertEqual(_decide(cmd, ctx).behavior, "ask", cmd)

    def test_legit_commands_under_the_grant_still_allow(self):
        ctx = _ctx(allow=("echo:*",))
        self.assertEqual(_decide("echo hello world", ctx).behavior, "allow")
        # Single quotes make $() literal — no execution, so allow is fine.
        self.assertEqual(_decide("echo '$(x)'", ctx).behavior, "allow")
        # Arithmetic runs no command; parameter expansion / quoted parens too.
        self.assertEqual(_decide("echo $((1 + 1))", ctx).behavior, "allow")
        self.assertEqual(_decide('echo "${HOME}"', ctx).behavior, "allow")
        self.assertEqual(_decide('echo "(literal paren)"', ctx).behavior, "allow")

    def test_deny_still_wins_over_substitution_command(self):
        # Even though the guard makes it ask, an explicit echo deny still denies.
        ctx = _ctx(allow=("echo:*",), deny=("echo:*",))
        self.assertEqual(_decide('echo "$(rm -rf /)"', ctx).behavior, "deny")


class TestSafetyScreenBackstop(unittest.TestCase):
    """Belt-and-suspenders: with the REAL bash safety screen wired in (not the
    passthrough stub the other tests use), genuinely-dangerous commands are
    caught even without any rule — and the substitution forms stay ask whether
    the guard or the analyzer fires. Pins that the two layers agree."""

    def _decide_faithful(self, command, allow=()):
        from src.tool_system.tools.bash.bash_tool import check_bash_command_safety

        class _FaithfulBash:
            name = "Bash"

            def check_permissions(self, tool_input, context):
                r = check_bash_command_safety(tool_input.get("command", ""), cwd=None)
                return r if r is not None else PermissionPassthroughResult()

        ctx = ToolPermissionContext(
            always_allow_rules={"session": [f"Bash({r})" for r in allow]}
        )
        return has_permissions_to_use_tool_inner(
            _FaithfulBash(), {"command": command}, ctx
        ).behavior

    def test_dangerous_command_asks_without_a_rule(self):
        self.assertEqual(self._decide_faithful("rm -rf /tmp/x"), "ask")

    def test_substitution_forms_ask_even_with_a_grant(self):
        # The guard fires first; the safety screen would also catch these — both
        # layers point the same way, so this can never silently over-allow.
        for cmd in (
            'echo "$(rm -rf /)"',
            'cat <(rm -rf /)',
            'echo \\$(rm -rf /)',
            "echo $'x'$(rm -rf /)",
        ):
            self.assertEqual(self._decide_faithful(cmd, allow=("echo:*", "cat:*")),
                             "ask", cmd)

    def test_plain_safe_command_still_allows_under_grant(self):
        self.assertEqual(self._decide_faithful("echo hello", allow=("echo:*",)),
                         "allow")


class TestDenyNormalization(unittest.TestCase):
    """deny/ask reduce to the program that actually runs (env + safe wrappers +
    leading-backslash) so wrapping can't bypass a Bash(rm:*) deny."""

    def test_safe_wrappers_cannot_bypass_deny(self):
        ctx = _ctx(deny=("rm:*",))
        for cmd in (
            "timeout 5 rm -rf x",
            "timeout --preserve-status 5s rm -rf x",
            "nohup rm -rf x",
            "nice -n 5 rm -rf x",
            "time rm -rf x",
            "nohup FOO=1 timeout 5 rm -rf x",  # interleaved wrapper + env
            "echo hi && timeout 5 rm -rf x",   # inside a compound sub
        ):
            self.assertEqual(_decide(cmd, ctx).behavior, "deny", cmd)

    def test_leading_backslash_cannot_bypass_deny(self):
        ctx = _ctx(deny=("rm:*",))
        self.assertEqual(_decide("\\rm -rf x", ctx).behavior, "deny")
        self.assertEqual(_decide("echo a && \\rm -rf x", ctx).behavior, "deny")

    def test_wrapper_stripping_does_not_false_allow(self):
        # timeout-wrapped command is NOT auto-allowed just because a sub-word
        # matches (allow does not normalize wrappers).
        ctx = _ctx(allow=("rm:*",))
        self.assertEqual(_decide("timeout 5 rm -rf x", ctx).behavior, "ask")


if __name__ == "__main__":
    unittest.main()
