"""Frontmatter field validators shared across agent / skill / output-style loaders.

Mirrors helpers from typescript/src/utils/frontmatterParser.ts,
typescript/src/utils/effort.ts, and
typescript/src/utils/permissions/PermissionMode.ts.

Each parser is fail-open: invalid values log a debug warning and return
``None`` (or an empty list) rather than raising, so a single malformed
frontmatter field never prevents a config file from loading.
"""
from __future__ import annotations

import logging
from typing import Any

from src.permissions.types import EXTERNAL_PERMISSION_MODES, ExternalPermissionMode

logger = logging.getLogger(__name__)

EFFORT_LEVELS: frozenset[str] = frozenset({"low", "medium", "high", "max"})


def parse_effort_value(value: Any) -> str | None:
    """Port of TS ``parseEffortValue`` (typescript/src/utils/effort.ts).

    Accepts:
      * One of ``EFFORT_LEVELS`` (case-insensitive) → returned lowercased.
      * An int or numeric string → returned as a stringified integer.
    Anything else logs a warning and returns ``None``.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        logger.warning("frontmatter effort=%r is not a valid level", value)
        return None
    if isinstance(value, int):
        return str(value)
    s = str(value).strip().lower()
    if not s:
        return None
    if s in EFFORT_LEVELS:
        return s
    try:
        return str(int(s, 10))
    except (TypeError, ValueError):
        pass
    logger.warning(
        "frontmatter effort=%r is not a valid level (expected one of %s or an integer)",
        value,
        sorted(EFFORT_LEVELS),
    )
    return None


def parse_positive_int(value: Any) -> int | None:
    """Port of TS ``parsePositiveIntFromFrontmatter``.

    Accepts an int or numeric string. Returns ``None`` for missing,
    non-positive, or non-numeric values.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    try:
        n = int(str(value).strip(), 10)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def parse_permission_mode(value: Any) -> ExternalPermissionMode | None:
    """Validate a frontmatter ``permission-mode`` / ``permissionMode`` value.

    Only the external modes (``default | plan | acceptEdits |
    bypassPermissions | dontAsk``) are accepted from user frontmatter — the
    internal ``auto`` / ``bubble`` modes are runtime-only and must not be
    declared on disk.
    """
    if value is None or value == "":
        return None
    s = str(value).strip()
    if s in EXTERNAL_PERMISSION_MODES:
        return s  # type: ignore[return-value]
    logger.warning(
        "frontmatter permission-mode=%r is not recognized (valid: %s)",
        value,
        ", ".join(EXTERNAL_PERMISSION_MODES),
    )
    return None


def parse_string_list(value: Any, *, csv_ok: bool = True) -> list[str]:
    """Coerce a YAML frontmatter value into a ``list[str]``.

    Accepts:
      * A list of strings → kept as-is (non-string entries skipped).
      * A scalar string → split on commas when ``csv_ok=True``, else
        wrapped in a single-element list.
      * ``None`` or empty → ``[]``.
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    s = str(value).strip()
    if not s:
        return []
    if csv_ok:
        return [part.strip() for part in s.split(",") if part.strip()]
    return [s]


def parse_hooks(value: Any, *, owner_name: str) -> dict[str, Any] | None:
    """Validate a frontmatter ``hooks:`` block.

    Mirrors the shape check from src/skills/loader.py:_coerce_hooks and the
    TS ``HooksSchema`` validation in loadAgentsDir.ts. Returns the dict on
    shape-match; ``None`` (with a debug log) on any structural mismatch so
    the caller can keep loading the agent without hooks rather than crashing.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        logger.debug(
            "%s hooks: expected dict, got %s; dropping",
            owner_name, type(value).__name__,
        )
        return None

    try:
        from src.hooks.hook_types import ALL_HOOK_EVENTS
        valid_events = set(ALL_HOOK_EVENTS)
    except Exception:
        valid_events = set()

    for event_name, matchers in value.items():
        if valid_events and event_name not in valid_events:
            logger.debug(
                "%s hooks: unknown event %r; dropping all hooks",
                owner_name, event_name,
            )
            return None
        if not isinstance(matchers, list):
            logger.debug(
                "%s hooks.%s: expected list of matchers, got %s",
                owner_name, event_name, type(matchers).__name__,
            )
            return None
        for matcher in matchers:
            if not isinstance(matcher, dict):
                logger.debug(
                    "%s hooks.%s: matcher must be a dict",
                    owner_name, event_name,
                )
                return None
            inner = matcher.get("hooks")
            if not isinstance(inner, list):
                logger.debug(
                    "%s hooks.%s.hooks: required list missing or wrong type",
                    owner_name, event_name,
                )
                return None
            for cmd in inner:
                if not isinstance(cmd, dict) or "type" not in cmd:
                    logger.debug(
                        "%s hooks.%s.hooks[]: each entry needs a `type` field",
                        owner_name, event_name,
                    )
                    return None
    return value
