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


# TS parity (bashPermissions.ts:98-103 MAX_SUBCOMMANDS_FOR_SECURITY_CHECK):
# above this we refuse to split — per-sub work must stay bounded, and "refuse"
# degrades to today's ask-every-time, never to a wider allow.
MAX_SUBCOMMANDS: int = 50

# GH#11380 parity (bashPermissions.ts:105-110): cap the per-subcommand rules
# suggested for one compound prompt; beyond this the label degrades and saving
# 10+ rules from a single prompt is more likely noise than intent.
MAX_SUGGESTED_RULES_FOR_COMPOUND: int = 5


def contains_executable_substitution(command: str) -> bool:
    """True if ``command`` contains command substitution that EXECUTES —
    ``$(…)``, backticks, or bash-5.3 value substitution ``${ …}`` / ``${| …}`` —
    anywhere NOT inside single quotes (single quotes make them literal; a
    backslash-escaped ``\\$``/``\\```` in double quotes is literal too).

    The safety analyzer (``analyze_bash_command``) tokenizes ``$(…)`` into an
    opaque placeholder and never rates the inner command, and the content
    matcher compares raw substrings — so ``echo "$(rm -rf /)"`` with an
    ``echo:*`` allow rule would run the ``rm`` unseen. Callers use this to
    REFUSE to auto-allow such a command (it prompts instead), converting a
    silent over-allow into a review. It does NOT make a ``rm:*`` deny fire on
    the hidden command — that needs the analyzer to recurse (a larger change).
    """

    in_single = False
    in_double = False
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if ch == "\\" and not in_single:
            i += 2  # escaped char (incl. \$ / \` in double quotes) — literal
            continue
        if ch == "'" and not in_double:
            # `$'…'` ANSI-C quoting desyncs this scanner (escaped quotes inside)
            # — refuse defensively rather than risk missing a trailing $().
            if not in_single and i > 0 and command[i - 1] == "$":
                return True
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single:
            if ch == "`":
                return True
            if ch == "$" and i + 1 < n and command[i + 1] == "{":
                after = command[i + 2] if i + 2 < n else ""
                if after == "" or after.isspace() or after == "|":
                    return True
            if ch == "$" and i + 1 < n and command[i + 1] == "(":
                if i + 2 < n and command[i + 2] == "(":
                    i += 3  # `$((` arithmetic — evaluates, runs no command
                    continue
                return True  # `$(` command substitution
            # A bare `(` outside quotes is a subshell / group / the paren of
            # process substitution `<(`/`>(` — all execute. (Inside double
            # quotes `(` is literal; `$(`'s own paren is handled above.) This
            # is what catches `\$(rm)` — the `\$` is literal but `(rm)` is a
            # subshell — plus `cat <(rm)` / `tee >(rm) f`.
            if not in_double and ch == "(":
                return True
        i += 1
    return False


def split_chained_command(command: str) -> list[str] | None:
    """Split ``command`` on unquoted chaining operators (``&&``, ``||``,
    ``;``, ``|``, ``|&``, ``&``, newline) into its sub-commands.

    Port of the ESSENTIAL semantics of TS ``splitCommand``
    (utils/bash/commands.ts:85-265) for the permission layer: the original is
    a shell-quote-based module hardened against heredoc/continuation/comment
    parser differentials; this port instead REFUSES (returns ``None``) on any
    construct it cannot split with certainty:

    - command substitution (``$(`` or backticks) and ANSI-C quoting (``$'`` —
      an escaped quote inside it inverts a naive quote scanner, the same blind
      spot :func:`contains_unquoted_chaining` documents),
    - process substitution / subshells / grouping (any unquoted paren),
    - heredocs (``<<``) and backslash-newline continuations (TS commands.ts:96
      documents the odd/even-backslash join attack),
    - unterminated quotes, or more than :data:`MAX_SUBCOMMANDS` pieces.

    Refusal is always SAFE: callers fall back to today's behavior (the
    whole-command matcher refuses chained commands → prompt). The scanner is
    the same traversal as :func:`contains_unquoted_chaining` so the two agree
    on what counts as chaining; redirections (``2>&1``, ``&>``, ``>``) are NOT
    operators and stay inside their sub-command's text (the port's matcher
    treats redirects as plain arguments — the documented pre-existing
    limitation shared with simple commands; splitting does not widen it).
    """

    in_single = False
    in_double = False
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(command)

    def _flush() -> None:
        piece = "".join(buf).strip()
        if piece:
            parts.append(piece)
        buf.clear()

    while i < n:
        ch = command[i]
        if ch == "\\" and not in_single:
            if i + 1 < n and command[i + 1] == "\n":
                return None  # continuation-join attack surface (TS commands.ts:96)
            # Escaped char (outside single quotes): literal, never an operator —
            # and, in double quotes, `\$`/`\`` are literal (no substitution),
            # so consuming the pair here also stops the substitution check below
            # from firing on an escaped dollar/backtick.
            buf.append(command[i : i + 2])
            i += 2
            continue
        # Command substitution EXECUTES inside double quotes too (only single
        # quotes make it literal), so refuse `$(`/backtick whenever NOT inside
        # single quotes — else `echo "$(rm -rf /)" | cat` would split into
        # [echo "$(rm -rf /)", cat] and get auto-allowed by echo:*/cat:* while
        # bash runs the rm. This is the per-sub-command injection guard TS gets
        # from bashCommandIsSafeAsync (bashPermissions.ts:2360-2380).
        if not in_single:
            if ch == "`":
                return None
            if ch == "$" and i + 1 < n and command[i + 1] == "(":
                return None
            # bash 5.3 value substitution `${ cmd; }` / `${| cmd; }` EXECUTES
            # cmd; plain `${VAR}` / `${VAR:-x}` does not. Distinguish by the
            # sigil right after `{` (whitespace or `|`).
            if ch == "$" and i + 1 < n and command[i + 1] == "{":
                after = command[i + 2] if i + 2 < n else ""
                if after == "" or after.isspace() or after == "|":
                    return None
        if ch == "'" and not in_double:
            # ANSI-C quoting $'…' has escape semantics this scanner does not
            # model — refuse rather than mis-split.
            if not in_single and i > 0 and command[i - 1] == "$":
                return None
            in_single = not in_single
            buf.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
        elif not in_single and not in_double:
            # ($(…) / backtick already refused above, in or out of double quotes)
            if ch in "()":
                return None  # subshell / grouping / process substitution
            if ch == "<" and i + 1 < n and command[i + 1] == "<":
                return None  # heredoc
            if ch in (";", "\n"):
                _flush()
            elif ch == "|":
                if i > 0 and command[i - 1] == ">":
                    buf.append(ch)  # `>|` force-clobber redirect, not a pipe
                else:
                    _flush()
                    if i + 1 < n and command[i + 1] in "|&":
                        i += 1  # `||` / `|&` are single operators
            elif ch == "&":
                if i + 1 < n and command[i + 1] == "&":
                    _flush()
                    i += 1
                elif i > 0 and command[i - 1] in "<>":
                    buf.append(ch)  # 2>&1-style redirection
                elif i + 1 < n and command[i + 1] == ">":
                    buf.append(ch)  # &> redirection
                else:
                    _flush()  # lone & — separator/background
            else:
                buf.append(ch)
        else:
            buf.append(ch)
        i += 1

    if in_single or in_double:
        return None  # unterminated quote — do not guess
    _flush()
    if not parts or len(parts) > MAX_SUBCOMMANDS:
        return None
    return parts


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


def _rule_value_for_subcommand(sub: str) -> PermissionRuleValue | None:
    """Best rule VALUE for one sub-command of a compound: 2-word prefix →
    safe first-word prefix → exact string (same ladder as the simple-command
    path below, but yielding a value so the caller can aggregate several into
    ONE ``addRules`` update — TS bashPermissions.ts:2487-2547 collectedRules).

    Bare shells yield nothing (D1 guard): an "exact" rule is exact-OR-word-
    prefix at match time (check.py — TS parity), so ``Bash(bash)`` would match
    ``bash anything`` ≈ ``Bash(*)``. The sub then simply never matches and the
    compound keeps prompting — conservative, mirroring the existing heredoc/
    multiline bare-shell suppression."""

    sub = sub.strip()
    if not sub:
        return None
    prefix = get_simple_command_prefix(sub) or get_safe_first_word_prefix(sub)
    if prefix:
        return PermissionRuleValue(
            tool_name=BASH_TOOL_NAME, rule_content=f"{prefix}:*"
        )
    tokens = [t for t in sub.split() if t]
    remaining = _skip_safe_env_assignments(tokens)
    first = (remaining or tokens)[0] if (remaining or tokens) else ""
    if first in BARE_SHELL_PREFIXES:
        return None
    return PermissionRuleValue(tool_name=BASH_TOOL_NAME, rule_content=sub)


def suggestions_for_compound_command(command: str) -> list[PermissionUpdate] | None:
    """Per-sub-command "don't ask again" rules for a compound command,
    aggregated into a SINGLE ``addRules`` update (TS builds exactly one:
    bashPermissions.ts:2540-2547), deduped in sub-command order and capped at
    :data:`MAX_SUGGESTED_RULES_FOR_COMPOUND` (GH#11380).

    ``None`` when the command cannot be split with certainty — callers fall
    back to the legacy single-suggestion ladder.
    """

    subs = split_chained_command(command)
    if subs is None:
        return None
    seen: dict[str, PermissionRuleValue] = {}
    for sub in subs:
        value = _rule_value_for_subcommand(sub)
        if value is None or value.rule_content in seen:
            continue
        seen[str(value.rule_content)] = value
    if not seen:
        return None
    rules = tuple(list(seen.values())[:MAX_SUGGESTED_RULES_FOR_COMPOUND])
    return [
        PermissionUpdateAddRules(
            destination="localSettings", behavior="allow", rules=rules
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

    if contains_unquoted_chaining(command):
        # Compound (incl. multiline): per-sub-command rules aggregated into
        # one addRules update (TS parity — bashPermissions.ts merge flow).
        # Accepting it lets the match-time per-sub check auto-allow the whole
        # pipeline next time (every sub must match). Splitter refusal falls
        # back to the legacy conservative ladders below.
        compound = suggestions_for_compound_command(command)
        if compound is not None:
            return compound
        if "\n" in command:
            # Legacy multiline fallback: first line as a prefix rule (TS took
            # the first line verbatim); compound/bare-shell first lines are
            # suppressed for the same reasons as the heredoc D1 guard.
            first_line = command.split("\n", 1)[0].strip()
            if contains_unquoted_chaining(first_line):
                prefix = get_simple_command_prefix(first_line)
                return suggestion_for_prefix(prefix) if prefix else []
            tokens = first_line.split()
            if len(tokens) == 1 and tokens[0] in BARE_SHELL_PREFIXES:
                return []
            return suggestion_for_prefix(first_line)
        # Legacy single-line-compound fallback: the first sub-command's
        # 2-word prefix; no derivable prefix → no suggestion (an exact rule
        # containing chaining would be unmatchable by the whole-command
        # matcher: dead).
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
    "MAX_SUBCOMMANDS",
    "MAX_SUGGESTED_RULES_FOR_COMPOUND",
    "SAFE_ENV_VARS",
    "SAFE_PREFIX_COMMANDS",
    "contains_executable_substitution",
    "contains_unquoted_chaining",
    "get_safe_first_word_prefix",
    "get_simple_command_prefix",
    "split_chained_command",
    "suggestion_for_prefix",
    "suggestion_for_exact_command",
    "suggestions_for_bash_command",
    "suggestions_for_compound_command",
]
