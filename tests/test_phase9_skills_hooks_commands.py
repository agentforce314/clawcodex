"""Phase-9 / WI-9.3 + WI-9.4 — /skills + /hooks command tests.

WI-9.3: ``/skills`` builds a structured menu-item list (consumable by
interactive TUI components) and a text fallback. The menu items
include source / status / has_hooks / context fields beyond the
pre-Phase-9 flat listing.

WI-9.4: ``/hooks`` is a new command that lists configured hooks from
the active snapshot, grouped by event and sorted by source priority.
Respects the workspace-trust gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.command_system.builtins import (
    HOOKS_COMMAND,
    SKILLS_COMMAND,
    _build_skills_menu_items,
    _format_skills_menu_text,
    hooks_command_call,
    skills_command_call,
)
from src.command_system.engine import CommandContext
from src.hooks.config_manager import HookConfigManager, HookConfigSnapshot
from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.registry import AsyncHookRegistry


@dataclass
class _MockToolContext:
    hook_config_manager: Any | None = None
    workspace_trusted: bool = True


@dataclass
class _MockCommandContext:
    cwd: Path | None = None
    workspace_root: Path | None = None
    config: dict = field(default_factory=dict)
    tool_context: Any | None = None


def _manager_with(hooks: dict[str, list[HookConfig]]) -> HookConfigManager:
    m = HookConfigManager(registry=AsyncHookRegistry(), settings_path="/dev/null")
    m._snapshot = HookConfigSnapshot(hooks=hooks, timestamp=0.0, source_path=None)
    return m


# ---------------------------------------------------------------------------
# /skills — interactive menu data shape (WI-9.3)
# ---------------------------------------------------------------------------


class TestSkillsMenuItems:
    def test_menu_items_include_chapter_fields(self):
        # Each item must carry the fields the TUI component / SDK
        # consumer needs.
        skill = type("Skill", (), {
            "name": "my-skill",
            "description": "does a thing",
            "when_to_use": "when needed",
            "loaded_from": "user",
            "hooks": None,
            "context": "inline",
        })()
        items = _build_skills_menu_items([skill])
        assert len(items) == 1
        item = items[0]
        assert item["name"] == "my-skill"
        assert item["description"] == "does a thing"
        assert item["when_to_use"] == "when needed"
        assert item["source"] == "user"
        assert item["status"] == "installed"
        assert item["has_hooks"] is False
        assert item["context"] == "inline"

    def test_forked_skill_marked_in_context(self):
        skill = type("Skill", (), {
            "name": "forky",
            "description": "forked",
            "when_to_use": None,
            "loaded_from": "project",
            "hooks": None,
            "context": "fork",
        })()
        items = _build_skills_menu_items([skill])
        assert items[0]["context"] == "fork"

    def test_skill_with_hooks_flagged(self):
        skill = type("Skill", (), {
            "name": "hooked",
            "description": "has hooks",
            "when_to_use": None,
            "loaded_from": "user",
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]},
            "context": "inline",
        })()
        items = _build_skills_menu_items([skill])
        assert items[0]["has_hooks"] is True


class TestSkillsMenuTextFormat:
    def test_empty_text(self):
        text = _format_skills_menu_text([])
        assert "No skills available" in text

    def test_text_includes_flag_legend(self):
        items = [{
            "name": "x", "description": "d", "when_to_use": None,
            "source": "user", "status": "installed",
            "has_hooks": False, "context": "inline",
        }]
        text = _format_skills_menu_text(items)
        assert "Flags:" in text

    def test_forked_skill_gets_F_flag(self):
        items = [{
            "name": "forky", "description": "", "when_to_use": None,
            "source": "user", "status": "installed",
            "has_hooks": False, "context": "fork",
        }]
        text = _format_skills_menu_text(items)
        assert "[F]" in text

    def test_skill_with_hooks_gets_H_flag(self):
        items = [{
            "name": "hooked", "description": "", "when_to_use": None,
            "source": "user", "status": "installed",
            "has_hooks": True, "context": "inline",
        }]
        text = _format_skills_menu_text(items)
        assert "[H]" in text


# ---------------------------------------------------------------------------
# /hooks command (WI-9.4)
# ---------------------------------------------------------------------------


class TestHooksCommand:
    def test_no_tool_context_helpful_message(self):
        ctx = _MockCommandContext(tool_context=None)
        result = hooks_command_call("", ctx)
        assert "no ToolContext" in result.value or "configuration issue" in result.value

    def test_no_hooks_configured_message(self):
        manager = _manager_with({})
        tool_ctx = _MockToolContext(hook_config_manager=manager)
        ctx = _MockCommandContext(tool_context=tool_ctx)
        result = hooks_command_call("", ctx)
        assert "No hooks configured" in result.value

    def test_lists_command_hook(self):
        hook = HookConfig(
            type="command",
            command="echo audit",
            matcher="Bash",
            source=HookSource.USER_SETTINGS,
        )
        manager = _manager_with({"PreToolUse": [hook]})
        tool_ctx = _MockToolContext(hook_config_manager=manager)
        ctx = _MockCommandContext(tool_context=tool_ctx)

        result = hooks_command_call("", ctx)
        assert "PreToolUse" in result.value
        assert "type=command" in result.value
        assert "echo audit" in result.value
        assert "userSettings" in result.value

    def test_groups_by_event_and_source_priority(self):
        # Multiple events + multiple sources. Event names sort
        # alphabetically; within an event, sources sort by priority
        # (USER_SETTINGS=0 first, POLICY_SETTINGS=3 later, etc.).
        hooks = {
            "Stop": [
                HookConfig(type="command", command="stop-cmd",
                           source=HookSource.USER_SETTINGS),
            ],
            "PreToolUse": [
                HookConfig(type="command", command="plugin-cmd",
                           source=HookSource.PLUGIN_HOOK),
                HookConfig(type="command", command="user-cmd",
                           source=HookSource.USER_SETTINGS),
                HookConfig(type="command", command="policy-cmd",
                           source=HookSource.POLICY_SETTINGS),
            ],
        }
        manager = _manager_with(hooks)
        tool_ctx = _MockToolContext(hook_config_manager=manager)
        ctx = _MockCommandContext(tool_context=tool_ctx)

        result = hooks_command_call("", ctx)
        # PreToolUse precedes Stop alphabetically.
        assert result.value.index("PreToolUse") < result.value.index("Stop")
        # Within PreToolUse: user (priority 0) < policy (3) < plugin (999).
        pre_section = result.value.split("Stop")[0]
        assert pre_section.index("user-cmd") < pre_section.index("policy-cmd")
        assert pre_section.index("policy-cmd") < pre_section.index("plugin-cmd")

    def test_workspace_untrusted_only_shows_policy(self):
        # Trust gate: only POLICY_SETTINGS hooks shown when workspace
        # is untrusted (matches executor's runtime gate).
        hooks = {
            "PreToolUse": [
                HookConfig(type="command", command="user-cmd",
                           source=HookSource.USER_SETTINGS),
                HookConfig(type="command", command="policy-cmd",
                           source=HookSource.POLICY_SETTINGS),
            ],
        }
        manager = _manager_with(hooks)
        tool_ctx = _MockToolContext(
            hook_config_manager=manager, workspace_trusted=False,
        )
        ctx = _MockCommandContext(tool_context=tool_ctx)

        result = hooks_command_call("", ctx)
        assert "policy-cmd" in result.value
        assert "user-cmd" not in result.value
        # Diagnostic message mentions trust state.
        assert "untrusted" in result.value.lower()

    def test_lists_callback_hook_with_placeholder_descriptor(self):
        # Callback hooks can't show their callable in a flat string;
        # the descriptor uses ``<programmatic>``.
        hook = HookConfig(
            type="callback",
            callback_ref=lambda evt: None,
            source=HookSource.SESSION_HOOK,
        )
        manager = _manager_with({"PreToolUse": [hook]})
        tool_ctx = _MockToolContext(hook_config_manager=manager)
        ctx = _MockCommandContext(tool_context=tool_ctx)

        result = hooks_command_call("", ctx)
        assert "type=callback" in result.value
        assert "<programmatic>" in result.value


class TestCommandRegistration:
    def test_hooks_command_registered_in_builtins(self):
        from src.command_system.builtins import get_builtin_commands
        names = [c.name for c in get_builtin_commands()]
        assert "hooks" in names

    def test_skills_command_still_registered(self):
        from src.command_system.builtins import get_builtin_commands
        names = [c.name for c in get_builtin_commands()]
        assert "skills" in names
