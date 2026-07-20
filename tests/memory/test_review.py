"""Background-review pure logic (src/memory/review.py): the summarizer
(committed-successes only, snapshot-id skip, staged exclusion, off/on/
verbose modes), counter hydration, user-turn counting, and the organic
Memory-call detector."""

from __future__ import annotations

import json

from src.memory.review import (
    collect_tool_use_ids,
    count_staged_actions,
    format_review_summary,
    format_staged_notice,
    hydrate_turns_since_memory,
    summarize_review_actions,
    turn_used_memory_tool,
)
from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import create_message


def _memory_call(tool_use_id: str, args: dict, result: dict) -> list:
    """An assistant tool_use + its user tool_result pair."""
    return [
        create_message("assistant", [
            ToolUseBlock(id=tool_use_id, name="Memory", input=args),
        ]),
        create_message("user", [
            ToolResultBlock(tool_use_id=tool_use_id, content=json.dumps(result)),
        ]),
    ]


_ADD_OK = {"success": True, "done": True, "target": "memory", "message": "Entry added."}


class TestSummarizer:
    def test_committed_success_summarized(self):
        msgs = _memory_call("tu1", {"action": "add", "target": "memory", "content": "fact"}, _ADD_OK)
        actions = summarize_review_actions(msgs, set())
        assert actions == ["Memory updated"]

    def test_user_target_label(self):
        result = dict(_ADD_OK, target="user")
        msgs = _memory_call("tu1", {"action": "add", "target": "user", "content": "fact"}, result)
        assert summarize_review_actions(msgs, set()) == ["User profile updated"]

    def test_snapshot_ids_skipped(self):
        msgs = _memory_call("tu1", {"action": "add", "target": "memory", "content": "old"}, _ADD_OK)
        prior = collect_tool_use_ids(msgs)
        assert prior == {"tu1"}
        assert summarize_review_actions(msgs, prior) == []

    def test_failed_result_ignored(self):
        msgs = _memory_call(
            "tu1", {"action": "add", "target": "memory", "content": "x"},
            {"success": False, "error": "over budget"},
        )
        assert summarize_review_actions(msgs, set()) == []

    def test_staged_result_not_counted_as_committed(self):
        msgs = _memory_call(
            "tu1", {"action": "add", "target": "memory", "content": "x"},
            {"success": True, "staged": True, "pending_id": "abc"},
        )
        assert summarize_review_actions(msgs, set()) == []

    def test_staged_results_counted_separately(self):
        msgs = _memory_call(
            "tu1", {"action": "add", "target": "memory", "content": "x"},
            {"success": True, "staged": True, "pending_id": "abc"},
        )
        assert count_staged_actions(msgs, set()) == 1
        assert count_staged_actions(msgs, {"tu1"}) == 0  # inherited → skipped
        assert format_staged_notice(1) == (
            "💾 Self-improvement review: 1 memory write staged for review "
            "— /memory pending"
        )
        assert "2 memory writes staged" in format_staged_notice(2)
        assert format_staged_notice(0) is None

    def test_non_memory_tool_results_ignored(self):
        msgs = [
            create_message("assistant", [
                ToolUseBlock(id="tu9", name="Read", input={"file_path": "/x"}),
            ]),
            create_message("user", [
                ToolResultBlock(tool_use_id="tu9", content=json.dumps({"success": True})),
            ]),
        ]
        assert summarize_review_actions(msgs, set()) == []

    def test_off_mode_suppresses(self):
        msgs = _memory_call("tu1", {"action": "add", "target": "memory", "content": "f"}, _ADD_OK)
        assert summarize_review_actions(msgs, set(), notification_mode="off") == []

    def test_verbose_mode_previews_content(self):
        msgs = _memory_call(
            "tu1",
            {"action": "add", "target": "memory", "content": "User prefers terse replies"},
            _ADD_OK,
        )
        actions = summarize_review_actions(msgs, set(), notification_mode="verbose")
        assert actions == ["Memory ➕ User prefers terse replies"]

    def test_verbose_batch_previews_each_op(self):
        args = {
            "target": "memory",
            "operations": [
                {"action": "remove", "old_text": "stale entry"},
                {"action": "add", "content": "fresh entry"},
            ],
        }
        result = {"success": True, "target": "memory", "message": "Applied 2 operation(s)."}
        actions = summarize_review_actions(
            _memory_call("tu1", args, result), set(), notification_mode="verbose"
        )
        assert actions == ["Memory ➖ stale entry", "Memory ➕ fresh entry"]

    def test_format_summary(self):
        assert format_review_summary([]) is None
        line = format_review_summary(["Memory updated", "Memory updated"])
        assert line == "💾 Self-improvement review: Memory updated"


class TestCounters:
    def test_hydration_modulo(self):
        assert hydrate_turns_since_memory(0, 10) == 0
        assert hydrate_turns_since_memory(7, 10) == 7
        assert hydrate_turns_since_memory(23, 10) == 3
        assert hydrate_turns_since_memory(5, 0) == 0

    def test_turn_used_memory_tool(self):
        turn = [
            create_message("assistant", [
                TextBlock(text="saving"),
                ToolUseBlock(id="t2", name="Memory", input={"action": "add"}),
            ]),
        ]
        assert turn_used_memory_tool(turn) is True
        assert turn_used_memory_tool([create_message("assistant", "plain")]) is False
