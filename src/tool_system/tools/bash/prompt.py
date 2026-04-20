"""Prompt generation and timeout configuration for the bash tool."""

from __future__ import annotations

import os

_DEFAULT_TIMEOUT_MS = 120_000  # 2 minutes
_MAX_TIMEOUT_MS = 600_000  # 10 minutes


def get_default_timeout_ms() -> int:
    raw = os.environ.get("BASH_DEFAULT_TIMEOUT_MS")
    if raw is not None:
        try:
            return max(1000, int(raw))
        except (ValueError, TypeError):
            pass
    return _DEFAULT_TIMEOUT_MS


def get_max_timeout_ms() -> int:
    raw = os.environ.get("BASH_MAX_TIMEOUT_MS")
    if raw is not None:
        try:
            return max(1000, int(raw))
        except (ValueError, TypeError):
            pass
    return _MAX_TIMEOUT_MS


def get_bash_prompt() -> str:
    max_ms = get_max_timeout_ms()
    default_ms = get_default_timeout_ms()
    max_min = max_ms / 60_000
    default_min = default_ms / 60_000

    tool_preferences = "\n".join([
        "- File search: Use Glob (NOT find or ls)",
        "- Content search: Use Grep (NOT grep or rg)",
        "- Read files: Use Read (NOT cat/head/tail)",
        "- Edit files: Use Edit (NOT sed/awk)",
        "- Write files: Use Write (NOT echo >/cat <<EOF)",
        "- Communication: Output text directly (NOT echo/printf)",
    ])

    multiple_commands = "\n".join([
        "- If the commands are independent and can run in parallel, make multiple "
        "Bash tool calls in a single message.",
        "- If the commands depend on each other and must run sequentially, use a "
        "single Bash call with '&&' to chain them together.",
        "- Use ';' only when you need to run commands sequentially but don't care "
        "if earlier commands fail.",
        "- DO NOT use newlines to separate commands (newlines are ok in quoted strings).",
    ])

    git_instructions = "\n".join([
        "- Prefer to create a new commit rather than amending an existing commit.",
        "- Before running destructive operations (e.g., git reset --hard, "
        "git push --force, git checkout --), consider whether there is a safer "
        "alternative that achieves the same goal.",
        "- Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, "
        "-c commit.gpgsign=false) unless the user has explicitly asked for it.",
    ])

    sleep_instructions = "\n".join([
        "- Do not sleep between commands that can run immediately -- just run them.",
        "- If your command is long running and you would like to be notified when "
        "it finishes -- use `run_in_background`. No sleep needed.",
        "- Do not retry failing commands in a sleep loop -- diagnose the root cause.",
        "- If you must poll an external process, use a check command (e.g. "
        "`gh run view`) rather than sleeping first.",
        "- If you must sleep, keep the duration short (1-5 seconds) to avoid "
        "blocking the user.",
    ])

    avoid_commands = "`find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo`"

    return "\n".join([
        "Executes a given bash command and returns its output.",
        "",
        "The working directory persists between commands, but shell state does not. "
        "The shell environment is initialized from the user's profile (bash or zsh).",
        "",
        f"IMPORTANT: Avoid using this tool to run {avoid_commands} commands, "
        "unless explicitly instructed or after you have verified that a dedicated "
        "tool cannot accomplish your task. Instead, use the appropriate dedicated "
        "tool as this will provide a much better experience for the user:",
        "",
        tool_preferences,
        "",
        "While the Bash tool can do similar things, it's better to use the built-in "
        "tools as they provide a better user experience and make it easier to review "
        "tool calls and give permission.",
        "",
        "# Instructions",
        "- If your command will create new directories or files, first use this tool "
        "to run `ls` to verify the parent directory exists and is the correct location.",
        "- Always quote file paths that contain spaces with double quotes in your "
        'command (e.g., cd "path with spaces/file.txt").',
        "- Try to maintain your current working directory throughout the session by "
        "using absolute paths and avoiding usage of `cd`. You may use `cd` if the "
        "User explicitly requests it.",
        f"- You may specify an optional timeout in milliseconds (up to "
        f"{max_ms}ms / {max_min:g} minutes). By default, your command will timeout "
        f"after {default_ms}ms ({default_min:g} minutes).",
        "- When issuing multiple commands:",
        multiple_commands,
        "- For git commands:",
        git_instructions,
        "- Avoid unnecessary `sleep` commands:",
        sleep_instructions,
    ])
