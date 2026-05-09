"""Phase-9 / WI-9.1 — callback hook type tests.

Closes gap analysis #12. Callback hooks are an in-process Python
callable invoked synchronously by the executor — no subprocess fork
(unlike command), no LLM call (unlike agent/prompt), no HTTP (unlike
http). The chapter cites a ~70% latency reduction over command hooks;
the Python equivalent skips the subprocess + JSON round-trip.

Coverage:
  * Sync callback fires in-process; HookResult flows through.
  * Async callback awaited; HookResult flows through.
  * Returning ``None`` is treated as no-op success.
  * Returning unsupported type → blocking_error.
  * Callback raising → blocking_error (exception isolation).
  * No ``callback_ref`` → blocking_error.
  * Decision routes through aggregation; emission stream fires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.hooks.aggregation import AggregatedHookResult
from src.hooks.config_manager import HookConfigManager, HookConfigSnapshot
from src.hooks.events import (
    clear_hook_event_state,
    register_hook_event_handler,
)
from src.hooks.exec_callback_hook import execute_callback_hook
from src.hooks.hook_executor import _dispatch_hook_by_type, _run_hooks_for_event
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


@pytest.fixture(autouse=True)
def _isolate_event_stream():
    clear_hook_event_state()
    yield
    clear_hook_event_state()


# ---------------------------------------------------------------------------
# Direct execute_callback_hook tests
# ---------------------------------------------------------------------------


class TestCallbackHookDirect:
    @pytest.mark.asyncio
    async def test_sync_callback_returns_hookresult(self):
        from src.hooks.hook_types import HookResult

        captured: dict[str, Any] = {}

        def my_cb(event_data):
            captured["event"] = event_data.get("hook_event")
            return HookResult(
                exit_code=0,
                additional_contexts=["audit log entry"],
            )

        hook = HookConfig(type="callback", callback_ref=my_cb)
        result = await execute_callback_hook(
            hook, {"hook_event": "PreToolUse", "tool_name": "Bash"},
        )

        assert captured["event"] == "PreToolUse"
        assert result.exit_code == 0
        assert result.additional_contexts == ["audit log entry"]

    @pytest.mark.asyncio
    async def test_async_callback_awaited(self):
        from src.hooks.hook_types import HookResult

        async def my_async_cb(event_data):
            return HookResult(exit_code=0, additional_contexts=["from async"])

        hook = HookConfig(type="callback", callback_ref=my_async_cb)
        result = await execute_callback_hook(hook, {"hook_event": "PreToolUse"})
        assert result.additional_contexts == ["from async"]

    @pytest.mark.asyncio
    async def test_returning_none_is_noop_success(self):
        # Common pattern: a logging callback that doesn't have anything
        # actionable to return.
        def silent(event_data):
            pass

        hook = HookConfig(type="callback", callback_ref=silent)
        result = await execute_callback_hook(hook, {})
        assert result.exit_code == 0
        assert result.blocking_error is None
        assert result.additional_contexts is None

    @pytest.mark.asyncio
    async def test_callback_raising_becomes_blocking_error(self):
        # Exception isolation: callback that raises doesn't propagate
        # up through the executor; converted to blocking_error.
        def crashing(event_data):
            raise RuntimeError("callback boom")

        hook = HookConfig(type="callback", callback_ref=crashing)
        result = await execute_callback_hook(hook, {})
        assert result.blocking_error is not None
        assert "callback boom" in result.blocking_error
        assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_no_callback_ref_blocks(self):
        hook = HookConfig(type="callback", callback_ref=None)
        result = await execute_callback_hook(hook, {})
        assert result.blocking_error is not None
        assert "callback_ref" in result.blocking_error.lower()

    @pytest.mark.asyncio
    async def test_non_callable_callback_ref_blocks(self):
        hook = HookConfig(type="callback", callback_ref="not a callable")
        result = await execute_callback_hook(hook, {})
        assert result.blocking_error is not None
        assert "not callable" in result.blocking_error.lower()

    @pytest.mark.asyncio
    async def test_unsupported_return_type_blocks(self):
        # Programmer error: callback returns something that isn't
        # HookResult or None.
        def bad(event_data):
            return {"decision": "allow"}  # raw dict, not HookResult

        hook = HookConfig(type="callback", callback_ref=bad)
        result = await execute_callback_hook(hook, {})
        assert result.blocking_error is not None
        assert "unsupported type" in result.blocking_error.lower()


# ---------------------------------------------------------------------------
# Dispatcher routes callback type
# ---------------------------------------------------------------------------


class TestDispatcherRoutesCallback:
    @pytest.mark.asyncio
    async def test_dispatch_routes_callback_to_callback_executor(self):
        from src.hooks.hook_types import HookResult

        called = {"n": 0}

        def my_cb(event_data):
            called["n"] += 1
            return HookResult(
                exit_code=0,
                permission_behavior="allow",
                hook_permission_decision_reason="ok",
            )

        hook = HookConfig(type="callback", callback_ref=my_cb)
        result = await _dispatch_hook_by_type(hook, {"hook_event": "PreToolUse"})

        assert called["n"] == 1
        assert result.permission_behavior == "allow"


# ---------------------------------------------------------------------------
# Aggregation + emission for callback hooks
# ---------------------------------------------------------------------------


class TestCallbackAggregationAndEmission:
    @pytest.mark.asyncio
    async def test_callback_decision_routes_through_aggregation(self):
        # Snapshot has a callback hook that denies. The executor
        # collects + aggregates, and the aggregated decision is "deny."
        from src.hooks.hook_types import HookResult

        def deny_cb(event_data):
            return HookResult(
                permission_behavior="deny",
                hook_permission_decision_reason="callback says no",
            )

        callback_hook = HookConfig(
            type="callback",
            callback_ref=deny_cb,
            source=HookSource.SESSION_HOOK,
        )
        manager = _manager_with({"PreToolUse": [callback_hook]})
        ctx = _MockContext(hook_config_manager=manager)

        yields = []
        async for item in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_use_id": "u1"},
            ctx,
        ):
            yields.append(item)

        # Aggregated permission_behavior is deny.
        permission_yields = [y for y in yields if "permission_behavior" in y]
        assert len(permission_yields) == 1
        assert permission_yields[0]["permission_behavior"] == "deny"
        assert "callback says no" in str(permission_yields[0])

    @pytest.mark.asyncio
    async def test_emission_fires_for_callback_hooks(self):
        # The hook event emission stream (Phase-6) fires for callback
        # hooks the same way it fires for command/http/prompt/agent.
        from src.hooks.hook_types import HookResult

        def my_cb(event_data):
            return HookResult(exit_code=0)

        callback_hook = HookConfig(
            type="callback",
            callback_ref=my_cb,
            source=HookSource.SESSION_HOOK,
        )
        manager = _manager_with({"PreToolUse": [callback_hook]})
        ctx = _MockContext(hook_config_manager=manager)

        events: list[dict] = []
        register_hook_event_handler(events.append)

        async for _ in _run_hooks_for_event(
            "PreToolUse", "Bash", {"tool_name": "Bash"}, ctx,
        ):
            pass

        types = [e["type"] for e in events]
        assert "hook_started" in types
        assert "hook_response" in types
        assert "hook_aggregated" in types

    @pytest.mark.asyncio
    async def test_callback_raising_does_not_break_executor(self):
        # End-to-end exception isolation: a callback that raises produces
        # a blocking_error, and the executor pipeline completes.
        def crashing(event_data):
            raise RuntimeError("end-to-end boom")

        callback_hook = HookConfig(
            type="callback",
            callback_ref=crashing,
            source=HookSource.SESSION_HOOK,
        )
        manager = _manager_with({"PreToolUse": [callback_hook]})
        ctx = _MockContext(hook_config_manager=manager)

        yields = []
        async for item in _run_hooks_for_event(
            "PreToolUse", "Bash", {"tool_name": "Bash"}, ctx,
        ):
            yields.append(item)

        # Executor completed (we got yields). Aggregated yield carries
        # the blocking_error.
        blocking = [y for y in yields if "blocking_error" in y]
        assert len(blocking) == 1
        assert "end-to-end boom" in str(blocking[0])
