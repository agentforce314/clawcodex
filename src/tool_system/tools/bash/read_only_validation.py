"""Lightweight read-only check for bash commands.

Splits on pipes and checks each sub-command's leading token against
a known allowlist.  Rejects shell meta-characters that could bypass
the simple token check (back-ticks, $(), etc.).

For ``git``, also validates the subcommand against a read-only allowlist.
Without this, ``git push`` / ``git commit`` / ``git reset`` would
classify as concurrency-safe and could race on ``.git/index.lock``
when emitted in the same model turn.
"""

from __future__ import annotations

import re
import shlex

READONLY_COMMANDS: frozenset[str] = frozenset([
    "cat", "head", "tail", "wc", "stat", "strings", "hexdump", "od", "nl",
    "ls", "tree", "du", "exa", "eza",
    "grep", "rg", "find", "fd", "fdfind", "ag", "ack", "locate",
    "diff", "comm", "cmp",
    "id", "uname", "free", "df", "locale", "groups", "nproc",
    "basename", "dirname", "realpath", "readlink",
    "cal", "uptime", "date",
    "cut", "paste", "tr", "column", "tac", "rev", "fold",
    "expand", "unexpand", "fmt", "numfmt", "sort", "uniq",
    "pwd", "whoami", "which", "type", "file",
    "git", "true", "false", "sleep", "echo", "printf",
    "expr", "test", "getconf", "seq", "jq",
    "ps", "pgrep", "lsof", "netstat", "ss",
    "sha256sum", "sha1sum", "md5sum",
    "base64", "man", "info", "help",
    "hostname", "tput",
])

# Subcommands of ``git`` that don't mutate the working tree, index, or
# refs. Anything not on this allowlist is treated as not read-only,
# including stash/worktree (their ``list`` form is read but
# sub-subcommand parsing isn't worth the complexity here — the
# conservative classification is a parallelism loss, not a correctness
# loss).
GIT_READ_SUBCOMMANDS: frozenset[str] = frozenset([
    "status", "diff", "log", "show", "blame", "branch", "tag",
    "describe", "rev-parse", "rev-list", "ls-files", "ls-tree",
    "cat-file", "config", "remote", "shortlog", "name-rev", "reflog",
    "whatchanged",
])

_SHELL_METACHARS = re.compile(r"[;&|`$(){}><]")


def is_command_read_only(command: str) -> bool:
    """Return True when *command* is a pipeline of known read-only binaries."""
    stripped = command.strip()
    if not stripped:
        return False

    for sub in re.split(r"\s*\|\s*", stripped):
        sub = sub.strip()
        if not sub:
            continue
        if _SHELL_METACHARS.search(sub):
            return False
        try:
            tokens = shlex.split(sub, posix=True)
        except ValueError:
            return False
        if not tokens:
            return False
        base = tokens[0].split("/")[-1]
        if base not in READONLY_COMMANDS:
            return False
        if base == "git":
            # `git` alone is help/usage — treat as not safe (ambiguous).
            if len(tokens) < 2:
                return False
            git_subcmd = tokens[1].lower()
            if git_subcmd not in GIT_READ_SUBCOMMANDS:
                return False
    return True
