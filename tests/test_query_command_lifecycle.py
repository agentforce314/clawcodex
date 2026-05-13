"""Ch5/G.3 — outer query() wrapper + command-lifecycle dispatch tests.

Verifies the contracts from chapter 5 §"The Two-Layer Entry Point":
  * The outer query() wrapper tracks consumed_command_uuids.
  * On NATURAL termination, notify_command_lifecycle(uuid, "completed")
    fires for every consumed UUID.
  * On .aclose() / exception, the completion notifications are
    SKIPPED — a failed turn does not falsely declare success.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import UserMessage
from src.utils.abort_controller import AbortController

from src.query.query import QueryParams, query
from src.query.transitions import TerminalHolder
from src.query.command_lifecycle import (
    clear_lifecycle_listeners,
    register_lifecycle_listener,
)


def _run(coro):
    return asyncio.run(coro)


class _Base(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()
        clear_lifecycle_listeners()

    def tearDown(self):
        clear_lifecycle_listeners()
        self.temp_dir.cleanup()


class TestCommandLifecycleListener(_Base):
    """G.3 — register/notify lifecycle listener contract."""

    def test_register_and_unregister_listener(self):
        events = []
        unregister = register_lifecycle_listener(
            lambda uuid, status: events.append((uuid, status)),
        )
        from src.query.command_lifecycle import notify_command_lifecycle
        notify_command_lifecycle("cmd-1", "completed")
        self.assertEqual(events, [("cmd-1", "completed")])

        unregister()
        notify_command_lifecycle("cmd-2", "completed")
        # After unregister, no more events.
        self.assertEqual(events, [("cmd-1", "completed")])

    def test_clear_lifecycle_listeners(self):
        events = []
        register_lifecycle_listener(
            lambda uuid, status: events.append((uuid, status)),
        )
        clear_lifecycle_listeners()
        from src.query.command_lifecycle import notify_command_lifecycle
        notify_command_lifecycle("cmd", "completed")
        self.assertEqual(events, [])


class TestOuterQueryWrapper(_Base):
    """G.3 — the outer query() wrapper fires lifecycle events only on
    natural termination."""

    def _params(self, provider):
        return QueryParams(
            messages=[UserMessage(content="Hi")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=5,
        )

    def test_no_consumed_uuids_no_lifecycle_events(self):
        """With no consumed_command_uuids appended, the outer wrapper
        fires nothing — even on a successful turn."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Done.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        events = []
        register_lifecycle_listener(
            lambda uuid, status: events.append((uuid, status)),
        )

        params = self._params(provider)
        holder = TerminalHolder()

        async def run():
            async for _ in query(params, terminal_holder=holder):
                pass

        _run(run())
        self.assertEqual(holder.value.reason, "completed")
        self.assertEqual(events, [])

    def test_consumed_uuids_fire_on_natural_termination(self):
        """When the inner loop appends UUIDs to consumed_command_uuids,
        the outer wrapper fires `notify_command_lifecycle(uuid,
        "completed")` for each on natural termination."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Done.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        events = []
        register_lifecycle_listener(
            lambda uuid, status: events.append((uuid, status)),
        )

        # Monkey-patch the inner loop to append UUIDs to simulate
        # slash-command consumption. We can't easily wire the real
        # slash-command queue (out of ch05 scope), so this exercises
        # the wrapper's dispatch directly.
        # ``from src.query.query import _query_loop_inner`` won't work
        # because src/query/__init__.py re-exports the ``query``
        # function, shadowing the submodule. Use importlib explicitly.
        import importlib
        query_module = importlib.import_module("src.query.query")

        original_inner = query_module._query_loop_inner

        async def patched_inner(
            params,
            *,
            terminal_holder,
            consumed_command_uuids,
            natural_termination,
        ):
            consumed_command_uuids.append("cmd-abc")
            consumed_command_uuids.append("cmd-xyz")
            async for msg in original_inner(
                params,
                terminal_holder=terminal_holder,
                consumed_command_uuids=consumed_command_uuids,
                natural_termination=natural_termination,
            ):
                yield msg

        params = self._params(provider)
        holder = TerminalHolder()

        async def run():
            query_module._query_loop_inner = patched_inner
            try:
                async for _ in query(params, terminal_holder=holder):
                    pass
            finally:
                query_module._query_loop_inner = original_inner

        _run(run())

        self.assertEqual(holder.value.reason, "completed")
        # Both UUIDs fired with "completed" status, IN ORDER.
        self.assertEqual(events, [
            ("cmd-abc", "completed"),
            ("cmd-xyz", "completed"),
        ])

    def test_aclose_skips_lifecycle_events(self):
        """If the outer generator is closed mid-iteration (consumer
        calls .aclose() or stops iterating early), the natural-
        termination flag stays False and the completion notifications
        are SKIPPED. Chapter §"The Two-Layer Entry Point": "a failed
        turn does not mark commands as successfully processed.""\""""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="thinking",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "tool_1",
                "name": "Bash",
                "input": {"command": "true", "description": "noop"},
            }],
        )

        events = []
        register_lifecycle_listener(
            lambda uuid, status: events.append((uuid, status)),
        )

        # ``from src.query.query import _query_loop_inner`` won't work
        # because src/query/__init__.py re-exports the ``query``
        # function, shadowing the submodule. Use importlib explicitly.
        import importlib
        query_module = importlib.import_module("src.query.query")

        original_inner = query_module._query_loop_inner

        async def patched_inner(
            params,
            *,
            terminal_holder,
            consumed_command_uuids,
            natural_termination,
        ):
            consumed_command_uuids.append("cmd-abc")
            # Yield one message then have the consumer stop iterating.
            count = 0
            async for msg in original_inner(
                params,
                terminal_holder=terminal_holder,
                consumed_command_uuids=consumed_command_uuids,
                natural_termination=natural_termination,
            ):
                count += 1
                yield msg
                if count >= 2:
                    # Caller will stop iterating after this. We never
                    # set natural_termination[0] = True.
                    return

        params = self._params(provider)
        holder = TerminalHolder()

        async def run():
            query_module._query_loop_inner = patched_inner
            try:
                gen = query(params, terminal_holder=holder)
                count = 0
                async for _ in gen:
                    count += 1
                    if count >= 2:
                        break
                # Force generator finalization via aclose.
                await gen.aclose()
            finally:
                query_module._query_loop_inner = original_inner

        _run(run())

        # Lifecycle events MUST NOT fire — natural_termination[0]
        # never flipped to True.
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
