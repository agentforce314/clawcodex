"""Phase G acceptance tests: QueryDeps injection (G.1/G.2) and
consumed_command_uuids lifecycle (G.3).
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.query.deps import QueryDeps, production_deps
from src.query.query import (
    QueryParams,
    _notify_command_lifecycle,
    run_query,
    set_command_lifecycle_notifier,
)
from src.query.transitions import TerminalHolder
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.content_blocks import TextBlock
from src.types.messages import AssistantMessage, UserMessage
from src.utils.abort_controller import AbortController


def _run(coro):
    return asyncio.run(coro)


def _make_params(workspace: Path, provider: MagicMock, **kwargs) -> QueryParams:
    registry = build_default_registry()
    context = ToolContext(workspace_root=workspace)
    return QueryParams(
        messages=[UserMessage(content="Hi")],
        system_prompt="You are helpful.",
        tools=registry.list_tools(),
        tool_registry=registry,
        tool_use_context=context,
        provider=provider,
        abort_controller=AbortController(),
        max_turns=kwargs.pop("max_turns", 10),
        **kwargs,
    )


# --- G.1: QueryDeps shape + production_deps factory --------------


class TestQueryDepsShape(unittest.TestCase):
    def test_query_deps_has_four_slots(self):
        deps = QueryDeps()
        self.assertTrue(hasattr(deps, "call_model"))
        self.assertTrue(hasattr(deps, "microcompact"))
        self.assertTrue(hasattr(deps, "autocompact"))
        self.assertTrue(hasattr(deps, "uuid"))

    def test_production_deps_wires_real_microcompact(self):
        d = production_deps()
        from src.services.compact.autocompact import auto_compact_if_needed
        from src.services.compact.compact import microcompact_messages
        self.assertIs(d.microcompact, microcompact_messages)
        self.assertIs(d.autocompact, auto_compact_if_needed)
        self.assertIsNone(d.call_model)

    def test_uuid_returns_string(self):
        d = production_deps()
        result = d.uuid()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


# --- G.2: deps.call_model injection ------------------------------


class TestCallModelInjection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fake_call_model_used_when_set(self):
        fake_invoked = {"n": 0}

        async def fake_call_model(**kwargs):
            fake_invoked["n"] += 1
            msg = AssistantMessage(
                content=[TextBlock(text="From the fake.")],
                stop_reason="end_turn",
            )
            return [msg], []

        provider = MagicMock()
        provider.chat.side_effect = AssertionError(
            "Real provider.chat must not be called when deps.call_model is set",
        )
        provider.chat_stream_response.side_effect = AssertionError(
            "Real chat_stream_response must not be called either",
        )

        deps = QueryDeps(call_model=fake_call_model)
        params = _make_params(self.workspace, provider, deps=deps)
        messages, terminal = _run(run_query(params))

        self.assertEqual(fake_invoked["n"], 1)
        self.assertEqual(terminal.reason, "completed")
        assistants = [m for m in messages if isinstance(m, AssistantMessage)]
        self.assertGreaterEqual(len(assistants), 1)


# --- G.3: consumed_command_uuids + lifecycle notifier ------------


class TestCommandLifecycleHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.observed: list[tuple[str, str]] = []

        def _sub(uuid: str, status: str) -> None:
            self.observed.append((uuid, status))

        # set_command_lifecycle_notifier returns a remover.
        self._remove_sub = set_command_lifecycle_notifier(_sub)

    def tearDown(self):
        self._remove_sub()
        # Idempotent: removing twice is a no-op.
        self._remove_sub()
        self.tmp.cleanup()

    def test_no_consumed_uuids_no_notifications(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="ok",
            model="t",
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = _make_params(self.workspace, provider)
        _, _ = _run(run_query(params))
        self.assertEqual(self.observed, [])

    def test_lifecycle_notifier_invokable(self):
        _notify_command_lifecycle("abc-123", "completed")
        self.assertEqual(self.observed, [("abc-123", "completed")])

    def test_aclose_skips_completed_notifications(self):
        """G.3: consumer-side cancellation via .aclose() must not
        fire 'completed' notifications even for UUIDs that were
        consumed mid-turn."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="ok",
            model="t",
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = _make_params(self.workspace, provider)
        from src.query.query import TerminalHolder, query as query_gen
        gen = query_gen(params, terminal_holder=TerminalHolder())

        async def consume_then_close():
            async for _msg in gen:
                break
            await gen.aclose()

        _run(consume_then_close())
        completed = [t for t in self.observed if t[1] == "completed"]
        self.assertEqual(completed, [])


if __name__ == "__main__":
    unittest.main()
