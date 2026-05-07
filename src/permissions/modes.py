from __future__ import annotations

import logging

from .types import (
    EXTERNAL_PERMISSION_MODES,
    PERMISSION_MODES,
    ExternalPermissionMode,
    PermissionMode,
)

log = logging.getLogger(__name__)


_MODE_CONFIG: dict[PermissionMode, dict[str, str]] = {
    "default": {"title": "Default", "short_title": "Default", "symbol": "", "external": "default"},
    "plan": {"title": "Plan Mode", "short_title": "Plan", "symbol": "⏸", "external": "plan"},
    "acceptEdits": {"title": "Accept edits", "short_title": "Accept", "symbol": "⏵⏵", "external": "acceptEdits"},
    "bypassPermissions": {"title": "Bypass Permissions", "short_title": "Bypass", "symbol": "⏵⏵", "external": "bypassPermissions"},
    "dontAsk": {"title": "Don't Ask", "short_title": "DontAsk", "symbol": "⏵⏵", "external": "dontAsk"},
    # Internal modes — neither user-addressable nor persisted to settings.json.
    # `external` maps them to a sensible external display mode (parity with
    # typescript/src/utils/permissions/PermissionMode.ts:80-90).
    "auto": {"title": "Auto mode", "short_title": "Auto", "symbol": "⏵⏵", "external": "default"},
    "bubble": {"title": "Bubble", "short_title": "Bubble", "symbol": "↑", "external": "default"},
}


def _get_config(mode: PermissionMode) -> dict[str, str]:
    return _MODE_CONFIG.get(mode, _MODE_CONFIG["default"])


def permission_mode_title(mode: PermissionMode) -> str:
    return _get_config(mode)["title"]


def permission_mode_short_title(mode: PermissionMode) -> str:
    return _get_config(mode)["short_title"]


def permission_mode_symbol(mode: PermissionMode) -> str:
    return _get_config(mode)["symbol"]


def permission_mode_from_string(s: str) -> PermissionMode:
    if s in PERMISSION_MODES:
        return s  # type: ignore[return-value]
    return "default"


def is_default_mode(mode: PermissionMode | None) -> bool:
    return mode is None or mode == "default"


def to_external_permission_mode(mode: PermissionMode) -> ExternalPermissionMode:
    """Map a possibly-internal mode to its external representation.

    Mirrors ``toExternalPermissionMode`` in
    ``typescript/src/utils/permissions/PermissionMode.ts:111-115``. ``auto``
    and ``bubble`` are internal and surface to external consumers as
    ``"default"``.
    """
    config = _get_config(mode)
    external = config.get("external", "default")
    return external  # type: ignore[return-value]


def is_external_permission_mode(mode: PermissionMode) -> bool:
    """True when ``mode`` is in :data:`EXTERNAL_PERMISSION_MODES`.

    Mirrors ``isExternalPermissionMode`` in
    ``typescript/src/utils/permissions/PermissionMode.ts:97-105``. The TS
    reference adds an internal ``USER_TYPE === 'ant'`` guard; we omit it
    because that gate is Anthropic-internal and not part of the public
    Python contract.
    """
    return mode in EXTERNAL_PERMISSION_MODES


def initial_permission_mode_from_cli(
    *,
    permission_mode_cli: str | None = None,
    dangerously_skip_permissions: bool = False,
    settings_default_mode: str | None = None,
) -> PermissionMode:
    """Resolve the effective :class:`PermissionMode` from CLI flags + settings.

    Mirrors ``initialPermissionModeFromCLI`` in
    ``typescript/src/utils/permissions/permissionSetup.ts:690``.

    Priority order (first match wins):

    1. ``--dangerously-skip-permissions`` -> ``bypassPermissions``
    2. ``--permission-mode <name>``       -> the parsed mode
    3. ``settings.permissions.defaultMode``
    4. fallback to ``default``

    Unknown / mistyped mode strings degrade to ``default`` via
    :func:`permission_mode_from_string`.
    """
    candidates: list[PermissionMode] = []
    if dangerously_skip_permissions:
        candidates.append("bypassPermissions")
    if permission_mode_cli:
        candidates.append(permission_mode_from_string(permission_mode_cli))
    if settings_default_mode:
        candidates.append(permission_mode_from_string(settings_default_mode))
    if candidates:
        return candidates[0]
    return "default"


def has_allow_bypass_permissions_mode() -> bool:
    """Return True if any trusted settings source enables bypass mode availability.

    Mirrors ``hasAllowBypassPermissionsMode`` in
    ``typescript/src/utils/settings/settings.ts:897``.

    The TS reference reads ``permissions.allowBypassPermissionsMode`` from
    user, local, flag, and policy settings — projectSettings is intentionally
    excluded because a malicious project could otherwise auto-enable bypass.

    Python today merges all settings sources into a single ``SettingsSchema``
    (see ``src/settings/settings.py``). We read the merged ``extra`` dict for
    the same key. When source-segmented settings land we can tighten this.
    """
    try:
        from src.settings.settings import get_settings
    except Exception:
        return False

    try:
        settings = get_settings()
    except Exception:
        return False

    perms = settings.extra.get("permissions") if hasattr(settings, "extra") else None
    if isinstance(perms, dict):
        return bool(perms.get("allowBypassPermissionsMode"))
    return False
