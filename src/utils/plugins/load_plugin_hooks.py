"""Plugin-tier hook loader.

Mirrors TS ``typescript/src/utils/plugins/loadPluginHooks.ts``. Per assumption
A2 (plan §19), plugins ship a separate ``hooks.json`` per plugin directory
rather than burying hooks under the manifest's ``hooks`` field. Cleaner
separation; easier plugin-manifest evolution.

Discovery:
    1. ``CLAUDE_PLUGINS_ROOT`` env var (test fixtures + admin override).
    2. ``~/.claude/plugins/`` (legacy default).

Each plugin is a directory under the plugins root. We look for
``<plugin>/hooks.json``; missing files are silently skipped (a plugin
needn't ship hooks).

The loaded ``HookConfig`` objects carry:
  * ``source = HookSource.PLUGIN_HOOK`` (priority 999, sorts last).
  * ``skill_root = <plugin_dir>`` so subprocess env injection (WI-1.5)
    populates ``CLAUDE_PLUGIN_ROOT`` correctly when the plugin's hook fires.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.sources._common import parse_hooks_file

logger = logging.getLogger(__name__)


def get_plugins_root() -> Path | None:
    env_override = os.environ.get("CLAUDE_PLUGINS_ROOT")
    if env_override:
        p = Path(env_override).expanduser().resolve()
        return p if p.exists() else None
    default = Path.home() / ".claude" / "plugins"
    return default if default.exists() else None


def load_plugin_hooks(
    plugins_root: Path | None = None,
) -> dict[str, list[HookConfig]]:
    root = plugins_root if plugins_root is not None else get_plugins_root()
    if root is None or not root.exists():
        return {}

    merged: dict[str, list[HookConfig]] = {}
    for plugin_dir in sorted(root.iterdir()):
        if not plugin_dir.is_dir():
            continue
        hooks_path = plugin_dir / "hooks.json"
        if not hooks_path.exists():
            continue

        plugin_hooks = parse_hooks_file(hooks_path, source=HookSource.PLUGIN_HOOK)
        # Stamp ``skill_root`` so CLAUDE_PLUGIN_ROOT is set when a plugin
        # hook fires (WI-1.5).
        for event_hooks in plugin_hooks.values():
            for hook in event_hooks:
                hook.skill_root = str(plugin_dir)

        for event, hooks in plugin_hooks.items():
            merged.setdefault(event, []).extend(hooks)

    return merged
