"""Real agent/query/tool lifecycles with only the external model replaced."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall
from src.utils.message_queue_manager import clear_pending_notifications


class ResearchProvider:
    """Read a fixture file, then answer; record follow-ups including history."""

    model = "runtime-test"

    def __init__(self, source: Path) -> None:
        self.source = source
        self.requests: list[str] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_next = False
        self.always_use_tool = False

    def chat_stream_response(self, *args, **kwargs):
        raise NotImplementedError

    def chat(self, messages, tools=None, **kwargs):
        request = json.dumps(messages, default=str)
        self.requests.append(request)
        if self.block_next:
            self.block_next = False
            self.entered.set()
            assert self.release.wait(8), "test did not release provider"
        if "CORRECTION" in request and not self.always_use_tool:
            content, calls = "CORRECTION processed with prior findings", None
        elif "fixture finding" in request and not self.always_use_tool:
            content, calls = "Research finished: fixture finding", None
        else:
            content = "Reading the source"
            calls = [
                {
                    "id": f"read-source-{len(self.requests)}",
                    "name": "Read",
                    "input": {
                        "file_path": str(self.source),
                    },
                }
            ]
        return ChatResponse(
            content=content,
            model=self.model,
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="tool_use" if calls else "stop",
            tool_uses=calls,
        )


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path / "config"))
    source = tmp_path / "source.txt"
    source.write_text("fixture finding\n")
    provider = ResearchProvider(source)
    context = ToolContext(workspace_root=tmp_path)
    registry = build_default_registry(provider=provider)
    clear_pending_notifications()
    yield provider, context, registry
    provider.release.set()
    context.abort_controller.abort()
    from src.tasks.local_agent import kill_async_agent

    for state in context.runtime_tasks.all():
        kill_async_agent(state.id, context.runtime_tasks, enqueue_notification=False)
    for task in context.task_manager.list():
        task.thread.join(timeout=10)
    clear_pending_notifications()


def dispatch(registry, context, tool_name, **input):
    if tool_name == "Agent":
        input.setdefault("description", "Runtime verification")
    result = registry.dispatch(ToolCall(name=tool_name, input=input), context)
    assert not result.is_error, result.output
    return result.output


def wait_finished(context, agent_id, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = context.runtime_tasks.get(agent_id)
        if state and state.status in {"completed", "failed", "killed"}:
            # The status transition precedes the worker's final cleanup.
            if not context.agent_supervisor.snapshot()["active"]:
                return state
        time.sleep(0.01)
    pytest.fail(f"worker did not finish: {context.runtime_tasks.get(agent_id)}")


def test_nested_notifications_reach_the_parent_and_orphans_reach_the_session(runtime):
    from src.agent.subagent_context import (
        SubagentContextOverrides,
        create_subagent_context,
    )
    from src.query.query import _drain_pending_user_messages
    from src.utils.message_queue_manager import (
        drain_pending_notifications,
        enqueue_pending_notification,
    )

    _, context, registry = runtime
    parent = create_subagent_context(
        context, SubagentContextOverrides(agent_id="parent-worker")
    )
    launched = dispatch(
        registry, parent, "Agent", prompt="Read the source", run_in_background=True
    )
    state = wait_finished(context, launched["agent_id"])
    assert state.status == "completed"
    assert state.notification_recipient == "parent-worker"
    assert not drain_pending_notifications(
        scope=context.runtime_tasks, recipient=None, active_recipients={"parent-worker"}
    )
    messages = _drain_pending_user_messages(parent)
    assert len(messages) == 1 and "fixture finding" in messages[0].content
    assert not _drain_pending_user_messages(parent)
    enqueue_pending_notification(
        value="late child result",
        scope=context.runtime_tasks,
        recipient="parent-worker",
    )
    assert [
        n.value
        for n in drain_pending_notifications(
            scope=context.runtime_tasks, recipient=None, active_recipients=set()
        )
    ] == ["late child result"]


def test_background_worker_reads_source_and_resumes_with_history(runtime):
    provider, context, registry = runtime
    launched = dispatch(
        registry,
        context,
        "Agent",
        name="researcher",
        description="Inspect source",
        prompt="Inspect source ownership",
        run_in_background=True,
    )
    agent_id = launched["agent_id"]
    state = wait_finished(context, agent_id)
    assert state.status == "completed"
    assert "fixture finding" in state.result_text
    assert len(provider.requests) == 2  # the Read tool really executed
    dispatch(
        registry,
        context,
        "SendMessage",
        to="researcher",
        message="CORRECTION: include session expiry",
        summary="Inspect session expiry too",
    )
    state = wait_finished(context, agent_id)
    assert state.status == "completed"
    assert "CORRECTION processed" in state.result_text
    assert "Inspect source ownership" in provider.requests[-1]
    assert "Research finished: fixture finding" in provider.requests[-1]
    assert context.agent_name_registry.get("researcher") == agent_id


def test_message_arriving_during_final_response_is_not_lost(runtime):
    provider, context, registry = runtime
    # Complete once so the next provider response has no tool use. A correction
    # accepted while that response is in flight still needs another model turn.
    launched = dispatch(
        registry,
        context,
        "Agent",
        name="researcher",
        description="Inspect source",
        prompt="fixture finding",
        run_in_background=True,
    )
    wait_finished(context, launched["agent_id"])
    provider.block_next = True
    resumed = registry.dispatch(
        ToolCall(
            name="SendMessage",
            input={
                "to": "researcher",
                "message": "Review your previous findings",
                "summary": "Review findings",
            },
        ),
        context,
    )
    assert not resumed.is_error, resumed.output
    assert provider.entered.wait(5), "resume never started a model request"
    try:
        dispatch(
            registry,
            context,
            "SendMessage",
            to="researcher",
            message="CORRECTION during final response",
            summary="Late correction",
        )
    finally:
        provider.release.set()
    state = wait_finished(context, launched["agent_id"])
    assert "CORRECTION processed" in state.result_text
    assert not state.pending_messages


def test_concurrent_sends_resume_once_and_process_both_messages(runtime):
    provider, context, registry = runtime
    launched = dispatch(
        registry,
        context,
        "Agent",
        prompt="fixture finding",
        description="Research",
        run_in_background=True,
    )
    agent_id = launched["agent_id"]
    wait_finished(context, agent_id)
    provider.block_next = True

    def send(letter):
        return dispatch(
            registry,
            context,
            "SendMessage",
            to=agent_id,
            message=f"CORRECTION {letter}",
            summary=f"Correction {letter}",
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, ["A", "B"]))
        assert all(r["success"] for r in results)
        assert provider.entered.wait(5)
        assert context.agent_supervisor.live_count() == 1
    finally:
        provider.release.set()
    state = wait_finished(context, agent_id)
    assert state.status == "completed"
    assert "CORRECTION A" in provider.requests[-1]
    assert "CORRECTION B" in provider.requests[-1]


def test_resume_survives_hud_eviction_and_obeys_pause(runtime):
    provider, context, registry = runtime
    launched = dispatch(
        registry,
        context,
        "Agent",
        name="researcher",
        prompt="fixture finding",
        run_in_background=True,
    )
    agent_id = launched["agent_id"]
    original = wait_finished(context, agent_id)
    context.agent_supervisor.set_paused(True)
    denied = registry.dispatch(
        ToolCall(
            name="SendMessage",
            input={
                "to": "researcher",
                "message": "CORRECTION",
                "summary": "Follow up",
            },
        ),
        context,
    )
    assert denied.is_error
    assert context.runtime_tasks.get(agent_id) == original
    assert len(provider.requests) == 1
    context.agent_supervisor.set_paused(False)
    context.runtime_tasks.remove(agent_id)
    dispatch(
        registry,
        context,
        "SendMessage",
        to="researcher",
        message="CORRECTION after eviction",
        summary="Follow up",
    )
    assert "CORRECTION processed" in wait_finished(context, agent_id).result_text


def test_parallel_named_spawns_have_one_live_owner(runtime):
    from src.tool_system.errors import ToolInputError

    provider, context, registry = runtime
    provider.block_next = True

    def spawn(_):
        try:
            return dispatch(
                registry,
                context,
                "Agent",
                name="same-name",
                prompt="fixture finding",
                run_in_background=True,
            )
        except ToolInputError:
            return None

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(spawn, range(8)))
        winners = [r for r in results if r is not None]
        assert len(winners) == 1
        assert context.agent_supervisor.live_count() == 1
        assert len(context.runtime_tasks.all()) == 1
    finally:
        provider.release.set()
    assert wait_finished(context, winners[0]["agent_id"]).status == "completed"


def test_foreground_delegation_executes_real_tool_and_returns_output(runtime):
    provider, context, registry = runtime
    result = dispatch(registry, context, "Agent", prompt="Inspect source ownership")
    assert result["status"] == "completed"
    assert "fixture finding" in str(result)
    assert len(provider.requests) == 2
    assert context.agent_supervisor.live_count() == 0


def test_max_turns_is_failure_and_not_a_completed_delegation(runtime):
    from dataclasses import replace

    from src.agent.agent_definitions import get_built_in_agents

    provider, context, registry = runtime
    provider.always_use_tool = True
    definition = next(
        d for d in get_built_in_agents() if d.agent_type == "general-purpose"
    )
    context.options.agent_definitions = {
        "active_agents": [replace(definition, max_turns=1)]
    }
    launched = dispatch(
        registry, context, "Agent", prompt="Keep reading", run_in_background=True
    )
    state = wait_finished(context, launched["agent_id"])
    assert state.status == "failed"
    assert "max_turns" in state.error
    assert context.agent_supervisor.live_count() == 0


def test_two_worker_sessions_receive_only_their_own_completion(runtime):
    from xml.etree import ElementTree

    from src.utils.message_queue_manager import drain_pending_notifications

    provider, first, registry = runtime
    second = ToolContext(workspace_root=first.workspace_root)
    first_result = dispatch(
        registry,
        first,
        "Agent",
        prompt="fixture finding",
        description="Parser <ownership> & expiry",
        run_in_background=True,
    )
    second_result = dispatch(
        registry, second, "Agent", prompt="fixture finding", run_in_background=True
    )
    wait_finished(first, first_result["agent_id"])
    wait_finished(second, second_result["agent_id"])
    for context, result in [(first, first_result), (second, second_result)]:
        notices = drain_pending_notifications(scope=context.runtime_tasks)
        assert len(notices) == 1
        envelope = ElementTree.fromstring(notices[0].value)
        assert envelope.findtext("task-id") == result["agent_id"]
        assert envelope.findtext("status") == "completed"
        if context is first:
            assert "Parser <ownership> & expiry" in envelope.findtext("summary")
        assert drain_pending_notifications(scope=context.runtime_tasks) == []


def test_workflow_tool_enforces_budget_through_real_worker_runs(runtime):
    provider, context, registry = runtime
    script = (
        'meta = {"name": "budget-test", "description": "Bound a worker queue"}\n'
        'return await parallel([agent("fixture finding") for _ in range(10)])'
    )
    launched = dispatch(
        registry, context, "Workflow", script=script, budget_total=30, max_concurrent=1
    )
    state = wait_finished(context, launched["task_id"])
    assert state.status == "completed"
    assert len(provider.requests) == 2  # 15 tokens each; later work never starts
    assert sum(value is not None for value in state.result) == 2
    assert state.run._budget.spent() == 30


def test_workflow_can_be_stopped_before_its_thread_enters_the_engine(
    runtime, monkeypatch
):
    provider, context, registry = runtime
    gate = threading.Event()
    original_start = context.task_manager.start

    def delayed_start(*, name, target):
        def delayed(stop):
            assert gate.wait(8)
            target(stop)

        return original_start(name=name, target=delayed)

    monkeypatch.setattr(context.task_manager, "start", delayed_start)
    script = (
        'meta = {"name": "early-stop", "description": "Stop before startup"}\n'
        'return await agent("fixture finding")'
    )
    try:
        launched = dispatch(registry, context, "Workflow", script=script)
        assert context.runtime_tasks.get(launched["task_id"]).status == "running"
        dispatch(registry, context, "TaskStop", task_id=launched["task_id"])
    finally:
        gate.set()
    for task in context.task_manager.list():
        task.thread.join(timeout=8)
        assert not task.thread.is_alive()
    assert context.runtime_tasks.get(launched["task_id"]).status == "killed"
    assert not provider.requests
    from src.utils.message_queue_manager import drain_pending_notifications

    notices = drain_pending_notifications(scope=context.runtime_tasks)
    assert len(notices) == 1 and "<status>killed</status>" in notices[0].value


def test_workflow_worker_is_visible_and_task_stop_reaps_it(runtime):
    provider, context, registry = runtime
    provider.block_next = True
    script = (
        'meta = {"name": "stop-test", "description": "Stop a worker"}\n'
        'return await agent("fixture finding")'
    )
    launched = dispatch(registry, context, "Workflow", script=script)
    assert provider.entered.wait(5)
    try:
        assert context.agent_supervisor.live_count() == 1
        dispatch(registry, context, "TaskStop", task_id=launched["task_id"])
    finally:
        provider.release.set()
    state = wait_finished(context, launched["task_id"])
    assert state.status == "killed"
    assert context.agent_supervisor.live_count() == 0


async def test_failed_workflow_attempt_still_charges_observed_usage(runtime):
    from src.agent.agent_definitions import get_built_in_agents
    from src.utils.abort_controller import AbortController
    from src.workflow.runner import LiveAgentRunner
    from src.workflow.types import AgentSpec

    provider, context, registry = runtime
    provider.always_use_tool = True
    definition = next(
        agent
        for agent in get_built_in_agents()
        if agent.agent_type == "general-purpose"
    )
    runner = LiveAgentRunner(
        provider=provider,
        tool_registry=registry,
        parent_context=context,
        base_tools=registry.list_tools(),
        resolve_agent=lambda _: definition,
        max_turns=1,
    )
    outcome = await runner.run(
        AgentSpec(prompt="Read repeatedly"), abort=AbortController(), index="0"
    )
    assert outcome.error and "max_turns" in outcome.error
    assert outcome.tokens == 15
    assert outcome.tool_use_count == 1
    assert context.agent_supervisor.live_count() == 0
