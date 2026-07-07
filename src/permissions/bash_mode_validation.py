"""acceptEdits-mode Bash auto-allow — port of
``typescript/src/tools/BashTool/modeValidation.ts`` plus the dangerous-removal
guard from ``typescript/src/tools/BashTool/pathValidation.ts:70-108`` /
``typescript/src/utils/permissions/pathValidation.ts:331-367``.

In the original, entering acceptEdits (shift+tab) doesn't just auto-accept the
file-edit TOOLS — filesystem-write shell commands (``mkdir touch rm rmdir mv
cp sed``) and redirect-free read-only commands are auto-allowed too, with
``rm``/``rmdir`` still gated on critical paths (``/``, ``~``, direct children
of ``/``). The port previously left every one of these prompting.

Port adaptation (documented, strictly narrower): TS validates write-command
paths through its ``checkPathConstraints`` engine *before* mode handling; this
port instead requires every path argument of an auto-allowed write command to
resolve inside the allowed roots (same containment substitute the read-only
gate uses) — outside → normal prompt flow.
"""

from __future__ import annotations

import os
import re
from typing import Sequence

from .bash_suggestions import (
    contains_executable_substitution,
    contains_unquoted_chaining,
    split_chained_command,
)
from .read_only_commands import (
    _contains_unquoted_operator,
    _extract_path_candidates,
    _tokenize_simple,
    check_read_only_constraints,
    contains_unquoted_expansion,
    is_command_read_only,
)
from .types import (
    PermissionAskDecision,
    SafetyCheckDecisionReason,
)

__all__ = [
    "ACCEPT_EDITS_READ_ONLY_COMMANDS",
    "ACCEPT_EDITS_WRITE_COMMANDS",
    "check_accept_edits_bash",
    "check_dangerous_removal_paths",
    "is_dangerous_removal_path",
    "rule_allow_path_gate",
]

# TS modeValidation.ts:11-19
ACCEPT_EDITS_WRITE_COMMANDS: frozenset[str] = frozenset(
    {"mkdir", "touch", "rm", "rmdir", "mv", "cp", "sed"}
)

# TS modeValidation.ts:22-37 — still must pass the read-only validator so
# redirects and mutating forms fall through to the normal prompt flow.
ACCEPT_EDITS_READ_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "grep", "cat", "ls", "find", "head", "tail", "echo",
        "pwd", "wc", "sort", "uniq", "diff",
    }
)


def is_dangerous_removal_path(resolved_path: str) -> bool:
    """Port of ``isDangerousRemovalPath`` (utils/permissions/pathValidation.ts:331).

    Critical targets an ``rm``/``rmdir`` must never touch without an explicit
    prompt: ``/``, anything ending in ``/*``, ``$HOME``, and direct children
    of ``/`` (``/usr``, ``/tmp``, ``/etc`` — but not ``/usr/local``).
    """
    forward = re.sub(r"[\\/]+", "/", resolved_path)
    if forward == "*" or forward.endswith("/*"):
        return True
    normalized = forward if forward == "/" else forward.rstrip("/") or "/"
    if normalized == "/":
        return True
    home = re.sub(r"[\\/]+", "/", os.path.expanduser("~")).rstrip("/")
    if normalized == home:
        return True
    if os.path.dirname(normalized) == "/":
        return True
    return False


def _extract_output_redirect_targets(command: str) -> list[str] | None:
    """Targets of unquoted output redirects (``>``, ``>>``, ``>|``, ``&>``,
    ``N>``) in ``command``. Returns ``None`` when the command can't be scanned
    with certainty (fail closed). ``2>&1``-style fd dups are not file writes
    and are skipped. Quote/escape-aware; the target token may be quoted."""
    targets: list[str] = []
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
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if not in_single and not in_double and ch == ">":
            # step past >, >>, >| and any leading fd digit already consumed by
            # the caller loop; find the following word (the target).
            j = i + 1
            while j < n and command[j] in ">|":
                j += 1
            while j < n and command[j] in " \t":
                j += 1
            # `>&WORD`: bash treats it as an fd DUP only when WORD is a number
            # or `-` (``2>&1``, ``>&-``); otherwise ``>&file`` redirects BOTH
            # stdout+stderr to that FILE (must be gated). So consume the `&`
            # and fall through to read the target word, unless a digit/`-`
            # follows (then it's a dup → skip).
            if j < n and command[j] == "&":
                k = j + 1
                while k < n and command[k] in " \t":
                    k += 1
                if k >= n or command[k].isdigit() or command[k] == "-":
                    # `>&1` / `>&-` fd dup/close — not a file write.
                    i = k + 1
                    continue
                j = k  # `>&file` — read `file` as the target below
            # read the target word (up to whitespace / operator / quote start)
            if j >= n:
                return None  # dangling redirect — cannot resolve
            tk_start = j
            buf = []
            while j < n:
                c = command[j]
                if c == "\\" and j + 1 < n:
                    buf.append(command[j + 1]); j += 2; continue
                if c == "'":
                    k = command.find("'", j + 1)
                    if k == -1:
                        return None
                    buf.append(command[j + 1:k]); j = k + 1; continue
                if c == '"':
                    k = command.find('"', j + 1)
                    if k == -1:
                        return None
                    buf.append(command[j + 1:k]); j = k + 1; continue
                if c in " \t\n;|&<>()":
                    break
                buf.append(c); j += 1
            target = "".join(buf)
            if not target or tk_start == j:
                return None
            targets.append(target)
            i = j
            continue
        i += 1
    return targets


# Redirecting to these device sinks is not a filesystem write we need to gate —
# `cmd 2>/dev/null` is a ubiquitous idiom. TS treats them as safe.
_SAFE_REDIRECT_TARGETS: frozenset[str] = frozenset({
    "/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty",
})


def _output_redirects_within_roots(
    command: str, cwd: str, allowed_roots: Sequence[str]
) -> bool:
    targets = _extract_output_redirect_targets(command)
    if targets is None:
        return False  # couldn't scan → fail closed
    targets = [t for t in targets if t not in _SAFE_REDIRECT_TARGETS]
    if not targets:
        return True
    # Any dynamic target (`$VAR`, glob) is unresolvable → fail closed.
    if any(("$" in t or "*" in t or "?" in t or "`" in t) for t in targets):
        return False
    resolved_roots = []
    for root in allowed_roots:
        try:
            resolved_roots.append(os.path.realpath(str(root)))
        except OSError:
            continue
    if not resolved_roots:
        return False
    for t in targets:
        try:
            rp = os.path.realpath(os.path.join(cwd, os.path.expanduser(t)))
        except OSError:
            return False
        if not any(rp == r or rp.startswith(r + os.sep) for r in resolved_roots):
            return False
    return True


def _has_unquoted_dollar(command: str) -> bool:
    """True if a ``$`` (any expansion form — ``$VAR``, ``${VAR}``, ``$(…)``)
    appears outside single quotes. ``contains_unquoted_expansion`` deliberately
    mirrors TS and only matches ``$`` + a name char, so it misses ``${VAR}``;
    a write TARGET that can't be statically resolved must fail closed, so here
    we reject ANY unquoted ``$`` (TS ``isCommandSafeViaFlagParsing`` refuses
    every token containing ``$`` for the same reason)."""
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
        elif ch == "$" and not in_single:
            return True
        i += 1
    return False


def _filter_out_flags(args: list[str]) -> list[str]:
    """Positional args with POSIX ``--`` handling (pathValidation.ts:126-139:
    ``rm -- -/../x`` must still have its path extracted and validated)."""
    out: list[str] = []
    after_double_dash = False
    for arg in args:
        if after_double_dash:
            out.append(arg)
        elif arg == "--":
            after_double_dash = True
        elif not arg.startswith("-"):
            out.append(arg)
    return out


def check_dangerous_removal_paths(
    command: str, args: list[str], cwd: str
) -> PermissionAskDecision | None:
    """Port of ``checkDangerousRemovalPaths`` (BashTool/pathValidation.ts:70).

    Returns an ask (empty suggestions — never encourage saving a dangerous
    command) when any target is critical, else ``None``.
    """
    for path in _filter_out_flags(args):
        clean = os.path.expanduser(path.strip("'\""))
        # NOTE: deliberately NOT resolving symlinks — /tmp must be caught even
        # though it symlinks to /private/tmp on macOS (TS comment, :81-83).
        absolute = clean if os.path.isabs(clean) else os.path.normpath(
            os.path.join(cwd, clean)
        )
        if is_dangerous_removal_path(absolute):
            return PermissionAskDecision(
                behavior="ask",
                message=(
                    f"Dangerous {command} operation detected: '{absolute}'\n\n"
                    "This command would remove a critical system directory. "
                    "This requires explicit approval and cannot be "
                    "auto-allowed by permission rules."
                ),
                decision_reason=SafetyCheckDecisionReason(
                    reason=f"Dangerous {command} operation on critical path: {absolute}",
                    classifier_approvable=False,
                ),
                suggestions=(),
            )
    return None


_REDIRECT_TOKEN = re.compile(r"(?:^|[^<>])(>>?|>\|)(?:[^&]|$)")


def _has_shell_redirection(command: str) -> bool:
    """Approximation of TS ``hasShellRedirection`` (modeValidation.ts:57-73):
    any unquoted output-redirect operator in the ORIGINAL input disqualifies
    the read-only auto-allow branch. Reuses the quote-aware operator scanner —
    ``<``/``>`` of any form count (TS lists ``> >> >| &> 1> 2>`` …; refusing
    input redirects too only re-prompts, never widens)."""
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
        elif not in_single and not in_double and ch in "<>":
            return True
        i += 1
    return False


def check_accept_edits_bash(
    command: str,
    *,
    cwd: str,
    allowed_roots: Sequence[str],
) -> PermissionAskDecision | bool:
    """acceptEdits-mode resolution for a Bash command.

    Returns ``True`` (auto-allow: every sub-command is an in-roots filesystem
    write command or passes the read-only validator), an ask decision (a
    dangerous ``rm``/``rmdir`` target — surfaced even in acceptEdits), or
    ``False`` (no mode-specific handling → normal flow).

    TS resolves per sub-command inside its per-sub pipeline; expressing it as
    "ALL subs must be auto-allowable" is the same acceptance set for the
    compound whole (any non-qualifying sub → False → the normal flow, which
    still ends in a prompt).
    """
    stripped = command.strip()
    if not stripped:
        return False
    if contains_executable_substitution(stripped):
        return False

    if contains_unquoted_chaining(stripped):
        subs = split_chained_command(stripped)
        if not subs:
            return False
    else:
        subs = [stripped]

    redirection_anywhere = _has_shell_redirection(stripped)

    for sub in subs:
        sub = sub.strip()
        if (
            _has_unquoted_dollar(sub)
            or contains_unquoted_expansion(sub)
            or _contains_unquoted_operator(sub)
        ):
            return False
        tokens = _tokenize_simple(sub)
        if not tokens:
            return False
        base = os.path.basename(tokens[0])

        if base in ACCEPT_EDITS_WRITE_COMMANDS:
            if base in ("rm", "rmdir"):
                dangerous = check_dangerous_removal_paths(base, tokens[1:], cwd)
                if dangerous is not None:
                    return dangerous
            # Containment substitute for TS checkPathConstraints: every path
            # argument must stay inside the allowed roots.
            if not _write_paths_within_roots(tokens, cwd, allowed_roots):
                return False
            continue

        if base in ACCEPT_EDITS_READ_ONLY_COMMANDS and not redirection_anywhere:
            if is_command_read_only(sub) and check_read_only_constraints(
                sub, cwd=cwd, allowed_roots=allowed_roots
            ):
                continue
            return False

        return False

    return True


def rule_allow_path_gate(
    command: str, *, cwd: str, allowed_roots: Sequence[str]
) -> bool:
    """May a matched Bash ALLOW rule actually fire for this (sub-)command?

    The original runs ``checkPathConstraints`` (step 3) BEFORE the allow rule
    (step 5) in ``bashToolCheckPermission`` (bashPermissions.ts:1089-1122), so
    a saved ``Bash(rm:*)`` can never reach an out-of-workspace or critical
    target — those still prompt. Scope: the filesystem-write path-command set
    (``mkdir touch rm rmdir mv cp sed``); every other command (reads, ``git``,
    ``pytest``, …) is un-gated.

    DOCUMENTED DEVIATION from TS (sanctioned in the design review): TS requires
    ``acceptEdits`` mode for these commands and never honors a ``Bash(rm:*)``
    content rule in default mode; this port instead honors an explicit
    ``Bash(rm:*)``-style grant for IN-WORKSPACE, non-critical targets (and for
    the skill ``allowed-tools`` contract, where a declared ``Bash(touch:*)``
    must run). It is NEVER looser than TS for the cases that matter: an
    out-of-roots path or a dangerous-removal target (``/``, ``~``, a direct
    child of ``/``) still fails the gate → prompt/deny.

    False = the rule must not fire → the command re-prompts / flows on.
    """
    tokens = _tokenize_simple(command.strip())
    if not tokens:
        return False  # unparseable — fail closed to the prompt
    # An output redirect (`>`/`>>`) can write ANY command's stdout to a path —
    # ``echo x > /etc/y`` under ``Bash(echo:*)`` would escape the workspace even
    # though ``echo`` is not a write command. TS gates redirect targets via
    # checkCommandOperatorPermissions; here, a redirect to an unresolvable or
    # out-of-roots target fails the gate for EVERY command (the read-only path
    # refuses redirects outright — this covers the rule-allow path). Bounded
    # stand-in for the full operator subsystem; only ever adds a prompt.
    if not _output_redirects_within_roots(command, cwd, allowed_roots):
        return False
    base = os.path.basename(tokens[0])
    if base not in ACCEPT_EDITS_WRITE_COMMANDS:
        return True
    # A write command whose target we cannot statically resolve must never
    # auto-run: an unquoted ``$VAR``/``${VAR}``/``$(…)`` or a glob (``rm -rf
    # $HOME``, ``rm -rf *``, ``rm -rf ${OUT}``) expands at runtime to something
    # the containment / dangerous-removal checks below never saw. Fail closed →
    # prompt. (The acceptEdits path applies the same guard per sub-command.)
    if (
        _has_unquoted_dollar(command)
        or contains_unquoted_expansion(command)
        or contains_executable_substitution(command)
    ):
        return False
    if base in ("rm", "rmdir"):
        if check_dangerous_removal_paths(base, tokens[1:], cwd) is not None:
            return False
    return _write_paths_within_roots(tokens, cwd, allowed_roots)


def _write_paths_within_roots(
    tokens: list[str], cwd: str, allowed_roots: Sequence[str]
) -> bool:
    resolved_roots = []
    for root in allowed_roots:
        try:
            resolved_roots.append(os.path.realpath(str(root)))
        except OSError:
            continue
    if not resolved_roots:
        return False

    for cand in _extract_path_candidates(tokens) + _filter_out_flags(tokens[1:]):
        try:
            rp = os.path.realpath(os.path.join(cwd, os.path.expanduser(cand)))
        except OSError:
            return False
        ok = any(rp == r or rp.startswith(r + os.sep) for r in resolved_roots)
        if not ok:
            return False
    return True
