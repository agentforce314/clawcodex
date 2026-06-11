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

    return suggestion_for_exact_command(command)


__all__ = [
    "BARE_SHELL_PREFIXES",
    "BASH_TOOL_NAME",
    "SAFE_ENV_VARS",
    "contains_unquoted_chaining",
    "get_simple_command_prefix",
    "suggestion_for_prefix",
    "suggestion_for_exact_command",
    "suggestions_for_bash_command",
]
