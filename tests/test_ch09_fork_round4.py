"""ch09 round-4 acceptance tests: Layer 1 system-prompt threading — the
fork child inherits the parent's EXACT rendered prompt (byte-identical), not
DEFAULT_AGENT_PROMPT.

Covers my-docs/port-improvement-round-4/ch09-fork-agents-round4-plan.md.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext


_PARENT_LIST_PROMPT = [
    {"type": "text", "text": "You are a helpful CLI agent.",
     "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "## Project Instructions\nBe concise."},
]


def _completion(text="ok"):
    return ChatResponse(content=text, model="test-model",
                        usage={"input_tokens": 1, "output_tokens": 1},
                        finish_reason="end_turn", tool_uses=None)


class TestResolveParentPrompt(unittest.TestCase):
    def _ctx(self, rendered):
        ctx = ToolContext(workspace_root=Path("/tmp"))
        ctx.rendered_system_prompt = rendered
        return ctx

    def test_prefers_rendered_list(self):
        from src.tool_system.tools.agent import _resolve_parent_system_prompt
        from src.agent.agent_definitions import get_built_in_agents

        out = _resolve_parent_system_prompt(
            self._ctx(_PARENT_LIST_PROMPT), get_built_in_agents(),
        )
        self.assertEqual(out, _PARENT_LIST_PROMPT)

    def test_prefers_rendered_str(self):
        from src.tool_system.tools.agent import _resolve_parent_system_prompt
        from src.agent.agent_definitions import get_built_in_agents

        out = _resolve_parent_system_prompt(
            self._ctx("PARENT STRING PROMPT"), get_built_in_agents(),
        )
        self.assertEqual(out, "PARENT STRING PROMPT")

    def test_none_when_unset(self):
        from src.tool_system.tools.agent import _resolve_parent_system_prompt
        from src.agent.agent_definitions import get_built_in_agents

        ctx = self._ctx(None)
        ctx.agent_type = None
        out = _resolve_parent_system_prompt(ctx, get_built_in_agents())
        self.assertIsNone(out)


class TestQueryCapturesRenderedPrompt(unittest.TestCase):
    """query() must populate tool_use_context.rendered_system_prompt so a
    fork spawned during the turn threads it."""

    def _drive(self, system_prompt):
        from src.query.query import QueryParams, run_query
        from src.tool_system.defaults import build_default_registry
        from src.types.messages import UserMessage
        from src.utils.abort_controller import AbortController

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _completion()
        registry = build_default_registry()
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ToolContext(workspace_root=Path(tmp))
            params = QueryParams(
                messages=[UserMessage(content="hi")],
                system_prompt=system_prompt,
                tools=registry.list_tools(),
                tool_registry=registry,
                tool_use_context=ctx,
                provider=provider,
                abort_controller=AbortController(),
                max_turns=1,
            )
            asyncio.run(run_query(params))
            return ctx.rendered_system_prompt

    def test_captures_list_prompt(self):
        self.assertEqual(self._drive(_PARENT_LIST_PROMPT), _PARENT_LIST_PROMPT)

    def test_captures_str_prompt(self):
        self.assertEqual(self._drive("You are helpful."), "You are helpful.")


class TestForkThreadsParentPrompt(unittest.TestCase):
    """End-to-end: with the parent context carrying a rendered list prompt,
    a fork threads THAT prompt into the child's query — not DEFAULT_AGENT_PROMPT."""

    def test_fork_child_system_prompt_is_parent_prompt(self):
        import os

        from src.agent.run_agent import RunAgentParams, run_agent
        from src.agent.agent_definitions import FORK_AGENT
        from src.tool_system.context import ToolUseOptions
        from src.tool_system.defaults import build_default_registry

        parent = ToolContext(workspace_root=Path("/tmp"))
        parent.rendered_system_prompt = _PARENT_LIST_PROMPT
        parent.options = ToolUseOptions(tools=[])

        # Resolve the parent prompt exactly as the fork path does.
        from src.tool_system.tools.agent import _resolve_parent_system_prompt
        from src.agent.agent_definitions import get_built_in_agents

        resolved = _resolve_parent_system_prompt(parent, get_built_in_agents())
        self.assertEqual(resolved, _PARENT_LIST_PROMPT)

        captured = {}

        async def _fake_query(qp):
            captured["system_prompt"] = qp.system_prompt
            return
            yield

        provider = MagicMock()
        provider.model = "test-model"
        params = RunAgentParams(
            parent_context=parent,
            agent_definition=FORK_AGENT,
            prompt="",
            available_tools=[],
            tool_registry=build_default_registry(),
            provider=provider,
            parent_system_prompt=resolved,
            use_exact_tools=True,
            context_messages=[],
        )

        async def _drain(agen):
            async for _ in agen:
                pass

        with patch("src.query.query.query", _fake_query):
            asyncio.run(_drain(run_agent(params)))

        # The child threaded the PARENT's exact prompt, not DEFAULT_AGENT_PROMPT.
        self.assertEqual(captured.get("system_prompt"), _PARENT_LIST_PROMPT)
        from src.agent.constants import DEFAULT_AGENT_PROMPT

        self.assertNotEqual(captured.get("system_prompt"), DEFAULT_AGENT_PROMPT)


if __name__ == "__main__":
    unittest.main()
