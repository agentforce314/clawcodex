"""Phase 0 / WI-0.2 — workspace-trust gate tests.

The chapter (``ch12-extensibility.md`` §"The Snapshot Security Model") describes
``shouldSkipHookDueToTrust`` as a centralized gate at the top of
``executeHooks()``. Introduced after two CVEs:
  - SessionEnd hooks executing when a user *declined* the trust dialog.
  - SubagentStop hooks firing before trust was presented.

Both share the same root cause: hooks firing in lifecycle states where the user
had not consented to workspace code execution. The gate closes that window.

Policy hooks (``HookSource.POLICY_SETTINGS``) are NOT subject to the gate per
the chapter's "policy layer always wins" semantic. We test all four cells of
the (trusted × policy) matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.hooks.config_manager import HookConfigManager, HookConfigSnapshot
from src.hooks.hook_executor import _run_hooks_for_event
from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.registry import AsyncHookRegistry
from src.hooks.trust_gate import should_skip_hook_due_to_trust


@dataclass
class _MockOptions:
    hooks: dict[str, Any] | None = None
    tools: list[Any] = field(default_factory=list)


@dataclass
class _MockContext:
    options: _MockOptions = field(default_factory=_MockOptions)
    hook_config_manager: Any | None = None
    workspace_trusted: bool = False
    abort_controller: Any | None = None


def _manager_with(hooks: dict[str, list[HookConfig]]) -> HookConfigManager:
    m = HookConfigManager(registry=AsyncHookRegistry(), settings_path="/dev/null")
    m._snapshot = HookConfigSnapshot(hooks=hooks, timestamp=0.0, source_path=None)
    return m


# ---------------------------------------------------------------------------
# The predicate itself
# ---------------------------------------------------------------------------


class TestShouldSkipHookDueToTrust:
    def test_untrusted_workspace_skips(self):
        ctx = _MockContext(workspace_trusted=False)
        assert should_skip_hook_due_to_trust(ctx) is True

    def test_trusted_workspace_does_not_skip(self):
        ctx = _MockContext(workspace_trusted=True)
        assert should_skip_hook_due_to_trust(ctx) is False

    def test_missing_attribute_treated_as_untrusted(self):
        # Bare object without workspace_trusted attribute → fail-safe to True.
        class Bare:
            pass
        assert should_skip_hook_due_to_trust(Bare()) is True


# ---------------------------------------------------------------------------
# End-to-end: _run_hooks_for_event respects the gate
# ---------------------------------------------------------------------------


class TestExecutorRespectsGate:
    @pytest.mark.asyncio
    async def test_untrusted_workspace_skips_user_hooks(self, tmp_path):
        marker = tmp_path / "user_hook_fired.txt"
        user_hook = HookConfig(
            type="command",
            command=f"echo 'user' > {marker}",
            source=HookSource.USER_SETTINGS,
        )
        ctx = _MockContext(
            workspace_trusted=False,
            hook_config_manager=_manager_with({"PreToolUse": [user_hook]}),
        )

        async for _ in _run_hooks_for_event(
            "PreToolUse", "Bash", {"tool_name": "Bash"}, ctx,
        ):
            pass

        # User hook did NOT fire because workspace is untrusted.
        assert not marker.exists()

    @pytest.mark.asyncio
    async def test_untrusted_workspace_still_runs_policy_hooks(self, tmp_path):
        marker = tmp_path / "policy_hook_fired.txt"
        policy_hook = HookConfig(
            type="command",
            command=f"echo 'policy' > {marker}",
            source=HookSource.POLICY_SETTINGS,
        )
        ctx = _MockContext(
            workspace_trusted=False,
            hook_config_manager=_manager_with({"PreToolUse": [policy_hook]}),
        )

        async for _ in _run_hooks_for_event(
            "PreToolUse", "Bash", {"tool_name": "Bash"}, ctx,
        ):
            pass

        # Policy hook DID fire — the policy layer always wins.
        assert marker.exists()
        assert "policy" in marker.read_text()

    @pytest.mark.asyncio
    async def test_trusted_workspace_runs_all_hooks(self, tmp_path):
        user_marker = tmp_path / "user.txt"
        policy_marker = tmp_path / "policy.txt"

        user_hook = HookConfig(
            type="command",
            command=f"echo 'u' > {user_marker}",
            source=HookSource.USER_SETTINGS,
        )
        policy_hook = HookConfig(
            type="command",
            command=f"echo 'p' > {policy_marker}",
            source=HookSource.POLICY_SETTINGS,
        )
        ctx = _MockContext(
            workspace_trusted=True,
            hook_config_manager=_manager_with({"PreToolUse": [user_hook, policy_hook]}),
        )

        async for _ in _run_hooks_for_event(
            "PreToolUse", "Bash", {"tool_name": "Bash"}, ctx,
        ):
            pass

        # Both fired.
        assert user_marker.exists()
        assert policy_marker.exists()

    @pytest.mark.asyncio
    async def test_untrusted_workspace_with_only_user_hooks_yields_nothing(self):
        """When the gate strips everything, the executor yields no items."""
        user_hook = HookConfig(
            type="command", command="echo x", source=HookSource.USER_SETTINGS,
        )
        ctx = _MockContext(
            workspace_trusted=False,
            hook_config_manager=_manager_with({"PreToolUse": [user_hook]}),
        )

        items = []
        async for r in _run_hooks_for_event(
            "PreToolUse", "Bash", {"tool_name": "Bash"}, ctx,
        ):
            items.append(r)

        assert items == []
