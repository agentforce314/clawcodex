"""ch06 round-3 pipeline tests: aggregate skip-set (WI-1), call-input
discipline (WI-2), base-tool lookup fallback (WI-3).

Plan: my-docs/python-port-improvement-round-3/ch06-tools-round3-plan.md.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from src.services.tool_execution.tool_execution import run_tool_use
from src.services.tool_execution.tool_result_persistence import (
    MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
    PERSISTED_OUTPUT_TAG,
)
from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.protocol import ToolResult
from src.types.messages import AssistantMessage


def _run(coro):
    return asyncio.run(coro)


class _ToolUse:
    def __init__(self, name: str, input_: dict, id_: str = "toolu_r3_1"):
        self.name = name
        self.input = input_
        self.id = id_


_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": True}


def _stub_tool(name: str, call_fn, *, max_chars: float = 30_000, backfill=None):
    return build_tool(
        name=name,
        input_schema=_SCHEMA,
        call=call_fn,
        prompt="stub",
        description="stub",
        max_result_size_chars=max_chars,
        backfill_observable_input=backfill,
    )


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _context(self, tools: list, mode: str = "bypassPermissions") -> ToolContext:
        ctx = ToolContext(
            workspace_root=self.workspace,
            options=ToolUseOptions(tools=tools),
        )
        ctx.permission_context.mode = mode
        return ctx

    def _execute(self, ctx: ToolContext, tool_use: _ToolUse, can_use_tool=None) -> list:
        # The services lane fails CLOSED without a permission handler, so
        # tests that want execution must say so explicitly.
        if can_use_tool is None:
            def can_use_tool(*_a, **_k):
                return {"behavior": "allow"}

        async def drive():
            updates = []
            async for update in run_tool_use(
                tool_use,
                AssistantMessage(content="using a tool"),
                can_use_tool,
                ctx,
            ):
                updates.append(update)
            return updates

        return _run(drive())

    @staticmethod
    def _result_blocks(updates: list) -> list[dict]:
        # Services lane yields dict blocks; the production lane wraps
        # ToolResultBlock dataclass instances — normalize both to dicts.
        blocks = []
        for u in updates:
            msg = getattr(u, "message", u)
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        blocks.append(block)
                    elif hasattr(block, "tool_use_id") and hasattr(block, "content"):
                        blocks.append({
                            "type": "tool_result",
                            "tool_use_id": block.tool_use_id,
                            "content": block.content,
                            "is_error": getattr(block, "is_error", False),
                        })
        return blocks


# ---------------------------------------------------------------------------
# WI-1 — aggregate counter skips non-finite-threshold tools
# ---------------------------------------------------------------------------


class TestAggregateSkipSet(_Base):
    def test_inf_tool_does_not_count_and_does_not_push_later_results(self):
        big = "x" * (MAX_TOOL_RESULTS_PER_MESSAGE_CHARS + 50_000)
        read_like = _stub_tool(
            "ReadLike",
            lambda _inp, _ctx: ToolResult(name="ReadLike", output=big),
            max_chars=float("inf"),
        )
        small_tool = _stub_tool(
            "SmallTool",
            lambda _inp, _ctx: ToolResult(name="SmallTool", output="y" * 1_000),
        )
        ctx = self._context([read_like, small_tool])

        updates = self._execute(ctx, _ToolUse("ReadLike", {}, "toolu_inf"))
        blocks = self._result_blocks(updates)
        self.assertEqual(len(blocks), 1)
        # inf opt-out: content intact (pre-existing ad7e026 behavior)...
        self.assertNotIn(PERSISTED_OUTPUT_TAG, str(blocks[0].get("content", "")))
        # ...and (round-3) it does NOT count toward the aggregate.
        self.assertEqual(ctx.tool_result_chars_so_far, 0)

        updates2 = self._execute(ctx, _ToolUse("SmallTool", {}, "toolu_small"))
        blocks2 = self._result_blocks(updates2)
        self.assertEqual(len(blocks2), 1)
        # The Read fan-out must not force-persist a later small result.
        self.assertNotIn(PERSISTED_OUTPUT_TAG, str(blocks2[0].get("content", "")))
        self.assertEqual(ctx.tool_result_chars_so_far, 1_000)

    def test_finite_tool_still_force_persisted_at_cap_boundary(self):
        small_tool = _stub_tool(
            "SmallTool",
            lambda _inp, _ctx: ToolResult(name="SmallTool", output="y" * 1_000),
        )
        ctx = self._context([small_tool])
        ctx.tool_result_chars_so_far = MAX_TOOL_RESULTS_PER_MESSAGE_CHARS - 100

        updates = self._execute(ctx, _ToolUse("SmallTool", {}, "toolu_cap"))
        blocks = self._result_blocks(updates)
        self.assertEqual(len(blocks), 1)
        self.assertIn(PERSISTED_OUTPUT_TAG, str(blocks[0].get("content", "")))
        # Counter grows by the wrapper size (~header + preview), not an
        # unbounded original.
        self.assertLess(
            ctx.tool_result_chars_so_far,
            MAX_TOOL_RESULTS_PER_MESSAGE_CHARS + 2_500,
        )


class TestProductionLaneAggregate(_Base):
    def test_production_lane_skips_inf_tool_counting(self):
        # ch07 unification: the production lane IS orchestrator.run_tools
        # — pin the aggregate skip-set through it (the slim
        # _dispatch_single_tool lane this test originally pinned was
        # retired in ch07 PR-1).
        from src.services.tool_execution.orchestrator import run_tools

        big = "x" * (MAX_TOOL_RESULTS_PER_MESSAGE_CHARS + 50_000)
        read_like = _stub_tool(
            "ReadLike",
            lambda _i, _c: ToolResult(name="ReadLike", output=big),
            max_chars=float("inf"),
        )
        small_tool = _stub_tool(
            "SmallTool",
            lambda _i, _c: ToolResult(name="SmallTool", output="y" * 1_000),
        )
        ctx = self._context([read_like, small_tool])

        def allow(*_a, **_k):
            return {"behavior": "allow"}

        async def drive(name, tid):
            updates = []
            async for u in run_tools(
                [SimpleNamespace(name=name, input={}, id=tid)],
                [AssistantMessage(content="batch")],
                allow,
                ctx,
            ):
                if u.message is not None:
                    updates.append(u.message)
            return updates

        msgs = _run(drive("ReadLike", "toolu_prod_inf"))
        self.assertEqual(ctx.tool_result_chars_so_far, 0)
        blocks = self._result_blocks(msgs)
        self.assertEqual(len(blocks), 1)
        self.assertNotIn(PERSISTED_OUTPUT_TAG, str(blocks[0].get("content", "")))

        msgs2 = _run(drive("SmallTool", "toolu_prod_small"))
        blocks2 = self._result_blocks(msgs2)
        self.assertNotIn(PERSISTED_OUTPUT_TAG, str(blocks2[0].get("content", "")))
        self.assertEqual(ctx.tool_result_chars_so_far, 1_000)


# ---------------------------------------------------------------------------
# WI-2 — call() input discipline (TS toolExecution.ts:838-853, 1212-1237)
# ---------------------------------------------------------------------------


def _backfill_expand(inp: dict[str, Any]) -> None:
    fp = inp.get("file_path")
    if isinstance(fp, str):
        inp["file_path"] = "/abs/expanded/" + fp.lstrip("~/")
    inp["_resolved"] = True


class TestCallInputDiscipline(_Base):
    def _seen_call_input(self, hook_results=None) -> dict:
        seen: dict[str, Any] = {}

        def call(inp, _ctx):
            seen.update({"input": inp})
            return ToolResult(name="BackfillTool", output="ok")

        tool = _stub_tool("BackfillTool", call, backfill=_backfill_expand)
        ctx = self._context([tool])

        if hook_results is None:
            self._execute(ctx, _ToolUse("BackfillTool", {"file_path": "~/orig.txt"}))
        else:
            async def fake_hooks(_ctx, _tool, processed_input, _tid):
                for r in hook_results(processed_input):
                    yield r

            with mock.patch(
                "src.services.tool_execution.tool_hooks.run_pre_tool_use_hooks",
                fake_hooks,
            ):
                self._execute(
                    ctx, _ToolUse("BackfillTool", {"file_path": "~/orig.txt"})
                )
        return seen["input"]

    def test_no_hook_call_sees_model_original_file_path(self):
        got = self._seen_call_input()
        # Branch 1 (no-hook): {**clone, file_path: original} — original
        # path restored, other backfilled keys flow through.
        self.assertEqual(got["file_path"], "~/orig.txt")
        self.assertTrue(got.get("_resolved"))

    def test_hook_echoing_clone_restores_original_file_path(self):
        def echo_with_marker(processed_input):
            fresh = dict(processed_input)  # fresh object, same file_path
            fresh["_hook_marker"] = True
            return [{"type": "hookUpdatedInput", "updatedInput": fresh}]

        got = self._seen_call_input(echo_with_marker)
        self.assertEqual(got["file_path"], "~/orig.txt")   # restored
        self.assertTrue(got.get("_hook_marker"))           # hook change kept

    def test_hook_with_different_file_path_converges_fully(self):
        def rewrite(_processed_input):
            return [{
                "type": "hookUpdatedInput",
                "updatedInput": {"file_path": "/hook/chose/this.txt"},
            }]

        got = self._seen_call_input(rewrite)
        self.assertEqual(got["file_path"], "/hook/chose/this.txt")

    def test_write_relative_path_e2e_result_embeds_model_original(self):
        from src.tool_system.defaults import build_default_registry

        registry = build_default_registry()
        write_tool = next(
            t for t in registry.list_tools() if t.name == "Write"
        )
        target_name = "ch06_r3_e2e_target.txt"
        ctx = self._context([write_tool])
        updates = self._execute(
            ctx,
            _ToolUse(
                "Write",
                {"file_path": target_name, "content": "hello round 3"},
            ),
        )
        target = self.workspace / target_name
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(), "hello round 3")
        # A cwd regression must fail loudly, not litter the repo root.
        self.assertFalse((Path.cwd() / target_name).exists())
        blocks = self._result_blocks(updates)
        text = str(blocks[0].get("content", "")) if blocks else ""
        joined = text + "".join(
            str(getattr(getattr(u, "message", u), "toolUseResult", ""))
            for u in updates
        )
        # TS parity: the result embeds the MODEL-ORIGINAL path, not the
        # backfill-expanded absolute path.
        self.assertIn(target_name, joined)
        self.assertNotIn(str(self.workspace / target_name), joined)


# ---------------------------------------------------------------------------
# WI-3 — base-tool lookup fallback (TS toolExecution.ts:335-341)
# ---------------------------------------------------------------------------


class TestBaseToolFallback(_Base):
    def test_pool_hidden_tool_resolves_via_base_list(self):
        ran: list = []

        def call(_inp, _ctx):
            ran.append(True)
            return ToolResult(name="HiddenTool", output="ran")

        hidden = _stub_tool("HiddenTool", call)
        fake_registry = SimpleNamespace(list_tools=lambda: [hidden])

        ctx = self._context([])  # NOT in the active pool
        with mock.patch(
            "src.tool_system.defaults.build_default_registry",
            return_value=fake_registry,
        ):
            updates = self._execute(ctx, _ToolUse("HiddenTool", {}))
        self.assertTrue(ran)
        blocks = self._result_blocks(updates)
        self.assertFalse(blocks[0].get("is_error", False))

    def test_unknown_name_still_errors(self):
        ctx = self._context([])
        updates = self._execute(ctx, _ToolUse("NoSuchToolEver", {}))
        blocks = self._result_blocks(updates)
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0].get("is_error"))
        self.assertIn("No such tool available", str(blocks[0].get("content", "")))

    def test_fallback_resolved_tool_still_goes_through_permissions(self):
        # The fallback is not a permission bypass: in the services lane,
        # permission resolution (can_use_tool) still runs for a
        # fallback-resolved tool, and a deny stops call().
        ran: list = []

        def call(_inp, _ctx):
            ran.append(True)
            return ToolResult(name="HiddenTool", output="ran")

        hidden = _stub_tool("HiddenTool", call)
        fake_registry = SimpleNamespace(list_tools=lambda: [hidden])

        def deny_all(*_a, **_k):
            return {"behavior": "deny", "message": "denied by policy"}

        ctx = self._context([])
        with mock.patch(
            "src.tool_system.defaults.build_default_registry",
            return_value=fake_registry,
        ):
            async def drive():
                updates = []
                async for update in run_tool_use(
                    _ToolUse("HiddenTool", {}),
                    AssistantMessage(content="using a tool"),
                    deny_all,
                    ctx,
                ):
                    updates.append(update)
                return updates

            updates = _run(drive())
        self.assertFalse(ran)
        blocks = self._result_blocks(updates)
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0].get("is_error"))
        self.assertIn("denied by policy", str(blocks[0].get("content", "")))

    def test_services_lane_fails_closed_without_handler(self):
        # No can_use_tool → deny, call() never runs (TS cannot express a
        # missing handler; the production lane's handlerless ask path
        # denies the same way).
        ran: list = []

        def call(_inp, _ctx):
            ran.append(True)
            return ToolResult(name="NoHandlerTool", output="ran")

        tool = _stub_tool("NoHandlerTool", call)
        ctx = self._context([tool])

        async def drive():
            updates = []
            async for update in run_tool_use(
                _ToolUse("NoHandlerTool", {}),
                AssistantMessage(content="using a tool"),
                None,
                ctx,
            ):
                updates.append(update)
            return updates

        updates = _run(drive())
        self.assertFalse(ran)
        blocks = self._result_blocks(updates)
        self.assertTrue(blocks[0].get("is_error"))
        self.assertIn("no handler available", str(blocks[0].get("content", "")))

    def test_services_lane_fails_closed_on_throwing_handler(self):
        ran: list = []

        def call(_inp, _ctx):
            ran.append(True)
            return ToolResult(name="ThrowTool", output="ran")

        def exploding_handler(*_a, **_k):
            raise RuntimeError("handler crashed")

        tool = _stub_tool("ThrowTool", call)
        ctx = self._context([tool])
        updates = self._execute(
            ctx, _ToolUse("ThrowTool", {}), can_use_tool=exploding_handler
        )
        self.assertFalse(ran)
        blocks = self._result_blocks(updates)
        self.assertTrue(blocks[0].get("is_error"))
        self.assertIn("Permission handler failed", str(blocks[0].get("content", "")))

    def test_production_lane_plan_mode_denies_write_tool_handlerless(self):
        # PRODUCTION lane (registry.dispatch): plan mode without a
        # bypass grant resolves a write-ish tool to ask; with no
        # permission handler the ask fails CLOSED and call() never runs.
        from src.tool_system.registry import ToolRegistry, ToolCall

        ran: list = []

        def call(_inp, _ctx):
            ran.append(True)
            return ToolResult(name="PlanWrite", output="ran")

        tool = _stub_tool("PlanWrite", call)
        registry = ToolRegistry([tool])
        ctx = self._context([tool], mode="plan")
        result = registry.dispatch(
            ToolCall(name="PlanWrite", input={}, tool_use_id="toolu_plan"),
            ctx,
        )
        self.assertTrue(result.is_error)
        self.assertFalse(ran)


if __name__ == "__main__":
    unittest.main()
