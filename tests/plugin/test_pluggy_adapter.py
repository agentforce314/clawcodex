"""Tests for Pluggy adapter (Task #6)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.hooks._pluggy_adapter import (
    ClawCodexHooks,
    HookPluginAdapter,
    PluggyHookManager,
    is_pluggy_available,
)
from src.hooks.hook_types import HookConfig, HookSource


class TestPluggyAvailable:
    def test_pluggy_is_available(self):
        assert is_pluggy_available() is True


class TestPluggyHookManager:
    def test_manager_initialization(self):
        manager = PluggyHookManager("clawcodex")
        assert manager.pm is not None
        assert len(manager._registered_hooks) == 0

    def test_register_plugin(self):
        manager = PluggyHookManager("clawcodex")

        class TestPlugin:
            def pre_tool_use(self, tool_name, tool_input, context):
                return None

        plugin = TestPlugin()
        result = manager.register(plugin, "PreToolUse")
        assert result is plugin
        assert "PreToolUse" in manager._registered_hooks
        assert plugin in manager._registered_hooks["PreToolUse"]

    def test_deregister_plugin(self):
        manager = PluggyHookManager("clawcodex")

        class TestPlugin:
            def pre_tool_use(self, tool_name, tool_input, context):
                return None

        plugin = TestPlugin()
        manager.register(plugin, "PreToolUse")
        result = manager.deregister(plugin, "PreToolUse")
        assert result is True
        assert "PreToolUse" not in manager._registered_hooks or plugin not in manager._registered_hooks.get("PreToolUse", [])

    def test_get_plugins(self):
        manager = PluggyHookManager("clawcodex")

        class TestPlugin:
            def pre_tool_use(self, tool_name, tool_input, context):
                return None

        plugin = TestPlugin()
        manager.register(plugin, "PreToolUse")
        plugins = manager.get_plugins()
        assert plugin in plugins


class TestClawCodexHooks:
    def test_hooks_class_exists(self):
        assert ClawCodexHooks is not None

    def test_hooks_has_pre_tool_use(self):
        assert hasattr(ClawCodexHooks, "pre_tool_use")

    def test_hooks_has_post_tool_use(self):
        assert hasattr(ClawCodexHooks, "post_tool_use")

    def test_hooks_has_session_start(self):
        assert hasattr(ClawCodexHooks, "session_start")

    def test_hooks_has_session_end(self):
        assert hasattr(ClawCodexHooks, "session_end")


class TestHookPluginAdapter:
    def test_adapter_initialization(self):
        config = HookConfig(type="command", command="echo test")
        adapter = HookPluginAdapter(config)
        assert adapter.config == config

    def test_adapter_with_empty_command(self):
        config = HookConfig(type="command", command="")
        adapter = HookPluginAdapter(config)
        result = adapter.pre_tool_use("Bash", {}, None)
        assert result is None


class TestBackwardCompatibility:
    def test_pluggy_hook_manager_instantiable(self):
        """Ensure PluggyHookManager can be instantiated."""
        manager = PluggyHookManager("clawcodex")
        assert manager is not None
        assert hasattr(manager, "register")
        assert hasattr(manager, "deregister")
        assert hasattr(manager, "call")

    def test_clawcodex_hooks_has_all_core_events(self):
        """Ensure ClawCodexHooks has all core events."""
        required_events = [
            "pre_tool_use",
            "post_tool_use",
            "session_start",
            "session_end",
        ]
        for event in required_events:
            assert hasattr(ClawCodexHooks, event)