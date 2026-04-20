from __future__ import annotations

from enum import Enum
from typing import Literal


class CommandSafety(Enum):
    SAFE = "safe"
    READ_ONLY = "read_only"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    DANGEROUS = "dangerous"
    UNKNOWN = "unknown"


CommandSafetyLevel = Literal["safe", "read_only", "write", "destructive", "dangerous", "unknown"]

SAFE_COMMANDS: frozenset[str] = frozenset({
    "echo", "printf", "true", "false", "test", "[", "[[",
    "pwd", "whoami", "date", "uname", "basename", "dirname",
    "seq", "yes", "sleep", "wait", "exit", "return",
    "export", "unset", "set", "alias", "unalias",
    "source", ".", "cd", "pushd", "popd", "dirs",
    "read", "local", "declare", "typeset", "readonly",
    "shift", "getopts", "let", "expr",
    "trap", "hash", "times", "builtin", "command",
    "type", "help", "compgen", "complete",
    "bg", "fg", "jobs", "disown", "suspend",
    "ulimit", "umask", "history", "fc",
    "bind", "enable", "shopt",
    "nproc", "arch", "lsb_release",
    "tput", "clear", "reset",
    "realpath", "readlink",
})

READ_ONLY_COMMANDS: frozenset[str] = frozenset({
    "cat", "head", "tail", "less", "more",
    "wc", "sort", "uniq", "diff", "comm", "cmp",
    "find", "locate", "which", "whereis", "whence",
    "ls", "tree", "file", "stat", "du", "df",
    "env", "printenv", "id", "groups", "hostname",
    "ps", "top", "htop", "uptime", "free", "vmstat",
    "lsof", "fuser", "pgrep",
    "grep", "egrep", "fgrep", "rg", "ag",
    "strings", "xxd", "od", "hexdump",
    "md5sum", "sha1sum", "sha256sum", "shasum",
    "tar", "gzip", "gunzip", "zcat", "bzip2", "bunzip2", "xz", "unxz",
    "zip", "unzip",
    "jq", "yq", "xmllint",
    "column", "cut", "paste", "join", "fmt", "fold", "nl", "expand", "unexpand",
    "tr", "rev", "tac",
    "git",
    "pip", "pip3", "npm", "yarn", "pnpm", "bun", "cargo", "go", "make",
    "docker", "kubectl",
    "man", "info", "apropos",
    "xargs",
})

WRITE_COMMANDS: frozenset[str] = frozenset({
    "cp", "mv", "mkdir", "touch", "tee",
    "sed", "awk", "gawk", "mawk", "nawk",
    "patch", "install",
    "ln", "mktemp", "mkfifo",
    "git-add", "git-commit", "git-checkout", "git-merge",
    "git-rebase", "git-stash", "git-branch", "git-tag",
    "git-reset", "git-revert", "git-cherry-pick",
    "git-init", "git-clone", "git-pull", "git-fetch",
    "pip-install", "npm-install", "yarn-add",
})

DESTRUCTIVE_COMMANDS: frozenset[str] = frozenset({
    "rm", "rmdir", "shred", "truncate",
    "git-clean",
})

DANGEROUS_COMMANDS: frozenset[str] = frozenset({
    "chmod", "chown", "chgrp",
    "sudo", "su", "doas",
    "dd", "mkfs", "fdisk", "parted",
    "mount", "umount",
    "kill", "killall", "pkill",
    "reboot", "shutdown", "halt", "poweroff",
    "iptables", "ip6tables", "nft",
    "systemctl", "service",
    "crontab", "at",
    "useradd", "userdel", "usermod", "groupadd", "groupdel",
    "passwd",
    "curl", "wget",
    "ssh", "scp", "rsync", "sftp",
    "nc", "ncat", "netcat", "socat",
    "git-push", "git-force-push",
    "eval", "exec",
    "python", "python3", "python2",
    "node", "deno", "tsx",
    "ruby", "perl", "php", "lua",
    "npx", "bunx",
    "bash", "sh", "zsh", "fish",
})

GIT_WRITE_SUBCOMMANDS: frozenset[str] = frozenset({
    "add", "commit", "checkout", "merge", "rebase",
    "stash", "branch", "tag", "reset", "revert",
    "cherry-pick", "init", "clone", "pull", "fetch",
    "switch", "restore", "bisect", "worktree",
    "submodule", "am", "apply", "format-patch",
    "mv", "rm",
})

GIT_DESTRUCTIVE_SUBCOMMANDS: frozenset[str] = frozenset({
    "clean",
})

GIT_DANGEROUS_SUBCOMMANDS: frozenset[str] = frozenset({
    "push", "force-push",
})

GIT_READ_SUBCOMMANDS: frozenset[str] = frozenset({
    "status", "log", "diff", "show", "blame",
    "shortlog", "describe", "rev-parse", "rev-list",
    "ls-files", "ls-tree", "ls-remote",
    "remote", "config", "help", "version",
    "grep", "reflog", "stash-list", "tag-list",
    "branch-list", "whatchanged", "name-rev",
    "cherry", "count-objects", "fsck",
    "verify-commit", "verify-tag",
})

NPM_SAFE_SUBCOMMANDS: frozenset[str] = frozenset({
    "list", "ls", "info", "view", "search", "help",
    "version", "outdated", "audit", "doctor", "explain",
    "why", "fund", "pack", "config", "get", "whoami",
    "bin", "prefix", "root",
})


def _classify_git(argv: list[str]) -> CommandSafety:
    if len(argv) < 2:
        return CommandSafety.READ_ONLY
    subcmd = argv[1].lstrip("-")
    if subcmd in GIT_READ_SUBCOMMANDS:
        return CommandSafety.READ_ONLY
    if subcmd in GIT_DANGEROUS_SUBCOMMANDS:
        return CommandSafety.DANGEROUS
    if subcmd in GIT_DESTRUCTIVE_SUBCOMMANDS:
        return CommandSafety.DESTRUCTIVE
    if subcmd in GIT_WRITE_SUBCOMMANDS:
        return CommandSafety.WRITE
    return CommandSafety.READ_ONLY


def _classify_npm_like(argv: list[str]) -> CommandSafety:
    if len(argv) < 2:
        return CommandSafety.READ_ONLY
    subcmd = argv[1].lstrip("-")
    if subcmd in NPM_SAFE_SUBCOMMANDS:
        return CommandSafety.READ_ONLY
    if subcmd in ("install", "ci", "add", "remove", "uninstall", "update", "upgrade"):
        return CommandSafety.WRITE
    if subcmd in ("run", "exec", "start", "test"):
        return CommandSafety.DANGEROUS
    if subcmd in ("publish", "unpublish", "deprecate"):
        return CommandSafety.DANGEROUS
    return CommandSafety.WRITE


def classify_command(argv: list[str]) -> CommandSafety:
    if not argv:
        return CommandSafety.SAFE
    cmd = argv[0]
    base = cmd.rsplit("/", 1)[-1]

    if base == "git":
        return _classify_git(argv)
    if base in ("npm", "yarn", "pnpm", "bun"):
        return _classify_npm_like(argv)
    if base == "sed" and any(a in ("-i", "--in-place") for a in argv[1:]):
        return CommandSafety.WRITE
    if base == "sed":
        return CommandSafety.READ_ONLY

    if base in SAFE_COMMANDS:
        return CommandSafety.SAFE
    if base in READ_ONLY_COMMANDS:
        return CommandSafety.READ_ONLY
    if base in WRITE_COMMANDS:
        return CommandSafety.WRITE
    if base in DESTRUCTIVE_COMMANDS:
        return CommandSafety.DESTRUCTIVE
    if base in DANGEROUS_COMMANDS:
        return CommandSafety.DANGEROUS
    return CommandSafety.UNKNOWN


def get_command_safety(command_name: str) -> CommandSafety:
    return classify_command([command_name])


def is_read_only_command(argv: list[str]) -> bool:
    safety = classify_command(argv)
    return safety in (CommandSafety.SAFE, CommandSafety.READ_ONLY)


def is_safe_command(argv: list[str]) -> bool:
    return classify_command(argv) == CommandSafety.SAFE
