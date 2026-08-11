from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .types import PermissionMode


PermissionProfileName = Literal["parity", "full-access", "managed"]


@dataclass(frozen=True)
class PermissionProfile:
    """Permission profile contract for Phase C — Permission x Execution.

    Profiles describe the launch posture above the lower-level PermissionMode.
    The mode still drives the permission engine; the profile decides which
    floor and bypass affordances a launch is allowed to start with.
    """

    name: PermissionProfileName
    default_mode: PermissionMode
    implicit_full_access: bool
    bypass_available_by_default: bool
    bypass_selectable_by_default: bool
    repo_settings_can_loosen: bool = False
    pre_trust_project_execution: bool = False


PERMISSION_PROFILES: dict[PermissionProfileName, PermissionProfile] = {
    "parity": PermissionProfile(
        name="parity",
        default_mode="default",
        implicit_full_access=False,
        bypass_available_by_default=False,
        bypass_selectable_by_default=False,
    ),
    "full-access": PermissionProfile(
        name="full-access",
        default_mode="bypassPermissions",
        implicit_full_access=True,
        bypass_available_by_default=False,
        bypass_selectable_by_default=True,
    ),
    "managed": PermissionProfile(
        name="managed",
        default_mode="default",
        implicit_full_access=False,
        bypass_available_by_default=False,
        bypass_selectable_by_default=False,
    ),
}


def permission_profile_from_string(
    value: str | None,
    *,
    default: PermissionProfileName = "parity",
) -> PermissionProfile:
    """Return a profile by name, accepting common spelling variants."""

    if value is None:
        return PERMISSION_PROFILES[default]
    normalized = value.strip().lower().replace("_", "-")
    if normalized == "fullaccess":
        normalized = "full-access"
    if normalized in PERMISSION_PROFILES:
        return PERMISSION_PROFILES[normalized]  # type: ignore[index]
    return PERMISSION_PROFILES[default]


def resolve_permission_profile(
    value: str | None,
    *,
    implicit_full_access: bool,
) -> PermissionProfile:
    """Resolve the effective profile for a launch.

    ``None`` preserves the historical surface contract: interactive launches
    are full-access by default, non-interactive launches are parity/default.
    Explicit values pin the posture regardless of the surface default.
    """

    if value is not None:
        return permission_profile_from_string(value)
    return PERMISSION_PROFILES["full-access" if implicit_full_access else "parity"]
