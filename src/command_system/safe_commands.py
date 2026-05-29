"""
Remote / bridge safe-command filtering.

Python port of the REMOTE_SAFE_COMMANDS / BRIDGE_SAFE_COMMANDS / isBridgeSafeCommand
logic in typescript/src/commands.ts:643-712. Gates which slash commands may run in
--remote mode and over the Remote Control bridge (mobile/web), now load-bearing after
the CLI-parity transport port (src/transports/).

Membership is by command NAME (not object identity as in TS): a frozenset of names is
stable across registry instances and lets the policy name commands that are not ported
yet (the allowlist is a forward-looking policy, not a runtime object set).
"""

from __future__ import annotations

from .types import Command, CommandType

# Safe in --remote mode: only affect local TUI state; no fs/git/shell/IDE/MCP.
# TS commands.ts:643-662 (18 commands).
REMOTE_SAFE_COMMANDS: frozenset[str] = frozenset({
    "session", "exit", "clear", "help", "theme", "logo", "color", "vim",
    "cost", "usage", "copy", "btw", "feedback", "plan", "keybindings",
    "statusline", "stickers", "mobile",
})

# 'local' commands explicitly safe over the Remote Control bridge: produce text
# output that streams back to mobile/web, no terminal-only side effects.
# TS commands.ts:676-685 (6 commands). 'summary' is an ant-internal import in TS
# (kept here as a forward-looking policy name; not yet ported).
BRIDGE_SAFE_COMMANDS: frozenset[str] = frozenset({
    "compact", "clear", "cost", "summary", "release-notes", "files",
})


def is_bridge_safe_command(cmd: Command) -> bool:
    """
    Whether a slash command is safe to execute when its input arrived over the
    Remote Control bridge. Port of commands.ts:697-701.

    Rule: 'prompt' commands expand to text -> always safe; 'local' commands need an
    explicit opt-in via BRIDGE_SAFE_COMMANDS; any future interactive ('local-jsx')
    type renders UI and is always blocked. Python has no interactive type yet, so the
    branches reduce to PROMPT -> True, LOCAL -> allowlist.
    """
    if cmd.command_type == CommandType.PROMPT:
        return True  # prompt commands expand to text -> always safe
    if cmd.command_type == CommandType.LOCAL:
        return cmd.name in BRIDGE_SAFE_COMMANDS
    # Any future interactive ('local-jsx') type renders UI -> blocked by default.
    return False


def filter_commands_for_remote_mode(commands: list[Command]) -> list[Command]:
    """Keep only commands safe for --remote mode. Port of commands.ts:709-711.

    Expects the DEDUPED output of get_commands() (where builtins own their names),
    not a raw skill list. Membership is by NAME, so a user skill named e.g. 'clear'
    would pass this filter unless the aggregator's dedupe has already let the builtin
    claim that name first (the TS caller likewise feeds getCommands() output here).
    """
    return [cmd for cmd in commands if cmd.name in REMOTE_SAFE_COMMANDS]
