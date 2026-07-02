"""ch01 round-4 WI-1 acceptance tests: hooks-subsystem bootstrap.

Before this round, nothing in production constructed a
``HookConfigManager`` or populated ``tool_use_context.hook_config_manager``
/ the global ``AsyncHookRegistry`` — configured hooks never fired.
``bootstrap_hook_config_manager`` is the single wire both production
``ToolContext`` construction sites (agent-server ``_build_runtime``,
headless ``run_headless``) now call.

Covers:
  * happy path — temp settings.json entries land in BOTH read paths
    (frozen snapshot + global registry);
  * the executors' actual read predicate (``has_hook_for_event``) sees the
    configs once the manager is attached to a real ToolContext;
  * settings.hooks.enabled=False → None, registry untouched;
  * malformed settings.json → manager with empty snapshot, no raise;
  * idempotence — bootstrapping twice does not duplicate registry entries;
  * running-event-loop misuse → None (documented sync-only contract).
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.hooks.config_manager import (
    HookConfigManager,
    bootstrap_hook_config_manager,
)
from src.hooks.registry import (
    get_global_hook_registry,
    reset_global_hook_registry,
)


def _write_settings(dir_path: Path, hooks: dict | str) -> Path:
    settings = dir_path / "settings.json"
    if isinstance(hooks, str):
        settings.write_text(hooks, encoding="utf-8")
    else:
        settings.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    return settings


_TWO_EVENTS = {
    "PreToolUse": [
        {"type": "command", "command": "echo pre", "matcher": "Write"},
    ],
    "PostSampling": [
        {"type": "command", "command": "echo post"},
    ],
}


class TestBootstrapHookConfigManager(unittest.TestCase):
    def setUp(self) -> None:
        reset_global_hook_registry()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        reset_global_hook_registry()
        self._tmp.cleanup()

    def test_happy_path_populates_snapshot_and_registry(self) -> None:
        settings = _write_settings(self.tmp, _TWO_EVENTS)

        manager = bootstrap_hook_config_manager(settings_path=settings)

        self.assertIsInstance(manager, HookConfigManager)
        assert manager is not None
        snapshot = manager.snapshot
        assert snapshot is not None
        self.assertIn("PreToolUse", snapshot.hooks)
        self.assertIn("PostSampling", snapshot.hooks)

        registry = get_global_hook_registry()
        self.assertTrue(
            asyncio.run(registry.has_hooks_for_event("PreToolUse", "Write")),
        )
        self.assertTrue(
            asyncio.run(registry.has_hooks_for_event("PostSampling")),
        )

    def test_executor_read_path_sees_configs_via_tool_context(self) -> None:
        """The production predicate the tool pipeline uses must go live."""
        from src.hooks.hook_executor import has_hook_for_event
        from src.tool_system.context import ToolContext

        settings = _write_settings(self.tmp, _TWO_EVENTS)
        context = ToolContext(workspace_root=self.tmp)
        self.assertFalse(has_hook_for_event("PreToolUse", context))

        context.hook_config_manager = bootstrap_hook_config_manager(
            settings_path=settings,
        )

        self.assertTrue(has_hook_for_event("PreToolUse", context))
        self.assertTrue(has_hook_for_event("PostSampling", context))
        self.assertFalse(has_hook_for_event("SessionStart", context))

    def test_hooks_disabled_returns_none_and_leaves_registry_empty(self) -> None:
        settings = _write_settings(self.tmp, _TWO_EVENTS)

        class _Knob:
            enabled = False

        class _Settings:
            hooks = _Knob()

        with patch(
            "src.settings.settings.load_settings", return_value=_Settings(),
        ):
            manager = bootstrap_hook_config_manager(settings_path=settings)

        self.assertIsNone(manager)
        registry = get_global_hook_registry()
        self.assertFalse(
            asyncio.run(registry.has_hooks_for_event("PreToolUse", "Write")),
        )

    def test_malformed_settings_yields_empty_snapshot_without_raise(self) -> None:
        settings = _write_settings(self.tmp, "{not json")

        manager = bootstrap_hook_config_manager(settings_path=settings)

        self.assertIsNotNone(manager)
        assert manager is not None
        snapshot = manager.snapshot
        assert snapshot is not None
        self.assertTrue(snapshot.is_empty)

    def test_bootstrap_twice_does_not_duplicate_registry_entries(self) -> None:
        settings = _write_settings(self.tmp, _TWO_EVENTS)

        bootstrap_hook_config_manager(settings_path=settings)
        bootstrap_hook_config_manager(settings_path=settings)

        registry = get_global_hook_registry()
        hooks = asyncio.run(registry.get_hooks_for_event("PostSampling"))
        self.assertEqual(len(hooks), 1)

    def test_canonical_matcher_group_format_expands(self) -> None:
        """Real Claude Code settings nest hooks in matcher groups
        ({"matcher": ..., "hooks": [...]}); the loader must expand them
        with the group matcher propagated — not parse the group itself
        into an empty-command junk hook."""
        settings = _write_settings(self.tmp, {
            "PreToolUse": [{
                "matcher": "Write",
                "hooks": [
                    {"type": "command", "command": "echo grouped-1"},
                    {"type": "command", "command": "echo grouped-2",
                     "matcher": "Edit"},
                ],
            }],
            # Malformed flat entry: command-type with no command — dropped.
            "Stop": [{"type": "command"}],
        })

        manager = bootstrap_hook_config_manager(settings_path=settings)

        assert manager is not None
        snapshot = manager.snapshot
        assert snapshot is not None
        pre = snapshot.hooks.get("PreToolUse", [])
        self.assertEqual(
            [(h.command, h.matcher) for h in pre],
            [("echo grouped-1", "Write"), ("echo grouped-2", "Edit")],
        )
        self.assertEqual(snapshot.hooks.get("Stop", []), [])

        registry = get_global_hook_registry()
        self.assertTrue(
            asyncio.run(registry.has_hooks_for_event("PreToolUse", "Write")),
        )
        self.assertFalse(
            asyncio.run(registry.has_hooks_for_event("Stop")),
        )

    def test_called_from_running_loop_returns_none(self) -> None:
        settings = _write_settings(self.tmp, _TWO_EVENTS)

        async def _inside_loop():
            return bootstrap_hook_config_manager(settings_path=settings)

        self.assertIsNone(asyncio.run(_inside_loop()))


if __name__ == "__main__":
    unittest.main()
