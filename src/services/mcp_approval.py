"""Project ``.mcp.json`` server approval (components C7).

Port of TS ``MCPServerApprovalDialog.tsx`` +
``services/mcp/utils.ts getProjectMcpServerStatus`` (:351-390): servers
configured by a repo's ``.mcp.json`` are UNTRUSTED until the user
approves them — a checked-out repository must not get to launch
arbitrary MCP processes on its own say-so.

Status precedence (TS-faithful):
``disabledMcpjsonServers`` (name-normalized) → rejected;
``enabledMcpjsonServers`` → approved; ``enableAllProjectMcpServers`` →
approved; else **pending**.

Settings layering: TS reads these keys from MERGED settings
(``getSettings_DEPRECATED`` → user → project → local; list values are
unioned by ``settingsMergeCustomizer``, scalars last-writer-wins). This
port reads the **user and local** tiers only — the project tier
(``<cwd>/.clawcodex/settings.json``, committable) is DELIBERATELY
excluded so a repo cannot self-approve its own ``.mcp.json`` servers
by also committing settings. This also ignores a repo-committed
``disabledMcpjsonServers`` — accepted: the failure mode is fail-safe
(server stays pending → user is prompted). Deviation from TS,
strictly safer.
Choices persist to the local tier (TS MCPServerApprovalDialog.tsx:31-48
writes localSettings) — ``<cwd>/.clawcodex/settings.local.json``.

Enforcement has exactly two gates in ``services/mcp/config``, applied
to the ``.mcp.json``-derived scopes only (Python ``local`` = cwd
``.mcp.json``, ``project`` = parent-dir ``.mcp.json``; other scopes are
the user's or operator's own configuration and pass untouched):

* ``get_all_mcp_configs`` filters each ``.mcp.json`` scope BEFORE the
  cross-scope merge — pre-merge so a repo server name-colliding with a
  user-scope server can never shadow-then-drop it;
* ``get_mcp_config_by_name`` returns ``None`` for a non-approved
  ``.mcp.json`` server, closing the per-name resolve path
  (``reconnect_mcp_server`` etc.).

Both gates sit on the choke points every current and future consumer
(doctor, /mcp, the eventual runtime mount) reads through; non-TUI
sessions get warning notices for pending servers instead of a prompt.

C8 update (was deferred in C7): the TS auto-approve branches are now
ported — ``skipDangerousModePermissionPrompt`` (the C8 bypass-dialog
acceptance) and non-interactive sessions both resolve to ``approved``
(TS utils.ts:377-403), so headless/SDK runs match TS instead of the
C7 interim skip-with-warning.
"""

from __future__ import annotations

import json
import os
from typing import Any

from src.services.mcp.normalization import normalize_name_for_mcp
from src.services.startup_gates import _read_json_dict

ENABLED_KEY = "enabledMcpjsonServers"
DISABLED_KEY = "disabledMcpjsonServers"
ENABLE_ALL_KEY = "enableAllProjectMcpServers"

_MCPJSON_SCOPES = frozenset({"project", "local"})


def _local_settings_path(cwd: str | None = None) -> str:
    from src.permissions import settings_paths

    return settings_paths.local_settings_path(cwd)


def _read_merged_settings(cwd: str | None = None) -> dict[str, Any]:
    """User tier then local tier (TS merge order; project tier excluded
    on purpose — see module docstring). Lists union, scalars last-win."""

    from src.permissions import settings_paths

    merged: dict[str, Any] = {}
    for path in (
        settings_paths.user_settings_path(),
        _local_settings_path(cwd),
    ):
        layer = _read_json_dict(path)
        for key in (ENABLED_KEY, DISABLED_KEY):
            value = layer.get(key)
            if isinstance(value, list):
                names = [str(n) for n in value]
                existing = merged.get(key, [])
                merged[key] = existing + [
                    n for n in names if n not in existing
                ]
        if ENABLE_ALL_KEY in layer:
            merged[ENABLE_ALL_KEY] = layer.get(ENABLE_ALL_KEY)
    return merged


def _write_local_settings(data: dict[str, Any], cwd: str | None = None) -> bool:
    path = _local_settings_path(cwd)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return True
    except OSError:
        return False


def get_mcpjson_server_status(
    server_name: str, *, cwd: str | None = None
) -> str:
    """``approved`` | ``rejected`` | ``pending`` (TS utils.ts:351)."""

    settings = _read_merged_settings(cwd)
    normalized = normalize_name_for_mcp(server_name)
    disabled = settings.get(DISABLED_KEY) or []
    if any(normalize_name_for_mcp(str(n)) == normalized for n in disabled):
        return "rejected"
    enabled = settings.get(ENABLED_KEY) or []
    if any(normalize_name_for_mcp(str(n)) == normalized for n in enabled):
        return "approved"
    if settings.get(ENABLE_ALL_KEY) is True:
        return "approved"
    # TS utils.ts:377-403 (deferred from C7, unlocked by C8): two
    # auto-approve branches where no approval popup can be shown.
    # (1) The user accepted the bypass-permissions dialog at some point
    #     (skipDangerousModePermissionPrompt — read from user/local
    #     settings only; TS explicitly excludes the project tier so a
    #     repo cannot accept on the user's behalf).
    # (2) Non-interactive session (SDK / -p / piped input) — the user
    #     explicitly chose that mode and its docs warn to use trusted
    #     directories only.
    # TS additionally gates both on isSettingSourceEnabled(
    # 'projectSettings'); this port has no setting-source disabling, so
    # that condition is always true here.
    try:
        from src.services.startup_gates import (
            has_skip_dangerous_mode_permission_prompt,
        )

        if has_skip_dangerous_mode_permission_prompt(cwd):
            return "approved"
    except Exception:
        pass
    try:
        from src.bootstrap.state import get_is_non_interactive_session

        if get_is_non_interactive_session():
            return "approved"
    except Exception:
        pass
    return "pending"


def list_pending_mcpjson_servers(cwd: str | None = None) -> list[str]:
    """Names of ``.mcp.json`` servers awaiting a decision."""

    from src.services.mcp.config import get_mcp_configs_by_scope

    names: list[str] = []
    for scope in ("project", "local"):
        try:
            servers, _errors = get_mcp_configs_by_scope(scope)  # type: ignore[arg-type]
        except Exception:
            continue
        for name in servers:
            if name in names:
                continue
            if get_mcpjson_server_status(name, cwd=cwd) == "pending":
                names.append(name)
    return sorted(names)


def record_mcpjson_choice(
    server_name: str, choice: str, *, cwd: str | None = None
) -> bool:
    """Persist one decision: ``enable`` | ``enable_all`` | ``disable``."""

    settings = _read_json_dict(_local_settings_path(cwd))

    def _names(key: str) -> list[str]:
        value = settings.get(key)
        return [str(n) for n in value] if isinstance(value, list) else []

    if choice == "enable":
        enabled = _names(ENABLED_KEY)
        if server_name not in enabled:
            enabled.append(server_name)
        settings[ENABLED_KEY] = enabled
    elif choice == "enable_all":
        settings[ENABLE_ALL_KEY] = True
    elif choice == "disable":
        disabled = _names(DISABLED_KEY)
        if server_name not in disabled:
            disabled.append(server_name)
        settings[DISABLED_KEY] = disabled
    else:
        return False
    return _write_local_settings(settings, cwd)


def is_mcpjson_scope(scope: Any) -> bool:
    return str(scope) in _MCPJSON_SCOPES


def filter_unapproved_mcpjson_servers(
    servers: dict[str, Any], *, cwd: str | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Drop non-approved ``.mcp.json``-scoped servers from a scope map.

    Returns ``(kept, pending_notices)``. Non-``.mcp.json`` scopes pass
    through untouched. Rejected servers are dropped SILENTLY — an
    explicit user decision is not a health problem (TS likewise loads
    nothing and warns nothing for disabled servers); only still-pending
    servers get an actionable notice.
    """

    kept: dict[str, Any] = {}
    notices: list[str] = []
    for name, scoped in servers.items():
        scope = getattr(scoped, "scope", None)
        if not is_mcpjson_scope(scope):
            kept[name] = scoped
            continue
        status = get_mcpjson_server_status(name, cwd=cwd)
        if status == "approved":
            kept[name] = scoped
        elif status == "pending":
            notices.append(
                f"MCP server '{name}' from .mcp.json is awaiting approval "
                "— open the TUI to approve, or add it to "
                f"{ENABLED_KEY} in .clawcodex/settings.local.json"
            )
    return kept, notices


__all__ = [
    "DISABLED_KEY",
    "ENABLED_KEY",
    "ENABLE_ALL_KEY",
    "filter_unapproved_mcpjson_servers",
    "get_mcpjson_server_status",
    "is_mcpjson_scope",
    "list_pending_mcpjson_servers",
    "record_mcpjson_choice",
]
