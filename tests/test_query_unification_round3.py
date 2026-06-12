"""ch07 round-3 PR-1 acceptance: the unified production tool lane.

query() now consumes orchestrator.run_tools (TS query.ts:1537-1565
ungated branch) — these pins prove production gained the 14-step
pipeline: hooks fire, context modifiers apply, permissions fail closed,
the pool sync routes pool-local tools, and the display feed survives.
Plan: my-docs/python-port-improvement-round-3/ch07-concurrency-round3-plan.md.
"""

from __future__ import annotations

import asyncio
import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.providers.base import ChatResponse
from src.query.query import QueryParams, run_query
from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolResult
from src.tool_system.registry import ToolRegistry
from src.types.messages import UserMessage
from src.utils.abort_controller import AbortController


def _run(coro):
    return asyncio.run(coro)


_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": True}


def _tool_use_response(name="StubTool", tid="toolu_u1"):
    return ChatResponse(
        content="using the tool",
        model="claude-test",
        usage={"input_tokens": 5, "output_tokens": 3},
        finish_reason="tool_use",
        tool_uses=[{"id": tid, "name": name, "input": {}}],
    )


def _completion():
    return ChatResponse(
        content="Done. The task is complete.",
        model="claude-test",
        usage={"input_tokens": 5, "output_tokens": 3},
        finish_reason="end_turn",
        tool_uses=None,
    )


def _provider(responses):
    provider = mock.MagicMock()
    provider.model = "claude-test"
    provider.chat_stream_response.side_effect = NotImplementedError()
    seq = list(responses)

    def chat(*a, **k):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    provider.chat.side_effect = chat
    return provider


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _params(self, tools, provider, mode="bypassPermissions"):
        registry = ToolRegistry(tools)
        ctx = ToolContext(workspace_root=self.workspace)
        ctx.permission_context.mode = mode
        return QueryParams(
            messages=[UserMessage(content="Hi")],
            system_prompt="You are helpful.",
            tools=tools,
            tool_registry=registry,
            tool_use_context=ctx,
            provider=provider,
            abort_controller=AbortController(),
            max_turns=4,
        )


class TestUnifiedLane(_Base):
    def test_pool_local_tool_executes_via_options_tools_sync(self):
        # The stub is NOT in the default registry — execution proves the
        # options.tools sync routes the ACTIVE pool (no sync would mean
        # the base-list fallback misses it -> "No such tool" error).
        ran: list = []

        def call(_i, _c):
            ran.append(True)
            return ToolResult(name="StubTool", output="ok")

        tool = build_tool(
            name="StubTool", input_schema=_SCHEMA, call=call,
            prompt="s", description="s",
        )
        provider = _provider([_tool_use_response(), _completion()])
        _msgs, terminal = _run(run_query(self._params([tool], provider)))
        self.assertEqual(terminal.reason, "completed")
        self.assertTrue(ran)

    def test_pre_tool_use_hooks_fire_in_production(self):
        # The 14-step lane is the only caller of run_pre_tool_use_hooks —
        # observing it during run_query proves production unification.
        seen: list = []

        async def fake_hooks(_ctx, tool, _inp, _tid):
            seen.append(tool.name)
            if False:
                yield

        tool = build_tool(
            name="StubTool", input_schema=_SCHEMA,
            call=lambda _i, _c: ToolResult(name="StubTool", output="ok"),
            prompt="s", description="s",
        )
        provider = _provider([_tool_use_response(), _completion()])
        with mock.patch(
            "src.services.tool_execution.tool_hooks.run_pre_tool_use_hooks",
            fake_hooks,
        ):
            _msgs, terminal = _run(run_query(self._params([tool], provider)))
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(seen, ["StubTool"])

    def test_serial_context_modifier_visible_to_next_tool(self):
        observed: list = []

        def first_call(_i, ctx):
            def modify(c):
                # NOTE: user_modified is unsuitable as a marker — the
                # pipeline reassigns it per call from the permission
                # decision. cwd is a real, stable field.
                derived = dataclasses.replace(c)
                derived.cwd = "/modified/by/first"
                return derived

            return ToolResult(
                name="First", output="one", context_modifier=modify,
            )

        def second_call(_i, ctx):
            observed.append(str(ctx.cwd))
            return ToolResult(name="Second", output="two")

        first = build_tool(
            name="First", input_schema=_SCHEMA, call=first_call,
            prompt="f", description="f",
        )
        second = build_tool(
            name="Second", input_schema=_SCHEMA, call=second_call,
            prompt="s", description="s",
        )
        resp = ChatResponse(
            content="two tools",
            model="claude-test",
            usage={"input_tokens": 5, "output_tokens": 3},
            finish_reason="tool_use",
            tool_uses=[
                {"id": "toolu_m1", "name": "First", "input": {}},
                {"id": "toolu_m2", "name": "Second", "input": {}},
            ],
        )
        provider = _provider([resp, _completion()])
        _msgs, terminal = _run(run_query(self._params([first, second], provider)))
        self.assertEqual(terminal.reason, "completed")
        # Serial batch (both default non-concurrency-safe): the second
        # tool sees the first tool's modifier-derived context.
        self.assertEqual(observed, ["/modified/by/first"])

    def test_permission_deny_fails_closed_in_default_mode(self):
        # default mode + no permission handler: ask -> fail-closed deny;
        # call() never runs; the loop completes with an error result.
        ran: list = []

        def call(_i, _c):
            ran.append(True)
            return ToolResult(name="StubTool", output="nope")

        tool = build_tool(
            name="StubTool", input_schema=_SCHEMA, call=call,
            prompt="s", description="s",
        )
        provider = _provider([_tool_use_response(), _completion()])
        msgs, terminal = _run(
            run_query(self._params([tool], provider, mode="default"))
        )
        self.assertEqual(terminal.reason, "completed")
        self.assertFalse(ran)
        # The ask resolved to a fail-closed DENY: an is_error tool_result
        # went back to the model (its message text is the ask prompt).
        error_blocks = [
            b
            for m in msgs
            for b in (getattr(m, "content", None) or [])
            if (isinstance(b, dict) and b.get("type") == "tool_result"
                and b.get("is_error"))
        ]
        self.assertEqual(len(error_blocks), 1)

    def test_display_feed_reads_metadata_tool_output(self):
        # (j): run_tool_use's ToolResultBlock carries dict outputs as
        # metadata["tool_output"] for the REPL preview extraction.
        from src.services.tool_execution.tool_execution import run_tool_use
        from src.types.content_blocks import ToolResultBlock
        from src.types.messages import AssistantMessage

        structured = {"filePath": "x.txt", "structuredPatch": []}

        tool = build_tool(
            name="DictTool", input_schema=_SCHEMA,
            call=lambda _i, _c: ToolResult(name="DictTool", output=structured),
            prompt="d", description="d",
        )
        ctx = ToolContext(workspace_root=self.workspace)
        ctx.permission_context.mode = "bypassPermissions"
        ctx.options.tools = [tool]

        async def drive():
            out = []
            async for u in run_tool_use(
                __import__("types").SimpleNamespace(
                    name="DictTool", input={}, id="toolu_d1",
                ),
                AssistantMessage(content="t"),
                lambda *a, **k: {"behavior": "allow"},
                ctx,
            ):
                out.append(u)
            return out

        updates = _run(drive())
        blocks = [
            b
            for u in updates
            for b in (getattr(getattr(u, "message", u), "content", None) or [])
            if isinstance(b, ToolResultBlock)
        ]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].metadata.get("tool_output"), structured)


if __name__ == "__main__":
    unittest.main()


class TestCanUseToolAdapter(_Base):
    def test_user_modified_synthesis_on_dialog_updates(self):
        # ask -> handler allows WITH "don't ask again" updates and a fresh
        # updated_input -> userModified True reaches the call context.
        from src.services.tool_execution.can_use_tool_adapter import (
            build_can_use_tool,
        )
        from types import SimpleNamespace

        tool = build_tool(
            name="AskTool", input_schema=_SCHEMA,
            call=lambda _i, _c: ToolResult(name="AskTool", output="ok"),
            prompt="a", description="a",
        )
        ctx = ToolContext(workspace_root=self.workspace)
        ctx.permission_context.mode = "default"

        def handler(_request):
            # PermissionAskHandler reply contract (handler.py:60-70):
            # behavior + updated_input + chosen_updates.
            return SimpleNamespace(
                behavior="allow",
                updated_input={"answer": 42},
                chosen_updates=(),
                message="",
            )

        ctx.permission_handler = handler
        adapter = build_can_use_tool(ctx)
        decision = adapter(tool, {}, ctx, None, "toolu_um1")
        self.assertEqual(decision["behavior"], "allow")
        self.assertEqual(decision["updatedInput"], {"answer": 42})
        self.assertTrue(decision["userModified"])

    def test_user_modified_false_on_plain_allow(self):
        from src.services.tool_execution.can_use_tool_adapter import (
            build_can_use_tool,
        )

        tool = build_tool(
            name="FreeTool", input_schema=_SCHEMA,
            call=lambda _i, _c: ToolResult(name="FreeTool", output="ok"),
            prompt="f", description="f",
        )
        ctx = ToolContext(workspace_root=self.workspace)
        ctx.permission_context.mode = "bypassPermissions"
        adapter = build_can_use_tool(ctx)
        decision = adapter(tool, {}, ctx, None, "toolu_um2")
        self.assertEqual(decision["behavior"], "allow")
        self.assertFalse(decision["userModified"])
