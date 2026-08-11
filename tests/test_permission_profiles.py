from __future__ import annotations

from src.permissions.profiles import (
    PERMISSION_PROFILES,
    permission_profile_from_string,
    resolve_permission_profile,
)
from src.permissions.modes import resolve_interactive_permission_state


def _resolve(**kw):
    kw.setdefault("permission_mode_cli", None)
    kw.setdefault("dangerously_skip_permissions", False)
    kw.setdefault("allow_dangerously_skip_permissions", False)
    return resolve_interactive_permission_state(**kw)


def test_profile_catalog_matches_phase_c_names():
    assert tuple(PERMISSION_PROFILES) == ("parity", "full-access", "managed")
    assert PERMISSION_PROFILES["parity"].default_mode == "default"
    assert PERMISSION_PROFILES["full-access"].default_mode == "bypassPermissions"
    assert PERMISSION_PROFILES["managed"].default_mode == "default"


def test_profile_name_parser_accepts_spelling_variants():
    assert permission_profile_from_string("full_access").name == "full-access"
    assert permission_profile_from_string("fullAccess").name == "full-access"
    assert permission_profile_from_string("PARITY").name == "parity"
    assert permission_profile_from_string("unknown").name == "parity"


def test_none_profile_preserves_existing_surface_default():
    assert resolve_permission_profile(None, implicit_full_access=True).name == "full-access"
    assert resolve_permission_profile(None, implicit_full_access=False).name == "parity"


def test_parity_profile_forces_default_floor_even_interactively():
    mode, available, selectable = _resolve(
        implicit_full_access=True,
        permission_profile="parity",
    )
    assert (mode, available, selectable) == ("default", False, False)


def test_full_access_profile_can_raise_noninteractive_floor():
    mode, available, selectable = _resolve(
        implicit_full_access=False,
        permission_profile="full-access",
    )
    assert (mode, available, selectable) == ("bypassPermissions", False, True)


def test_managed_profile_disables_bypass_even_when_flags_request_it():
    mode, available, selectable = _resolve(
        permission_profile="managed",
        implicit_full_access=True,
        permission_mode_cli="bypassPermissions",
        dangerously_skip_permissions=True,
        allow_dangerously_skip_permissions=True,
    )
    assert (mode, available, selectable) == ("default", False, False)
