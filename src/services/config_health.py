"""Config/settings file health checks (components C6).

Degraded port of the TS validation surfaces (``InvalidConfigDialog`` /
``InvalidSettingsDialog`` / ``ValidationErrorsList`` / ``StatusNotices``
startup warnings): Python's config loader (`config.py _read_json`)
silently falls back to ``{}`` on a malformed file — the session works,
but the user's settings are IGNORED with no signal. This module turns
that into honest startup warnings; the TUI shows them as transcript
rows and ``/doctor`` lists them.

Deliberate divergences: NO reset-vs-exit gate (TS InvalidConfigDialog
offers one) — both loaders now degrade to "file ignored" on every
problem class this module detects (C6 hardened ``config._read_json``
and ``permissions.setup._load_settings_file`` to guarantee it), so a
blocking dialog would gate on a condition that cannot occur.
``KeybindingWarnings.tsx`` is NOT ported here — it rides on the parked
user-keybindings-file subsystem (the `/keybindings` deferral).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigWarning:
    path: str
    problem: str

    def message(self) -> str:
        return f"{self.path}: {self.problem} — file ignored"


def _check_json_file(path: str) -> ConfigWarning | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        return ConfigWarning(path=path, problem=f"invalid JSON ({exc.msg} at line {exc.lineno})")
    except UnicodeDecodeError:
        return ConfigWarning(path=path, problem="invalid encoding (not UTF-8)")
    except OSError as exc:
        return ConfigWarning(path=path, problem=f"unreadable ({exc.strerror or exc})")
    if not isinstance(data, dict):
        return ConfigWarning(
            path=path, problem="top level must be a JSON object"
        )
    return None


def collect_config_warnings(cwd: str | None = None) -> list[ConfigWarning]:
    """Health-check every config/settings file the session reads.

    Project config paths resolve exactly like the loader does
    (git-root-anchored via ``get_project_config_path`` — review M1); the
    permission-settings trio + managed file use ``settings_paths``.
    """

    from src.config import (
        GLOBAL_CONFIG_DIR,
        get_local_config_path,
        get_project_config_path,
    )
    from src.permissions.settings_paths import (
        local_settings_path,
        project_settings_path,
        user_settings_path,
    )
    from src.settings.managed_path import resolve_managed_settings_path

    base = cwd or os.getcwd()
    candidates: list[str] = [str(GLOBAL_CONFIG_DIR / "config.json")]
    project_cfg = get_project_config_path(base)
    if project_cfg is not None:
        candidates.append(str(project_cfg))
    local_cfg = get_local_config_path(base)
    if local_cfg is not None:
        candidates.append(str(local_cfg))
    candidates.extend(
        [
            user_settings_path(),
            project_settings_path(base),
            local_settings_path(base),
        ]
    )
    try:
        managed = resolve_managed_settings_path()
        if managed is not None:
            candidates.append(str(managed))
    except Exception:
        pass
    warnings: list[ConfigWarning] = []
    seen: set[str] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        warning = _check_json_file(path)
        if warning is not None:
            warnings.append(warning)
    return warnings


def collect_rule_warnings(cwd: str | None = None) -> list[str]:
    """Dangerous + shadowed permission-rule warnings (C6 review M3 —
    delivers the surfacing the C1 wiring deferred to this phase; TS
    ValidationErrorsList family)."""

    from src.permissions.settings_paths import default_setup_paths
    from src.permissions.setup import setup_permissions

    base = cwd or os.getcwd()
    try:
        setup = setup_permissions(cwd=base, **default_setup_paths(base))
    except Exception:
        return []
    out: list[str] = []
    for warning in setup.warnings:
        content = f"({warning.rule_content})" if warning.rule_content else ""
        out.append(
            f"dangerous permission rule {warning.tool_name}{content} "
            f"in {warning.source}"
        )
    for allow_rule, deny_rule in setup.shadowed_rules:
        out.append(
            f"allow rule {allow_rule.rule_value.tool_name} is shadowed by "
            f"deny rule {deny_rule.rule_value.tool_name}"
        )
    return out


__all__ = ["ConfigWarning", "collect_config_warnings", "collect_rule_warnings"]
