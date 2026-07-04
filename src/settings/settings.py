"""Settings loading, merging, and caching matching TypeScript settings/settings.ts."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any

from ..config import ConfigManager, _deep_merge
from .constants import DEFAULT_SETTINGS
from .types import SettingsSchema

logger = logging.getLogger(__name__)

_settings_cache: SettingsSchema | None = None


def invalidate_settings_cache() -> None:
    """Clear the cached settings."""
    global _settings_cache
    _settings_cache = None


def load_settings(
    *,
    config_manager: ConfigManager | None = None,
    cwd: str | Path | None = None,
    extra_overrides: dict[str, Any] | None = None,
) -> SettingsSchema:
    """Load settings from config hierarchy + defaults.

    Merge order: DEFAULT_SETTINGS < global config "settings" < project < local < extra_overrides.
    """
    if config_manager is None:
        config_manager = ConfigManager(cwd=cwd)

    base = dataclasses.asdict(DEFAULT_SETTINGS)

    # Pull "settings" sub-key from each config level
    global_settings = config_manager.load_global().get("settings", {})
    project_settings = config_manager.load_project().get("settings", {})
    local_settings = config_manager.load_local().get("settings", {})

    merged = base
    if global_settings:
        merged = _deep_merge(merged, global_settings)
    if project_settings:
        merged = _deep_merge(merged, project_settings)
    if local_settings:
        merged = _deep_merge(merged, local_settings)
    if extra_overrides:
        merged = _deep_merge(merged, extra_overrides)

    return SettingsSchema.from_dict(merged)


def get_settings(
    *,
    config_manager: ConfigManager | None = None,
    cwd: str | Path | None = None,
) -> SettingsSchema:
    """Get cached settings (load on first call)."""
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = load_settings(config_manager=config_manager, cwd=cwd)
    return _settings_cache


def apply_persisted_model(provider: Any, provider_name: str) -> bool:
    """Apply the persisted ``(model, model_provider)`` pair to a provider.

    The effective read-side of the ch03 round-3 model persistence: the
    port's live model channel is ``provider.model``, so a persisted
    `/model` choice must reach the constructed provider to survive a
    restart. Mirrors ``getUserSpecifiedModelSetting``
    (TS ``utils/model/model.ts:109-135``) including its provider-match
    guard — a model persisted under another provider is ignored (the
    cross-provider staleness failure documented there). Callers gate on
    explicit overrides: an explicit ``--model``-style choice made at
    construction wins (override-first precedence), so call this only when
    no explicit model was supplied.

    Returns True iff the persisted model was applied.
    """
    try:
        s = get_settings()
    except Exception:
        return False
    if s.model and s.model_provider == provider_name:
        provider.model = s.model
        return True
    return False

def update_local_settings(
    updates: dict[str, Any], *, cwd: str | Path | None = None,
) -> bool:
    """Merge ``updates`` into the LOCAL settings tier and persist.

    OS-1 G3 — the ``updateSettingsForSource('localSettings', ...)`` analog
    (Settings/Config.tsx:1600): writes the ``settings`` sub-key of the
    project-local config file (``.claude/config.local.json``), creating the
    file/dir as needed, atomically (tempfile + replace), then invalidates
    the settings cache. Returns False (logged) on any failure — persistence
    is best-effort; callers' in-memory state still applies.
    """
    import json as _json
    import logging as _logging
    import os as _os
    import tempfile as _tempfile

    from src.config import get_local_config_path

    logger = _logging.getLogger(__name__)
    try:
        path = get_local_config_path(cwd)
        if path is None:
            # Outside a git root there is no local tier — persist to the
            # GLOBAL config's settings block instead (also merged by
            # load_settings), so the choice survives everywhere.
            from src.config import GLOBAL_CONFIG_FILE

            path = Path(GLOBAL_CONFIG_FILE)
        cfg_dir = path.parent
        cfg_dir.mkdir(parents=True, exist_ok=True)
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:  # noqa: BLE001 — missing/corrupt starts fresh
            data = {}
        settings_block = data.get("settings")
        if not isinstance(settings_block, dict):
            settings_block = {}
        data["settings"] = _deep_merge(settings_block, updates)
        fd, tmp = _tempfile.mkstemp(dir=str(cfg_dir), prefix=".settings-")
        try:
            with _os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(_json.dumps(data, indent=2) + "\n")
            _os.replace(tmp, path)
        finally:
            if _os.path.exists(tmp):
                try:
                    _os.unlink(tmp)
                except OSError:
                    pass
        invalidate_settings_cache()
        return True
    except Exception:  # noqa: BLE001
        logger.debug("update_local_settings failed", exc_info=True)
        return False

