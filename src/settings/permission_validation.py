"""Permission rule validation matching TypeScript settings/permissionValidation.ts."""

from __future__ import annotations

import re
from typing import Any

from .types import PermissionRule


def validate_permission_rules(rules: list[PermissionRule]) -> list[str]:
    """Validate a list of permission rules, returning error messages."""
    errors: list[str] = []

    for i, rule in enumerate(rules):
        prefix = f"permissions[{i}]"

        if not rule.tool:
            errors.append(f"{prefix}: 'tool' is required")

        if rule.regex:
            try:
                re.compile(rule.regex)
            except re.error as e:
                errors.append(f"{prefix}: invalid regex {rule.regex!r}: {e}")

        if rule.glob and rule.regex:
            errors.append(f"{prefix}: cannot specify both 'glob' and 'regex'")

    # Check for duplicate rules (same tool + same glob/regex)
    seen: set[str] = set()
    for i, rule in enumerate(rules):
        key = f"{rule.tool}|{rule.glob or ''}|{rule.regex or ''}"
        if key in seen:
            errors.append(f"permissions[{i}]: duplicate rule for tool={rule.tool!r}")
        seen.add(key)

    return errors
