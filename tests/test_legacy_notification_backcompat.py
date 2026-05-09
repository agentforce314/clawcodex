"""Phase-1 / WI-1.1 — legacy ``Notification + matcher`` back-compat reader.

Per assumption A1 + critic confirm: ``SessionStart`` / ``SessionEnd`` /
``PreCompact`` / ``PostCompact`` are first-class events post-Phase-1, but
existing settings.json files using the legacy form
(``Notification + matcher: "onSessionStart"``) must keep working for one
CHANGELOG cycle.

The team-lead specifically requested:
  > Back-compat reader test: a settings.json with `Notification + matcher:
  > SessionStart` still routes to SessionStart handler.
"""

from __future__ import annotations

import json
import warnings

import pytest

from src.hooks.config_manager import load_hooks_from_settings


class TestLegacyMatcherTranslation:
    def test_on_session_start_matcher_translates_to_first_class(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({
            "hooks": {"Notification": [{
                "type": "command", "command": "echo start",
                "matcher": "onSessionStart",
            }]}
        }))
        with pytest.warns(DeprecationWarning, match="onSessionStart"):
            snapshot = load_hooks_from_settings(path)
        # The hook is now under SessionStart, NOT under Notification.
        assert "SessionStart" in snapshot.hooks
        assert snapshot.hooks["SessionStart"][0].command == "echo start"
        # And it's NOT under Notification anymore (translated, not duplicated).
        assert "Notification" not in snapshot.hooks or not snapshot.hooks.get("Notification")

    def test_on_session_end_matcher_translates(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({
            "hooks": {"Notification": [{
                "type": "command", "command": "echo end",
                "matcher": "onSessionEnd",
            }]}
        }))
        with pytest.warns(DeprecationWarning, match="onSessionEnd"):
            snapshot = load_hooks_from_settings(path)
        assert "SessionEnd" in snapshot.hooks
        assert snapshot.hooks["SessionEnd"][0].command == "echo end"

    def test_on_compact_matcher_translates_to_pre_compact(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({
            "hooks": {"Notification": [{
                "type": "command", "command": "echo c",
                "matcher": "onCompact",
            }]}
        }))
        with pytest.warns(DeprecationWarning, match="onCompact"):
            snapshot = load_hooks_from_settings(path)
        # Convention: legacy ``onCompact`` → ``PreCompact`` (about-to-happen).
        assert "PreCompact" in snapshot.hooks

    def test_bare_event_name_as_matcher_also_translates(self, tmp_path):
        # Team-lead's literal example: ``matcher: "SessionStart"`` (without
        # the ``on`` prefix). Both forms translate to the same first-class
        # event.
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({
            "hooks": {"Notification": [{
                "type": "command", "command": "echo s",
                "matcher": "SessionStart",
            }]}
        }))
        with pytest.warns(DeprecationWarning, match="SessionStart"):
            snapshot = load_hooks_from_settings(path)
        assert "SessionStart" in snapshot.hooks
        assert snapshot.hooks["SessionStart"][0].command == "echo s"

    def test_notification_without_matching_matcher_stays_under_notification(self, tmp_path):
        # A genuine ``Notification`` hook with a non-lifecycle matcher (e.g.,
        # ``onPermissionRequest`` — not in the legacy lifecycle map) is NOT
        # translated; it stays under Notification.
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({
            "hooks": {"Notification": [{
                "type": "command", "command": "echo n",
                "matcher": "onPermissionRequest",
            }]}
        }))
        # No deprecation warning expected.
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            snapshot = load_hooks_from_settings(path)
        deprecations = [w for w in captured if issubclass(w.category, DeprecationWarning)]
        assert deprecations == []
        assert "Notification" in snapshot.hooks
        assert snapshot.hooks["Notification"][0].matcher == "onPermissionRequest"

    def test_first_class_event_no_warning(self, tmp_path):
        # Settings.json using first-class names directly: no deprecation
        # warning fires (the warning is only for the legacy form).
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({
            "hooks": {"SessionStart": [{
                "type": "command", "command": "echo modern",
            }]}
        }))
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            snapshot = load_hooks_from_settings(path)
        deprecations = [w for w in captured if issubclass(w.category, DeprecationWarning)]
        assert deprecations == []
        assert "SessionStart" in snapshot.hooks

    def test_multiple_legacy_entries_each_warn(self, tmp_path):
        # Two legacy entries → two warnings.
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({
            "hooks": {"Notification": [
                {"type": "command", "command": "echo a", "matcher": "onSessionStart"},
                {"type": "command", "command": "echo b", "matcher": "onSessionEnd"},
            ]}
        }))
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            snapshot = load_hooks_from_settings(path)
        deprecations = [w for w in captured if issubclass(w.category, DeprecationWarning)]
        assert len(deprecations) == 2
        assert "SessionStart" in snapshot.hooks
        assert "SessionEnd" in snapshot.hooks


class TestLifecycleRoutersReadFirstClass:
    """The lifecycle routers (``run_session_start_hooks`` etc.) now read
    from first-class events, not from ``Notification + matcher``.
    """

    @pytest.mark.asyncio
    async def test_session_start_router_reads_first_class_event(self):
        from src.hooks.session_hooks import SESSION_START_EVENT
        # The constant is now ``SessionStart`` (first-class), not ``Notification``.
        assert SESSION_START_EVENT == "SessionStart"

    @pytest.mark.asyncio
    async def test_session_end_router_reads_first_class_event(self):
        from src.hooks.session_hooks import SESSION_END_EVENT
        assert SESSION_END_EVENT == "SessionEnd"

    @pytest.mark.asyncio
    async def test_compact_router_reads_pre_compact(self):
        from src.hooks.session_hooks import COMPACT_EVENT
        assert COMPACT_EVENT == "PreCompact"
