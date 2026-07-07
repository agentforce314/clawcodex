"""Guardrail tests for the permission-loosening rework.

The loosening lets saved rules fire (no more un-grantable class asks) and
auto-allows read-only commands — these tests pin the boundaries that must NOT
have loosened with it (design-critic blockers #1 and #2):

1. eval-like builtins (TS EVAL_LIKE_BUILTINS): always ask, empty suggestions,
   never minted as a rule, never honored via a prefix rule — only a raw
   exact-string allow fires (TS checkEarlyExitDeny honors exact allows on the
   semantics path).
2. Rule-allowed path-write commands stay contained: ``Bash(rm:*)`` must not
   auto-run ``rm -rf ~`` / out-of-workspace targets (TS runs
   checkPathConstraints BEFORE allow rules).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.permissions.bash_suggestions import suggestions_for_bash_command
from src.permissions.check import has_permissions_to_use_tool_inner
from src.permissions.types import ToolPermissionContext
from src.tool_system.tools.bash.bash_tool import BashTool


class _ToolUseContext:
    """Minimal stand-in for ToolContext: cwd + allowed_roots + perm context."""

    def __init__(self, root: str, perm: ToolPermissionContext) -> None:
        self.cwd = root
        self._root = root
        self.permission_context = perm

    def allowed_roots(self):
        return [self._root]


def _ctx(allow=(), mode="default"):
    return ToolPermissionContext(
        mode=mode,
        always_allow_rules={"session": [f"Bash({r})" for r in allow]},
    )


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = str(Path(self.tmp.name).resolve())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def decide(self, command: str, allow=(), mode="default"):
        perm = _ctx(allow=allow, mode=mode)
        return has_permissions_to_use_tool_inner(
            BashTool,
            {"command": command},
            perm,
            tool_use_context=_ToolUseContext(self.root, perm),
        )


class TestEvalLikeBuiltins(_Base):
    def test_eval_asks_with_empty_suggestions(self) -> None:
        decision = self.decide('eval "ls -la"')
        self.assertEqual(decision.behavior, "ask")
        self.assertFalse(getattr(decision, "suggestions", None))

    def test_eval_prefix_rule_never_fires(self) -> None:
        # Even a (hand-written) Bash(eval:*) rule must not auto-allow: the
        # structural ask precedes content-rule matching, as in TS where
        # checkSemantics precedes the allow rules.
        decision = self.decide('eval "rm -rf /"', allow=["eval:*"])
        self.assertEqual(decision.behavior, "ask")

    def test_eval_exact_allow_does_not_fire(self) -> None:
        # TS checkSemanticsDeny honors ONLY deny rules on the semantics path —
        # never an allow. Even an exact ``Bash(eval "ls")`` rule must not run
        # eval (the arguments are code; a static allow can't vouch for them).
        decision = self.decide('eval "ls"', allow=['eval "ls"'])
        self.assertEqual(decision.behavior, "ask")

    def test_no_suggestion_minted_for_eval_like(self) -> None:
        for cmd in ('eval "x"', "source setup.sh", "trap 'rm -rf /' EXIT",
                    "command rm -rf /", "hash -p /tmp/evil ls",
                    "let 'x=a[$(id)]'"):
            self.assertEqual(suggestions_for_bash_command(cmd), [], cmd)

    def test_zsh_dangerous_builtins_refused(self) -> None:
        # TS ZSH_DANGEROUS_BUILTINS — refused like eval-like builtins (parity
        # + defense-in-depth if a zsh path ever becomes reachable).
        for cmd, rule in (
            ("zmodload zsh/system", "zmodload:*"),
            ("zf_rm -rf x", "zf_rm:*"),
            ("sysopen -w -o creat -u 3 /etc/y", "sysopen:*"),
            ("emulate sh -c x", "emulate:*"),
        ):
            self.assertEqual(self.decide(cmd, allow=[rule]).behavior, "ask", cmd)
            self.assertEqual(suggestions_for_bash_command(cmd), [], cmd)

    def test_wrapper_hidden_eval_still_asks(self) -> None:
        decision = self.decide('nohup eval "ls"')
        self.assertEqual(decision.behavior, "ask")
        self.assertFalse(getattr(decision, "suggestions", None))

    def test_compound_with_eval_leg_asks(self) -> None:
        decision = self.decide('ls && eval "x"', allow=["ls:*", "eval:*"])
        self.assertEqual(decision.behavior, "ask")

    def test_name_eval_subscript_attack_blocked(self) -> None:
        # `printf -v 'a[$(id)]'` etc. arithmetically evaluate the array
        # subscript → run $(id) even single-quoted. Must ask under any grant,
        # mint no suggestion, and NOT run via an exact rule (TS checkSemantics
        # is deny-only).
        for cmd, allow in (
            ("printf -v 'a[$(id)]' x", ["printf:*"]),
            ("test -v 'a[$(id)]'", ["test:*"]),
            ("[[ 'a[$(id)]' -eq 0 ]]", ["[[:*"]),
            ("read -a 'arr[$(id)]'", ["read:*"]),
            ("unset 'a[`id`]'", ["unset:*"]),
            ("wait -p 'a[$(id)]'", ["wait:*"]),
            ("FOO=1 printf -v 'a[$(id)]' x", ["printf:*"]),
            ("printf -v 'a[$(id)]' x", ["printf -v 'a[$(id)]' x"]),  # exact
        ):
            self.assertEqual(self.decide(cmd, allow=allow).behavior, "ask", cmd)
        from src.permissions.bash_suggestions import suggestions_for_bash_command
        self.assertEqual(suggestions_for_bash_command("printf -v 'a[$(id)]' x"), [])

    def test_name_eval_builtins_benign_uses_work(self) -> None:
        for cmd, allow in (
            ("printf '%s' hello", ["printf:*"]),
            ("printf -v myvar hello", ["printf:*"]),
            ("test -f foo.txt", ["test:*"]),
            ("read var", ["read:*"]),
        ):
            self.assertEqual(self.decide(cmd, allow=allow).behavior, "allow", cmd)

    def test_exact_allow_honored_for_parse_refusal_not_semantics(self) -> None:
        # Too-complex/substitution: an exact rule is honored (checkEarlyExitDeny
        # allows exact). Eval-like/subscript: NEVER honored (checkSemanticsDeny).
        self.assertEqual(
            self.decide("a=$(date); echo $a", allow=["a=$(date); echo $a"]).behavior,
            "allow",
        )
        self.assertEqual(
            self.decide('eval "ls"', allow=['eval "ls"']).behavior, "ask"
        )

    def test_proc_environ_exfil_blocked(self) -> None:
        # Reading /proc/*/environ leaks another process's env (secrets); TS
        # checkSemantics refuses it regardless of rules. Must ask in every
        # form — redirect, `..` traversal, backslash evasion, exact rule,
        # allow-all — and mint no suggestion.
        for cmd, allow in (
            ("cat /proc/self/environ", ["cat:*"]),
            ("cat /proc/1234/environ", ["cat:*"]),
            ("cat < /proc/self/environ", ["cat:*"]),
            ("cat /proc/self/../self/environ", ["cat:*"]),
            (r"cat /proc/self/\environ", ["cat:*"]),
            ("cat /proc/self/environ", ["cat /proc/self/environ"]),
            ("head /proc/self/environ", ["*"]),
        ):
            self.assertEqual(self.decide(cmd, allow=allow).behavior, "ask", cmd)
        from src.permissions.bash_suggestions import suggestions_for_bash_command
        self.assertEqual(
            suggestions_for_bash_command("cat /proc/self/environ"), []
        )

    def test_path_binary_named_eval_is_not_the_builtin(self) -> None:
        # ./eval is a user binary, not the shell builtin — normal flow (it
        # still prompts here because there is no rule and it's not read-only).
        decision = self.decide("./eval --version")
        self.assertEqual(decision.behavior, "ask")
        # ...but it is not the structural eval refusal: suggestions exist.
        self.assertTrue(getattr(decision, "suggestions", None))


class TestWriteCommandRuleGate(_Base):
    # A matched Bash write-command allow (``Bash(rm:*)`` / ``Bash(*)``) is
    # path-gated exactly as TS runs checkPathConstraints before the allow rule:
    # a DANGEROUS-removal target (`/`, `~`, direct child of `/`) or an
    # OUT-OF-WORKSPACE path can never auto-run — those still prompt.
    # DOCUMENTED DEVIATION (design-review sanctioned): an in-workspace,
    # non-critical target IS honored under an explicit grant.
    def test_rm_rule_blocked_on_dangerous_targets(self) -> None:
        for cmd in ("rm -rf ~", "rm -rf /", "rm -rf /etc"):
            decision = self.decide(cmd, allow=["rm:*"])
            self.assertEqual(decision.behavior, "ask", cmd)

    def test_rm_rule_blocked_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            decision = self.decide(f"rm -rf {other}/x", allow=["rm:*"])
            self.assertEqual(decision.behavior, "ask")

    def test_rm_rule_allowed_inside_workspace(self) -> None:
        # Documented deviation: explicit Bash(rm:*) fires for in-workspace,
        # non-critical targets.
        decision = self.decide("rm -rf build", allow=["rm:*"])
        self.assertEqual(decision.behavior, "allow")

    def test_mv_rule_blocked_on_exfil_target(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            decision = self.decide(
                f"mv secret.txt {other}/exfil", allow=["mv:*"]
            )
            self.assertEqual(decision.behavior, "ask")

    def test_mv_rule_allowed_inside_workspace(self) -> None:
        decision = self.decide("mv a.txt b.txt", allow=["mv:*"])
        self.assertEqual(decision.behavior, "allow")

    def test_exact_write_rule_also_path_gated(self) -> None:
        # Even an exact rule can't auto-run a write on a dangerous path (TS
        # honors exact allows AFTER checkPathConstraints).
        decision = self.decide("rm -rf /etc/foo", allow=["rm -rf /etc/foo"])
        self.assertEqual(decision.behavior, "ask")

    def test_bare_bash_grant_still_path_gates_writes(self) -> None:
        # Content-less Bash (allow-all) is path-gated for writes too (TS: Bash(*)
        # runs checkPathConstraints). In-workspace write runs; dangerous does not.
        self.assertEqual(self.decide("rm -rf build", allow=["*"]).behavior, "allow")
        self.assertEqual(self.decide("rm -rf ~", allow=["*"]).behavior, "ask")

    def test_bare_bash_compound_dangerous_leg_blocked(self) -> None:
        decision = self.decide("echo hi && rm -rf ~", allow=["*"])
        self.assertEqual(decision.behavior, "ask")

    def test_compound_rm_leg_blocked_on_dangerous_target(self) -> None:
        decision = self.decide("ls && rm -rf ~", allow=["ls:*", "rm:*"])
        self.assertEqual(decision.behavior, "ask")

    def test_write_with_unresolvable_target_fails_closed(self) -> None:
        # A write whose target is a runtime expansion ($VAR / ${VAR} / $() /
        # glob) can't be statically contained — it must fail closed, even under
        # an explicit grant and even in acceptEdits, in ALL forms.
        for cmd, allow, mode in (
            ("rm -rf $HOME", ["rm:*"], "default"),
            ("rm -rf ${HOME}", ["rm:*"], "default"),
            ("rm -rf $HOME", ["*"], "default"),
            ("rm -rf *", ["rm:*"], "default"),
            ("mv a ${OUT}", ["mv:*"], "default"),
            ("cp x $DEST", ["cp:*"], "default"),
            ("rm -rf ${HOME}", [], "acceptEdits"),
            ("rm -rf $HOME", [], "acceptEdits"),
        ):
            self.assertEqual(
                self.decide(cmd, allow=allow, mode=mode).behavior, "ask",
                f"{cmd} [{mode}]",
            )

    def test_output_redirect_outside_workspace_blocked(self) -> None:
        # An output redirect can write ANY command's stdout out of the
        # workspace; even a read-command grant (echo:*/cat:*) must not let it
        # escape (TS gates redirect targets).
        for cmd, allow in (
            ("echo x > /etc/y", ["echo:*"]),
            ("echo x >> /etc/passwd", ["echo:*"]),
            ("echo x > ../out", ["echo:*"]),
            ("echo x > $HOME/f", ["echo:*"]),
            ("cat f > /etc/y", ["cat:*"]),
            ("echo x > /etc/y", ["*"]),  # allow-all too
        ):
            self.assertEqual(self.decide(cmd, allow=allow).behavior, "ask", cmd)

    def test_in_workspace_redirect_and_dev_null_allowed(self) -> None:
        for cmd, allow in (
            ("echo x > local.txt", ["echo:*"]),
            ("echo x > sub/out.txt", ["echo:*"]),
            ("grep x f 2>/dev/null", ["grep:*"]),
            ("cat f > /dev/null", ["cat:*"]),
        ):
            self.assertEqual(self.decide(cmd, allow=allow).behavior, "allow", cmd)

    def test_amp_redirect_to_file_gated_but_fd_dup_allowed(self) -> None:
        # `>&file` redirects BOTH streams to a FILE (must gate out-of-roots);
        # `>&<digit>` / `>&-` / `2>&1` are fd dup/close (not a file write).
        for cmd in ("echo x >&/etc/y", "echo x >& /etc/y", "echo x >&../out"):
            self.assertEqual(self.decide(cmd, allow=["echo:*"]).behavior, "ask", cmd)
        for cmd in ("echo x >&2", "echo x >&-", "grep p f 2>&1", "echo x >&local.txt"):
            self.assertEqual(
                self.decide(cmd, allow=["echo:*", "grep:*"]).behavior, "allow", cmd
            )

    def test_non_write_commands_fire_normally(self) -> None:
        decision = self.decide("git push origin main", allow=["git push:*"])
        self.assertEqual(decision.behavior, "allow")


class TestGrantableEverydayTools(_Base):
    """The actual loosening: rules fire and suggestions exist for the tools
    that used to be un-grantable class asks."""

    def test_pytest_prompt_carries_prefix_suggestion(self) -> None:
        decision = self.decide("pytest -q")
        self.assertEqual(decision.behavior, "ask")
        suggestions = list(getattr(decision, "suggestions", None) or ())
        self.assertTrue(suggestions)
        rule = suggestions[0].rules[0]
        self.assertEqual(rule.rule_content, "pytest:*")

    def test_saved_rule_fires_for_dangerous_class(self) -> None:
        for cmd, rule in (
            ("git push origin main", "git push:*"),
            ("python x.py", "python:*"),
            ("npm run lint", "npm run:*"),
            ("pytest -k foo", "pytest:*"),
        ):
            decision = self.decide(cmd, allow=[rule])
            self.assertEqual(decision.behavior, "allow", cmd)

    def test_read_only_auto_allows_without_rule(self) -> None:
        for cmd in ("ls -la", "git status", "git diff", "pwd",
                    "git status && ls"):
            decision = self.decide(cmd)
            self.assertEqual(decision.behavior, "allow", cmd)

    def test_mixed_rule_plus_readonly_compound(self) -> None:
        decision = self.decide("pytest -q && git status", allow=["pytest:*"])
        self.assertEqual(decision.behavior, "allow")


if __name__ == "__main__":
    unittest.main()
