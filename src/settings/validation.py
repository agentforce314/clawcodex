"""Settings validation matching TypeScript settings/validation.ts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .constants import VALID_EFFORT_VALUES, VALID_OUTPUT_STYLES, VALID_PERMISSION_MODES
from .types import SettingsSchema


@dataclass
class ValidationError:
    """A single validation error."""
    field: str
    message: str
    value: Any = None


def _effective_default_permission_mode(settings: SettingsSchema) -> str | None:
    """Resolve the effective default permission mode from F-47 channels.

    Priority:

    1. ``settings.permissions.default_mode`` (preferred, structured).
    2. Top-level ``settings.permission_mode`` (back-compat reading channel
       kept for older binaries that wrote the mode outside the
       ``permissions`` block).
    """
    pc = settings.permissions
    default_mode = getattr(pc, "default_mode", None)
    if default_mode:
        return default_mode
    legacy = (settings.permission_mode or "").strip()
    return legacy or None


def validate_settings(settings: SettingsSchema) -> list[ValidationError]:
    """Validate a SettingsSchema, returning a list of errors (empty = valid)."""
    errors: list[ValidationError] = []

    # Effort
    if settings.effort and settings.effort not in VALID_EFFORT_VALUES:
        errors.append(ValidationError(
            field="effort",
            message=f"Invalid effort value: {settings.effort!r}. Must be one of {VALID_EFFORT_VALUES}",
            value=settings.effort,
        ))

    # F-47: permission default mode is read from ``permissions.default_mode``
    # first, then the top-level ``permission_mode`` (back-compat). Empty
    # strings on both are treated as "unset" and skip the enum check --
    # this avoids F-47 defaulting `permission_mode` to ``""`` from being
    # reported as an invalid mode.
    effective_default_mode = _effective_default_permission_mode(settings)
    if effective_default_mode is not None and effective_default_mode not in VALID_PERMISSION_MODES:
        errors.append(ValidationError(
            field="permissions.defaultMode",
            message=f"Invalid default permission mode: {effective_default_mode!r}",
            value=effective_default_mode,
        ))

    # Output style
    if settings.output_style.style not in VALID_OUTPUT_STYLES:
        errors.append(ValidationError(
            field="output_style.style",
            message=f"Invalid output style: {settings.output_style.style!r}",
            value=settings.output_style.style,
        ))

    # Max width
    if settings.output_style.max_width < 40:
        errors.append(ValidationError(
            field="output_style.max_width",
            message="max_width must be >= 40",
            value=settings.output_style.max_width,
        ))

    # Max turns (0 = unlimited, otherwise must be positive)
    if settings.max_turns < 0:
        errors.append(ValidationError(
            field="max_turns",
            message="max_turns must be >= 0",
            value=settings.max_turns,
        ))

    # Max cost
    if settings.max_cost_usd < 0:
        errors.append(ValidationError(
            field="max_cost_usd",
            message="max_cost_usd must be >= 0",
            value=settings.max_cost_usd,
        ))

    # Session retention
    if settings.session_retention_days < 1:
        errors.append(ValidationError(
            field="session_retention_days",
            message="session_retention_days must be >= 1",
            value=settings.session_retention_days,
        ))

    # Compact threshold
    if settings.compact.threshold_tokens < 1000:
        errors.append(ValidationError(
            field="compact.threshold_tokens",
            message="compact threshold must be >= 1000",
            value=settings.compact.threshold_tokens,
        ))

    # Hooks timeout
    if settings.hooks.timeout_ms < 1000:
        errors.append(ValidationError(
            field="hooks.timeout_ms",
            message="hooks timeout must be >= 1000ms",
            value=settings.hooks.timeout_ms,
        ))

    # F-47: permissions.rules is a dict[str, list[str]] (allow/deny/ask).
    # The legacy list[PermissionRule] path is gone (Sub-H); rule strings
    # are kept verbatim on disk and validated as non-empty here.
    rules = settings.permissions.rules
    for behavior in ("allow", "deny", "ask"):
        bucket = rules.get(behavior, [])
        if not isinstance(bucket, list):
            errors.append(ValidationError(
                field=f"permissions.rules.{behavior}",
                message=f"permissions.rules.{behavior} must be a list",
                value=bucket,
            ))
            continue
        for j, rule_str in enumerate(bucket):
            if not isinstance(rule_str, str) or not rule_str.strip():
                errors.append(ValidationError(
                    field=f"permissions.rules.{behavior}[{j}]",
                    message="Rule must be a non-empty string",
                    value=rule_str,
                ))

    return errors
