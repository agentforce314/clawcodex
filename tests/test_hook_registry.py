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
        # Phase-1 / WI-1.2 — priority scheme reshuffled to match the chapter
        # table (``ch12-extensibility.md`` §"Six Hook Sources"):
        #   USER_SETTINGS  = 0   (highest priority — sorts first)
        #   POLICY_SETTINGS = 3
        #   PLUGIN_HOOK    = 999 (sentinel: always last)
        # Note: priority is *display ordering* only. The chapter's "policy
        # cannot be overridden" semantic is enforced by ``apply_policy_cascade``
        # (Phase 2 / WI-2.3), not by priority. The sort order below reflects
        # priority, not security precedence.
        config_plugin = HookConfig(type="command", command="echo plugin", source=HookSource.PLUGIN_HOOK)
        config_settings = HookConfig(type="command", command="echo settings", source=HookSource.USER_SETTINGS)
        config_policy = HookConfig(type="command", command="echo policy", source=HookSource.POLICY_SETTINGS)

        await registry.register("PreToolUse", config_plugin, HookSource.PLUGIN_HOOK)
        await registry.register("PreToolUse", config_settings, HookSource.USER_SETTINGS)
        await registry.register("PreToolUse", config_policy, HookSource.POLICY_SETTINGS)

        hooks = await registry.get_hooks_for_event("PreToolUse")
        assert len(hooks) == 3
        assert hooks[0].source == HookSource.USER_SETTINGS
        assert hooks[1].source == HookSource.POLICY_SETTINGS
        assert hooks[2].source == HookSource.PLUGIN_HOOK

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
        await registry.register("PreToolUse", config_settings, HookSource.USER_SETTINGS)
        await registry.register("PreToolUse", config_plugins, HookSource.PLUGIN_HOOK)
        assert registry.hook_count == 2
        removed = await registry.clear_source(HookSource.PLUGIN_HOOK)
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
    """Phase-1 / WI-1.2 — six-source priority scheme.

    Old enum values ``POLICY`` / ``SETTINGS`` / ``PLUGINS`` are preserved as
    deprecated aliases (``EnumMeta``-level ``__getattr__``); ``FRONTMATTER``
    and ``SKILLS`` had no producer in Phase 0 (gap analysis §5) and are
    removed outright.
    """

    def test_priority_ordering(self):
        # Phase-1 priority scheme: USER_SETTINGS=0, PROJECT=1, LOCAL=2,
        # POLICY_SETTINGS=3, SESSION_HOOK=4, PLUGIN_HOOK=999 (sentinel).
        assert HookSource.USER_SETTINGS.priority < HookSource.PROJECT_SETTINGS.priority
        assert HookSource.PROJECT_SETTINGS.priority < HookSource.LOCAL_SETTINGS.priority
        assert HookSource.LOCAL_SETTINGS.priority < HookSource.POLICY_SETTINGS.priority
        assert HookSource.POLICY_SETTINGS.priority < HookSource.SESSION_HOOK.priority
        assert HookSource.SESSION_HOOK.priority < HookSource.PLUGIN_HOOK.priority

    def test_plugin_hook_priority_is_999(self):
        # Sentinel value: any tier added later (e.g., a future "managed-cloud"
        # source) inserts between LOCAL and PLUGIN without disturbing plugin
        # ordering.
        assert HookSource.PLUGIN_HOOK.priority == 999

    def test_source_values(self):
        # camelCase wire format matches TS' source string identifiers.
        assert HookSource.USER_SETTINGS.value == "userSettings"
        assert HookSource.PROJECT_SETTINGS.value == "projectSettings"
        assert HookSource.LOCAL_SETTINGS.value == "localSettings"
        assert HookSource.POLICY_SETTINGS.value == "policySettings"
        assert HookSource.SESSION_HOOK.value == "sessionHook"
        assert HookSource.PLUGIN_HOOK.value == "pluginHook"

    def test_is_policy_predicate(self):
        # The trust gate (WI-0.2) and policy cascade (WI-2.3) ask
        # "is this source the enterprise tier?" via ``is_policy``.
        assert HookSource.POLICY_SETTINGS.is_policy is True
        assert HookSource.USER_SETTINGS.is_policy is False
        assert HookSource.PLUGIN_HOOK.is_policy is False
