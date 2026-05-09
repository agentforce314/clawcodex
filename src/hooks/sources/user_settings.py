"""User-tier settings loader: ``~/.claude/settings.json``.

The chapter's "highest priority" tier — personal config that follows the
user across projects. Mirrors TS ``hooksSettings.ts`` user-settings path.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..hook_types import HookConfig, HookSource
from ._common import parse_hooks_file


def get_user_settings_path() -> Path:
    """Return the canonical user-settings path.

    Honors ``CLAUDE_CONFIG_DIR`` env-var override (test fixtures use this);
    falls back to ``~/.claude/settings.json``. Mirrors the legacy
    ``_get_settings_path`` in ``config_manager.py``.
    """
    env_override = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve() / "settings.json"
    return Path.home() / ".claude" / "settings.json"


def load_user_hooks(path: Path | None = None) -> dict[str, list[HookConfig]]:
    return parse_hooks_file(
        path if path is not None else get_user_settings_path(),
        source=HookSource.USER_SETTINGS,
    )
