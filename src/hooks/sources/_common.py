"""Shared helpers used by all per-source loaders.

Loads a settings.json-shaped file, applies the legacy ``Notification +
matcher`` back-compat translation (Phase-1 / WI-1.1), and tags every parsed
``HookConfig`` with the source enum value the caller specifies.

Returns ``dict[event, list[HookConfig]]`` so the ``HookConfigManager`` can
merge per-event lists across sources cheaply.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..hook_types import HookConfig, HookSource

logger = logging.getLogger(__name__)


def parse_hooks_file(
    path: Path,
    *,
    source: HookSource,
) -> dict[str, list[HookConfig]]:
    """Read one JSON settings file and return its hooks tagged with ``source``.

    Returns ``{}`` (empty dict, not None) if the file doesn't exist or fails
    to parse — fail-soft so a missing tier doesn't break startup.

    Reuses the back-compat translation in
    ``config_manager.load_hooks_from_settings`` for the legacy
    ``Notification + matcher: "onSessionStart"`` form.
    """
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read hooks settings from %s: %s", path, exc)
        return {}

    hooks_raw = data.get("hooks", {})
    if not isinstance(hooks_raw, dict):
        return {}

    # Reuse the existing parse + back-compat path for consistency. Loading
    # via ``load_hooks_from_settings`` rebuilds the snapshot wrapper; we
    # only need the events dict, so we replicate the parse loop here.
    from ..config_manager import _parse_hook_config, _translate_legacy_notification_entry

    hooks: dict[str, list[HookConfig]] = {}
    for event_name, hook_list in hooks_raw.items():
        if not isinstance(hook_list, list):
            continue
        for hook_raw in hook_list:
            if not isinstance(hook_raw, dict):
                continue
            target_event = event_name
            if event_name == "Notification":
                translated = _translate_legacy_notification_entry(hook_raw)
                if translated is not None:
                    target_event = translated
            hooks.setdefault(target_event, []).append(
                _parse_hook_config(hook_raw, source=source)
            )
    return hooks
