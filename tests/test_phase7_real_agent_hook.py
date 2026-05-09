"""Phase-7 / WI-7.2 — real agent hook regression tests.

Per chapter §"Agent Hooks":
  * Multi-turn LLM dialogue (each turn validates structured output).
  * 50-turn cap (default; configurable via ``hook.timeout`` / ``max_turns``
    kwarg).
  * ``dontAsk`` permission semantics (irrelevant in this single-skill
    validator implementation; documented + accepted for forward-compat
    with a full run_agent integration that uses tools).
  * Final response: structured JSON matching the Phase-1 ``HookOutput``
    schema. Validated via Pydantic; unknown fields rejected; bad
    decision values rejected.

Pre-Phase-7 ``execute_agent_hook`` was a single-call evaluator with no
turn cap and no schema validation (gap analysis #21). These tests pin
the new contract.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.hooks.exec_agent_hook import DEFAULT_MAX_TURNS, execute_agent_hook
from src.hooks.hook_types import HookConfig


def _make_provider_with_responses(*responses: str):
    """Build a mock provider that yields responses[i] on the i'th call.

    Each response is a string (the LLM's content text). Used to script
    multi-turn behavior: response[0] is the first turn's reply,
    response[1] is the second, etc.
    """
    call_count = {"n": 0}

    async def chat_async(**kwargs):
        idx = call_count["n"]
        if idx >= len(responses):
            # Default to the last response on overflow (simulates an
            # agent that's stuck on the same answer).
            idx = len(responses) - 1
        call_count["n"] += 1
        mock_response = MagicMock()
        mock_response.content = responses[idx]
        return mock_response

    mock_provider = MagicMock()
    mock_provider.chat_async = chat_async
    mock_provider._call_count = call_count  # for inspection
    return mock_provider


# ---------------------------------------------------------------------------
# Single-turn happy path (matches existing exec_agent_hook tests, kept here
# alongside multi-turn tests so the contract surface is in one file)
# ---------------------------------------------------------------------------


class TestSingleTurnDecision:
    @pytest.mark.asyncio
    async def test_valid_decision_first_turn(self):
        provider = _make_provider_with_responses(
            json.dumps({"decision": "allow", "reason": "looks fine"}),
        )
        config = HookConfig(type="agent", agent_instructions="Check Bash calls")
        result = await execute_agent_hook(
            config, {"tool_name": "Bash"}, provider=provider,
        )
        assert result.exit_code == 0
        assert result.permission_behavior == "allow"
        assert result.hook_permission_decision_reason == "looks fine"
        # Only one turn used.
        assert provider._call_count["n"] == 1


# ---------------------------------------------------------------------------
# Multi-turn iteration with retry-on-malformed-JSON
# ---------------------------------------------------------------------------


class TestMultiTurnIteration:
    @pytest.mark.asyncio
    async def test_three_turn_iteration_to_valid_decision(self):
        # Team-lead's headline: agent hook test that runs 3+ turns.
        # Turns 1-2 produce malformed responses; turn 3 produces valid
        # JSON. Hook returns the turn-3 decision.
        provider = _make_provider_with_responses(
            "I'm thinking about it...",                    # turn 1: prose, no JSON
            "Maybe... {invalid json",                       # turn 2: malformed JSON
            json.dumps({"decision": "allow", "reason": "OK"}),  # turn 3: valid
        )
        config = HookConfig(type="agent", agent_instructions="Validate")
        result = await execute_agent_hook(
            config, {"tool_name": "Bash"}, provider=provider, max_turns=10,
        )
        assert result.exit_code == 0
        assert result.permission_behavior == "allow"
        # Three turns used to reach valid output.
        assert provider._call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_embedded_json_in_prose_extracted(self):
        # Mirrors pre-Phase-7 behavior: agent wraps JSON in prose.
        # Schema-direct parse fails; embedded-object extraction
        # succeeds. Counts as ONE turn.
        provider = _make_provider_with_responses(
            'Here is my evaluation:\n{"decision": "deny", "reason": "blocked"}\nDone.',
        )
        config = HookConfig(type="agent", agent_instructions="x")
        result = await execute_agent_hook(
            config, {}, provider=provider,
        )
        assert result.permission_behavior == "deny"
        assert provider._call_count["n"] == 1


# ---------------------------------------------------------------------------
# 50-turn cap (configurable via max_turns kwarg)
# ---------------------------------------------------------------------------


class TestMaxTurnsCap:
    @pytest.mark.asyncio
    async def test_cap_at_3_turns_halts_with_blocking_error(self):
        # Team-lead's headline: test with cap=3, agent never produces
        # valid JSON, hook returns blocking_error after 3 attempts.
        provider = _make_provider_with_responses(
            "no json here",
            "still no json",
            "still no json",
            "this would be a 4th attempt — must NOT happen",
        )
        config = HookConfig(type="agent", agent_instructions="x")
        result = await execute_agent_hook(
            config, {}, provider=provider, max_turns=3,
        )
        assert result.blocking_error is not None
        assert "3 turn" in result.blocking_error or "did not produce" in result.blocking_error
        # Exactly 3 turns — no 4th attempt.
        assert provider._call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_default_max_turns_is_50(self):
        # Constant matches chapter — pinned so a refactor doesn't drop
        # the cap to e.g. 5.
        assert DEFAULT_MAX_TURNS == 50

    @pytest.mark.asyncio
    async def test_succeeds_within_cap(self):
        # 5-turn cap; agent produces valid JSON on turn 2. Hook returns
        # success without exhausting the cap.
        provider = _make_provider_with_responses(
            "thinking...",
            json.dumps({"decision": "ask", "reason": "uncertain"}),
        )
        config = HookConfig(type="agent", agent_instructions="x")
        result = await execute_agent_hook(
            config, {}, provider=provider, max_turns=5,
        )
        assert result.exit_code == 0
        assert result.permission_behavior == "ask"
        assert provider._call_count["n"] == 2


# ---------------------------------------------------------------------------
# Structured output validation (Pydantic HookOutput schema)
# ---------------------------------------------------------------------------


class TestStructuredOutputValidation:
    @pytest.mark.asyncio
    async def test_invalid_decision_value_rejected(self):
        # Capital-D "Deny" doesn't match the literal — the headline
        # failure mode the Phase-1 schema closes. Agent hook iterates;
        # if all turns produce schema-invalid output, returns
        # blocking_error.
        provider = _make_provider_with_responses(
            json.dumps({"decision": "Deny", "reason": "x"}),  # capital D
            json.dumps({"decision": "Deny", "reason": "x"}),
        )
        config = HookConfig(type="agent", agent_instructions="x")
        result = await execute_agent_hook(
            config, {}, provider=provider, max_turns=2,
        )
        assert result.blocking_error is not None
        assert provider._call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_unknown_field_rejected(self):
        # ``extra="forbid"`` on HookOutput. Unknown field → schema
        # error → retry. With max_turns=1, blocking_error.
        provider = _make_provider_with_responses(
            json.dumps({"decision": "allow", "stowaway": 1}),
        )
        config = HookConfig(type="agent", agent_instructions="x")
        result = await execute_agent_hook(
            config, {}, provider=provider, max_turns=1,
        )
        assert result.blocking_error is not None

    @pytest.mark.asyncio
    async def test_updated_input_round_trips(self):
        # Optional fields (updatedInput) survive validation and surface
        # on the HookResult.
        provider = _make_provider_with_responses(
            json.dumps({
                "decision": "allow",
                "updatedInput": {"command": "safer-cmd"},
            }),
        )
        config = HookConfig(type="agent", agent_instructions="x")
        result = await execute_agent_hook(
            config, {}, provider=provider,
        )
        assert result.permission_behavior == "allow"
        assert result.updated_input == {"command": "safer-cmd"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_no_provider_blocks(self):
        config = HookConfig(type="agent", agent_instructions="x")
        result = await execute_agent_hook(config, {}, provider=None)
        assert result.blocking_error is not None
        assert "provider" in result.blocking_error.lower()

    @pytest.mark.asyncio
    async def test_no_instructions_blocks(self):
        config = HookConfig(type="agent", agent_instructions=None)
        result = await execute_agent_hook(config, {}, provider=MagicMock())
        assert result.blocking_error is not None

    @pytest.mark.asyncio
    async def test_provider_exception_blocks(self):
        async def crashing_chat(**kwargs):
            raise RuntimeError("API failure")
        mock_provider = MagicMock()
        mock_provider.chat_async = crashing_chat
        config = HookConfig(type="agent", agent_instructions="x")
        result = await execute_agent_hook(config, {}, provider=mock_provider)
        assert result.blocking_error is not None
        assert "API failure" in result.blocking_error

    @pytest.mark.asyncio
    async def test_dont_ask_kwarg_accepted(self):
        # Forward-compat: dont_ask is accepted (would be wired to
        # permission_mode_override="dontAsk" in a future run_agent
        # integration). Test that passing it doesn't blow up.
        provider = _make_provider_with_responses(
            json.dumps({"decision": "allow"}),
        )
        config = HookConfig(type="agent", agent_instructions="x")
        result = await execute_agent_hook(
            config, {}, provider=provider, dont_ask=True,
        )
        assert result.exit_code == 0
