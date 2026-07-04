"""SKILLS-2 — the ``/update-config`` bundled skill (ADAPTED port).

updateConfig.ts is a settings-editing skill; ported ADAPTED (hand-authored,
grounded in the port's real on-disk loaders) rather than verbatim, because a
verbatim port would teach the model to write settings the port cannot parse.
These pin the port-correct topology + shapes and the absence of TS-only /
internal keys.
"""
from __future__ import annotations

import json

import pytest

from src.skills.bundled import init_bundled_skills
from src.skills.bundled.update_config import (
    UPDATE_CONFIG_PROMPT,
    _get_prompt_for_command,
)
from src.skills.bundled_skills import (
    clear_bundled_skills,
    get_bundled_skill_by_name,
)


@pytest.fixture()
def _fresh_registry():
    clear_bundled_skills()
    init_bundled_skills()
    yield
    clear_bundled_skills()


class TestRegistration:
    def test_registered(self, _fresh_registry):
        u = get_bundled_skill_by_name("update-config")
        assert u is not None
        assert u.user_invocable is True
        assert u.allowed_tools == ["Read"]  # N1: read-before-write auto-approve

    def test_args_appended_as_focus(self):
        out = _get_prompt_for_command("allow npm without prompting")
        assert "## User Request\n\nallow npm without prompting" in out
        assert _get_prompt_for_command("") == UPDATE_CONFIG_PROMPT


class TestPortCorrectTopology:
    def test_edit_target_is_clawcodex_not_claude(self):
        p = UPDATE_CONFIG_PROMPT
        assert ".clawcodex/settings.json" in p
        # the ONLY mention of .claude is the explicit "do NOT write here" warning
        assert "Do NOT write to" in p
        assert "`<project>/.claude/settings.json`" in p
        # ~/.claude is never taught as an edit target
        assert "~/.claude/settings.json" not in p

    def test_three_scopes(self):
        p = UPDATE_CONFIG_PROMPT
        assert "~/.clawcodex/settings.json" in p
        assert "<project>/.clawcodex/settings.json" in p
        assert "<project>/.clawcodex/settings.local.json" in p

    def test_config_block_file(self):
        assert "~/.clawcodex/config.json" in UPDATE_CONFIG_PROMPT


class TestPortCorrectShapes:
    def test_five_permission_modes(self):
        p = UPDATE_CONFIG_PROMPT
        for mode in ("default", "plan", "acceptEdits", "bypassPermissions", "dontAsk"):
            assert mode in p, mode

    def test_permissions_allow_deny_ask_strings(self):
        p = UPDATE_CONFIG_PROMPT
        assert '"allow"' in p and '"deny"' in p and '"ask"' in p
        assert "Bash(npm:*)" in p  # a real, parseable rule string
        assert "additionalDirectories" in p

    def test_permission_example_rules_actually_parse(self):
        # the rule strings in the prompt must parse in the port's real loader
        from src.permissions.loader import settings_to_rules

        rules = settings_to_rules(
            {"allow": ["Bash(npm:*)", "Read", "Edit(src/**)"],
             "deny": ["Bash(rm -rf:*)"], "ask": ["Bash(git push:*)"]},
            source="user_settings",
        )
        assert len(rules) == 5
        assert all(r.rule_value is not None for r in rules)

    def test_env_and_hooks(self):
        p = UPDATE_CONFIG_PROMPT
        assert '"env"' in p
        assert '"hooks"' in p and "matcher" in p and "PostToolUse" in p
        # hook types
        for t in ("command", "agent", "http", "prompt"):
            assert t in p

    def test_mcp_points_to_mcp_json_not_settings_key(self):
        p = UPDATE_CONFIG_PROMPT
        assert ".mcp.json" in p
        assert "enableAllProjectMcpServers" in p
        # NOT the TS settings.json MCP object
        assert '"mcpServers"' not in p


class TestNoWrongKeys:
    def test_absent_keys_not_taught(self):
        p = UPDATE_CONFIG_PROMPT
        for bad in (
            "cleanupPeriodDays",
            "respectGitignore",
            "spinnerTipsEnabled",
            "alwaysThinkingEnabled",
            "syntaxHighlightingDisabled",
        ):
            assert bad not in p, bad

    def test_internal_settingsschema_fields_not_taught_as_knobs(self):
        p = UPDATE_CONFIG_PROMPT
        for internal in (
            "advisor_model",
            "auto_mode_classifier",
            "memory_relevance_prefetch",
        ):
            assert internal not in p, internal

    def test_valid_json_examples(self):
        # every fenced ```json block must be parseable JSON
        import re

        blocks = re.findall(r"```json\n(.*?)\n```", UPDATE_CONFIG_PROMPT, re.DOTALL)
        assert len(blocks) >= 3
        for b in blocks:
            json.loads(b)  # raises on malformed
