"""WS-9 Build & Import Verification — integration tests.

Verifies all WS-9 modules can be imported and key classes/functions
are accessible.
"""

from __future__ import annotations

import importlib
import pytest


class TestImports:
    def test_import_streaming_executor(self):
        mod = importlib.import_module("src.services.tool_execution.streaming_executor")
        assert hasattr(mod, "StreamingToolExecutor")
        assert hasattr(mod, "ToolUseBlock")
        assert hasattr(mod, "MessageUpdate")

    def test_import_orchestrator(self):
        mod = importlib.import_module("src.services.tool_execution.orchestrator")
        assert hasattr(mod, "partition_tool_calls")
        assert hasattr(mod, "run_tools")
        assert hasattr(mod, "Batch")

    def test_import_tool_execution(self):
        mod = importlib.import_module("src.services.tool_execution.tool_execution")
        assert hasattr(mod, "run_tool_use")
        assert hasattr(mod, "MessageUpdateLazy")
        assert hasattr(mod, "ContextModifier")

    def test_import_tool_hooks(self):
        mod = importlib.import_module("src.services.tool_execution.tool_hooks")
        assert hasattr(mod, "run_pre_tool_use_hooks")
        assert hasattr(mod, "run_post_tool_use_hooks")
        assert hasattr(mod, "run_post_tool_use_failure_hooks")
        assert hasattr(mod, "resolve_hook_permission_decision")

    def test_import_token_estimation(self):
        mod = importlib.import_module("src.token_estimation")
        assert hasattr(mod, "rough_token_count_estimation")
        assert hasattr(mod, "rough_token_count_estimation_for_messages")
        assert hasattr(mod, "rough_token_count_estimation_for_block")
        assert hasattr(mod, "bytes_per_token_for_file_type")
        assert hasattr(mod, "rough_token_count_estimation_for_file_type")
        assert hasattr(mod, "count_tokens")
        assert hasattr(mod, "count_messages_tokens")

    def test_import_cost_tracker(self):
        mod = importlib.import_module("src.services.cost_tracker")
        assert hasattr(mod, "CostTracker")
        assert hasattr(mod, "PRICING")
        assert hasattr(mod, "UsageEvent")

    def test_import_token_budget(self):
        mod = importlib.import_module("src.query.token_budget")
        assert hasattr(mod, "check_token_budget")
        assert hasattr(mod, "BudgetTracker")
        assert hasattr(mod, "parse_token_budget")
        assert hasattr(mod, "find_token_budget_positions")
        assert hasattr(mod, "get_budget_continuation_message")

    def test_import_stop_hooks(self):
        mod = importlib.import_module("src.query.stop_hooks")
        assert hasattr(mod, "handle_stop_hooks")
        assert hasattr(mod, "StopHookResult")

    def test_import_hook_executor(self):
        mod = importlib.import_module("src.hooks.hook_executor")
        assert hasattr(mod, "execute_pre_tool_hooks")
        assert hasattr(mod, "execute_post_tool_hooks")
        assert hasattr(mod, "execute_stop_hooks")
        assert hasattr(mod, "has_hook_for_event")

    def test_import_hook_types(self):
        mod = importlib.import_module("src.hooks.hook_types")
        assert hasattr(mod, "HookConfig")
        assert hasattr(mod, "HookResult")
        assert hasattr(mod, "HookProgress")
        assert hasattr(mod, "TOOL_HOOK_EXECUTION_TIMEOUT_MS")

    def test_import_messages_new_functions(self):
        mod = importlib.import_module("src.types.messages")
        assert hasattr(mod, "create_attachment_message")
        assert hasattr(mod, "create_stop_hook_summary_message")
        assert hasattr(mod, "create_user_interruption_message")


class TestBasicIntegration:
    def test_cost_tracker_workflow(self):
        from src.services.cost_tracker import CostTracker
        tracker = CostTracker()
        tracker.record_usage("claude-sonnet-4-20250514", {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_read_input_tokens": 500,
        })
        assert tracker.get_total_cost() > 0
        assert tracker.get_cache_savings() > 0
        summary = tracker.get_summary()
        assert summary["event_count"] == 1

    def test_token_budget_workflow(self):
        from src.query.token_budget import (
            check_token_budget,
            create_budget_tracker,
            parse_token_budget,
        )
        budget = parse_token_budget("+500k do something")
        assert budget == 500000

        tracker = create_budget_tracker()
        decision = check_token_budget(tracker, None, budget, 100000)
        assert decision.action == "continue"

        decision = check_token_budget(tracker, None, budget, 480000)
        assert decision.action == "stop"

    def test_partition_and_tool_lookup(self):
        from pathlib import Path
        from src.services.tool_execution.orchestrator import partition_tool_calls
        from src.services.tool_execution.streaming_executor import ToolUseBlock
        from src.tool_system.build_tool import build_tool
        from src.tool_system.context import ToolContext, ToolUseOptions
        from src.tool_system.protocol import ToolResult

        safe_tool = build_tool(
            name="Read",
            input_schema={"type": "object", "properties": {}},
            call=lambda i, c: ToolResult(name="Read", output="ok"),
            is_concurrency_safe=lambda _: True,
        )
        unsafe_tool = build_tool(
            name="Write",
            input_schema={"type": "object", "properties": {}},
            call=lambda i, c: ToolResult(name="Write", output="ok"),
            is_concurrency_safe=lambda _: False,
        )
        ctx = ToolContext(
            workspace_root=Path("/tmp"),
            options=ToolUseOptions(tools=[safe_tool, unsafe_tool]),
        )

        blocks = [
            ToolUseBlock(id="1", name="Read", input={}),
            ToolUseBlock(id="2", name="Read", input={}),
            ToolUseBlock(id="3", name="Write", input={}),
            ToolUseBlock(id="4", name="Read", input={}),
        ]
        batches = partition_tool_calls(blocks, ctx)
        assert len(batches) == 3
        assert batches[0].is_concurrency_safe
        assert not batches[1].is_concurrency_safe
        assert batches[2].is_concurrency_safe

    def test_streaming_executor_unknown_tool(self):
        from pathlib import Path
        from src.services.tool_execution.streaming_executor import (
            StreamingToolExecutor,
            ToolUseBlock,
        )
        from src.tool_system.context import ToolContext, ToolUseOptions
        from src.types.messages import create_assistant_message
        from src.utils.abort_controller import AbortController

        ctx = ToolContext(
            workspace_root=Path("/tmp"),
            options=ToolUseOptions(tools=[]),
            abort_controller=AbortController(),
        )
        executor = StreamingToolExecutor([], None, ctx)
        executor.add_tool(
            ToolUseBlock(id="1", name="NonExistent", input={}),
            create_assistant_message(content="test"),
        )
        results = list(executor.get_completed_results())
        assert len(results) == 1
        assert results[0].message is not None

    def test_token_estimation_end_to_end(self):
        from src.token_estimation import (
            rough_token_count_estimation_for_messages,
            rough_token_count_estimation_for_content,
        )

        messages = [
            {"type": "user", "message": {"content": "Hello, help me write code"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Sure, let me help you with that."},
                {"type": "tool_use", "name": "Write", "input": {"file_path": "/tmp/test.py", "content": "print('hello')"}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "content": "File written successfully"},
            ]}},
        ]
        total = rough_token_count_estimation_for_messages(messages)
        assert total > 0
