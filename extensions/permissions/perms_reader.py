"""二开 permissions extensions — F-47 structured permissions read path.

Extracted from ``src/permissions/modes.py`` so the upstream mode
resolution stays free of F-47-specific config aggregation concerns.

Architecture::

    src/permissions/modes.py              ← upstream (calls hooks below)
        ↑ import
    extensions/permissions/perms_reader.py ← this module (F-47 read path)

Two public helpers:

* ``settings_perms_structured_is_explicit()`` — detects whether a
  ``PermissionsConfig`` object carries user-set (non-default) values.
* ``settings_perms()`` — aggregates legacy ``extra["permissions"]`` and
  structured ``PermissionsConfig.to_dict()`` into a single dict, with
  correct precedence.
"""

from __future__ import annotations

from typing import Any


def settings_perms_structured_is_explicit(perms_obj: Any) -> bool:
    """True when :class:`PermissionsConfig` carries any user-set value.

    F-47 (2026-06-02): the structured field is always populated (defaults
    are non-None), so we can't use "field is None" to detect "user set it".
    Instead we look for any value that diverges from the dataclass defaults
    -- a non-empty rules bucket, a behavior key not in the default
    3-behavior skeleton, a non-empty additional_directories, a non-empty
    additional, a default_mode, or allow_bypass_permissions_mode is True.

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


def settings_perms(settings: Any) -> dict[str, Any]:
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
      (see :func:`settings_perms_structured_is_explicit`). The structured
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
    structured_is_explicit = settings_perms_structured_is_explicit(perms_obj)

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


__all__ = [
    "settings_perms_structured_is_explicit",
    "settings_perms",
]
