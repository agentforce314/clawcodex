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


class TestModeResolution:
    """The FAITHFUL fix (critic C12 re-review): the disable must reset the
    MODE, not just the availability boolean — the port's check.py:456 bypasses
    on mode=="bypassPermissions" ALONE, so a lockdown that only flips the
    boolean is defeated at runtime."""

    def test_disable_skips_bypass_mode(self, monkeypatch):
        from src.permissions.modes import initial_permission_mode_from_cli

        monkeypatch.setattr(
            "src.permissions.modes.is_bypass_permissions_mode_disabled", lambda: True
        )
        # --dangerously-skip-permissions under a lockdown → NOT bypass
        assert initial_permission_mode_from_cli(dangerously_skip_permissions=True) == "default"

    def test_no_disable_keeps_bypass_mode(self, monkeypatch):
        from src.permissions.modes import initial_permission_mode_from_cli

        monkeypatch.setattr(
            "src.permissions.modes.is_bypass_permissions_mode_disabled", lambda: False
        )
        assert initial_permission_mode_from_cli(dangerously_skip_permissions=True) == "bypassPermissions"

    def test_disable_falls_through_to_explicit_mode(self, monkeypatch):
        from src.permissions.modes import initial_permission_mode_from_cli

        monkeypatch.setattr(
            "src.permissions.modes.is_bypass_permissions_mode_disabled", lambda: True
        )
        # bypass skipped → the next candidate (--permission-mode) wins
        assert initial_permission_mode_from_cli(
            dangerously_skip_permissions=True, permission_mode_cli="plan"
        ) == "plan"


class TestEndToEndEnforcement:
    """Drive the REAL enforcement (has_permissions_to_use_tool) — the critic's
    demand: prove a tool is NOT auto-allowed under --dangerously + a lockdown,
    not just that an intermediate boolean flipped."""

    def _mock_tool(self):
        from types import SimpleNamespace
        return SimpleNamespace(
            name="Bash",
            check_permissions=lambda *a, **k: SimpleNamespace(
                behavior="ask", decision_reason=None, rule_suggestions=None,
                updated_input=None, message=None,
            ),
            is_read_only=lambda *a, **k: False,
        )

    def test_dangerously_plus_disable_does_not_auto_allow(self, monkeypatch):
        from src.permissions.check import has_permissions_to_use_tool
        from src.permissions.modes import initial_permission_mode_from_cli
        from src.permissions.types import ToolPermissionContext

        monkeypatch.setattr(
            "src.permissions.modes.is_bypass_permissions_mode_disabled", lambda: True
        )
        # the resolved mode under --dangerously + lockdown must be default
        mode = initial_permission_mode_from_cli(dangerously_skip_permissions=True)
        assert mode == "default"
        # and default mode does NOT bypass — the tool's own ask stands
        ctx = ToolPermissionContext(mode=mode)
        result = has_permissions_to_use_tool(self._mock_tool(), {}, ctx)
        assert result.behavior != "allow", "lockdown defeated — tool auto-allowed!"

    def test_dangerously_without_disable_still_bypasses(self, monkeypatch):
        from src.permissions.check import has_permissions_to_use_tool
        from src.permissions.modes import initial_permission_mode_from_cli
        from src.permissions.types import ToolPermissionContext

        monkeypatch.setattr(
            "src.permissions.modes.is_bypass_permissions_mode_disabled", lambda: False
        )
        mode = initial_permission_mode_from_cli(dangerously_skip_permissions=True)
        assert mode == "bypassPermissions"
        ctx = ToolPermissionContext(mode=mode)
        result = has_permissions_to_use_tool(self._mock_tool(), {}, ctx)
        assert result.behavior == "allow"  # bypass still works when not disabled
