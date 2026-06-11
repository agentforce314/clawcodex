"""Custom status-line command runner (components C3a).

Port of TS ``executeStatusLineCommand`` (utils/hooks.ts:4819-4870) + the
input payload built in ``components/StatusLine.tsx:60-126``: when settings
carry ``statusLine: {"type": "command", "command": "..."}``, run the
command with a JSON status payload on stdin (5s timeout) and display its
output in the status area. Non-zero exit, timeout, or a missing config →
no text (TS returns undefined). Deliberate adaptation: TS joins ALL
non-empty stdout lines (hooks.ts:4878-4890, multi-line bar); Python's
status row is a single line, so only the FIRST stdout line is shown.

Settings layer: the ``statusLine`` key lives in the C1 standalone
settings files (``settings_paths``: user ``~/.clawcodex/settings.json``,
project/local ``<cwd>/.clawcodex/settings{,.local}.json``) — the same
files that hold ``permissions``, mirroring TS settings.json. Merge order
user < project < local (last wins). The TS managed-settings/trust gates
have no Python analog yet (no managed hooks, no trust store) — noted
divergence; the C8 trust work revisits.

Degraded input payload: only the fields Python can truthfully populate
(model, workspace, version, context_window, cost.total_cost_usd, vim) —
absent TS fields (output_style, rate_limits, agent, remote, worktree) are
omitted rather than faked.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

STATUS_LINE_TIMEOUT_SECONDS = 5.0  # TS hooks.ts:4822 timeoutMs=5000


def read_status_line_config(cwd: str | None = None) -> dict[str, Any] | None:
    """Return the merged ``statusLine`` settings entry, or ``None``.

    Only ``{"type": "command", "command": str}`` entries are actionable
    (TS: ``statusLine.type !== 'command'`` → undefined).
    """

    from src.permissions.settings_paths import (
        local_settings_path,
        project_settings_path,
        user_settings_path,
    )

    merged: dict[str, Any] | None = None
    for path in (
        user_settings_path(),
        project_settings_path(cwd),
        local_settings_path(cwd),
    ):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            # ValueError covers JSONDecodeError AND UnicodeDecodeError —
            # a mis-encoded settings file must not escape the reader.
            continue
        entry = data.get("statusLine")
        if isinstance(entry, dict):
            merged = entry
    return merged


def build_status_line_input(
    *,
    model_id: str = "",
    cwd: str = "",
    session_id: str = "",
    total_input_tokens: int = 0,
    total_output_tokens: int = 0,
    last_turn_input_tokens: int = 0,
    context_window_size: int = 0,
    total_cost_usd: float | None = None,
    vim_mode: str | None = None,
    version: str = "",
) -> dict[str, Any]:
    """The JSON payload piped to the command (StatusLine.tsx:60-126 subset)."""

    # TS shapes (third-party scripts are written against the real
    # product): current_usage is the USAGE OBJECT or null
    # (StatusLine.tsx:47,93 + utils/context.ts:162-188), and the
    # percentages are rounded-half-up, clamped INTS (context.ts:179-183).
    used_pct = 0
    if context_window_size > 0:
        used_pct = int(
            min(
                100.0,
                max(
                    0.0,
                    (last_turn_input_tokens / context_window_size) * 100.0,
                ),
            )
            + 0.5
        )
        used_pct = min(100, used_pct)
    current_usage: dict[str, Any] | None = None
    if last_turn_input_tokens:
        current_usage = {
            "input_tokens": last_turn_input_tokens,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    payload: dict[str, Any] = {
        "hook_event_name": "StatusLine",
        "session_id": session_id,
        "model": {"id": model_id, "display_name": model_id},
        "workspace": {"current_dir": cwd, "project_dir": cwd},
        "version": version,
        "cost": {"total_cost_usd": total_cost_usd},
        "context_window": {
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "context_window_size": context_window_size,
            "current_usage": current_usage,
            "used_percentage": used_pct,
            "remaining_percentage": max(0, 100 - used_pct),
        },
    }
    if vim_mode:
        payload["vim"] = {"mode": vim_mode}
    return payload


def execute_status_line_command(
    status_input: dict[str, Any],
    *,
    cwd: str | None = None,
    timeout: float = STATUS_LINE_TIMEOUT_SECONDS,
    config: dict[str, Any] | None = None,
) -> str | None:
    """Run the configured command; return its first stdout line or None.

    ``config`` overrides the settings read (tests). User-configured
    command, user's own machine — executed via the shell like every other
    hook runner; failures are logged at debug and swallowed (a broken
    statusline must never break the session).
    """

    entry = config if config is not None else read_status_line_config(cwd)
    if not isinstance(entry, dict) or entry.get("type") != "command":
        return None
    command = entry.get("command")
    if not isinstance(command, str) or not command.strip():
        return None

    try:
        proc = subprocess.run(
            command,
            shell=True,
            input=json.dumps(status_input),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd or None,
        )
    except subprocess.TimeoutExpired:
        logger.debug("statusLine command timed out after %ss", timeout)
        return None
    except OSError as exc:
        logger.debug("statusLine command failed to start: %s", exc)
        return None
    if proc.returncode != 0:
        logger.debug("statusLine command exited %s", proc.returncode)
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    return out.splitlines()[0]


__all__ = [
    "STATUS_LINE_TIMEOUT_SECONDS",
    "build_status_line_input",
    "execute_status_line_command",
    "read_status_line_config",
]
