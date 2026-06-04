"""Phase 0 / WI-0.1 — snapshot freezing regression tests.

The chapter (``ch12-extensibility.md`` §"The Snapshot Security Model") says:

    > captureHooksConfigSnapshot() is called once during startup. From that
    > point, executeHooks() reads from the snapshot, never re-reading settings
    > files implicitly.

Before WI-0.1 the Python port had ``_get_hooks_from_settings`` reading
``tool_use_context.options.hooks`` on every call (``hook_executor.py:39-66``)
and ``_run_hooks_for_event`` invoking that function per turn (``:210``). Both
sites bypassed the ``HookConfigSnapshot`` that ``HookConfigManager.load()``
built. An attacker who edited ``~/.claude/settings.json`` after the trust
dialog could land arbitrary code on the next tool call.

These tests pin the corrected behavior:

1. The executor reads from the snapshot, not from ``options.hooks``.
2. Mutating ``settings.json`` after the snapshot is captured does not affect
   in-flight tool calls.
3. The legacy ``options.hooks`` fallback emits a ``DeprecationWarning`` when
   used.
"""

from __future__ import annotations

import asyncio
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.hooks.config_manager import (
    HookConfigManager,
    HookConfigSnapshot,
    load_hooks_from_settings,
)
from src.hooks.hook_executor import (
    _get_hooks_from_snapshot,
    _run_hooks_for_event,
    has_hook_for_event,
)
from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.registry import AsyncHookRegistry


@dataclass
class _MockOptions:
    hooks: dict[str, Any] | None = None
    tools: list[Any] = field(default_factory=list)


@dataclass
class _MockContext:
    """Minimal stand-in for ToolContext for executor calls."""
    options: _MockOptions = field(default_factory=_MockOptions)
    hook_config_manager: Any | None = None
    workspace_trusted: bool = True       # default True so trust gate doesn't fire
    abort_controller: Any | None = None


def _build_manager_with_snapshot(hooks: dict[str, list[HookConfig]]) -> HookConfigManager:
    """Build a HookConfigManager whose snapshot contains the given hooks.

    Skips disk I/O — directly assigns the snapshot field. The constructor
    requires an AsyncHookRegistry for ``self._registry``; we pass a fresh one.
    """
    manager = HookConfigManager(registry=AsyncHookRegistry(), settings_path="/dev/null")
    manager._snapshot = HookConfigSnapshot(hooks=hooks, timestamp=0.0, source_path=None)
    return manager


# ---------------------------------------------------------------------------
# WI-0.1 regression — executor reads from snapshot, not from options.hooks
# ---------------------------------------------------------------------------


class TestExecutorReadsFromSnapshot:
    def test_get_hooks_from_snapshot_when_manager_present(self):
        real_hook = HookConfig(type="command", command="echo real", source=HookSource.USER_SETTINGS)
        manager = _build_manager_with_snapshot({"PreToolUse": [real_hook]})

        ctx = _MockContext(
            options=_MockOptions(hooks={"PreToolUse": [{"type": "command", "command": "echo BOGUS"}]}),
            hook_config_manager=manager,
        )

        # When the manager is present, the snapshot wins.
        result = _get_hooks_from_snapshot(ctx)

        assert "PreToolUse" in result
        assert len(result["PreToolUse"]) == 1
        assert result["PreToolUse"][0].command == "echo real"
        # The bogus options.hooks entry must NOT have leaked through.
        assert all("BOGUS" not in (h.command or "") for h in result["PreToolUse"])

    def test_has_hook_for_event_consults_snapshot(self):
        real_hook = HookConfig(type="command", command="echo x", source=HookSource.USER_SETTINGS)
        manager = _build_manager_with_snapshot({"PreToolUse": [real_hook]})

        ctx = _MockContext(hook_config_manager=manager)

        assert has_hook_for_event("PreToolUse", ctx) is True
        assert has_hook_for_event("PostToolUse", ctx) is False

    def test_no_manager_no_options_returns_empty(self):
        ctx = _MockContext()  # both unset
        assert _get_hooks_from_snapshot(ctx) == {}

    def test_manager_without_loaded_snapshot_returns_empty(self):
        # Manager exists but snapshot was never populated — treat as empty.
        manager = HookConfigManager(registry=AsyncHookRegistry(), settings_path="/dev/null")
        # _snapshot stays at its default (None per __init__).
        ctx = _MockContext(hook_config_manager=manager)
        assert _get_hooks_from_snapshot(ctx) == {}


# ---------------------------------------------------------------------------
# WI-0.1 regression — _run_hooks_for_event uses snapshot path, not :210 bypass
# ---------------------------------------------------------------------------


class TestRunHooksForEventReadsSnapshot:
    @pytest.mark.asyncio
    async def test_run_hooks_fires_snapshot_hook_not_options_hook(self, tmp_path):
        """The headline regression test: settings.json bogus + snapshot real
        → only the real hook fires.
        """
        # The "real" hook writes to a file we'll inspect.
        marker = tmp_path / "real_fired.txt"
        real_hook = HookConfig(
            type="command",
            command=f"echo 'real' > {marker}",
            source=HookSource.USER_SETTINGS,
        )
        manager = _build_manager_with_snapshot({"PreToolUse": [real_hook]})

        # Bogus options.hooks would normally fire a different command if the
        # bypass were still live.
        bogus_marker = tmp_path / "bogus_fired.txt"
        ctx = _MockContext(
            options=_MockOptions(hooks={
                "PreToolUse": [{
                    "type": "command",
                    "command": f"echo 'bogus' > {bogus_marker}",
                }]
            }),
            hook_config_manager=manager,
        )

        results = []
        async for r in _run_hooks_for_event(
            "PreToolUse", "Bash", {"tool_name": "Bash"}, ctx,
        ):
            results.append(r)

        # Real hook fired → marker file exists with "real"
        assert marker.exists()
        assert "real" in marker.read_text()
        # Bogus hook did NOT fire → marker file does not exist
        assert not bogus_marker.exists()

    @pytest.mark.asyncio
    async def test_settings_mutation_after_snapshot_does_not_affect_executor(self, tmp_path):
        """Write a settings.json with hook A; capture snapshot; mutate to hook B;
        execute. Hook A fires (from snapshot), not hook B (mutated on disk).
        """
        settings_path = tmp_path / "settings.json"
        marker_a = tmp_path / "a_fired.txt"
        marker_b = tmp_path / "b_fired.txt"

        settings_path.write_text(json.dumps({
            "hooks": {"PreToolUse": [{
                "type": "command",
                "command": f"echo 'A' > {marker_a}",
            }]}
        }))

        # Capture snapshot (this is what bootstrap would do).
        snapshot = load_hooks_from_settings(settings_path)
        manager = HookConfigManager(registry=AsyncHookRegistry(), settings_path=settings_path)
        manager._snapshot = snapshot

        # Now mutate the settings file on disk to a different hook.
        settings_path.write_text(json.dumps({
            "hooks": {"PreToolUse": [{
                "type": "command",
                "command": f"echo 'B' > {marker_b}",
            }]}
        }))

        # Execute — snapshot was captured before the mutation.
        ctx = _MockContext(hook_config_manager=manager)
        async for _ in _run_hooks_for_event(
            "PreToolUse", "Bash", {"tool_name": "Bash"}, ctx,
        ):
            pass

        # The original (pre-mutation) hook fired.
        assert marker_a.exists()
        assert "A" in marker_a.read_text()
        # The mutated hook did NOT fire — the snapshot froze the original.
        assert not marker_b.exists()


# ---------------------------------------------------------------------------
# Legacy options.hooks fallback (deprecated, one CHANGELOG cycle)
# ---------------------------------------------------------------------------


class TestLegacyOptionsHooksFallback:
    def test_legacy_path_emits_deprecation_warning(self):
        # No hook_config_manager set; only options.hooks.
        ctx = _MockContext(
            options=_MockOptions(hooks={
                "PreToolUse": [{"type": "command", "command": "echo legacy"}]
            }),
        )

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            result = _get_hooks_from_snapshot(ctx)

        # The legacy path returns the parsed hooks for back-compat.
        assert "PreToolUse" in result
        assert result["PreToolUse"][0].command == "echo legacy"

        # ...and emits a DeprecationWarning.
        deprecation_warnings = [
            w for w in captured if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) >= 1
        msg = str(deprecation_warnings[0].message)
        assert "options.hooks" in msg
        assert "deprecated" in msg.lower()

    def test_empty_options_hooks_does_not_warn(self):
        # Empty options.hooks is the common case; no warning.
        ctx = _MockContext(options=_MockOptions(hooks=None))
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            result = _get_hooks_from_snapshot(ctx)
        assert result == {}
        assert not any(issubclass(w.category, DeprecationWarning) for w in captured)
