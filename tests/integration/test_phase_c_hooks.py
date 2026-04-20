import asyncio

import pytest

from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.registry import AsyncHookRegistry
from src.hooks.ssrf_guard import is_safe_url, validate_hook_url


class TestHooksIntegration:
    @pytest.fixture
    def registry(self):
        return AsyncHookRegistry()

    def test_register_and_retrieve(self, registry):
        async def _run():
            config = HookConfig(command="echo test")
            hook = await registry.register("PreToolUse", config, HookSource.SETTINGS)
            assert hook is not None
            hooks = await registry.get_hooks_for_event("PreToolUse")
            assert len(hooks) == 1
            return True

        assert asyncio.run(_run())

    def test_priority_ordering(self, registry):
        async def _run():
            await registry.register(
                "PreToolUse",
                HookConfig(command="plugin-hook"),
                HookSource.PLUGINS,
            )
            await registry.register(
                "PreToolUse",
                HookConfig(command="policy-hook"),
                HookSource.POLICY,
            )
            await registry.register(
                "PreToolUse",
                HookConfig(command="settings-hook"),
                HookSource.SETTINGS,
            )
            hooks = await registry.get_hooks_for_event("PreToolUse")
            assert hooks[0].source == HookSource.POLICY
            assert hooks[1].source == HookSource.SETTINGS
            assert hooks[2].source == HookSource.PLUGINS

        asyncio.run(_run())

    def test_ssrf_blocks_private(self):
        safe, reason = validate_hook_url("http://127.0.0.1:8080/hook", resolve_dns=False)
        assert safe is False
        assert reason is not None

    def test_ssrf_allows_public(self):
        assert is_safe_url("https://hooks.example.com/webhook", resolve_dns=False) is True

    def test_clear_source(self, registry):
        async def _run():
            await registry.register(
                "PreToolUse",
                HookConfig(command="a"),
                HookSource.PLUGINS,
            )
            await registry.register(
                "PreToolUse",
                HookConfig(command="b"),
                HookSource.SETTINGS,
            )
            cleared = await registry.clear_source(HookSource.PLUGINS)
            assert cleared == 1
            hooks = await registry.get_hooks_for_event("PreToolUse")
            assert len(hooks) == 1
            assert hooks[0].source == HookSource.SETTINGS

        asyncio.run(_run())
