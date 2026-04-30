"""Bundled ``/debug`` skill — minimal port of ``bundled/debug.ts``.

Reads the tail of a session debug log (last ~64 KB / 20 lines), surfaces
the log path, settings paths, and the user's issue description so the
model can diagnose. Scoped to ``Read``/``Grep``/``Glob`` tools.

The full TS skill calls ``enableDebugLogging()`` to opt-in to logging
mid-session. Python has no equivalent helper today, so the "just
enabled" call is a no-op (TODO when the Python runtime grows one). The
prompt still surfaces the resolved log path so the user can tail it
out-of-band.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from ..bundled_skills import BundledSkillDefinition, register_bundled_skill

logger = logging.getLogger(__name__)


_DEFAULT_DEBUG_LINES_READ = 20
_TAIL_READ_BYTES = 64 * 1024


def _get_debug_log_path() -> str:
    """Resolve the conventional session debug log path.

    Honors ``CLAUDE_CODE_DEBUG_LOG_PATH`` if set; otherwise falls back to
    ``$CLAUDE_CONFIG_DIR/debug.log`` (or ``~/.claude/debug.log``). The
    file may not exist — callers handle that case.
    """
    env = os.environ.get("CLAUDE_CODE_DEBUG_LOG_PATH")
    if env:
        return str(Path(env).expanduser())
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
    return str(base / "debug.log")


def _enable_debug_logging() -> bool:
    """Stub for TS ``enableDebugLogging``.

    Returns True if logging was already on, False otherwise. The Python
    runtime has no mid-session logging toggle yet — the function is a
    no-op that always reports "was already on" (so the prompt skips the
    "just enabled" hint). TODO: wire to a real logging toggle when one
    lands.
    """
    return True


def _format_file_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    n = float(num_bytes)
    for unit in units:
        if n < 1024 or unit == units[-1]:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{num_bytes} B"


def _read_log_tail(log_path: str) -> str:
    """Return a markdown-rendered tail block, or a graceful "no log" hint.

    Mirrors TS: stat → seek to ``size - TAIL_READ_BYTES`` → read → keep
    last ``DEFAULT_DEBUG_LINES_READ`` lines. Returns a short hint when
    the file is missing or unreadable.
    """
    try:
        p = Path(log_path)
        size = p.stat().st_size
        read_size = min(size, _TAIL_READ_BYTES)
        start_offset = max(0, size - read_size)
        with p.open("rb") as fh:
            fh.seek(start_offset)
            data = fh.read(read_size)
        text = data.decode("utf-8", errors="replace")
        tail_lines = text.split("\n")[-_DEFAULT_DEBUG_LINES_READ:]
        tail = "\n".join(tail_lines)
        return (
            f"Log size: {_format_file_size(size)}\n\n"
            f"### Last {_DEFAULT_DEBUG_LINES_READ} lines\n\n"
            f"```\n{tail}\n```"
        )
    except FileNotFoundError:
        return "No debug log exists yet — logging was just enabled."
    except OSError as exc:
        return (
            f"Failed to read last {_DEFAULT_DEBUG_LINES_READ} lines of "
            f"debug log: {exc}"
        )


def _settings_path_hint(scope: str) -> str:
    """Return a stable hint for the settings file at ``scope``.

    The Python codebase doesn't yet expose a unified
    ``getSettingsFilePathForSource`` equivalent; we render conventional
    paths so the user can locate them. Mirrors TS' three scopes.
    """
    if scope == "userSettings":
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        base = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
        return str(base / "settings.json")
    if scope == "projectSettings":
        return ".claude/settings.json"
    if scope == "localSettings":
        return ".claude/settings.local.json"
    return "(unknown scope)"


def _build_debug_prompt(args: str) -> str:
    was_already_logging = _enable_debug_logging()
    debug_log_path = _get_debug_log_path()
    log_info = _read_log_tail(debug_log_path)

    just_enabled_section = (
        ""
        if was_already_logging
        else (
            "\n## Debug Logging Just Enabled\n\n"
            "Debug logging was OFF for this session until now. Nothing prior to this /debug invocation was captured.\n\n"
            f"Tell the user that debug logging is now active at `{debug_log_path}`, ask them to reproduce the issue, then re-read the log. If they can't reproduce, they can also restart with `claude --debug` to capture logs from startup.\n"
        )
    )

    issue_description = args or (
        "The user did not describe a specific issue. Read the debug log and "
        "summarize any errors, warnings, or notable issues."
    )

    return (
        "# Debug Skill\n"
        "\n"
        "Help the user debug an issue they're encountering in this current Claude Code session.\n"
        f"{just_enabled_section}\n"
        "## Session Debug Log\n"
        "\n"
        f"The debug log for the current session is at: `{debug_log_path}`\n"
        "\n"
        f"{log_info}\n"
        "\n"
        "For additional context, grep for [ERROR] and [WARN] lines across the full file.\n"
        "\n"
        "## Issue Description\n"
        "\n"
        f"{issue_description}\n"
        "\n"
        "## Settings\n"
        "\n"
        f"Remember that settings are in:\n"
        f"* user - {_settings_path_hint('userSettings')}\n"
        f"* project - {_settings_path_hint('projectSettings')}\n"
        f"* local - {_settings_path_hint('localSettings')}\n"
        "\n"
        "## Instructions\n"
        "\n"
        "1. Review the user's issue description\n"
        f"2. The last {_DEFAULT_DEBUG_LINES_READ} lines show the debug file format. Look for [ERROR] and [WARN] entries, stack traces, and failure patterns across the file\n"
        "3. Consider launching the claude-code-guide subagent to understand the relevant Claude Code features\n"
        "4. Explain what you found in plain language\n"
        "5. Suggest concrete fixes or next steps\n"
    )


def register_debug_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            name="debug",
            description=(
                "Enable debug logging for this session and help diagnose issues"
            ),
            allowed_tools=["Read", "Grep", "Glob"],
            argument_hint="[issue description]",
            # disable_model_invocation: matches TS — user must opt in
            # via `/debug`; otherwise the description doesn't burn
            # context budget on every model turn.
            disable_model_invocation=True,
            user_invocable=True,
            get_prompt_for_command=_build_debug_prompt,
        )
    )
