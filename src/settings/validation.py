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

    # Permission mode
    if settings.permission_mode not in VALID_PERMISSION_MODES:
        errors.append(ValidationError(
            field="permission_mode",
            message=f"Invalid permission mode: {settings.permission_mode!r}",
            value=settings.permission_mode,
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

    # Permission rules
    for i, rule in enumerate(settings.permissions):
        if not rule.tool:
            errors.append(ValidationError(
                field=f"permissions[{i}].tool",
                message="Permission rule must have a 'tool' field",
            ))

    return errors
