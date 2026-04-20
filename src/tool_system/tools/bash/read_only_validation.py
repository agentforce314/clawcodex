"""Lightweight read-only check for bash commands.

Splits on pipes and checks each sub-command's leading token against
a known allowlist.  Rejects shell meta-characters that could bypass
the simple token check (back-ticks, $(), etc.).
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
    return True
