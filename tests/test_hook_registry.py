import asyncio
import pytest

from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.registry import (
    AsyncHookRegistry,
    RegisteredHook,
    get_global_hook_registry,
    reset_global_hook_registry,
)


@pytest.fixture
def registry():
    return AsyncHookRegistry()


class TestAsyncHookRegistry:
    def test_empty_registry(self, registry):
        assert registry.hook_count == 0

    @pytest.mark.asyncio
    async def test_register_hook(self, registry):
        config = HookConfig(type="command", command="echo test")
        hook = await registry.register("PreToolUse", config)
        assert isinstance(hook, RegisteredHook)
        assert hook.event == "PreToolUse"
        assert hook.config.command == "echo test"
        assert registry.hook_count == 1

    @pytest.mark.asyncio
    async def test_register_multiple_events(self, registry):
        config1 = HookConfig(type="command", command="echo pre")
        config2 = HookConfig(type="command", command="echo post")
        await registry.register("PreToolUse", config1)
        await registry.register("PostToolUse", config2)
        assert registry.hook_count == 2

    @pytest.mark.asyncio
    async def test_dedup_same_hook(self, registry):
        config = HookConfig(type="command", command="echo test")
        hook1 = await registry.register("PreToolUse", config)
        hook2 = await registry.register("PreToolUse", config)
        assert hook1 is hook2
        assert registry.hook_count == 1

    @pytest.mark.asyncio
    async def test_dedup_different_matcher(self, registry):
        config1 = HookConfig(type="command", command="echo test", matcher="Bash")
        config2 = HookConfig(type="command", command="echo test", matcher="Read")
        await registry.register("PreToolUse", config1)
        await registry.register("PreToolUse", config2)
        assert registry.hook_count == 2

    @pytest.mark.asyncio
    async def test_deregister_hook(self, registry):
        config = HookConfig(type="command", command="echo test")
        await registry.register("PreToolUse", config)
        assert registry.hook_count == 1
        removed = await registry.deregister("PreToolUse", config)
        assert removed is True
        assert registry.hook_count == 0

    @pytest.mark.asyncio
    async def test_deregister_nonexistent(self, registry):
        config = HookConfig(type="command", command="echo test")
        removed = await registry.deregister("PreToolUse", config)
        assert removed is False

    @pytest.mark.asyncio
    async def test_get_hooks_for_event(self, registry):
        config1 = HookConfig(type="command", command="echo 1")
        config2 = HookConfig(type="command", command="echo 2")
        await registry.register("PreToolUse", config1)
        await registry.register("PostToolUse", config2)
        hooks = await registry.get_hooks_for_event("PreToolUse")
        assert len(hooks) == 1
        assert hooks[0].config.command == "echo 1"

    @pytest.mark.asyncio
    async def test_get_hooks_with_tool_filter(self, registry):
        config1 = HookConfig(type="command", command="echo bash", matcher="Bash")
        config2 = HookConfig(type="command", command="echo read", matcher="Read")
        config3 = HookConfig(type="command", command="echo all")
        await registry.register("PreToolUse", config1)
        await registry.register("PreToolUse", config2)
        await registry.register("PreToolUse", config3)

        hooks = await registry.get_hooks_for_event("PreToolUse", tool_name="Bash")
        assert len(hooks) == 2
        commands = [h.config.command for h in hooks]
        assert "echo bash" in commands
        assert "echo all" in commands

    @pytest.mark.asyncio
    async def test_wildcard_matcher(self, registry):
        config = HookConfig(type="command", command="echo mcp", matcher="mcp__*")
        await registry.register("PreToolUse", config)
        hooks = await registry.get_hooks_for_event("PreToolUse", tool_name="mcp__server__tool")
        assert len(hooks) == 1

    @pytest.mark.asyncio
    async def test_suffix_wildcard_matcher(self, registry):
        config = HookConfig(type="command", command="echo", matcher="*Tool")
        await registry.register("PreToolUse", config)
        hooks = await registry.get_hooks_for_event("PreToolUse", tool_name="BashTool")
        assert len(hooks) == 1
        hooks = await registry.get_hooks_for_event("PreToolUse", tool_name="Read")
        assert len(hooks) == 0

    @pytest.mark.asyncio
    async def test_priority_ordering(self, registry):
        config_plugin = HookConfig(type="command", command="echo plugin", source=HookSource.PLUGINS)
        config_settings = HookConfig(type="command", command="echo settings", source=HookSource.SETTINGS)
        config_policy = HookConfig(type="command", command="echo policy", source=HookSource.POLICY)

        await registry.register("PreToolUse", config_plugin, HookSource.PLUGINS)
        await registry.register("PreToolUse", config_settings, HookSource.SETTINGS)
        await registry.register("PreToolUse", config_policy, HookSource.POLICY)

        hooks = await registry.get_hooks_for_event("PreToolUse")
        assert len(hooks) == 3
        assert hooks[0].source == HookSource.POLICY
        assert hooks[1].source == HookSource.SETTINGS
        assert hooks[2].source == HookSource.PLUGINS

    @pytest.mark.asyncio
    async def test_has_hooks_for_event(self, registry):
        assert await registry.has_hooks_for_event("PreToolUse") is False
        config = HookConfig(type="command", command="echo test")
        await registry.register("PreToolUse", config)
        assert await registry.has_hooks_for_event("PreToolUse") is True

    @pytest.mark.asyncio
    async def test_clear(self, registry):
        config = HookConfig(type="command", command="echo test")
        await registry.register("PreToolUse", config)
        await registry.register("PostToolUse", config)
        assert registry.hook_count == 2
        await registry.clear()
        assert registry.hook_count == 0

    @pytest.mark.asyncio
    async def test_clear_source(self, registry):
        config_settings = HookConfig(type="command", command="echo settings")
        config_plugins = HookConfig(type="command", command="echo plugins")
        await registry.register("PreToolUse", config_settings, HookSource.SETTINGS)
        await registry.register("PreToolUse", config_plugins, HookSource.PLUGINS)
        assert registry.hook_count == 2
        removed = await registry.clear_source(HookSource.PLUGINS)
        assert removed == 1
        assert registry.hook_count == 1

    @pytest.mark.asyncio
    async def test_register_batch(self, registry):
        hooks = [
            ("PreToolUse", HookConfig(type="command", command="echo 1")),
            ("PostToolUse", HookConfig(type="command", command="echo 2")),
            ("Stop", HookConfig(type="command", command="echo 3")),
        ]
        results = await registry.register_batch(hooks)
        assert len(results) == 3
        assert registry.hook_count == 3

    @pytest.mark.asyncio
    async def test_get_all_hooks(self, registry):
        config = HookConfig(type="command", command="echo test")
        await registry.register("PreToolUse", config)
        all_hooks = registry.get_all_hooks()
        assert len(all_hooks["PreToolUse"]) == 1
        assert len(all_hooks["PostToolUse"]) == 0


class TestGlobalRegistry:
    def test_get_global_registry(self):
        reset_global_hook_registry()
        reg = get_global_hook_registry()
        assert isinstance(reg, AsyncHookRegistry)
        reg2 = get_global_hook_registry()
        assert reg is reg2

    def test_reset_global_registry(self):
        reg1 = get_global_hook_registry()
        reset_global_hook_registry()
        reg2 = get_global_hook_registry()
        assert reg1 is not reg2


class TestHookSource:
    def test_priority_ordering(self):
        assert HookSource.POLICY.priority < HookSource.SETTINGS.priority
        assert HookSource.SETTINGS.priority < HookSource.FRONTMATTER.priority
        assert HookSource.FRONTMATTER.priority < HookSource.SKILLS.priority
        assert HookSource.SKILLS.priority < HookSource.PLUGINS.priority

    def test_source_values(self):
        assert HookSource.POLICY.value == "policy"
        assert HookSource.SETTINGS.value == "settings"
        assert HookSource.FRONTMATTER.value == "frontmatter"
        assert HookSource.SKILLS.value == "skills"
        assert HookSource.PLUGINS.value == "plugins"
