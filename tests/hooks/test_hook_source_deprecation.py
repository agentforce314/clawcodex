"""Phase-1 / WI-1.2 — ``HookSource`` deprecation alias regression tests.

Verifies the ``EnumMeta``-level ``__getattr__`` mechanism (N3 critic-resolved):
  * Legacy aliases ``SETTINGS`` / ``POLICY`` / ``PLUGINS`` resolve to the
    canonical values ``USER_SETTINGS`` / ``POLICY_SETTINGS`` / ``PLUGIN_HOOK``.
  * Each alias access emits a single ``DeprecationWarning``.
  * Identity comparison works (``HookSource.SETTINGS is HookSource.USER_SETTINGS``)
    so existing call sites that compare via ``is`` keep working through the
    deprecation cycle.
  * Truly unknown names raise ``AttributeError``.
  * Dunder access (``HookSource.__name__`` etc.) does NOT emit warnings.
"""

from __future__ import annotations

import warnings

import pytest

from src.hooks.hook_types import HookSource


class TestDeprecationAlias:
    def test_settings_alias_resolves_to_user_settings(self):
        with pytest.warns(DeprecationWarning, match="HookSource.SETTINGS is deprecated"):
            value = HookSource.SETTINGS
        assert value is HookSource.USER_SETTINGS

    def test_policy_alias_resolves_to_policy_settings(self):
        with pytest.warns(DeprecationWarning, match="HookSource.POLICY is deprecated"):
            value = HookSource.POLICY
        assert value is HookSource.POLICY_SETTINGS

    def test_plugins_alias_resolves_to_plugin_hook(self):
        with pytest.warns(DeprecationWarning, match="HookSource.PLUGINS is deprecated"):
            value = HookSource.PLUGINS
        assert value is HookSource.PLUGIN_HOOK

    def test_alias_emits_one_warning_per_access(self):
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            _ = HookSource.SETTINGS
        deprecations = [w for w in captured if issubclass(w.category, DeprecationWarning)]
        assert len(deprecations) == 1

    def test_alias_value_is_canonical(self):
        # Round-trip: alias .value == canonical .value because ``is`` returns
        # the same enum member.
        with pytest.warns(DeprecationWarning):
            settings_via_alias = HookSource.SETTINGS
        assert settings_via_alias.value == "userSettings"

    def test_unknown_attribute_raises(self):
        with pytest.raises(AttributeError):
            _ = HookSource.NONEXISTENT

    def test_dunder_access_does_not_warn(self):
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            # Class-level dunder access goes through metaclass __getattr__;
            # our override delegates to ``EnumMeta.__getattr__`` for dunders
            # so no warning fires.
            _ = HookSource.__name__
            _ = HookSource.__members__
        assert all(
            not issubclass(w.category, DeprecationWarning) for w in captured
        )

    def test_canonical_names_no_warning(self):
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            _ = HookSource.USER_SETTINGS
            _ = HookSource.POLICY_SETTINGS
            _ = HookSource.PLUGIN_HOOK
            _ = HookSource.PROJECT_SETTINGS
            _ = HookSource.LOCAL_SETTINGS
            _ = HookSource.SESSION_HOOK
        deprecations = [w for w in captured if issubclass(w.category, DeprecationWarning)]
        assert deprecations == []
