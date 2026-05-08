"""Apply and persist :class:`PermissionUpdate` objects.

Mirrors ``typescript/src/utils/permissions/PermissionUpdate.ts``. The TS file
threads updates through both an in-memory context (``applyPermissionUpdate``)
and on-disk settings (``persistPermissionUpdate``); we keep the same split.

The persistence helpers take an injectable ``settings_path_for_destination``
callable so callers can stub out filesystem access in tests. The default
resolver returns ``None`` for non-persistable destinations — callers should
gate on :func:`supports_persistence` before constructing the path.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import PurePosixPath
from typing import Any, Callable

from .rule_parser import (
    permission_rule_value_from_string,
    permission_rule_value_to_string,
)
from .types import (
    AdditionalWorkingDirectory,
    PermissionRuleValue,
    PermissionUpdate,
    PermissionUpdateAddDirectories,
    PermissionUpdateAddRules,
    PermissionUpdateDestination,
    PermissionUpdateRemoveDirectories,
    PermissionUpdateRemoveRules,
    PermissionUpdateReplaceRules,
    PermissionUpdateSetMode,
    ToolPermissionContext,
)

log = logging.getLogger(__name__)

PERSISTABLE_DESTINATIONS: tuple[PermissionUpdateDestination, ...] = (
    "userSettings",
    "projectSettings",
    "localSettings",
)


def supports_persistence(destination: PermissionUpdateDestination) -> bool:
    """True if ``destination`` is a persistable settings file source.

    Mirrors ``supportsPersistence`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:208-216``.
    ``cliArg`` and ``session`` are in-memory only.
    """
    return destination in PERSISTABLE_DESTINATIONS


def extract_rules(updates: list[PermissionUpdate] | None) -> list[PermissionRuleValue]:
    """Flatten ``addRules`` updates into a list of rule values.

    Mirrors ``extractRules`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:30-43``. Only
    ``addRules`` updates contribute — ``replaceRules`` / ``removeRules`` are
    ignored because their semantics differ.
    """
    if not updates:
        return []
    out: list[PermissionRuleValue] = []
    for update in updates:
        if isinstance(update, PermissionUpdateAddRules):
            out.extend(update.rules)
    return out


def has_rules(updates: list[PermissionUpdate] | None) -> bool:
    """True if ``updates`` contains at least one ``addRules`` rule."""
    return len(extract_rules(updates)) > 0


def _ruleset_key(behavior: str) -> str:
    if behavior == "allow":
        return "always_allow_rules"
    if behavior == "deny":
        return "always_deny_rules"
    return "always_ask_rules"


def _replace_ruleset(
    context: ToolPermissionContext,
    behavior: str,
    destination: PermissionUpdateDestination,
    new_strings: list[str],
) -> ToolPermissionContext:
    key = _ruleset_key(behavior)
    current = dict(getattr(context, key))
    current[destination] = new_strings
    kwargs: dict[str, Any] = {
        "mode": context.mode,
        "additional_working_directories": dict(context.additional_working_directories),
        "always_allow_rules": dict(context.always_allow_rules),
        "always_deny_rules": dict(context.always_deny_rules),
        "always_ask_rules": dict(context.always_ask_rules),
        "is_bypass_permissions_mode_available": context.is_bypass_permissions_mode_available,
        "should_avoid_permission_prompts": context.should_avoid_permission_prompts,
    }
    kwargs[key] = current
    return ToolPermissionContext(**kwargs)


def apply_permission_update(
    context: ToolPermissionContext,
    update: PermissionUpdate,
) -> ToolPermissionContext:
    """Apply a single :class:`PermissionUpdate`, returning a new context.

    Mirrors ``applyPermissionUpdate`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:55-188``. The
    update kinds and their semantics:

    - ``setMode`` — replace ``context.mode``.
    - ``addRules`` — append rule strings to the matching ruleset slot.
    - ``replaceRules`` — replace the ruleset slot with the supplied rules.
    - ``removeRules`` — drop matching rule strings from the slot.
    - ``addDirectories`` — register additional working directories.
    - ``removeDirectories`` — drop registered working directories by path.

    Returns a fresh :class:`ToolPermissionContext`; the input is left
    unchanged.
    """
    if isinstance(update, PermissionUpdateSetMode):
        log.debug("permission update: setMode -> %s", update.mode)
        return ToolPermissionContext(
            mode=update.mode,
            additional_working_directories=dict(context.additional_working_directories),
            always_allow_rules=dict(context.always_allow_rules),
            always_deny_rules=dict(context.always_deny_rules),
            always_ask_rules=dict(context.always_ask_rules),
            is_bypass_permissions_mode_available=context.is_bypass_permissions_mode_available,
            should_avoid_permission_prompts=context.should_avoid_permission_prompts,
        )

    if isinstance(update, PermissionUpdateAddRules):
        rule_strings = [permission_rule_value_to_string(r) for r in update.rules]
        log.debug(
            "permission update: addRules behavior=%s dest=%s rules=%s",
            update.behavior, update.destination, rule_strings,
        )
        existing = list(getattr(context, _ruleset_key(update.behavior)).get(update.destination, []))
        return _replace_ruleset(
            context, update.behavior, update.destination, existing + rule_strings,
        )

    if isinstance(update, PermissionUpdateReplaceRules):
        rule_strings = [permission_rule_value_to_string(r) for r in update.rules]
        log.debug(
            "permission update: replaceRules behavior=%s dest=%s rules=%s",
            update.behavior, update.destination, rule_strings,
        )
        return _replace_ruleset(
            context, update.behavior, update.destination, rule_strings,
        )

    if isinstance(update, PermissionUpdateRemoveRules):
        rule_strings = [permission_rule_value_to_string(r) for r in update.rules]
        log.debug(
            "permission update: removeRules behavior=%s dest=%s rules=%s",
            update.behavior, update.destination, rule_strings,
        )
        existing = list(getattr(context, _ruleset_key(update.behavior)).get(update.destination, []))
        to_remove = set(rule_strings)
        filtered = [r for r in existing if r not in to_remove]
        return _replace_ruleset(
            context, update.behavior, update.destination, filtered,
        )

    if isinstance(update, PermissionUpdateAddDirectories):
        log.debug(
            "permission update: addDirectories dest=%s dirs=%s",
            update.destination, list(update.directories),
        )
        new_dirs = dict(context.additional_working_directories)
        for path in update.directories:
            new_dirs[path] = AdditionalWorkingDirectory(
                path=path, source=update.destination,  # type: ignore[arg-type]
            )
        return ToolPermissionContext(
            mode=context.mode,
            additional_working_directories=new_dirs,
            always_allow_rules=dict(context.always_allow_rules),
            always_deny_rules=dict(context.always_deny_rules),
            always_ask_rules=dict(context.always_ask_rules),
            is_bypass_permissions_mode_available=context.is_bypass_permissions_mode_available,
            should_avoid_permission_prompts=context.should_avoid_permission_prompts,
        )

    if isinstance(update, PermissionUpdateRemoveDirectories):
        log.debug(
            "permission update: removeDirectories dirs=%s",
            list(update.directories),
        )
        new_dirs = dict(context.additional_working_directories)
        for path in update.directories:
            new_dirs.pop(path, None)
        return ToolPermissionContext(
            mode=context.mode,
            additional_working_directories=new_dirs,
            always_allow_rules=dict(context.always_allow_rules),
            always_deny_rules=dict(context.always_deny_rules),
            always_ask_rules=dict(context.always_ask_rules),
            is_bypass_permissions_mode_available=context.is_bypass_permissions_mode_available,
            should_avoid_permission_prompts=context.should_avoid_permission_prompts,
        )

    return context


def apply_permission_updates(
    context: ToolPermissionContext,
    updates: list[PermissionUpdate],
) -> ToolPermissionContext:
    """Fold :func:`apply_permission_update` over an ordered list of updates.

    Mirrors ``applyPermissionUpdates`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:196-206``.
    """
    out = context
    for update in updates:
        out = apply_permission_update(out, update)
    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

SettingsPathResolver = Callable[[PermissionUpdateDestination], str | None]


def _read_json(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: str, data: dict[str, Any]) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError:
        log.error("failed to persist settings to %s", path)
        return False


def persist_permission_update(
    update: PermissionUpdate,
    *,
    settings_path_for_destination: SettingsPathResolver,
) -> bool:
    """Persist one update to its destination's settings file.

    Mirrors ``persistPermissionUpdate`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:222-342``.

    Returns ``False`` if the destination is in-memory only or if the settings
    file write failed; ``True`` on a clean write. Non-persistable destinations
    (``cliArg``, ``session``) are a successful no-op from the caller's
    perspective and return ``False`` to make that observable in tests.
    """
    destination = update.destination
    if not supports_persistence(destination):
        return False

    path = settings_path_for_destination(destination)
    if not path:
        return False

    settings = _read_json(path)
    permissions = settings.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        permissions = {}
        settings["permissions"] = permissions

    if isinstance(update, PermissionUpdateAddRules):
        rule_strings = [permission_rule_value_to_string(r) for r in update.rules]
        existing = permissions.get(update.behavior, [])
        if not isinstance(existing, list):
            existing = []
        for rule_str in rule_strings:
            if rule_str not in existing:
                existing.append(rule_str)
        permissions[update.behavior] = existing

    elif isinstance(update, PermissionUpdateReplaceRules):
        rule_strings = [permission_rule_value_to_string(r) for r in update.rules]
        permissions[update.behavior] = rule_strings

    elif isinstance(update, PermissionUpdateRemoveRules):
        # Normalize via parse → serialize round-trip so a stored "Bash(*)"
        # matches a removal request for "Bash" (parity with TS line 282-287).
        target_strings = {permission_rule_value_to_string(r) for r in update.rules}
        existing = permissions.get(update.behavior, [])
        if not isinstance(existing, list):
            existing = []
        filtered: list[str] = []
        for rule_str in existing:
            normalized = permission_rule_value_to_string(
                permission_rule_value_from_string(rule_str)
            )
            if normalized not in target_strings:
                filtered.append(rule_str)
        permissions[update.behavior] = filtered

    elif isinstance(update, PermissionUpdateAddDirectories):
        existing = permissions.get("additionalDirectories", [])
        if not isinstance(existing, list):
            existing = []
        for d in update.directories:
            if d not in existing:
                existing.append(d)
        permissions["additionalDirectories"] = existing

    elif isinstance(update, PermissionUpdateRemoveDirectories):
        existing = permissions.get("additionalDirectories", [])
        if not isinstance(existing, list):
            existing = []
        target = set(update.directories)
        permissions["additionalDirectories"] = [d for d in existing if d not in target]

    elif isinstance(update, PermissionUpdateSetMode):
        permissions["defaultMode"] = update.mode

    return _write_json(path, settings)


def persist_permission_updates(
    updates: list[PermissionUpdate],
    *,
    settings_path_for_destination: SettingsPathResolver,
) -> list[bool]:
    """Persist a list of updates; returns the per-update success flag.

    Mirrors ``persistPermissionUpdates`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:349-353``.
    """
    return [
        persist_permission_update(
            u, settings_path_for_destination=settings_path_for_destination,
        )
        for u in updates
    ]


def create_read_rule_suggestion(
    dir_path: str,
    destination: PermissionUpdateDestination = "session",
) -> PermissionUpdate | None:
    """Build a ``Read(<path>/**)`` allow-rule suggestion for ``dir_path``.

    Mirrors ``createReadRuleSuggestion`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:361-389``. Returns
    ``None`` for the filesystem root (``"/"``) — too broad to be a meaningful
    permission target.
    """
    posix_path = dir_path.replace("\\", "/")
    if posix_path == "/":
        return None

    if PurePosixPath(posix_path).is_absolute():
        rule_content = f"/{posix_path}/**"
    else:
        rule_content = f"{posix_path}/**"

    return PermissionUpdateAddRules(
        type="addRules",
        rules=(PermissionRuleValue(tool_name="Read", rule_content=rule_content),),
        behavior="allow",
        destination=destination,
    )
