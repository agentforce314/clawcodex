"""Subagent reasoning effort: which level a spawned agent's requests carry.

The bug these lock down (2026-09-24). ``run_agent`` built the subagent's
``QueryParams`` with no ``thinking_effort``, so the wire boundary fell back to
the persisted ``settings.effort`` for EVERY subagent:

* the built-in Explore agent — "a fast agent that returns output as quickly
  as possible" — reasoned at the session's max on every search turn. A quick
  "what is this repo" delegation took ~121 s on gpt-6-astra at max vs ~57 s
  at low (three live runs each, same task and tools);
* an agent definition's ``effort:`` frontmatter was parsed and then dropped;
* a session-only level — headless ``--effort``, or ``/effort`` on a client
  that does not save preferences — never reached its subagents, which used
  the persisted setting instead.

TS runAgent.ts:514-518 resolves ``agentDefinition.effort ?? state.effortValue``
(the session's level). The port now does the same: query() captures its level
on the ToolContext, and run_agent takes the definition's level, else that one —
except that a BUILT-IN definition's level (Explore's ``low``) is only a ceiling
on the level already in force, so a session that configured no effort keeps
sending none (the field alone is a 400 on some wires, e.g. Groq's Llama models).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

from src.agent.agent_definitions import (
    EXPLORE_AGENT,
    GENERAL_PURPOSE_AGENT,
    PLAN_AGENT,
    AgentDefinition,
)
from src.agent.run_agent import RunAgentParams, resolve_subagent_effort, run_agent
from src.providers.base import ChatResponse
from src.providers.openrouter_provider import OpenRouterProvider
from src.query.query import QueryParams, query
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import UserMessage
from src.utils.abort_controller import AbortController


def _settings_effort(level: str):
    """Pin the persisted ``settings.effort`` — the fallback a subagent used to
    land on — so each test controls what "the old behavior" would send."""
    return mock.patch(
        "src.settings.settings.get_settings",
        return_value=SimpleNamespace(effort=level),
    )


def _text(content: str = "done") -> ChatResponse:
    return ChatResponse(
        content=content,
        model="openai/gpt-5.6-luna",
        usage={"input_tokens": 1, "output_tokens": 1},
        finish_reason="stop",
        tool_uses=None,
    )


def _openai_compat_mock(*responses: ChatResponse) -> MagicMock:
    """Takes the OpenAI-compatible wire branch (``reasoning_effort`` in
    ``extra_body``). Streaming is forced into the ``chat()`` fallback so every
    request's kwargs are on ``chat.call_args_list``. No ``provider_id`` table
    applies, so a subagent keeps this same instance (no model clone)."""
    provider = MagicMock(spec=OpenRouterProvider)
    provider.model = "openai/gpt-5.6-luna"
    provider.base_url = "https://openrouter.ai/api/v1"
    provider.chat_stream_response.side_effect = NotImplementedError()
    provider.chat.side_effect = list(responses) or [_text()]
    return provider


def _wire_effort(call) -> str | None:
    return (call.kwargs.get("extra_body") or {}).get("reasoning_effort")


class TestResolveSubagentEffort(unittest.TestCase):
    """The precedence itself: an authored definition level, else the parent's
    level, with a built-in's level capping — never adding — a level."""

    def setUp(self):
        # The ceiling reads the level in force, which falls back to
        # settings.effort; pin it so a developer's own config can't leak in.
        self._settings = _settings_effort("")
        self._settings.start()

    def tearDown(self):
        self._settings.stop()

    def _ctx(self, effort):
        ctx = ToolContext(workspace_root=Path(tempfile.gettempdir()))
        ctx.thinking_effort = effort
        return ctx

    def test_authored_definition_effort_wins(self):
        # A user/project agent's ``effort:`` is used as written (TS parity),
        # above or below the session's level, and even with none configured.
        agent = AgentDefinition(agent_type="t", when_to_use="t", source="project", effort="medium")
        self.assertEqual(resolve_subagent_effort(agent, self._ctx("max")), "medium")
        self.assertEqual(resolve_subagent_effort(agent, self._ctx("low")), "medium")
        self.assertEqual(resolve_subagent_effort(agent, self._ctx(None)), "medium")

    def test_builtin_level_is_a_ceiling(self):
        agent = AgentDefinition(agent_type="t", when_to_use="t", source="built-in", effort="medium")
        self.assertEqual(resolve_subagent_effort(agent, self._ctx("max")), "medium")
        self.assertEqual(resolve_subagent_effort(agent, self._ctx("low")), "low")

    def test_builtin_level_adds_nothing_to_an_unconfigured_session(self):
        # Nothing set anywhere → the parent sends no effort field, so Explore
        # must not either: the field alone is a 400 on some wires.
        self.assertIsNone(resolve_subagent_effort(EXPLORE_AGENT, self._ctx(None)))

    def test_builtin_ceiling_sees_the_settings_fallback(self):
        # The parent has no explicit level but runs on settings.effort=max
        # (what an unsaved-level session looks like) — Explore still caps.
        with _settings_effort("max"):
            self.assertEqual(resolve_subagent_effort(EXPLORE_AGENT, self._ctx(None)), "low")

    def test_no_definition_effort_inherits_the_parent_level(self):
        self.assertEqual(
            resolve_subagent_effort(GENERAL_PURPOSE_AGENT, self._ctx("xhigh")),
            "xhigh",
        )

    def test_parent_without_explicit_level_stays_unset(self):
        # None is not "no effort": it means the settings fallback, which is
        # exactly where the parent's own requests land.
        self.assertIsNone(resolve_subagent_effort(GENERAL_PURPOSE_AGENT, self._ctx(None)))

    def test_off_ladder_definition_level_falls_through_to_the_parent(self):
        # Frontmatter accepts integers (TS's numeric effort). No wire here
        # takes one, and resolve_thinking_effort would treat it as unset and
        # jump to settings — skipping the parent's explicit level.
        agent = AgentDefinition(agent_type="t", when_to_use="t", effort="7")
        self.assertEqual(resolve_subagent_effort(agent, self._ctx("high")), "high")

    def test_explore_declares_low_and_plan_inherits(self):
        self.assertEqual(EXPLORE_AGENT.effort, "low")
        self.assertEqual(resolve_subagent_effort(EXPLORE_AGENT, self._ctx("max")), "low")
        # Plan does real design work; it keeps the session's level.
        self.assertIsNone(PLAN_AGENT.effort)
        self.assertEqual(resolve_subagent_effort(PLAN_AGENT, self._ctx("max")), "max")


class TestQueryCapturesItsEffort(unittest.TestCase):
    """query() records its level on the context — the only way a subagent
    spawned mid-turn can learn what its parent runs at."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _drive(self, **extra):
        params = QueryParams(
            messages=[UserMessage(content="hi")],
            system_prompt="hello",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=_openai_compat_mock(),
            abort_controller=AbortController(),
            max_turns=1,
            **extra,
        )

        async def run():
            async for _ in query(params):
                pass

        asyncio.run(run())

    def test_explicit_level_is_captured(self):
        with _settings_effort("max"):
            self._drive(thinking_effort="low")
        self.assertEqual(self.context.thinking_effort, "low")

    def test_unset_level_overwrites_a_stale_capture(self):
        # A context reused across turns must not keep the previous turn's
        # level after the session drops back to the settings fallback.
        self.context.thinking_effort = "xhigh"
        with _settings_effort("max"):
            self._drive()
        self.assertIsNone(self.context.thinking_effort)


class TestSubagentEffortOnTheWire(unittest.TestCase):
    """What a subagent's request actually carries."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = build_default_registry()

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, agent: AgentDefinition, parent_effort: str | None) -> MagicMock:
        provider = _openai_compat_mock()
        parent = ToolContext(workspace_root=Path(self.tmp.name))
        parent.thinking_effort = parent_effort
        params = RunAgentParams(
            parent_context=parent,
            agent_definition=agent,
            prompt="look around",
            available_tools=self.registry.list_tools(),
            tool_registry=self.registry,
            provider=provider,
        )

        async def drain():
            async for _ in run_agent(params):
                pass

        asyncio.run(drain())
        self.assertEqual(provider.chat.call_count, 1)
        return provider

    def test_session_level_beats_the_persisted_setting(self):
        # The precedence inversion: /effort high (or --effort high) in the
        # session, max saved in settings. The subagent used to send max.
        with _settings_effort("max"):
            provider = self._run(GENERAL_PURPOSE_AGENT, "high")
        self.assertEqual(_wire_effort(provider.chat.call_args), "high")

    def test_explore_sends_low_under_a_max_session(self):
        # The reported slowness: Explore reasoned at the session's max.
        with _settings_effort("max"):
            provider = self._run(EXPLORE_AGENT, "max")
        self.assertEqual(_wire_effort(provider.chat.call_args), "low")

    def test_explore_sends_low_even_when_only_settings_say_max(self):
        # The TUI's common case: the session level equals settings.effort.
        with _settings_effort("max"):
            provider = self._run(EXPLORE_AGENT, None)
        self.assertEqual(_wire_effort(provider.chat.call_args), "low")

    def test_explore_adds_no_field_to_an_unconfigured_session(self):
        # The Groq/Llama shape: no effort anywhere, and a wire that 400s on
        # the field's mere presence. Before the ceiling, Explore sent "low".
        with _settings_effort(""):
            provider = self._run(EXPLORE_AGENT, None)
        self.assertIsNone(_wire_effort(provider.chat.call_args))

    def test_definition_frontmatter_effort_reaches_the_wire(self):
        agent = AgentDefinition(
            agent_type="prover",
            when_to_use="proofs",
            tools=["*"],
            source="project",
            effort="xhigh",
            get_system_prompt=lambda: "prove it",
        )
        with _settings_effort("low"):
            provider = self._run(agent, "medium")
        self.assertEqual(_wire_effort(provider.chat.call_args), "xhigh")

    def test_no_level_anywhere_still_omits_the_field(self):
        # Unchanged default path: nothing set → nothing sent.
        with _settings_effort(""):
            provider = self._run(GENERAL_PURPOSE_AGENT, None)
        self.assertIsNone(_wire_effort(provider.chat.call_args))


class TestEffortThroughTheAgentTool(unittest.TestCase):
    """The whole chain: a main turn at one level delegates through the real
    Agent tool, and the subagent's request carries the level it resolves to.
    Requests are sequential (the sync Agent call blocks the main turn), so
    chat.call_args_list reads [main, subagent, main]."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _delegate(self, subagent_type: str, main_effort: str) -> list:
        provider = _openai_compat_mock(
            ChatResponse(
                content="",
                model="openai/gpt-5.6-luna",
                usage={"input_tokens": 1, "output_tokens": 1},
                finish_reason="tool_calls",
                tool_uses=[{
                    "id": "call_1",
                    "name": "Agent",
                    "input": {
                        "description": "Survey the repo",
                        "prompt": "What is this repo?",
                        "subagent_type": subagent_type,
                    },
                }],
            ),
            _text("the subagent's report"),
            _text("the answer"),
        )
        registry = build_default_registry(provider=provider)
        context = ToolContext(workspace_root=Path(self.tmp.name))
        # Hermetic: the Agent tool would otherwise read agent definitions from
        # the real config dirs, where a user's own Explore.md or
        # general-purpose.md replaces the built-in under test.
        context.options.agent_definitions = {
            "active_agents": [GENERAL_PURPOSE_AGENT, EXPLORE_AGENT, PLAN_AGENT],
        }
        params = QueryParams(
            messages=[UserMessage(content="what is this repo?")],
            system_prompt="hello",
            tools=registry.list_tools(),
            tool_registry=registry,
            tool_use_context=context,
            provider=provider,
            abort_controller=AbortController(),
            max_turns=3,
            thinking_effort=main_effort,
        )

        async def run():
            async for _ in query(params):
                pass

        with _settings_effort("max"):
            asyncio.run(run())
        calls = provider.chat.call_args_list
        self.assertEqual(len(calls), 3, "expected main → subagent → main")
        return [_wire_effort(call) for call in calls]

    def test_general_purpose_follows_the_session_level(self):
        self.assertEqual(self._delegate("general-purpose", "medium"), ["medium", "medium", "medium"])

    def test_explore_runs_low_while_the_session_stays_max(self):
        self.assertEqual(self._delegate("Explore", "max"), ["max", "low", "max"])


if __name__ == "__main__":
    unittest.main()
