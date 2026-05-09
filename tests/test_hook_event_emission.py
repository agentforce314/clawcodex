"""Phase-6 / WI-6.1 — hook event emission stream regression tests.

Subscribers (UI / SDK / telemetry) register handlers that receive
notifications when hooks fire. The chapter §"Hook Event Emission" specifies
three event types:

  * ``hook_started``    — before each hook executes
  * ``hook_response``   — after each hook returns (per-hook decision)
  * ``hook_aggregated`` — once per executor invocation, post-aggregation
                          (the final decision + contributing_reasons)

Test coverage:
  * Subscriber registration / deregistration.
  * Idempotent unregister (calling the deregister fn twice is safe).
  * Subscriber exception isolation (one handler raising must NOT break
    the executor or other subscribers).
  * Ordering: ``hook_started`` precedes the matching ``hook_response``;
    ``hook_aggregated`` fires AFTER all per-hook responses.
  * Global enable flag suppresses all events.
  * ``clear_hook_event_state`` resets between tests.
  * End-to-end: drive ``_run_hooks_for_event`` with a configured snapshot
    and observe the full event stream.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.hooks.aggregation import AggregatedHookResult
from src.hooks.config_manager import HookConfigManager, HookConfigSnapshot
from src.hooks.events import (
    clear_hook_event_state,
    emit_hook_aggregated,
    emit_hook_response,
    emit_hook_started,
    register_hook_event_handler,
    set_all_hook_events_enabled,
)
from src.hooks.hook_executor import _run_hooks_for_event
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
    tool_use_id: str | None = None


def _manager_with(hooks: dict[str, list[HookConfig]]) -> HookConfigManager:
    m = HookConfigManager(registry=AsyncHookRegistry(), settings_path="/dev/null")
    m._snapshot = HookConfigSnapshot(hooks=hooks, timestamp=0.0, source_path=None)
    return m


@pytest.fixture(autouse=True)
def _isolate_event_stream():
    """Reset the module-level subscriber list between tests so one
    test's leftover handlers don't leak into the next.
    """
    clear_hook_event_state()
    yield
    clear_hook_event_state()


# ---------------------------------------------------------------------------
# Subscriber registration / deregistration
# ---------------------------------------------------------------------------


class TestSubscription:
    def test_subscriber_receives_started_event(self):
        events: list[dict] = []
        register_hook_event_handler(events.append)
        emit_hook_started(hook_id="h1", event="PreToolUse", command="x")
        assert len(events) == 1
        assert events[0]["type"] == "hook_started"
        assert events[0]["hook_id"] == "h1"

    def test_subscriber_receives_response_event(self):
        events: list[dict] = []
        register_hook_event_handler(events.append)
        emit_hook_response(
            hook_id="h1", event="PreToolUse",
            exit_code=0, duration_ms=42,
        )
        assert len(events) == 1
        assert events[0]["type"] == "hook_response"
        assert events[0]["exit_code"] == 0

    def test_subscriber_receives_aggregated_event(self):
        events: list[dict] = []
        register_hook_event_handler(events.append)
        agg = AggregatedHookResult(permission_behavior="allow")
        emit_hook_aggregated(event="PreToolUse", aggregated=agg)
        assert len(events) == 1
        assert events[0]["type"] == "hook_aggregated"
        assert events[0]["aggregated"] is agg

    def test_deregister_stops_delivery(self):
        events: list[dict] = []
        deregister = register_hook_event_handler(events.append)
        emit_hook_started(hook_id="h1", event="PreToolUse")
        deregister()
        emit_hook_started(hook_id="h2", event="PreToolUse")
        # Only h1 was delivered.
        assert len(events) == 1
        assert events[0]["hook_id"] == "h1"

    def test_idempotent_deregister(self):
        events: list[dict] = []
        deregister = register_hook_event_handler(events.append)
        deregister()
        # Second call must NOT raise.
        deregister()
        emit_hook_started(hook_id="x", event="PreToolUse")
        assert events == []


# ---------------------------------------------------------------------------
# Subscriber exception isolation
# ---------------------------------------------------------------------------


class TestExceptionIsolation:
    def test_handler_exception_does_not_break_dispatch(self):
        # First handler raises; second handler must STILL be called.
        events: list[dict] = []

        def crashing_handler(_event):
            raise RuntimeError("subscriber crash")

        register_hook_event_handler(crashing_handler)
        register_hook_event_handler(events.append)

        # Dispatch must NOT raise.
        emit_hook_started(hook_id="h", event="PreToolUse")

        # Second subscriber received the event.
        assert len(events) == 1

    def test_handler_exception_isolated_per_event(self):
        # Each event dispatch starts fresh — a crashing handler doesn't
        # taint the next emit call.
        crashes = {"count": 0}

        def crashing_handler(_event):
            crashes["count"] += 1
            raise ValueError(f"crash #{crashes['count']}")

        register_hook_event_handler(crashing_handler)

        emit_hook_started(hook_id="a", event="PreToolUse")
        emit_hook_started(hook_id="b", event="PreToolUse")
        emit_hook_started(hook_id="c", event="PreToolUse")

        # All three emit calls completed (didn't propagate the
        # ValueError out); the handler was called 3 times.
        assert crashes["count"] == 3


# ---------------------------------------------------------------------------
# Global enable flag
# ---------------------------------------------------------------------------


class TestGlobalEnableFlag:
    def test_set_enabled_false_suppresses_all(self):
        events: list[dict] = []
        register_hook_event_handler(events.append)
        set_all_hook_events_enabled(False)
        emit_hook_started(hook_id="x", event="PreToolUse")
        emit_hook_response(
            hook_id="x", event="PreToolUse", exit_code=0, duration_ms=1,
        )
        assert events == []

    def test_re_enable_resumes_delivery(self):
        events: list[dict] = []
        register_hook_event_handler(events.append)
        set_all_hook_events_enabled(False)
        emit_hook_started(hook_id="x", event="PreToolUse")
        set_all_hook_events_enabled(True)
        emit_hook_started(hook_id="y", event="PreToolUse")
        assert len(events) == 1
        assert events[0]["hook_id"] == "y"


# ---------------------------------------------------------------------------
# clear_hook_event_state
# ---------------------------------------------------------------------------


class TestClearState:
    def test_clear_removes_all_subscribers(self):
        events: list[dict] = []
        register_hook_event_handler(events.append)
        clear_hook_event_state()
        emit_hook_started(hook_id="x", event="PreToolUse")
        assert events == []


# ---------------------------------------------------------------------------
# End-to-end: emission via _run_hooks_for_event
# ---------------------------------------------------------------------------


class TestExecutorEmission:
    @pytest.mark.asyncio
    async def test_run_hooks_for_event_emits_started_response_aggregated(
        self, tmp_path,
    ):
        # Driving a real executor invocation produces the full event
        # sequence: started → response → aggregated.
        hook = HookConfig(
            type="command",
            command="echo emission-test",
            source=HookSource.USER_SETTINGS,
        )
        manager = _manager_with({"PreToolUse": [hook]})
        ctx = _MockContext(hook_config_manager=manager)

        events: list[dict] = []
        register_hook_event_handler(events.append)

        async for _ in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_use_id": "u1"},
            ctx,
        ):
            pass

        # Filter to the three event types we care about.
        types = [e["type"] for e in events]
        assert "hook_started" in types
        assert "hook_response" in types
        assert "hook_aggregated" in types

        # Ordering: started precedes the matching response, response
        # precedes the aggregated. We have ONE hook so the ordering is
        # easy to assert.
        started_idx = types.index("hook_started")
        response_idx = types.index("hook_response")
        aggregated_idx = types.index("hook_aggregated")
        assert started_idx < response_idx < aggregated_idx

    @pytest.mark.asyncio
    async def test_aggregated_event_carries_aggregation_payload(
        self, tmp_path,
    ):
        # The ``hook_aggregated`` payload carries the
        # AggregatedHookResult so subscribers don't need to track
        # individual responses to derive the final decision.
        hook = HookConfig(
            type="command",
            command='echo \'{"decision": "allow", "reason": "ok"}\'',
            source=HookSource.USER_SETTINGS,
        )
        manager = _manager_with({"PreToolUse": [hook]})
        ctx = _MockContext(hook_config_manager=manager)

        events: list[dict] = []
        register_hook_event_handler(events.append)

        async for _ in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_use_id": "u1"},
            ctx,
        ):
            pass

        agg_events = [e for e in events if e["type"] == "hook_aggregated"]
        assert len(agg_events) == 1
        agg = agg_events[0]["aggregated"]
        assert isinstance(agg, AggregatedHookResult)
        assert agg.permission_behavior == "allow"
        assert agg.hook_permission_decision_reason == "ok"

    @pytest.mark.asyncio
    async def test_no_aggregated_event_when_no_hooks_fire(self):
        # If the snapshot has no hooks for the event, no events fire at
        # all — no started, no response, no aggregated.
        manager = _manager_with({})  # empty
        ctx = _MockContext(hook_config_manager=manager)

        events: list[dict] = []
        register_hook_event_handler(events.append)

        async for _ in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash"},
            ctx,
        ):
            pass

        assert events == []

    @pytest.mark.asyncio
    async def test_subscriber_crash_does_not_break_executor(self):
        # End-to-end version of TestExceptionIsolation: a handler that
        # raises must NOT propagate up through the executor.
        hook = HookConfig(
            type="command", command="echo x",
            source=HookSource.USER_SETTINGS,
        )
        manager = _manager_with({"PreToolUse": [hook]})
        ctx = _MockContext(hook_config_manager=manager)

        def crashing(_event):
            raise RuntimeError("subscriber boom")

        register_hook_event_handler(crashing)

        # Executor must complete without raising.
        results = []
        async for item in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_use_id": "u1"},
            ctx,
        ):
            results.append(item)
        # Hook still ran (we got progress + attachment messages).
        assert len(results) > 0
