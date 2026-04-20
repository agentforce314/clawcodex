"""Settings change detection matching TypeScript settings/changeDetector.ts."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from .types import SettingsSchema


@dataclass
class SettingsDiff:
    """Represents differences between two SettingsSchema instances."""
    changed_keys: list[str] = field(default_factory=list)
    old_values: dict[str, Any] = field(default_factory=dict)
    new_values: dict[str, Any] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return len(self.changed_keys) > 0


class SettingsChangeDetector:
    """Detects changes between settings snapshots."""

    def __init__(self) -> None:
        self._previous: dict[str, Any] | None = None

    def snapshot(self, settings: SettingsSchema) -> None:
        """Take a snapshot of current settings."""
        self._previous = dataclasses.asdict(settings)

    def compute_diff(self, current: SettingsSchema) -> SettingsDiff:
        """Compute diff between stored snapshot and *current*."""
        current_dict = dataclasses.asdict(current)
        if self._previous is None:
            return SettingsDiff()

        return _diff_dicts(self._previous, current_dict)

    def detect_and_update(self, current: SettingsSchema) -> SettingsDiff:
        """Compute diff, then update the stored snapshot."""
        diff = self.compute_diff(current)
        self.snapshot(current)
        return diff


def _diff_dicts(
    old: dict[str, Any],
    new: dict[str, Any],
    prefix: str = "",
) -> SettingsDiff:
    """Recursively diff two flat/nested dicts."""
    changed: list[str] = []
    old_vals: dict[str, Any] = {}
    new_vals: dict[str, Any] = {}

    all_keys = set(old.keys()) | set(new.keys())
    for key in sorted(all_keys):
        full_key = f"{prefix}.{key}" if prefix else key
        ov = old.get(key)
        nv = new.get(key)
        if isinstance(ov, dict) and isinstance(nv, dict):
            sub = _diff_dicts(ov, nv, full_key)
            changed.extend(sub.changed_keys)
            old_vals.update(sub.old_values)
            new_vals.update(sub.new_values)
        elif ov != nv:
            changed.append(full_key)
            old_vals[full_key] = ov
            new_vals[full_key] = nv

    return SettingsDiff(changed_keys=changed, old_values=old_vals, new_values=new_vals)
