"""Phase-1 / WI-1.3 — ``HookConfig`` schema additions.

New fields:
  * ``if_condition: str | None`` — permission-rule grammar string. Parsed
    from ``if_condition`` or ``if`` (TS-native) keys in settings.json.
  * ``once: bool`` — remove after first successful execution. Honored by
    Phase-3 session-hook registration only.
  * ``skill_root: str | None`` — set at skill-hook registration time
    (Phase 3); not parsed from settings.json.

Also covers the validator updates in ``validate_hook_configs``: new fields
type-check (``if`` must be string, ``once`` must be bool).
"""

from __future__ import annotations

import json

import pytest

from src.hooks.config_manager import (
    _parse_hook_config,
    load_hooks_from_settings,
    validate_hook_configs,
)
from src.hooks.hook_types import HookConfig, HookSource


class TestHookConfigFields:
    def test_default_values(self):
        c = HookConfig()
        assert c.if_condition is None
        assert c.once is False
        assert c.skill_root is None

    def test_parse_if_condition_snake_case(self):
        c = _parse_hook_config({
            "type": "command", "command": "echo x",
            "if_condition": "Bash(git commit*)",
        })
        assert c.if_condition == "Bash(git commit*)"

    def test_parse_if_condition_ts_native_alias(self):
        # TS-native key: ``if``. Same field, alternate name.
        c = _parse_hook_config({
            "type": "command", "command": "echo x",
            "if": "Bash(git commit*)",
        })
        assert c.if_condition == "Bash(git commit*)"

    def test_parse_once_true(self):
        c = _parse_hook_config({
            "type": "command", "command": "echo x", "once": True,
        })
        assert c.once is True

    def test_parse_once_default_false(self):
        c = _parse_hook_config({"type": "command", "command": "echo x"})
        assert c.once is False

    def test_skill_root_not_parsed_from_settings(self):
        # ``skill_root`` is set at registration time (Phase 3), not at parse
        # time. A settings.json entry that includes ``skill_root`` is
        # ignored — the field stays None.
        c = _parse_hook_config({
            "type": "command", "command": "echo x",
            "skill_root": "/some/skill/dir",
        })
        assert c.skill_root is None


class TestSchemaValidation:
    def test_if_must_be_string(self):
        cfg = {"PreToolUse": [{"type": "command", "command": "x", "if": 42}]}
        errors = validate_hook_configs(cfg)
        if_errors = [e for e in errors if e.field == "if"]
        assert len(if_errors) == 1
        assert "string" in if_errors[0].message.lower()

    def test_once_must_be_bool(self):
        cfg = {"PreToolUse": [{"type": "command", "command": "x", "once": "yes"}]}
        errors = validate_hook_configs(cfg)
        once_errors = [e for e in errors if e.field == "once"]
        assert len(once_errors) == 1
        assert "boolean" in once_errors[0].message.lower()

    def test_valid_config_no_errors(self):
        cfg = {"PreToolUse": [{
            "type": "command", "command": "x",
            "if": "Bash(git*)", "once": True, "matcher": "Bash",
        }]}
        errors = validate_hook_configs(cfg)
        # No new-field errors. Other validation (existing) may flag things
        # but our new fields should not.
        new_field_errors = [e for e in errors if e.field in ("if", "once")]
        assert new_field_errors == []


class TestRoundTripFromSettingsJson:
    @pytest.mark.asyncio
    async def test_load_with_if_and_once_round_trip(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({
            "hooks": {"PreToolUse": [{
                "type": "command", "command": "guard.sh",
                "if": "Bash(git commit*)",
                "once": True,
                "matcher": "Bash",
            }]}
        }))
        snapshot = load_hooks_from_settings(path)
        hook = snapshot.hooks["PreToolUse"][0]
        assert hook.if_condition == "Bash(git commit*)"
        assert hook.once is True
        assert hook.matcher == "Bash"
