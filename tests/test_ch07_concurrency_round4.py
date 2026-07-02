"""ch07 round-4 acceptance tests: Agent concurrency-safe partitioning +
the MAX_TOOL_USE_CONCURRENCY coercion deadlock fix.

Covers my-docs/ch07-concurrency-round4-gap-analysis.md §2.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from types import SimpleNamespace

from src.services.tool_execution.orchestrator import (
    _get_max_tool_use_concurrency,
    classify_concurrency_safe,
    partition_tool_calls,
)
from src.tool_system.defaults import build_default_registry
from src.types.content_blocks import ToolUseBlock


def _find(registry, name):
    for t in registry.list_tools():
        if t.name == name:
            return t
    raise AssertionError(f"{name} not in registry")


class TestAgentConcurrencySafe(unittest.TestCase):
    """GAP A — the Agent tool is concurrency-safe (parallel), matching TS."""

    def setUp(self):
        self.registry = build_default_registry()
        self.agent = _find(self.registry, "Agent")

    def test_agent_declares_concurrency_safe(self):
        self.assertTrue(self.agent.is_concurrency_safe({"prompt": "x"}))

    def test_agent_not_read_only(self):
        # Deliberately different from TS (sub-agents run Edit/Write).
        self.assertFalse(self.agent.is_read_only({"prompt": "x"}))

    def test_consecutive_agents_partition_into_one_parallel_batch(self):
        blocks = [
            ToolUseBlock(id="a1", name="Agent", input={"prompt": "task 1"}),
            ToolUseBlock(id="a2", name="Agent", input={"prompt": "task 2"}),
            ToolUseBlock(id="a3", name="Agent", input={"prompt": "task 3"}),
        ]
        ctx = SimpleNamespace(
            options=SimpleNamespace(tools=self.registry.list_tools()),
        )
        batches = partition_tool_calls(blocks, ctx)
        # One parallel batch of three, not three serial batches.
        self.assertEqual(len(batches), 1)
        self.assertTrue(batches[0].is_concurrency_safe)
        self.assertEqual(len(batches[0].blocks), 3)

    def test_agent_then_edit_then_agent_partitions_3_batches(self):
        # Sanity: the greedy partition still breaks on a serial tool.
        blocks = [
            ToolUseBlock(id="a1", name="Agent", input={"prompt": "t"}),
            ToolUseBlock(id="e1", name="Edit",
                         input={"file_path": "/x", "old_string": "a",
                                "new_string": "b"}),
            ToolUseBlock(id="a2", name="Agent", input={"prompt": "t"}),
        ]
        ctx = SimpleNamespace(
            options=SimpleNamespace(tools=self.registry.list_tools()),
        )
        batches = partition_tool_calls(blocks, ctx)
        self.assertEqual([b.is_concurrency_safe for b in batches],
                         [True, False, True])

    def test_classify_agent_safe(self):
        self.assertTrue(
            classify_concurrency_safe(self.agent, {"prompt": "x"}),
        )


class TestConcurrencyCapCoercion(unittest.TestCase):
    """GAP B — a non-positive/garbage cap falls back to 10 (TS parseInt||10),
    never Semaphore(0) deadlock or Semaphore(-1) ValueError."""

    def _with_env(self, value):
        env = dict(os.environ)
        env.pop("CLAWCODEX_MAX_TOOL_USE_CONCURRENCY", None)
        env["CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY"] = value
        with patch.dict(os.environ, env, clear=True):
            return _get_max_tool_use_concurrency()

    def test_zero_falls_back_to_default(self):
        self.assertEqual(self._with_env("0"), 10)  # was Semaphore(0) → hang

    def test_negative_falls_back_to_default(self):
        self.assertEqual(self._with_env("-1"), 10)  # was Semaphore(-1) → error

    def test_garbage_falls_back_to_default(self):
        self.assertEqual(self._with_env("abc"), 10)

    def test_valid_value_honored(self):
        self.assertEqual(self._with_env("5"), 5)

    def test_unset_is_default(self):
        env = dict(os.environ)
        env.pop("CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY", None)
        env.pop("CLAWCODEX_MAX_TOOL_USE_CONCURRENCY", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_get_max_tool_use_concurrency(), 10)

    def test_legacy_alias_zero_also_coerces(self):
        env = dict(os.environ)
        env.pop("CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY", None)
        env["CLAWCODEX_MAX_TOOL_USE_CONCURRENCY"] = "0"
        with patch.dict(os.environ, env, clear=True):
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                self.assertEqual(_get_max_tool_use_concurrency(), 10)


class TestSubagentOptionsIsolation(unittest.TestCase):
    """critic MAJOR — parallel subagents must not share one options object
    (each query writes options.tools; sharing races across threads)."""

    def test_subagent_gets_own_options_copy(self):
        from src.agent.subagent_context import (
            SubagentContextOverrides,
            create_subagent_context,
        )
        from src.tool_system.context import ToolContext, ToolUseOptions

        parent = ToolContext(workspace_root=__import__("pathlib").Path("/tmp"))
        parent.options = ToolUseOptions(tools=["a", "b"])
        child = create_subagent_context(parent, SubagentContextOverrides())
        # Distinct object → writing child.options.tools can't clobber parent.
        self.assertIsNot(child.options, parent.options)
        child.options.tools = ["x"]
        self.assertEqual(parent.options.tools, ["a", "b"])


class TestCostAccumulatorThreadSafety(unittest.TestCase):
    """critic MAJOR — the cost RMW is atomic across threads (no lost
    updates under N parallel subagent recorders)."""

    def test_concurrent_record_api_usage_no_lost_updates(self):
        # critic m — a plain concurrent loop does NOT catch the race:
        # CPython's GIL makes the short real RMW window effectively atomic
        # under default scheduling, so the test would pass even with the
        # lock removed (false confidence). We WIDEN the RMW window (yield
        # between the read and the write) + a tiny switch interval so that,
        # WITHOUT the lock, lost updates are deterministic — and WITH the
        # lock they never happen.
        import sys
        import threading
        import time

        from src.bootstrap import state as _state
        from src.bootstrap.state import get_model_usage, reset_state_for_tests
        from src.cost_tracker import record_api_usage

        reset_state_for_tests()
        real_get = _state.get_model_usage

        def _slow_get_model_usage():
            # Force a scheduler yield inside the read-modify-write window so
            # a missing lock reliably interleaves two RMWs.
            result = real_get()
            time.sleep(0)
            return result

        n = 100
        old_interval = sys.getswitchinterval()
        # Patch the name cost_tracker resolves (it imported the symbol).
        import src.cost_tracker as _ct

        _orig = _ct.get_model_usage
        _ct.get_model_usage = _slow_get_model_usage
        sys.setswitchinterval(1e-6)
        try:
            def _worker():
                for _ in range(n):
                    record_api_usage(
                        "deepseek-v4-pro",
                        {"input_tokens": 1000, "output_tokens": 1000},
                    )

            threads = [threading.Thread(target=_worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            _ct.get_model_usage = _orig
            sys.setswitchinterval(old_interval)

        usage = get_model_usage().get("deepseek-v4-pro")
        # 8 threads × n calls × 1000 tokens each — the lock guarantees no
        # lost updates even with the widened window. (Without the lock this
        # assertion fails deterministically.)
        self.assertEqual(usage.input_tokens, 8 * n * 1000)
        self.assertEqual(usage.output_tokens, 8 * n * 1000)


class TestStreamingModuleRetired(unittest.TestCase):
    """WI-3 — query/streaming.py is retired."""

    def test_module_gone(self):
        with self.assertRaises(ModuleNotFoundError):
            import src.query.streaming  # noqa: F401


if __name__ == "__main__":
    unittest.main()
