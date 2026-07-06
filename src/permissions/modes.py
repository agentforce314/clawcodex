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
    disable_bypass_permissions_mode: bool | None = None,
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

    ``disable_bypass_permissions_mode`` (critic C12): when a bypass lockdown is
    in effect, the ``bypassPermissions`` candidate is SKIPPED (TS
    permissionSetup.ts:778-793 ``continue``s past it) so the resolved mode
    falls through to the next candidate / ``default``. This is the ONLY
    faithful way to enforce the lockdown, because the port's permission check
    (``check.py:456``) bypasses on ``mode == "bypassPermissions"`` ALONE — the
    availability boolean is consulted only for ``plan`` mode. ``None`` →
    resolve the lockdown here (so every caller is covered by default)."""
    if disable_bypass_permissions_mode is None:
        disable_bypass_permissions_mode = is_bypass_permissions_mode_disabled()
    candidates: list[PermissionMode] = []
    if dangerously_skip_permissions:
        candidates.append("bypassPermissions")
    if permission_mode_cli:
        candidates.append(permission_mode_from_string(permission_mode_cli))
    if settings_default_mode:
        candidates.append(permission_mode_from_string(settings_default_mode))
    for candidate in candidates:
        if candidate == "bypassPermissions" and disable_bypass_permissions_mode:
            log.warning(
                "Bypass permissions mode was disabled by settings/policy "
                "(permissions.disableBypassPermissionsMode); falling back."
            )
            continue  # TS: skip this mode if it's disabled
        return candidate
    return "default"


def has_allow_bypass_permissions_mode() -> bool:
    """Return True if a trusted settings source enables bypass availability.

    Mirrors ``hasAllowBypassPermissionsMode`` in
    ``typescript/src/utils/settings/settings.ts:897``: reads
    ``permissions.allowBypassPermissionsMode`` from the user (global) and local
    tiers, and INTENTIONALLY EXCLUDES the project tier
    (``<git-root>/.claude/config.json``) — that file is committable, so a
    cloned repo must not be able to auto-enable bypass (the TS comment: "a
    malicious project could otherwise enable bypass mode (security risk)").

    We read the RAW per-tier config dicts rather than the merged
    ``SettingsSchema`` for two reasons: the schema has no structured slot for
    this scalar (``permissions`` is modeled as a flat rule *list*), and the
    merged view can't tell the project tier apart from the trusted tiers, which
    is exactly the distinction the security exclusion turns on.
    """
    try:
        from src.config import ConfigManager

        cm = ConfigManager()
        # Global (user) + local (gitignored, operator-owned) only — never the
        # committable project tier.
        for loader in (cm.load_global, cm.load_local):
            perms = loader().get("settings", {}).get("permissions")
            if isinstance(perms, dict) and perms.get("allowBypassPermissionsMode"):
                return True
    except Exception:
        return False
    return False


def is_bypass_permissions_mode_disabled() -> bool:
    """Return True if a settings source DISABLES bypass availability.

    Mirrors TS ``settings.permissions?.disableBypassPermissionsMode ===
    'disable'`` (``permissionSetup.ts:939``), the negative guard on
    ``isBypassPermissionsModeAvailable`` that the port previously dropped — so
    an operator locking bypass down (managed MDM policy, or a user/local
    ``settings.json``) was SILENTLY IGNORED and bypass stayed available
    (a live fail-open; critic C12).

    Unlike the POSITIVE ``allowBypassPermissionsMode`` (which excludes the
    committable project tier so a malicious repo can't ENABLE bypass), a
    ``disable`` only ever REMOVES capability, so honoring it from ANY tier —
    including the managed/policy tier (the org-admin lockdown) and the project
    tier — is always safe. Reads the raw per-tier dicts (the SettingsSchema
    models ``permissions`` as a flat rule list, no scalar slot)."""
    try:
        from src.config import ConfigManager

        cm = ConfigManager()
        for loader in (cm.load_global, cm.load_local, cm.load_project):
            perms = loader().get("settings", {}).get("permissions")
            if isinstance(perms, dict) and perms.get("disableBypassPermissionsMode") == "disable":
                return True
        # Managed/policy tier (root-owned MDM preferences) — the primary
        # lockdown source. Read directly (setup.py:57 pattern).
        try:
            import json

            from src.settings.managed_path import resolve_managed_settings_path

            mp = resolve_managed_settings_path()
            if mp is not None and mp.exists():
                with mp.open() as f:
                    managed = json.load(f)
                perms = (managed or {}).get("permissions")
                if isinstance(perms, dict) and perms.get("disableBypassPermissionsMode") == "disable":
                    return True
        except Exception:
            # A managed policy file that fails to parse/read would silently
            # fail-OPEN the lockdown (parity with TS dropping a malformed
            # source); log it so an admin can diagnose why their policy isn't
            # taking effect (critic C12).
            log.debug("managed settings unreadable for bypass-disable check", exc_info=True)
    except Exception:
        return False
    return False
