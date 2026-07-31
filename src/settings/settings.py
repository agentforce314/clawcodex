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


def get_persisted_model(provider_name: str, *, provider_is_explicit: bool = False) -> str:
    """The persisted ``/model`` choice for ``provider_name``, or ``""``.

    The read side of the model persistence whose write side is
    ``state/app_state._on_main_loop_model_change``. Mirrors TS
    ``getUserSpecifiedModelSetting`` (``utils/model/model.ts:124-165``),
    which resolves the saved ``settings.model`` after an explicit override
    and before the built-in default. Callers own that precedence:

        model = explicit_override or get_persisted_model(provider) or default

    matching TS ``main.tsx:1984``
    (``userSpecifiedModel ?? getUserSpecifiedModelSetting() ?? null``).

    **Provider-match guard.** A persisted model is meaningful only with the
    provider that served it; TS documents the cross-provider staleness
    failure (a stale model fired at the wrong endpoint and 400s), and TS
    itself guards it by reading only the env var matching the active
    provider. Here the pairing is explicit: ``settings.model_provider``.

    **Fusion exception.** A fusion model (``providers/fusion_models.py``)
    names its OWN base provider in its record, so it is self-describing and
    the staleness hazard does not apply — it is restored even when it does
    not match the session's *default* provider, which is the whole point of
    having selected one. A disabled or since-deleted fusion name resolves to
    nothing and falls through to the default, rather than reaching the wire
    as a bogus id.

    ``provider_is_explicit`` marks that the caller's ``provider_name`` came
    from an explicit ``--provider`` flag rather than the configured default.
    That narrows the fusion exception: restoring a fusion model REPLACES the
    session provider with the record's base, so honouring it over an explicit
    flag would silently ignore what the user just typed. Explicit intent
    wins, matching the override-first precedence throughout this resolution.

    Never raises: any failure yields ``""`` and the caller falls back to the
    provider default. That covers the attribute reads too, not just the file
    load — this runs on the startup path of every entrypoint, and a settings
    object that does not carry these fields (a partial stub, an older
    persisted shape) must degrade to the default rather than abort a launch.
    """
    try:
        s = get_settings()
        model = str(getattr(s, "model", "") or "").strip()
        if not model:
            return ""
        persisted_provider = str(getattr(s, "model_provider", "") or "")
    except Exception:  # noqa: BLE001 — see the "never raises" contract above
        logger.debug("settings read during model restore failed", exc_info=True)
        return ""
    try:
        from src.providers.fusion_models import get_fusion_model

        fusion = get_fusion_model(model)
        if fusion is not None:
            if not fusion.enabled:
                return ""
            if provider_is_explicit and fusion.base.provider != provider_name:
                return ""
            return fusion.name
    except Exception:  # noqa: BLE001 — a fusion-config problem is not fatal
        logger.debug("fusion lookup during model restore failed", exc_info=True)
    return model if persisted_provider == provider_name else ""


def update_local_settings(
    updates: dict[str, Any], *, cwd: str | Path | None = None,
) -> bool:
    """Merge ``updates`` into the LOCAL settings tier and persist.

    OS-1 G3 — the ``updateSettingsForSource('localSettings', ...)`` analog
    (Settings/Config.tsx:1600): writes the ``settings`` sub-key of the
    project-local config file (``.clawcodex/config.local.json``), creating the
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

