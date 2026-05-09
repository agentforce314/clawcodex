"""Phase-7 follow-up D5 — provider wiring through hook-dispatch path.

Pre-D5: the executor (``_run_hooks_for_event``) always called
``_execute_command_hook`` regardless of ``hook.config.type``. Agent /
prompt / http hooks for non-lifecycle events (PreToolUse, PostToolUse,
Stop, etc.) silently degraded to spawning empty subprocesses.
``execute_prompt_hook`` and ``execute_agent_hook`` accepted ``provider``
kwargs but no caller threaded them through, so production sessions hit
the "Provider required" blocking_error.

D5 closes the gap with two changes:

  1. ``_dispatch_hook_by_type`` (new) routes each hook type to its
     proper executor (command / http / prompt / agent). LLM-driven
     types pull provider/model from ``tool_use_context``.
  2. Bootstrap (tui.py / headless.py / repl/core.py / subagent_context.py)
     populates ``ToolContext.provider`` + ``ToolContext.model``.
     Sub-agent contexts inherit from parent (matches the D3 pattern).

This test mirrors the D3 production-path test pattern: drives the real
executor → dispatch → executor body chain through a configured
HookConfigSnapshot (NOT a direct call to execute_prompt_hook), with a
mocked provider on the context. Asserts the prompt hook actually fired
and its response surfaced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.hooks.config_manager import HookConfigManager, HookConfigSnapshot
from src.hooks.hook_executor import _run_hooks_for_event, _dispatch_hook_by_type
from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.registry import AsyncHookRegistry


@dataclass
class _MockOptions:
    hooks: dict[str, Any] | None = None
    tools: list[Any] = field(default_factory=list)


@dataclass
class _MockContext:
    options: _MockOptions = field(default_factory=_MockOptions)
    hook_config_manager: Any | None = None
    workspace_trusted: bool = True
    abort_controller: Any | None = None
    session_hook_registry: Any | None = None
    session_id: str | None = None
    workspace_root: Path | None = None
    provider: Any | None = None
    model: str | None = None


def _manager_with(hooks: dict[str, list[HookConfig]]) -> HookConfigManager:
    m = HookConfigManager(registry=AsyncHookRegistry(), settings_path="/dev/null")
    m._snapshot = HookConfigSnapshot(hooks=hooks, timestamp=0.0, source_path=None)
    return m


# ---------------------------------------------------------------------------
# Dispatch by type — direct unit tests
# ---------------------------------------------------------------------------


class TestDispatchByType:
    @pytest.mark.asyncio
    async def test_command_hook_routes_to_command_executor(self):
        # Sanity: command hooks still run the command-hook executor.
        hook = HookConfig(type="command", command="echo cmd-test")
        result = await _dispatch_hook_by_type(hook, {"hook_event": "PreToolUse"})
        assert result.exit_code == 0
        assert "cmd-test" in (result.stdout or "")

    @pytest.mark.asyncio
    async def test_prompt_hook_routes_to_prompt_executor_with_provider(self):
        # Phase-7 D5: provider on context flows through to
        # execute_prompt_hook. Pre-D5 this dispatch didn't exist;
        # _run_hooks_for_event called _execute_command_hook for
        # ``hook.type=="prompt"`` and ran an empty subprocess.
        hook = HookConfig(type="prompt", prompt_text="Eval {tool_name}")

        mock_response = MagicMock()
        mock_response.content = "LLM said: ok"
        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(return_value=mock_response)

        ctx = _MockContext(provider=mock_provider, model="claude-sonnet-4")

        result = await _dispatch_hook_by_type(
            hook, {"hook_event": "PreToolUse", "tool_name": "Bash"},
            tool_use_context=ctx,
        )

        assert result.exit_code == 0
        assert result.additional_contexts == ["LLM said: ok"]
        # Provider was called with the rendered template.
        sent = mock_provider.chat_async.call_args.kwargs
        assert "Eval Bash" in sent["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_prompt_hook_no_provider_on_context_blocks(self):
        # When the bootstrap path didn't wire the provider (or a test
        # fixture doesn't populate it), the dispatcher passes ``None``
        # and the executor returns blocking_error.
        hook = HookConfig(type="prompt", prompt_text="x")
        ctx = _MockContext(provider=None)
        result = await _dispatch_hook_by_type(
            hook, {"hook_event": "PreToolUse"}, tool_use_context=ctx,
        )
        assert result.blocking_error is not None
        assert "provider" in result.blocking_error.lower()

    @pytest.mark.asyncio
    async def test_agent_hook_routes_with_provider_threaded(self):
        # Same pattern for agent hooks: provider on context flows
        # through to execute_agent_hook.
        import json
        hook = HookConfig(type="agent", agent_instructions="Validate")
        mock_response = MagicMock()
        mock_response.content = json.dumps({"decision": "allow", "reason": "ok"})
        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(return_value=mock_response)

        ctx = _MockContext(provider=mock_provider)
        result = await _dispatch_hook_by_type(
            hook, {"hook_event": "PreToolUse"}, tool_use_context=ctx,
        )
        assert result.exit_code == 0
        assert result.permission_behavior == "allow"

    @pytest.mark.asyncio
    async def test_unknown_hook_type_returns_no_op(self):
        # Defensive: validator at config-load time should have caught
        # this, but if an unknown type slips through we return a no-op
        # rather than crashing the executor.
        hook = HookConfig(type="bogus", command="x")  # type: ignore[arg-type]
        result = await _dispatch_hook_by_type(hook, {"hook_event": "x"})
        assert result.exit_code == 0
        assert result.additional_contexts is None


# ---------------------------------------------------------------------------
# Production-path E2E: drive _run_hooks_for_event with a snapshotted prompt
# hook + a context-mounted provider, asserting the response surfaces
# correctly. This is the analog of the D3 production-path test for the
# forked_skill_runner bridge.
# ---------------------------------------------------------------------------


class TestProductionPathPromptHook:
    @pytest.mark.asyncio
    async def test_prompt_hook_e2e_through_run_hooks_for_event(self):
        """The headline D5 production-path test.

        Setup: snapshot has a single prompt hook for PreToolUse.
        Context has provider mounted (mocked). Executor drives
        ``_run_hooks_for_event`` for PreToolUse + Bash; the prompt hook
        fires through the dispatch path and the response surfaces in
        the aggregated decision.

        Pre-D5 this test would have failed: the executor would have
        called ``_execute_command_hook`` on a hook with
        ``type="prompt"`` and ``command=""``, spawning an empty
        subprocess.
        """
        prompt_hook = HookConfig(
            type="prompt",
            prompt_text="Validate this Bash call: {tool_name}",
            source=HookSource.USER_SETTINGS,
        )

        mock_response = MagicMock()
        mock_response.content = "LLM verdict: looks fine"
        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(return_value=mock_response)

        manager = _manager_with({"PreToolUse": [prompt_hook]})
        ctx = _MockContext(
            hook_config_manager=manager,
            provider=mock_provider,
            model="claude-sonnet-4",
        )

        # Drive the executor's full pipeline: collect → dispatch by
        # type → aggregate.
        yields: list[dict[str, Any]] = []
        async for item in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_use_id": "u1"},
            ctx,
        ):
            yields.append(item)

        # The LLM was called.
        assert mock_provider.chat_async.called
        # Provider received the rendered prompt template.
        sent_messages = mock_provider.chat_async.call_args.kwargs["messages"]
        assert "Validate this Bash call: Bash" in sent_messages[0]["content"]

        # Aggregated additional_contexts carries the LLM response.
        agg_yields = [y for y in yields if "additional_contexts" in y]
        assert len(agg_yields) >= 1
        # Locate the response among additional_contexts.
        all_contexts = []
        for y in agg_yields:
            ac = y.get("additional_contexts")
            if ac:
                all_contexts.extend(ac)
        assert any("LLM verdict" in c for c in all_contexts), (
            f"Prompt hook response did not surface as additional_context "
            f"through the production dispatch path. Saw: {all_contexts!r}"
        )

    @pytest.mark.asyncio
    async def test_pre_d5_silent_failure_no_longer_silent(self):
        # Pre-D5: prompt hook with no provider configured silently
        # spawned an empty subprocess (because dispatch was always
        # _execute_command_hook). Now: dispatch routes correctly to
        # execute_prompt_hook which returns blocking_error.
        prompt_hook = HookConfig(
            type="prompt",
            prompt_text="x",
            source=HookSource.USER_SETTINGS,
        )
        manager = _manager_with({"PreToolUse": [prompt_hook]})
        # Context with NO provider mounted.
        ctx = _MockContext(hook_config_manager=manager, provider=None)

        yields: list[dict[str, Any]] = []
        async for item in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_use_id": "u1"},
            ctx,
        ):
            yields.append(item)

        # Expected: an aggregated blocking_error mentioning provider
        # configuration.
        blocking_yields = [y for y in yields if "blocking_error" in y]
        assert len(blocking_yields) == 1
        msg = str(blocking_yields[0])
        assert "provider" in msg.lower()


# ---------------------------------------------------------------------------
# Sub-agent context inheritance for provider/model (matches D3 pattern)
# ---------------------------------------------------------------------------


class TestSubAgentInheritsProvider:
    def test_subagent_inherits_provider_and_model(self):
        # When a sub-agent context is built from a parent context, the
        # parent's provider and model must propagate. Otherwise a hook
        # firing inside a sub-agent's tool call would hit the
        # no-provider blocking_error.
        from src.agent.subagent_context import create_subagent_context, SubagentContextOverrides
        from src.tool_system.context import ToolContext
        from src.permissions.types import ToolPermissionContext

        mock_provider = MagicMock()
        parent = ToolContext(
            workspace_root=Path("/tmp"),
            permission_context=ToolPermissionContext(mode="bypassPermissions"),
        )
        parent.provider = mock_provider
        parent.model = "claude-sonnet-4"

        sub = create_subagent_context(parent, overrides=SubagentContextOverrides())
        assert sub.provider is mock_provider
        assert sub.model == "claude-sonnet-4"
