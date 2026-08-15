"""`permissions.defaultMode` lives in config.json, not a second settings file.

`config.json` -> `settings.permissions` was already the source of truth for
`allowBypassPermissionsMode` / `disableBypassPermissionsMode`, so the MODE
belongs beside them. The standalone `~/.clawcodex/settings.json` stays
readable so an existing choice keeps working, but nothing writes it any more.

The user-visible bug behind this: Full Access is only a FLOOR, and a
persisted defaultMode outranks it — so a mode stored in a file the user did
not know existed made an interactive session silently ask for approval.
"""

from __future__ import annotations

import json

import pytest

from src.permissions.modes import (
    read_settings_default_mode,
    resolve_interactive_permission_state,
    set_settings_default_mode,
)


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Point config.json at a temp file and clear the manager cache.

    Invalidated on the way OUT as well as in: the manager is a process-wide
    singleton, so leaving it holding this temp config after monkeypatch
    restores the real path hands the next test someone else's config-home.
    """
    path = tmp_path / "config.json"
    path.write_text("{}")
    monkeypatch.setattr("src.config.get_global_config_path", lambda: path)
    import src.config as config_mod

    config_mod._get_default_manager().invalidate()
    try:
        yield path
    finally:
        config_mod._get_default_manager().invalidate()


def _write(path, perms):
    path.write_text(json.dumps({"settings": {"permissions": perms}}))
    import src.config as config_mod

    config_mod._get_default_manager().invalidate()


def test_reads_the_mode_from_config_json(config_file) -> None:
    _write(config_file, {"defaultMode": "acceptEdits"})

    assert read_settings_default_mode(None) == "acceptEdits"


def test_writes_the_mode_into_config_json(config_file) -> None:
    assert set_settings_default_mode("plan") is True

    saved = json.loads(config_file.read_text())
    assert saved["settings"]["permissions"]["defaultMode"] == "plan"


def test_a_write_keeps_the_neighbouring_bypass_flags(config_file) -> None:
    _write(config_file, {"allowBypassPermissionsMode": True})

    set_settings_default_mode("default")

    perms = json.loads(config_file.read_text())["settings"]["permissions"]
    assert perms["defaultMode"] == "default"
    assert perms["allowBypassPermissionsMode"] is True


def test_a_rule_list_is_not_clobbered(config_file) -> None:
    """`permissions` doubles as a flat rule LIST in the schema — a mode write
    must start a dict rather than overwrite whatever rules were there."""
    config_file.write_text(json.dumps({"settings": {"permissions": [{"tool": "Bash"}]}}))
    import src.config as config_mod

    config_mod._get_default_manager().invalidate()

    assert read_settings_default_mode(None) is None
    assert set_settings_default_mode("plan") is True
    assert json.loads(config_file.read_text())["settings"]["permissions"]["defaultMode"] == "plan"


def test_nothing_configured_leaves_full_access_standing(config_file) -> None:
    mode, _, _ = resolve_interactive_permission_state(
        permission_mode_cli=None,
        dangerously_skip_permissions=False,
        allow_dangerously_skip_permissions=False,
        cwd=None,
    )

    assert mode == "bypassPermissions"


def test_a_stored_mode_still_outranks_the_full_access_floor(config_file) -> None:
    """The exact shape of the reported bug, now in the file people can find."""
    _write(config_file, {"defaultMode": "default"})

    mode, _, _ = resolve_interactive_permission_state(
        permission_mode_cli=None,
        dangerously_skip_permissions=False,
        allow_dangerously_skip_permissions=False,
        cwd=None,
    )

    assert mode == "default"


def test_a_later_settings_writer_does_not_revert_the_mode(config_file) -> None:
    """Regression: the whole-tier writers (set_recap_enabled, set_effort, …)
    read-modify-write through the process-wide manager. When that manager's
    cache predated a defaultMode write (made by what used to be a private
    ConfigManager), saving the mutation wrote the PRE-defaultMode state back —
    the web settings page set "Ask every time", flipped the recap toggle, and
    the permission default silently reverted to Full Access."""
    import src.config as config_mod

    # Warm the shared cache the way a running agent does (boot-time reads).
    config_mod.load_config()

    assert set_settings_default_mode("default") is True

    config_mod.set_recap_enabled(False)

    saved = json.loads(config_file.read_text())
    assert saved["settings"]["recap_enabled"] is False
    assert saved["settings"]["permissions"]["defaultMode"] == "default"

    # And the same coherence the other way around.
    config_mod.set_effort("high")
    assert set_settings_default_mode("plan") is True

    saved = json.loads(config_file.read_text())
    assert saved["settings"]["effort"] == "high"
    assert saved["settings"]["permissions"]["defaultMode"] == "plan"
