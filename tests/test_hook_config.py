import json
import os
import tempfile
import pytest

from src.hooks.config_manager import (
    HookConfigManager,
    HookConfigSnapshot,
    load_hooks_from_settings,
    validate_hook_configs,
)
from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.registry import AsyncHookRegistry


class TestValidateHookConfigs:
    def test_empty_config(self):
        errors = validate_hook_configs({})
        assert errors == []

    def test_valid_command_hook(self):
        config = {
            "PreToolUse": [
                {"type": "command", "command": "echo test"}
            ]
        }
        errors = validate_hook_configs(config)
        assert errors == []

    def test_valid_http_hook(self):
        config = {
            "PostToolUse": [
                {"type": "http", "url": "https://example.com/hook"}
            ]
        }
        errors = validate_hook_configs(config)
        assert errors == []

    def test_valid_prompt_hook(self):
        config = {
            "PreToolUse": [
                {"type": "prompt", "promptText": "Always be careful"}
            ]
        }
        errors = validate_hook_configs(config)
        assert errors == []

    def test_valid_agent_hook(self):
        config = {
            "PreToolUse": [
                {"type": "agent", "agentInstructions": "Check safety"}
            ]
        }
        errors = validate_hook_configs(config)
        assert errors == []

    def test_unknown_event(self):
        config = {
            "UnknownEvent": [
                {"type": "command", "command": "echo test"}
            ]
        }
        errors = validate_hook_configs(config)
        assert len(errors) == 1
        assert errors[0].severity == "warning"

    def test_missing_command(self):
        config = {
            "PreToolUse": [
                {"type": "command"}
            ]
        }
        errors = validate_hook_configs(config)
        assert len(errors) == 1
        assert "command" in errors[0].message.lower()

    def test_missing_url(self):
        config = {
            "PreToolUse": [
                {"type": "http"}
            ]
        }
        errors = validate_hook_configs(config)
        assert len(errors) == 1
        assert "url" in errors[0].field.lower()

    def test_missing_prompt_text(self):
        config = {
            "PreToolUse": [
                {"type": "prompt"}
            ]
        }
        errors = validate_hook_configs(config)
        assert len(errors) == 1

    def test_missing_agent_instructions(self):
        config = {
            "PreToolUse": [
                {"type": "agent"}
            ]
        }
        errors = validate_hook_configs(config)
        assert len(errors) == 1

    def test_unknown_hook_type(self):
        config = {
            "PreToolUse": [
                {"type": "websocket", "url": "ws://example.com"}
            ]
        }
        errors = validate_hook_configs(config)
        assert len(errors) == 1

    def test_hook_list_not_array(self):
        config = {
            "PreToolUse": "not a list"
        }
        errors = validate_hook_configs(config)
        assert len(errors) == 1

    def test_hook_not_object(self):
        config = {
            "PreToolUse": ["not an object"]
        }
        errors = validate_hook_configs(config)
        assert len(errors) == 1

    def test_multiple_hooks(self):
        config = {
            "PreToolUse": [
                {"type": "command", "command": "echo 1"},
                {"type": "command", "command": "echo 2"},
            ],
            "PostToolUse": [
                {"type": "http", "url": "https://example.com/post"},
            ],
        }
        errors = validate_hook_configs(config)
        assert errors == []

    def test_hook_with_matcher(self):
        config = {
            "PreToolUse": [
                {"type": "command", "command": "echo test", "matcher": "Bash"}
            ]
        }
        errors = validate_hook_configs(config)
        assert errors == []


class TestLoadHooksFromSettings:
    def test_no_file(self):
        snapshot = load_hooks_from_settings("/nonexistent/settings.json")
        assert snapshot.is_empty

    def test_empty_hooks(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"hooks": {}}, f)
            f.flush()
            snapshot = load_hooks_from_settings(f.name)
        os.unlink(f.name)
        assert snapshot.is_empty

    def test_load_command_hooks(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "hooks": {
                    "PreToolUse": [
                        {"type": "command", "command": "echo pre", "matcher": "Bash"}
                    ],
                    "PostToolUse": [
                        {"type": "command", "command": "echo post"}
                    ],
                }
            }, f)
            f.flush()
            snapshot = load_hooks_from_settings(f.name)
        os.unlink(f.name)

        assert not snapshot.is_empty
        assert len(snapshot.hooks.get("PreToolUse", [])) == 1
        assert len(snapshot.hooks.get("PostToolUse", [])) == 1
        assert snapshot.hooks["PreToolUse"][0].command == "echo pre"
        assert snapshot.hooks["PreToolUse"][0].matcher == "Bash"

    def test_load_http_hooks(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "hooks": {
                    "PreToolUse": [
                        {"type": "http", "url": "https://example.com/hook"}
                    ]
                }
            }, f)
            f.flush()
            snapshot = load_hooks_from_settings(f.name)
        os.unlink(f.name)

        assert snapshot.hooks["PreToolUse"][0].type == "http"
        assert snapshot.hooks["PreToolUse"][0].url == "https://example.com/hook"

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json")
            f.flush()
            snapshot = load_hooks_from_settings(f.name)
        os.unlink(f.name)
        assert snapshot.is_empty


class TestHookConfigManager:
    @pytest.mark.asyncio
    async def test_load(self):
        registry = AsyncHookRegistry()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "hooks": {
                    "PreToolUse": [
                        {"type": "command", "command": "echo test"}
                    ]
                }
            }, f)
            f.flush()
            manager = HookConfigManager(registry, f.name)
            snapshot = await manager.load()

        os.unlink(f.name)
        assert not snapshot.is_empty
        assert registry.hook_count == 1

    @pytest.mark.asyncio
    async def test_reload_if_changed(self):
        registry = AsyncHookRegistry()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"hooks": {}}, f)
            f.flush()
            manager = HookConfigManager(registry, f.name)
            await manager.load()
            assert registry.hook_count == 0

            import time
            time.sleep(0.1)
            with open(f.name, "w") as f2:
                json.dump({
                    "hooks": {
                        "PreToolUse": [
                            {"type": "command", "command": "echo new"}
                        ]
                    }
                }, f2)

            changed = await manager.reload_if_changed()
            assert changed is True
            assert registry.hook_count == 1

        os.unlink(f.name)

    @pytest.mark.asyncio
    async def test_validate(self):
        registry = AsyncHookRegistry()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "hooks": {
                    "PreToolUse": [
                        {"type": "command"}
                    ]
                }
            }, f)
            f.flush()
            manager = HookConfigManager(registry, f.name)
            errors = await manager.validate()

        os.unlink(f.name)
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_validate_missing_file(self):
        registry = AsyncHookRegistry()
        manager = HookConfigManager(registry, "/nonexistent/file.json")
        errors = await manager.validate()
        assert errors == []
