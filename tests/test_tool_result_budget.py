"""
Tests for Layer 1: Tool Result Budget.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import Message, UserMessage, AssistantMessage
from src.services.compact.tool_result_budget import (
    apply_tool_result_budget,
    cleanup_budget_dir,
    BudgetManifest,
    STORED_REFERENCE_TEMPLATE,
    DEFAULT_MAX_RESULT_TOKENS,
)


def _make_assistant_with_tool_use(tool_id: str, tool_name: str = "Read") -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[ToolUseBlock(id=tool_id, name=tool_name, input={"file_path": "test.txt"})],
    )


def _make_user_with_tool_result(tool_id: str, content: str) -> UserMessage:
    return UserMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id=tool_id, content=content)],
    )


class TestApplyToolResultBudget(unittest.TestCase):
    """Tests for apply_tool_result_budget()."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.budget_dir = Path(self.tmpdir.name) / "budget"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_small_results_left_in_place(self):
        """Results below the threshold are not offloaded."""
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", "small result"),
        ]
        result, saved = apply_tool_result_budget(
            messages, self.budget_dir, max_result_tokens=10_000,
        )
        self.assertEqual(saved, 0)
        self.assertEqual(len(result), 2)

    def test_large_results_offloaded_to_disk(self):
        """Results above the threshold are written to disk."""
        large_content = "x" * 50_000  # ~12,500 tokens at 4 chars/token
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", large_content),
        ]
        result, saved = apply_tool_result_budget(
            messages, self.budget_dir, max_result_tokens=1_000,
        )
        self.assertGreater(saved, 0)

        # The tool result content should be a reference string
        user_msg = result[1]
        block = user_msg.content[0]
        self.assertIsInstance(block, ToolResultBlock)
        self.assertIn("[Tool result stored at:", block.content)

    def test_stored_file_contains_original_content(self):
        """The stored file on disk contains the original content."""
        large_content = "Hello " * 10_000
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", large_content),
        ]
        apply_tool_result_budget(messages, self.budget_dir, max_result_tokens=1_000)

        # Find the stored file
        stored_files = list(self.budget_dir.glob("result_*.txt"))
        self.assertEqual(len(stored_files), 1)
        self.assertEqual(stored_files[0].read_text(), large_content)

    def test_manifest_written(self):
        """A manifest file is created after offloading."""
        large_content = "x" * 50_000
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", large_content),
        ]
        apply_tool_result_budget(messages, self.budget_dir, max_result_tokens=1_000)

        manifest = BudgetManifest.load(self.budget_dir)
        self.assertEqual(len(manifest.stored), 1)
        self.assertEqual(manifest.stored[0].tool_use_id, "t1")

    def test_idempotent_on_already_stored(self):
        """Running twice doesn't re-store already-stored results."""
        large_content = "x" * 50_000
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", large_content),
        ]
        result1, saved1 = apply_tool_result_budget(
            messages, self.budget_dir, max_result_tokens=1_000,
        )
        self.assertGreater(saved1, 0)

        # Run again with the already-replaced messages
        result2, saved2 = apply_tool_result_budget(
            result1, self.budget_dir, max_result_tokens=1_000,
        )
        self.assertEqual(saved2, 0)

    def test_cleanup_removes_files(self):
        """cleanup_budget_dir() removes all stored files."""
        large_content = "x" * 50_000
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", large_content),
        ]
        apply_tool_result_budget(messages, self.budget_dir, max_result_tokens=1_000)
        self.assertTrue(self.budget_dir.exists())

        cleanup_budget_dir(self.budget_dir)
        self.assertFalse(self.budget_dir.exists())

    def test_mixed_small_and_large_results(self):
        """Only large results are offloaded; small ones stay."""
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", "small"),
            _make_assistant_with_tool_use("t2"),
            _make_user_with_tool_result("t2", "y" * 50_000),
        ]
        result, saved = apply_tool_result_budget(
            messages, self.budget_dir, max_result_tokens=1_000,
        )
        self.assertGreater(saved, 0)

        # First result unchanged
        self.assertEqual(result[1].content[0].content, "small")
        # Second result replaced
        self.assertIn("[Tool result stored at:", result[3].content[0].content)

    def test_empty_messages(self):
        """Empty message list returns empty."""
        result, saved = apply_tool_result_budget([], self.budget_dir)
        self.assertEqual(result, [])
        self.assertEqual(saved, 0)


class TestPerMessageAggregateBudget(unittest.TestCase):
    """WI-5.1: per-message tool-result aggregate cap (200K chars).

    Each block individually under the per-tool 50K threshold passes
    through alone — but if FIVE such blocks (5×40K=200K) all reach the
    message, the context budget is blown. This class verifies the
    aggregate gate at ``maybe_persist_large_tool_result``.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tool_results_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_under_budget_unchanged(self):
        """Aggregate counter below cap → block passes through unchanged."""
        from src.services.tool_execution.tool_result_persistence import (
            maybe_persist_large_tool_result,
        )
        block = {"type": "tool_result", "tool_use_id": "1", "content": "x" * 30_000}
        result = maybe_persist_large_tool_result(
            block,
            tool_name="Read",
            threshold=50_000,
            tool_results_dir=self.tool_results_dir,
            aggregate_chars_so_far=10_000,
        )
        self.assertEqual(result["content"], "x" * 30_000)

    def test_at_budget_threshold_persisted(self):
        """Adding a block that pushes past 200K → persisted to disk."""
        from src.services.tool_execution.tool_result_persistence import (
            maybe_persist_large_tool_result,
        )
        block = {"type": "tool_result", "tool_use_id": "2", "content": "x" * 40_000}
        result = maybe_persist_large_tool_result(
            block,
            tool_name="Read",
            threshold=50_000,
            tool_results_dir=self.tool_results_dir,
            aggregate_chars_so_far=180_000,
        )
        self.assertIn("<persisted-output>", result["content"])
        self.assertNotEqual(result["content"], "x" * 40_000)

    def test_simulated_five_parallel_reads_at_40k(self):
        """Five 40K reads sum to exactly 200K — at-cap, not over → all pass."""
        from src.services.tool_execution.tool_result_persistence import (
            compute_block_chars, maybe_persist_large_tool_result,
        )
        running = 0
        results = []
        for i in range(5):
            block = {"type": "tool_result", "tool_use_id": str(i), "content": "x" * 40_000}
            result = maybe_persist_large_tool_result(
                block,
                tool_name="Read",
                threshold=50_000,
                tool_results_dir=self.tool_results_dir,
                aggregate_chars_so_far=running,
            )
            running += compute_block_chars(result)
            results.append(result)
        # All five pass through (cumulative 200K == cap, not > cap).
        for r in results:
            self.assertEqual(r["content"], "x" * 40_000)

    def test_six_parallel_reads_triggers_persistence(self):
        """Six × 40K = 240K > 200K → 6th block persisted."""
        from src.services.tool_execution.tool_result_persistence import (
            maybe_persist_large_tool_result,
        )
        block = {"type": "tool_result", "tool_use_id": "6", "content": "x" * 40_000}
        result = maybe_persist_large_tool_result(
            block,
            tool_name="Read",
            threshold=50_000,
            tool_results_dir=self.tool_results_dir,
            aggregate_chars_so_far=200_000,
        )
        self.assertIn("<persisted-output>", result["content"])

    def test_max_constant_value(self):
        from src.services.tool_execution.tool_result_persistence import (
            MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
        )
        self.assertEqual(MAX_TOOL_RESULTS_PER_MESSAGE_CHARS, 200_000)

    def test_compute_block_chars_returns_size(self):
        from src.services.tool_execution.tool_result_persistence import (
            compute_block_chars,
        )
        self.assertEqual(
            compute_block_chars({"content": "hello"}),
            5,
        )

    def test_tool_use_context_carries_aggregate_field(self):
        """``ToolContext.tool_result_chars_so_far`` defaults to 0."""
        from src.tool_system.context import ToolContext
        ctx = ToolContext(workspace_root=Path("/tmp"))
        self.assertEqual(ctx.tool_result_chars_so_far, 0)

    def test_query_loop_resets_aggregate_each_turn(self):
        """WI-5.1 (post-Phase 5 critic M1): the counter MUST reset at each
        turn boundary, otherwise a session monotonically grows it and
        every tool result eventually persists regardless of size.

        Mirrors TS ``toolResultStorage.ts:collectCandidatesByMessage`` —
        each user message is a fresh aggregate budget.

        Structural AST test (post-Phase-5 critic M1, refined by M5):
        walk the AST of ``query.py`` and assert the assignment
        ``tool_use_context.tool_result_chars_so_far = 0`` has a
        ``while True:`` ancestor. This catches the most realistic
        refactor failure (hoisting the reset out of the loop body so it
        runs at most once per ``query()`` call). It does NOT verify
        reachability or relative ordering — the assignment could
        theoretically sit after a return or in dead code and the test
        would still pass. Manual placement at the top of the loop body
        is the contract; this test guards the structural half.
        """
        import ast
        from pathlib import Path
        query_src = Path(__file__).parent.parent / "src" / "query" / "query.py"
        tree = ast.parse(query_src.read_text())

        # Find every ``tool_use_context.tool_result_chars_so_far = 0`` and
        # check it is structurally inside a ``while True:`` loop body.
        target_assignments: list[ast.Assign] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr != "tool_result_chars_so_far":
                continue
            if not isinstance(target.value, ast.Name):
                continue
            if target.value.id != "tool_use_context":
                continue
            if not isinstance(node.value, ast.Constant) or node.value.value != 0:
                continue
            target_assignments.append(node)

        self.assertGreater(
            len(target_assignments), 0,
            "WI-5.1 per-turn reset missing from query.py — "
            "tool_use_context.tool_result_chars_so_far = 0 not found",
        )

        # Build a parent map and verify each reset has a ``while True:``
        # ancestor before reaching the function body.
        parent_of: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parent_of[child] = parent

        def _has_while_true_ancestor(node: ast.AST) -> bool:
            cursor: ast.AST | None = parent_of.get(node)
            while cursor is not None:
                if isinstance(cursor, ast.While):
                    cond = cursor.test
                    is_while_true = (
                        isinstance(cond, ast.Constant) and cond.value is True
                    )
                    if is_while_true:
                        return True
                cursor = parent_of.get(cursor)
            return False

        in_loop = [a for a in target_assignments if _has_while_true_ancestor(a)]
        self.assertGreater(
            len(in_loop), 0,
            "Reset must be INSIDE a ``while True:`` loop body — "
            "otherwise it runs at most once at function entry and the "
            "counter grows across turns",
        )


# ch07 unification note: TestProductionPathBudgetEnforcement (which drove
# the retired query._dispatch_single_tool with MagicMock plumbing) was
# replaced by unified-lane coverage — see
# tests/test_tool_pipeline_round3.py (aggregate skip-set, cap boundary,
# production-lane run_tools variants) and
# TestUnifiedLaneConcurrentCap below.


class TestUnifiedLaneConcurrentCap(unittest.TestCase):
    """Concurrent batches must not bypass the 200K per-message cap.

    The slim lane needed ``_aggregate_lock`` because dispatch ran in
    ``asyncio.to_thread``; the orchestrator lane's step-11
    read-decide-write runs await-free ON the event loop, so concurrent
    tools serialize naturally. This pins the end state: five 60K
    results through one concurrent batch stay within cap + wrapper
    slack.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_concurrent_batch_respects_cap(self):
        import asyncio

        from src.services.tool_execution.orchestrator import run_tools
        from src.services.tool_execution.tool_result_persistence import (
            MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
        )
        from src.tool_system.build_tool import build_tool
        from src.tool_system.context import ToolContext, ToolUseOptions
        from src.tool_system.protocol import ToolResult
        from src.types.messages import AssistantMessage

        def call(_inp, _ctx):
            return ToolResult(name="BigSafe", output="z" * 60_000)

        tool = build_tool(
            name="BigSafe",
            input_schema={"type": "object", "properties": {},
                          "additionalProperties": True},
            call=call,
            prompt="b",
            description="b",
            is_concurrency_safe=lambda _i: True,
            max_result_size_chars=100_000,
        )
        ctx = ToolContext(
            workspace_root=self.workspace,
            options=ToolUseOptions(tools=[tool]),
        )
        ctx.permission_context.mode = "bypassPermissions"

        from types import SimpleNamespace

        blocks = [
            SimpleNamespace(name="BigSafe", input={}, id=f"toolu_cap_{i}")
            for i in range(5)
        ]

        def allow(*_a, **_k):
            return {"behavior": "allow"}

        async def drive():
            async for _u in run_tools(
                blocks, [AssistantMessage(content="t")], allow, ctx,
            ):
                pass

        asyncio.run(drive())
        # 3x60K fit under 200K; the 4th/5th force-persist to wrappers.
        self.assertLessEqual(
            ctx.tool_result_chars_so_far,
            MAX_TOOL_RESULTS_PER_MESSAGE_CHARS + 6_000,
        )



if __name__ == "__main__":
    unittest.main()
