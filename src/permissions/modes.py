from __future__ import annotations

import logging
from typing import Any

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


def _settings_perms_structured_is_explicit(perms_obj: Any) -> bool:
    """True when :class:`PermissionsConfig` carries any user-set value.

    F-47 (2026-06-02): the structured field is always populated (defaults
    are non-None), so we can't use "field is None" to detect "user set it".
    Instead we look for any value that diverges from the dataclass defaults
    -- a non-empty :attr:`rules` bucket, a behavior key not in the
    default 3-behavior skeleton, a non-empty :attr:`additional_directories`,
    a non-empty :attr:`additional`, a :attr:`default_mode`, or
    ``allow_bypass_permissions_mode is True``.

    Note: explicitly setting ``allow_bypass_permissions_mode = False`` is
    indistinguishable from leaving it at the default. This is a known
    limitation; users who want to *disable* bypass should remove the
    ``permissions`` block entirely (not set it to ``False``).
    """
    if perms_obj is None:
        return False
    if perms_obj.allow_bypass_permissions_mode is True:
        return True
    if perms_obj.default_mode:
        return True
    # ``rules`` is initialized to a 3-behavior skeleton
    # ``{"allow": [], "deny": [], "ask": []}``. Treat that as default.
    default_rule_keys = {"allow", "deny", "ask"}
    rules = perms_obj.rules
    if any(rules.get(b) for b in ("allow", "deny", "ask")):
        return True
    if set(rules.keys()) - default_rule_keys:
        return True
    if perms_obj.additional_directories:
        return True
    if perms_obj.additional:
        return True
    return False


def _settings_perms(settings: Any) -> dict[str, Any]:
    """Aggregate all readable ``permissions`` sub-keys from a settings object.

    F-47 (2026-06-02): replaces the previous ``settings.extra["permissions"]``
    read path, which was the only working fallback under the legacy
    ``list[PermissionRule]`` schema but became a dead-end once F-47 promoted
    ``permissions`` to a structured :class:`PermissionsConfig` field.

    Semantic:

    * Legacy ``settings.extra["permissions"]`` is the *baseline* (covers
      pre-F-47 binaries that wrote the dict into ``extra``).
    * Structured :class:`PermissionsConfig` *overrides* the baseline only
      when it carries an explicit non-default value
      (see :func:`_settings_perms_structured_is_explicit`). The structured
      ``to_dict()`` keys then win over the legacy keys for the same field.
    * ``settings.permissions.additional`` is merged last and always wins
      (forward-compat bag for unknown sub-keys written by newer / custom
      config sources).

    Returns an empty dict when ``settings`` is ``None`` or has no usable
    permissions block — callers should treat empty as "no override".
    """
    bag: dict[str, Any] = {}
    if settings is None:
        return bag

    # 1. Legacy baseline (pre-F-47 / F-47-landing window fallback).
    legacy = getattr(settings, "extra", None)
    if isinstance(legacy, dict):
        legacy_perms = legacy.get("permissions")
        if isinstance(legacy_perms, dict):
            bag.update(legacy_perms)

    perms_obj = getattr(settings, "permissions", None)
    structured_is_explicit = _settings_perms_structured_is_explicit(perms_obj)

    # 2. Structured fields override legacy when explicit.
    if structured_is_explicit:
        to_dict = getattr(perms_obj, "to_dict", None)
        if callable(to_dict):
            try:
                rendered = to_dict()
            except Exception:
                rendered = None
            if isinstance(rendered, dict):
                bag.update(rendered)

    # 3. Forward-compat bag always wins for unknown sub-keys.
    if perms_obj is not None:
        additional = getattr(perms_obj, "additional", None)
        if isinstance(additional, dict):
            bag.update(additional)

    return bag


def has_allow_bypass_permissions_mode() -> bool:
    """Return True if any trusted settings source enables bypass mode availability.

    Mirrors ``hasAllowBypassPermissionsMode`` in
    ``typescript/src/utils/settings/settings.ts:897``.

    The TS reference reads ``permissions.allowBypassPermissionsMode`` from
    user, local, flag, and policy settings — projectSettings is intentionally
    excluded because a malicious project could otherwise auto-enable bypass.

    F-47: read through :func:`_settings_perms` which aggregates the
    structured ``PermissionsConfig`` field + the legacy
    ``settings.extra["permissions"]`` path. Pre-F-47 binaries that wrote
    the dict into ``extra`` keep working.
    """
    try:
        from src.settings.settings import get_settings
    except Exception:
        return False

    try:
        settings = get_settings()
    except Exception:
        return False

    return bool(_settings_perms(settings).get("allowBypassPermissionsMode"))
