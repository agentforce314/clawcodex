"""Chapter C12 — the disableBypassPermissionsMode negative guard.

The port's is_bypass_available dropped TS's two negative guards
(permissionSetup.ts:941-946): (bypass-requested OR allow-key) AND NOT
growthBookDisable AND NOT settingsDisable. The local settings-key guard has a
LIVE trigger in the open build (managed/user/local settings.json), so an
operator locking bypass down via permissions.disableBypassPermissionsMode:
"disable" was SILENTLY IGNORED — bypass stayed available (fail-open). This
pins is_bypass_permissions_mode_disabled() reading the trusted + managed +
project tiers.
"""
from __future__ import annotations

import json

import pytest

from src.permissions.modes import is_bypass_permissions_mode_disabled


def _patch_tiers(monkeypatch, *, glob=None, local=None, project=None, managed_path=None):
    """Patch the ConfigManager tier loaders + the managed path the guard reads."""
    def _cfg(perms):
        return {"settings": {"permissions": perms}} if perms is not None else {}
    monkeypatch.setattr("src.config.ConfigManager.load_global", lambda self: _cfg(glob))
    monkeypatch.setattr("src.config.ConfigManager.load_local", lambda self: _cfg(local))
    monkeypatch.setattr("src.config.ConfigManager.load_project", lambda self: _cfg(project))
    monkeypatch.setattr(
        "src.settings.managed_path.resolve_managed_settings_path", lambda: managed_path
    )


class TestDisableGuard:
    def test_absent_is_false(self, monkeypatch):
        _patch_tiers(monkeypatch)
        assert is_bypass_permissions_mode_disabled() is False

    def test_global_disable_honored(self, monkeypatch):
        _patch_tiers(monkeypatch, glob={"disableBypassPermissionsMode": "disable"})
        assert is_bypass_permissions_mode_disabled() is True

    def test_local_disable_honored(self, monkeypatch):
        _patch_tiers(monkeypatch, local={"disableBypassPermissionsMode": "disable"})
        assert is_bypass_permissions_mode_disabled() is True

    def test_project_disable_honored(self, monkeypatch):
        # a disable only RESTRICTS, so honoring the (committable) project tier
        # is safe — unlike the positive allow-key which excludes it.
        _patch_tiers(monkeypatch, project={"disableBypassPermissionsMode": "disable"})
        assert is_bypass_permissions_mode_disabled() is True

    def test_non_disable_value_ignored(self, monkeypatch):
        # TS checks === 'disable' exactly; any other value is not a lockdown
        _patch_tiers(monkeypatch, glob={"disableBypassPermissionsMode": "allow"})
        assert is_bypass_permissions_mode_disabled() is False

    def test_managed_policy_disable_honored(self, tmp_path, monkeypatch):
        managed = tmp_path / "managed.json"
        managed.write_text(json.dumps({"permissions": {"disableBypassPermissionsMode": "disable"}}))
        _patch_tiers(monkeypatch, managed_path=managed)
        assert is_bypass_permissions_mode_disabled() is True


class TestBypassAvailabilityWiring:
    """The guard must actually flip is_bypass_available OFF — even under an
    explicit --dangerously-skip-permissions (TS's unconditional
    `&& !settingsDisableBypassPermissionsMode`)."""

    def test_disable_overrides_dangerously(self, monkeypatch):
        import types

        import src.cli as cli

        monkeypatch.setattr(
            "src.permissions.modes.has_allow_bypass_permissions_mode", lambda: False
        )
        monkeypatch.setattr(
            "src.permissions.modes.is_bypass_permissions_mode_disabled", lambda: True
        )
        monkeypatch.setattr(
            "src.permissions.enforce_dangerous_skip_permissions_safety",
            lambda **k: None, raising=False,
        )
        args = types.SimpleNamespace(
            dangerously_skip_permissions=True,
            allow_dangerously_skip_permissions=False,
            permission_mode=None,
        )
        cli._resolve_permission_state(args)
        assert args._resolved_is_bypass_available is False  # disabled wins

    def test_available_when_not_disabled(self, monkeypatch):
        import types

        import src.cli as cli

        monkeypatch.setattr(
            "src.permissions.modes.has_allow_bypass_permissions_mode", lambda: False
        )
        monkeypatch.setattr(
            "src.permissions.modes.is_bypass_permissions_mode_disabled", lambda: False
        )
        monkeypatch.setattr(
            "src.permissions.enforce_dangerous_skip_permissions_safety",
            lambda **k: None, raising=False,
        )
        args = types.SimpleNamespace(
            dangerously_skip_permissions=True,
            allow_dangerously_skip_permissions=False,
            permission_mode=None,
        )
        cli._resolve_permission_state(args)
        assert args._resolved_is_bypass_available is True
