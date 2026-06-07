"""Bash call handler — executes whitelisted shell commands safely."""

from __future__ import annotations

import subprocess
import threading
from typing import Any


class BashCallError(Exception):
    """Raised when a bash command fails or times out."""

    pass


def execute_bash(command_template: str, params: dict[str, Any]) -> str:
    """Execute a bash command from a validated template.

    Args:
        command_template: A format-string command, e.g. ``"glab project view {project_id}"``.
        params: Mapping of placeholder names to resolved values.

    Returns:
        stdout from the subprocess.

    Raises:
        BashCallError: If the command exits non-zero or exceeds the timeout.
    """
    try:
        command = command_template.format(**params)
    except KeyError as exc:
        raise BashCallError(f"Missing parameter in template: {exc}") from exc
    except Exception as exc:
        raise BashCallError(f"Failed to format command template: {exc}") from exc

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise BashCallError(f"Command timed out after 30s: {command[:80]}") from exc
    except OSError as exc:
        raise BashCallError(f"Failed to execute: {exc}") from exc

    if result.returncode != 0:
        raise BashCallError(
            f"Command exited with {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )

    return result.stdout