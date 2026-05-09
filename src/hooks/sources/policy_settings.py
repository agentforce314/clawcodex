"""Policy-tier settings loader: enterprise-managed.

Cannot be overridden by user/project/local hooks. Enforces ``disableAllHooks``
and ``allowManagedHooksOnly`` cascades (handled in ``src/hooks/policy.py``).

Path resolution (in order):
    1. ``CLAUDE_POLICY_DIR`` env var (test fixtures + admin override).
    2. Platform default:
         macOS    → /Library/Application Support/com.anthropic.claude
         Windows  → %ALLUSERSPROFILE%\\AnthropicClaude
         Linux    → /etc/anthropic/claude

Returns both:
  * the parsed hooks (typed under ``HookSource.POLICY_SETTINGS``)
  * the raw policy config dict — needed by the cascade gate to read
    ``disableAllHooks`` / ``allowManagedHooksOnly`` flags. The flags live
    at the *top level* of the policy settings file, not under the ``hooks``
    key, so they need to flow back to the manager separately.
"""

from __future__ import annotations

import json
import logging
import os
import platform
from pathlib import Path
from typing import Any

from ..hook_types import HookConfig, HookSource
from ._common import parse_hooks_file

logger = logging.getLogger(__name__)


def get_policy_settings_path() -> Path:
    env_override = os.environ.get("CLAUDE_POLICY_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve() / "settings.json"
    system = platform.system()
    if system == "Darwin":
        return Path("/Library/Application Support/com.anthropic.claude/settings.json")
    if system == "Windows":
        prog_data = os.environ.get("ALLUSERSPROFILE", r"C:\ProgramData")
        return Path(prog_data) / "AnthropicClaude" / "settings.json"
    # Default to Linux/Unix layout.
    return Path("/etc/anthropic/claude/settings.json")


def load_policy_hooks(path: Path | None = None) -> dict[str, list[HookConfig]]:
    return parse_hooks_file(
        path if path is not None else get_policy_settings_path(),
        source=HookSource.POLICY_SETTINGS,
    )


def load_policy_config(path: Path | None = None) -> dict[str, Any]:
    """Read the full policy settings JSON object (not just hooks).

    Returns the *top-level* dict so callers can read ``disableAllHooks`` and
    ``allowManagedHooksOnly`` flags. Returns ``{}`` on any failure (missing
    file, bad JSON) — fail-soft so a missing policy file doesn't break
    startup.
    """
    p = path if path is not None else get_policy_settings_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read policy settings from %s: %s", p, exc)
        return {}
    return data if isinstance(data, dict) else {}
