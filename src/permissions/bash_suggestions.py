"""Heuristic "don't ask again" rule suggestions for Bash permission asks.

Port of the non-LLM derivation in typescript/src/tools/BashTool/
bashPermissions.ts (suggestionForExactCommand :245-273,
getSimpleCommandPrefix :140-168, extractPrefixBeforeHeredoc :285-316,
BARE_SHELL_PREFIXES :171-205, SAFE_ENV_VARS :357-410) and the shared
builders in utils/permissions/shellRuleMatching.ts (suggestionForPrefix
:211-227, suggestionForExactCommand :189-205 — both emit a single
``addRules``/``allow`` update destined for ``localSettings``).

Deliberate divergence: the TS Haiku-based prefix extractor
(utils/shell/prefix.ts ``getCommandSubcommandPrefix``) is NOT ported —
it is an LLM classifier call; C1 ships the deterministic heuristics only.
ANT_ONLY_SAFE_ENV_VARS is dropped (no ant user-type in Python).
"""

from __future__ import annotations

import re

from .types import (
    PermissionRuleValue,
    PermissionUpdate,
    PermissionUpdateAddRules,
)

BASH_TOOL_NAME = "Bash"

# TS bashPermissions.ts:93 — re.ASCII matches JS \w (ASCII-only).
_ENV_VAR_ASSIGN_RE = re.compile(r"^[A-Za-z_]\w*=", re.ASCII)

# TS bashPermissions.ts:165 — second token must look like a subcommand
# ("commit", "run"), not a flag (-rf), filename (a.txt), path, or number.
_SUBCOMMAND_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# TS bashPermissions.ts:171-205 — never suggest bare shells/wrappers as
# prefixes: `Bash(bash:*)` ≈ `Bash(*)` via `-c`; sudo/env/xargs likewise.
BARE_SHELL_PREFIXES = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "fish",
        "csh",
        "tcsh",
        "ksh",
        "dash",
        "cmd",
        "powershell",
        "pwsh",
        "env",
        "xargs",
        "nice",
        "stdbuf",
        "nohup",
        "timeout",
        "time",
        "sudo",
        "doas",
        "pkexec",
    }
)

# TS bashPermissions.ts:357-410 — env vars that CANNOT execute code or load
# libraries; safe to skip when extracting the command name. PATH/LD_*/
# PYTHONPATH/NODE_OPTIONS etc. must never be added here.
SAFE_ENV_VARS = frozenset(
    {
        "GOEXPERIMENT",
        "GOOS",
        "GOARCH",
        "CGO_ENABLED",
        "GO111MODULE",
        "RUST_BACKTRACE",
        "RUST_LOG",
        "NODE_ENV",
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "PYTEST_DEBUG",
        "ANTHROPIC_API_KEY",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "LC_TIME",
        "CHARSET",
        "TERM",
        "COLORTERM",
        "NO_COLOR",
        "FORCE_COLOR",
        "TZ",
        "LS_COLORS",
        "LSCOLORS",
        "GREP_COLOR",
        "GREP_COLORS",
        "GCC_COLORS",
        "TIME_STYLE",
        "BLOCK_SIZE",
        "BLOCKSIZE",
    }
)


def contains_unquoted_chaining(command: str) -> bool:
    """True when ``command`` chains multiple commands outside quotes.

    Detects ``&&``, ``||``, ``;``, ``|``, newlines, and lone ``&``
    (separator/background — both sides execute, same as ``;``) that are
    not inside single or double quotes. ``>&``, ``<&`` and ``&>`` are
    redirections, not chaining, and are skipped. Known non-goals,
    accepted because the per-sub-command safety screen rates every
    command before any allow rule can short-circuit: command
    substitution (``$(…)``/backticks) and ANSI-C quoting (``$'…'`` —
    an escaped quote inside it inverts this scanner's quote state).
    """

    in_single = False
    in_double = False
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if ch == "\\" and not in_single and i + 1 < n:
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch in (";", "|", "\n"):
                return True
            if ch == "&":
                if i + 1 < n and command[i + 1] == "&":
                    return True
                if i > 0 and command[i - 1] in "<>":
                    i += 1
                    continue  # 2>&1-style redirection
                if i + 1 < n and command[i + 1] == ">":
                    i += 2
                    continue  # &> redirection
                return True  # lone & — separator or backgrounding
        i += 1
    return False


def _skip_safe_env_assignments(tokens: list[str]) -> list[str] | None:
    """Drop leading VAR=value tokens; ``None`` if a non-safe var appears.

    Returning ``None`` (instead of skipping anyway) prevents suggesting
    prefix rules that can never match at allow-rule check time, because
    the rule matcher only strips SAFE env vars (TS :146-159 rationale).
    """

    i = 0
    while i < len(tokens) and _ENV_VAR_ASSIGN_RE.match(tokens[i]):
        var_name = tokens[i].split("=", 1)[0]
        if var_name not in SAFE_ENV_VARS:
            return None
        i += 1
    return tokens[i:]


def get_simple_command_prefix(command: str) -> str | None:
    """``'git commit -m "x"'`` → ``'git commit'``; None when no safe 2-word
    prefix exists (TS getSimpleCommandPrefix :140-168)."""

    tokens = [t for t in command.strip().split() if t]
    if not tokens:
        return None
    remaining = _skip_safe_env_assignments(tokens)
    if remaining is None or len(remaining) < 2:
        return None
    if not _SUBCOMMAND_RE.match(remaining[1]):
        return None
    return " ".join(remaining[:2])


# Commands safe to generalize to a first-word ``Bash(<cmd>:*)`` rule: each is
# read-only with respect to its OWN arguments — it cannot write a file, exec
# another program, or mutate system state via any flag/positional, regardless of
# arguments. This is the deterministic stand-in for TS's ``getFirstWordPrefix``
# (bashPermissions.ts:222), which TS surfaces as an *editable* dialog default the
# user can narrow; this port has no such field, so it must never auto-grant a
# generalization the user can't take back — hence the allowlist rather than
# "any first word".
#
# DELIBERATELY EXCLUDED (would be unsafe to generalize — and, crucially, a
# rule-matched ``Bash(<cmd>:*)`` allow is NOT re-screened by the bash safety
# check at match time, so the allowlist is the entire boundary): find / fd
# (``-exec`` / ``-delete`` / ``-x``), sort / uniq / tee / xxd / base64 / info
# (write via an output-file arg or flag — ``xxd in out``, ``base64 -o``,
# ``info --output``), cp / mv / dd / install / ln / truncate (write), command /
# env / xargs / shells (exec their args — also in BARE_SHELL_PREFIXES), sed / awk
# (``-i`` in-place / ``system()``), git / npm / make (mutating subcommands —
# read-only ``git`` subcommands still get the 2-word ``git status:*`` prefix),
# and ``date`` (``-s`` sets the clock).
#
# KNOWN LIMITATION (pre-existing for ALL Bash prefix rules — including the
# existing 2-word ``git status:*`` form — and shared with TS): an output
# redirect like ``ls > FILE`` is matched by ``Bash(ls:*)`` and can clobber FILE.
# Closing that needs a change to the rule MATCHER (refuse redirected commands),
# which applies to every prefix rule and is out of scope for this UX fix.
SAFE_PREFIX_COMMANDS: frozenset[str] = frozenset({
    # listing / navigation / fs metadata
    "ls", "pwd", "tree", "stat", "file", "basename", "dirname", "realpath",
    "readlink", "du", "df", "free",
    # file contents → stdout (no in-place / output-file arg). NB: xxd is
    # EXCLUDED — ``xxd in out`` / ``xxd -r in out`` writes the second positional.
    "cat", "head", "tail", "nl", "tac", "rev", "wc", "od", "hexdump",
    "strings",
    # text transforms: stdin/args → stdout (no output-file arg). NB: base64 is
    # EXCLUDED — BSD/macOS ``base64 -o out`` writes a file.
    "cut", "paste", "column", "fold", "expand", "unexpand", "fmt", "numfmt",
    "tr", "comm", "cmp", "diff", "jq",
    # read-only search (NO find/fd — they exec/delete)
    "grep", "egrep", "fgrep", "rg", "locate",
    # system / process / network info
    "whoami", "hostname", "uname", "arch", "id", "groups", "nproc", "uptime",
    "cal", "locale", "getconf", "printenv",
    "ps", "pgrep", "lsof", "netstat", "ss", "which", "type",
    # hashing (read → digest on stdout)
    "sha256sum", "sha1sum", "md5sum", "cksum",
    # pure / trivial. NB: info is EXCLUDED — GNU texinfo ``info --output=FILE``
    # writes a file; ``man`` has no such flag (and the pager shell-escape is
    # neutralized by the Bash tool's stdin=DEVNULL), so man stays.
    "echo", "printf", "seq", "expr", "test", "true", "false", "sleep",
    "man", "help", "tput",
})


def get_safe_first_word_prefix(command: str) -> str | None:
    """``'ls demos/'`` → ``'ls'``; ``None`` unless the first word is read-only
    w.r.t. its arguments (:data:`SAFE_PREFIX_COMMANDS`).

    Lets a single ``Bash(ls:*)`` grant cover ``ls`` of any path instead of a
    path-specific exact rule that re-prompts for every directory. The safe-set
    restriction is the security control — see the set's comment for why a bare
    ``getFirstWordPrefix`` port (TS) would be unsafe here.
    """
    tokens = [t for t in command.strip().split() if t]
    remaining = _skip_safe_env_assignments(tokens)
    if not remaining:
        return None
    cmd = remaining[0]
    # Same shape gate as the subcommand regex: reject paths (./x, /usr/bin/ls),
    # flags, and numbers — only a bare command name may generalize.
    if not _SUBCOMMAND_RE.match(cmd):
        return None
    if cmd not in SAFE_PREFIX_COMMANDS:
        return None
    return cmd


def _extract_prefix_before_heredoc(command: str) -> str | None:
    """Stable prefix before a ``<<`` heredoc (TS :285-316)."""

    idx = command.find("<<")
    if idx <= 0:
        return None
    before = command[:idx].strip()
    if not before:
        return None
    prefix = get_simple_command_prefix(before)
    if prefix:
        return prefix
    tokens = [t for t in before.split() if t]
    remaining = _skip_safe_env_assignments(tokens)
    if not remaining:
        return None
    # Deliberate divergence from TS (which has no guard here): a bare shell
    # before a heredoc (`bash <<EOF`) would yield Bash(bash:*) ≈ Bash(*).
    if remaining[0] in BARE_SHELL_PREFIXES:
        return None
    return " ".join(remaining[:2])


def suggestion_for_prefix(prefix: str) -> list[PermissionUpdate]:
    """``prefix`` → ``[addRules Bash(prefix:*) → localSettings]``
    (TS shellRuleMatching.ts:211-227)."""

    return [
        PermissionUpdateAddRules(
            destination="localSettings",
            behavior="allow",
            rules=(
                PermissionRuleValue(
                    tool_name=BASH_TOOL_NAME, rule_content=f"{prefix}:*"
                ),
            ),
        )
    ]


def suggestion_for_exact_command(command: str) -> list[PermissionUpdate]:
    """``command`` → ``[addRules Bash(command) → localSettings]``
    (TS shellRuleMatching.ts:189-205)."""

    return [
        PermissionUpdateAddRules(
            destination="localSettings",
            behavior="allow",
            rules=(
                PermissionRuleValue(
                    tool_name=BASH_TOOL_NAME, rule_content=command
                ),
            ),
        )
    ]


def suggestions_for_bash_command(command: str) -> list[PermissionUpdate]:
    """Best "don't ask again" rule for ``command``
    (TS suggestionForExactCommand :245-273).

    Order: heredoc prefix → first line of a multiline command → 2-word
    prefix → exact command. Callers should pass commands that already
    failed allow-rule matching; dangerous-command asks should NOT call
    this (TS passes ``suggestions: []`` there).
    """

    command = command.strip()
    if not command:
        return []

    heredoc_idx = command.find("<<")
    if heredoc_idx != -1:
        # Heredoc: either a stable prefix before the operator, or nothing —
        # never fall through to the multiline first-line branch (that would
        # bake the heredoc operator into the rule, e.g. "bash <<EOF:*").
        if heredoc_idx == 0:
            return []
        before = command[:heredoc_idx]
        if contains_unquoted_chaining(before):
            return []
        heredoc_prefix = _extract_prefix_before_heredoc(command)
        if heredoc_prefix:
            return suggestion_for_prefix(heredoc_prefix)
        return []

    if "\n" in command:
        # TS takes the first line verbatim as a prefix rule. Two Python
        # adjustments (both consequences of the D3 whole-string matcher):
        # a compound first line would mint a rule the chaining guard can
        # never match (the user would be re-prompted forever), so derive
        # the first sub-command's 2-word prefix instead; and a bare-shell
        # first line is suppressed for the same reason as the heredoc D1
        # guard.
        first_line = command.split("\n", 1)[0].strip()
        if contains_unquoted_chaining(first_line):
            prefix = get_simple_command_prefix(first_line)
            return suggestion_for_prefix(prefix) if prefix else []
        tokens = first_line.split()
        if len(tokens) == 1 and tokens[0] in BARE_SHELL_PREFIXES:
            return []
        return suggestion_for_prefix(first_line)

    if contains_unquoted_chaining(command):
        # Single-line compound: the first sub-command's 2-word prefix (a
        # SUBSET of TS's per-sub-command suggestions) — safe because the
        # match-time guard refuses to auto-allow chained commands, so the
        # rule only ever skips prompts for SIMPLE commands. No derivable
        # prefix → no suggestion (an exact rule containing chaining would
        # be unmatchable: dead).
        prefix = get_simple_command_prefix(command)
        return suggestion_for_prefix(prefix) if prefix else []

    prefix = get_simple_command_prefix(command)
    if prefix:
        return suggestion_for_prefix(prefix)

    # No 2-word prefix (e.g. ``ls demos/`` — second token is a path, not a
    # subcommand). For a command that is read-only w.r.t. its arguments, offer a
    # reusable first-word prefix (``Bash(ls:*)``) so the grant covers any path,
    # instead of a path-specific exact rule that re-prompts every directory.
    first_word = get_safe_first_word_prefix(command)
    if first_word:
        return suggestion_for_prefix(first_word)

    return suggestion_for_exact_command(command)


__all__ = [
    "BARE_SHELL_PREFIXES",
    "BASH_TOOL_NAME",
    "SAFE_ENV_VARS",
    "SAFE_PREFIX_COMMANDS",
    "contains_unquoted_chaining",
    "get_safe_first_word_prefix",
    "get_simple_command_prefix",
    "suggestion_for_prefix",
    "suggestion_for_exact_command",
    "suggestions_for_bash_command",
]
