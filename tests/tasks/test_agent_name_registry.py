"""WI-6.1 tests — agent name registry + collision policy."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.permissions.types import ToolPermissionContext
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall
from src.types.content_blocks import TextBlock
from src.types.messages import AssistantMessage


# ---------------------------------------------------------------------------
# Schema — name field declared
# ---------------------------------------------------------------------------


def test_agent_input_schema_declares_name_field() -> None:
    from src.tool_system.tools.agent import AGENT_INPUT_SCHEMA

    assert "name" in AGENT_INPUT_SCHEMA["properties"]
    assert AGENT_INPUT_SCHEMA["properties"]["name"]["type"] == "string"
    # Name is OPTIONAL — not in `required`.
    assert "name" not in AGENT_INPUT_SCHEMA["required"]


# ---------------------------------------------------------------------------
# Registry round-trip
# ---------------------------------------------------------------------------


def test_registry_field_default_empty(tmp_path: Path) -> None:
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )
    assert len(ctx.agent_name_registry) == 0


def test_named_async_agent_registers_name_to_id(tmp_path: Path) -> None:
    """Spawn with ``name="researcher"`` populates
    ``ctx.agent_name_registry["researcher"]``."""
    registry = build_default_registry(provider=object())
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )

    async def _fake(_params):
        yield AssistantMessage(content=[TextBlock(text="ok")])

    with patch("src.tool_system.tools.agent.run_agent", _fake):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "named",
                    "prompt": "x",
                    "name": "researcher",
                    "run_in_background": True,
                },
            ),
            ctx,
        )

    task_id = str(result.output["agent_id"])
    assert ctx.agent_name_registry.get("researcher") == task_id


def test_unnamed_spawn_does_not_register(tmp_path: Path) -> None:
    """No ``name`` → registry stays empty."""
    registry = build_default_registry(provider=object())
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )

    async def _fake(_params):
        yield AssistantMessage(content=[TextBlock(text="ok")])

    with patch("src.tool_system.tools.agent.run_agent", _fake):
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "unnamed",
                    "prompt": "x",
                    "run_in_background": True,
                },
            ),
            ctx,
        )

    assert len(ctx.agent_name_registry) == 0


# ---------------------------------------------------------------------------
# Collision policy
# ---------------------------------------------------------------------------


def test_collision_with_running_agent_raises(tmp_path: Path) -> None:
    """Spawning with a name already registered to a running task is
    an error — the model can re-attach via SendMessage rather than
    silently overwriting.

    ``ToolInputError`` propagates as a raised exception (matches the
    existing pattern for "missing prompt" / "unknown subagent_type"
    errors in ``_agent_call``); the dispatcher does not wrap input
    errors into is_error results."""
    from src.tool_system.errors import ToolInputError

    registry = build_default_registry(provider=object())
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )

    # Pre-populate the registry as if a running agent was already
    # registered.
    from src.tasks.local_agent import register_async_agent
    existing = register_async_agent(
        agent_id="a-existing", description="x", prompt="x",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )
    ctx.agent_name_registry._mapping["researcher"] = existing.id

    async def _fake(_params):
        yield AssistantMessage(content=[TextBlock(text="ok")])

    with patch("src.tool_system.tools.agent.run_agent", _fake):
        with pytest.raises(ToolInputError, match="already registered"):
            registry.dispatch(
                ToolCall(
                    name="Agent",
                    input={
                        "description": "collision",
                        "prompt": "x",
                        "name": "researcher",
                        "run_in_background": True,
                    },
                ),
                ctx,
            )

    # Original agent still in the registry; name still maps to it.
    assert ctx.agent_name_registry.get("researcher") == "a-existing"


# ---------------------------------------------------------------------------
# C1 — concurrent same-name spawn must NOT both succeed
# ---------------------------------------------------------------------------


def test_concurrent_same_name_spawn_only_one_wins(tmp_path: Path) -> None:
    """Critic Chunk-F-Phase-6 concern C1: the original Phase-6
    implementation had a TOCTOU window between the read of the
    existing binding and the write of the new one. Phase-7 wraps
    the registry as a typed ``AgentNameRegistry`` with an atomic
    ``claim_or_raise``. This test forces a literal race via
    ``threading.Barrier(2)`` and asserts only one of two concurrent
    same-name spawns wins; the other gets ``AgentNameAlreadyClaimedError``
    (translated to ``ToolInputError`` at the agent-tool boundary).

    Pre-fix: a serial coordinator-mode test would mask this race, but
    Phase-7's coordinator pattern explicitly spawns workers in
    parallel — at which point the second-spawn-with-same-name would
    silently steal the binding. This guard prevents the silent-steal
    regression.
    """
    import threading

    from src.services.swarm.agent_name_registry import (
        AgentNameAlreadyClaimedError,
        AgentNameRegistry,
    )
    from src.task_registry import RuntimeTaskRegistry
    from src.tasks.local_agent import register_async_agent

    runtime = RuntimeTaskRegistry()
    name_registry = AgentNameRegistry()

    # Pre-register two RUNNING agents with distinct ids; the test
    # races their name claims.
    register_async_agent(
        agent_id="a-1", description="x", prompt="x",
        agent_type="general-purpose", registry=runtime,
    )
    register_async_agent(
        agent_id="a-2", description="x", prompt="x",
        agent_type="general-purpose", registry=runtime,
    )

    barrier = threading.Barrier(2)
    outcomes: dict[str, str | type] = {}

    def claim_thread(label: str, agent_id: str) -> None:
        barrier.wait()  # both threads enter at the same instant
        try:
            name_registry.claim_or_raise("researcher", agent_id, runtime)
            outcomes[label] = "ok"
        except AgentNameAlreadyClaimedError:
            outcomes[label] = "raised"

    t1 = threading.Thread(target=claim_thread, args=("first", "a-1"))
    t2 = threading.Thread(target=claim_thread, args=("second", "a-2"))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # Exactly one thread wins; the other raises.
    statuses = list(outcomes.values())
    assert sorted(statuses) == ["ok", "raised"], (
        f"expected one ok / one raised under literal race; got {outcomes}"
    )
    # The registry holds the winner's binding.
    bound = name_registry.get("researcher")
    assert bound in {"a-1", "a-2"}


def test_collision_with_terminal_agent_overwrites(tmp_path: Path) -> None:
    """If the existing entry is terminal (completed/failed/killed),
    the new spawn overwrites the name → agent_id mapping. Older
    terminal holders remain reachable via raw task_id."""
    registry = build_default_registry(provider=object())
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )

    from src.tasks.local_agent import (
        complete_agent_task,
        register_async_agent,
    )
    existing = register_async_agent(
        agent_id="a-old", description="x", prompt="x",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )
    complete_agent_task("a-old", result_text="done", registry=ctx.runtime_tasks)
    ctx.agent_name_registry._mapping["researcher"] = existing.id

    async def _fake(_params):
        yield AssistantMessage(content=[TextBlock(text="ok")])

    with patch("src.tool_system.tools.agent.run_agent", _fake):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "fresh",
                    "prompt": "x",
                    "name": "researcher",
                    "run_in_background": True,
                },
            ),
            ctx,
        )

    new_id = str(result.output["agent_id"])
    assert result.is_error is False
    # Name now points at the new spawn.
    assert ctx.agent_name_registry.get("researcher") == new_id
    assert new_id != "a-old"
    # Old terminal entry still in the registry (reachable by raw ID).
    assert ctx.runtime_tasks.get("a-old") is not None
