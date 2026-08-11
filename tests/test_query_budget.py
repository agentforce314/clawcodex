from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.providers.base import ChatResponse
from src.query.budget import BudgetGuard, resolve_max_turns
from src.query.query import QueryParams, query
from src.query.transitions import TerminalHolder
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import SystemMessage, UserMessage
from src.utils.abort_controller import AbortController


def test_budget_guard_zero_limits_are_unlimited() -> None:
    guard = BudgetGuard(max_turns=0, max_cost_usd=0.0)
    assert guard.check(turn_count=10_000, cost_usd=10_000.0) is None


def test_budget_guard_checks_all_dimensions_deterministically() -> None:
    now = [50.0]
    guard = BudgetGuard(
        max_turns=3,
        max_cost_usd=1.5,
        max_input_tokens=100,
        max_output_tokens=20,
        deadline=60.0,
        clock=lambda: now[0],
    )
    assert guard.check(turn_count=4).reason == "max_turns"
    assert guard.check(cost_usd=1.5).reason == "max_cost"
    assert guard.check(input_tokens=100).reason == "max_input_tokens"
    assert guard.check(output_tokens=20).reason == "max_output_tokens"
    now[0] = 60.0
    assert guard.check().reason == "deadline"


def test_resolve_max_turns_precedence() -> None:
    assert resolve_max_turns(7, 9, default=50) == 7
    assert resolve_max_turns(None, 9, default=50) == 9
    assert resolve_max_turns(None, 0, default=50) == 50
    assert resolve_max_turns(0, 9, default=50) == 0


def test_max_cost_stops_before_tool_side_effect(tmp_path: Path) -> None:
    registry = build_default_registry()
    provider = MagicMock()
    provider.chat_stream_response.side_effect = NotImplementedError()
    target = tmp_path / "must-not-exist.txt"
    provider.chat.return_value = ChatResponse(
        content="Writing now",
        model="test",
        usage={"input_tokens": 10, "output_tokens": 5},
        finish_reason="tool_use",
        tool_uses=[{
            "id": "toolu_budget",
            "name": "Write",
            "input": {"file_path": str(target), "content": "side effect"},
        }],
    )
    params = QueryParams(
        messages=[UserMessage(content="write it")],
        system_prompt="test",
        tools=registry.list_tools(),
        tool_registry=registry,
        tool_use_context=ToolContext(workspace_root=tmp_path),
        provider=provider,
        abort_controller=AbortController(),
        max_turns=10,
        max_cost_usd=0.5,
    )
    holder = TerminalHolder()
    yielded = []

    async def run() -> None:
        async for message in query(params, terminal_holder=holder):
            yielded.append(message)

    with patch(
        "src.bootstrap.state.get_total_cost_usd",
        side_effect=[0.0, 0.0, 0.75],
    ):
        asyncio.run(run())

    assert holder.value is not None
    assert holder.value.reason == "max_cost"
    assert not target.exists()
    assert any(
        isinstance(message, SystemMessage)
        and message.subtype == "max_cost_reached"
        for message in yielded
    )
