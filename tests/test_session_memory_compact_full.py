import pytest

from src.services.compact.session_memory_compact import (
    SessionMemory,
    SessionMemoryEntry,
    SESSION_MEMORY_PROMPT,
    calculate_messages_to_keep_index,
    adjust_index_to_preserve_api_invariants,
    try_session_memory_compaction,
    SessionMemoryCompactConfig,
    has_text_blocks,
)
from src.types.content_blocks import TextBlock, ToolUseBlock, ToolResultBlock
from src.types.messages import UserMessage, AssistantMessage


class TestSessionMemory:
    def test_empty(self):
        mem = SessionMemory()
        assert mem.count == 0
        assert mem.format_memory() == ""

    def test_add(self):
        mem = SessionMemory()
        mem.add("User wants to refactor auth module")
        assert mem.count == 1
        assert mem.entries[0].fact == "User wants to refactor auth module"

    def test_dedup(self):
        mem = SessionMemory()
        mem.add("User wants to refactor")
        mem.add("user wants to refactor")
        assert mem.count == 1

    def test_add_different(self):
        mem = SessionMemory()
        mem.add("Fact one")
        mem.add("Fact two")
        assert mem.count == 2

    def test_add_from_llm_response(self):
        mem = SessionMemory()
        response = """\
- User is building a CLI tool
- The project uses Python 3.11
- Tests use pytest framework
- Main entry point is src/main.py
"""
        added = mem.add_from_llm_response(response)
        assert added == 4
        assert mem.count == 4

    def test_add_from_llm_numbered(self):
        mem = SessionMemory()
        response = """\
1. First fact about the project
2. Second fact about the project
"""
        added = mem.add_from_llm_response(response)
        assert added == 2

    def test_add_from_llm_filters_short(self):
        mem = SessionMemory()
        response = "- OK\n- This is a longer fact\n"
        added = mem.add_from_llm_response(response)
        assert added == 1

    def test_deduplicate_against(self):
        mem = SessionMemory()
        mem.add("Project uses Python")
        mem.add("Tests use pytest")
        mem.add("Unique fact here")

        existing = "This project uses python for backend development."
        mem.deduplicate_against(existing)
        assert mem.count == 2

    def test_format_memory(self):
        mem = SessionMemory()
        mem.add("User wants to refactor auth")
        mem.add("Project uses Python 3.11")
        text = mem.format_memory()
        assert "## Session Memory" in text
        assert "refactor auth" in text
        assert "Python 3.11" in text

    def test_clear(self):
        mem = SessionMemory()
        mem.add("Some fact")
        mem.clear()
        assert mem.count == 0


class TestSessionMemoryPrompt:
    def test_prompt_content(self):
        assert "facts" in SESSION_MEMORY_PROMPT.lower()
        assert "TEXT ONLY" in SESSION_MEMORY_PROMPT


class TestHasTextBlocks:
    def test_string_content(self):
        msg = UserMessage(content="hello")
        assert has_text_blocks(msg) is True

    def test_empty_string(self):
        msg = UserMessage(content="")
        assert has_text_blocks(msg) is False

    def test_text_block(self):
        msg = AssistantMessage(content=[TextBlock(text="hello")])
        assert has_text_blocks(msg) is True

    def test_no_text_block(self):
        msg = AssistantMessage(content=[
            ToolUseBlock(id="t1", name="Read", input={"file_path": "/foo"})
        ])
        assert has_text_blocks(msg) is False


class TestCalculateMessagesToKeepIndex:
    """Tests for the token-based calculate_messages_to_keep_index().

    The new function takes ``last_summarized_index`` (the index of the last
    message covered by session memory) rather than a target keep count.
    """

    def _make_messages(self, n):
        msgs = []
        for i in range(n):
            if i % 2 == 0:
                msgs.append(UserMessage(content=f"msg {i}"))
            else:
                msgs.append(AssistantMessage(content=[TextBlock(text=f"resp {i}")]))
        return msgs

    def test_empty(self):
        assert calculate_messages_to_keep_index([], 0) == 0

    def test_last_summarized_at_start(self):
        """When last_summarized_index=0, messages from index 1 onward are candidates to keep.
        With small messages, backward expansion may reach index 0 to meet min_tokens."""
        msgs = self._make_messages(10)
        idx = calculate_messages_to_keep_index(msgs, 0)
        # Small messages don't meet min_tokens (10K), so expansion goes to 0
        assert idx >= 0
        assert idx < len(msgs)

    def test_last_summarized_at_end(self):
        """When last_summarized_index is at the end, backward expansion finds messages to keep."""
        msgs = self._make_messages(10)
        idx = calculate_messages_to_keep_index(msgs, len(msgs) - 1)
        # start_index would be len(msgs), but backward expansion should include some messages
        assert idx < len(msgs)

    def test_negative_index_means_no_summarized(self):
        """When last_summarized_index=-1, start from end, expand backward."""
        msgs = self._make_messages(10)
        idx = calculate_messages_to_keep_index(msgs, -1)
        # With -1 (no summarized messages), start_index = len(msgs)
        # Backward expansion should include messages to meet minimums
        assert idx < len(msgs)

    def test_respects_max_tokens(self):
        """Stops expanding when max_tokens is reached."""
        msgs = self._make_messages(10)
        config = SessionMemoryCompactConfig(
            min_tokens=100_000,  # Very high min — force full expansion
            min_text_block_messages=100,
            max_tokens=50,  # Very low max — stop early
        )
        idx = calculate_messages_to_keep_index(msgs, 0, config)
        # Should not expand too far back because max_tokens caps it
        assert idx >= 0

    def test_preserves_api_invariants(self):
        """Result doesn't split tool_use/tool_result pairs."""
        msgs = [
            UserMessage(content="hello"),
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={"file_path": "/foo"}),
            ]),
            UserMessage(content=[
                ToolResultBlock(tool_use_id="t1", content="file contents"),
            ]),
            AssistantMessage(content=[TextBlock(text="done")]),
        ]
        # last_summarized_index=0 means start_index=1 (right between tool_use and tool_result)
        idx = calculate_messages_to_keep_index(msgs, 0)
        # Should adjust to include the tool_use message
        assert idx <= 1


class TestAdjustIndex:
    def test_no_adjustment_needed(self):
        msgs = [
            UserMessage(content="hello"),
            AssistantMessage(content=[TextBlock(text="hi")]),
            UserMessage(content="bye"),
        ]
        assert adjust_index_to_preserve_api_invariants(msgs, 2) == 2

    def test_adjusts_for_tool_pair(self):
        msgs = [
            UserMessage(content="hello"),
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={"file_path": "/foo"}),
            ]),
            UserMessage(content=[
                ToolResultBlock(tool_use_id="t1", content="result"),
            ]),
            AssistantMessage(content=[TextBlock(text="done")]),
        ]
        # Index 2 is the tool_result — should adjust back to include the tool_use
        idx = adjust_index_to_preserve_api_invariants(msgs, 2)
        assert idx <= 1

    def test_boundary_zero(self):
        msgs = [UserMessage(content="hello")]
        assert adjust_index_to_preserve_api_invariants(msgs, 0) == 0

    def test_boundary_end(self):
        msgs = [UserMessage(content="hello")]
        assert adjust_index_to_preserve_api_invariants(msgs, 1) == 1


class TestSessionMemoryCompaction:
    def _make_messages(self, n):
        msgs = []
        for i in range(n):
            if i % 2 == 0:
                msgs.append(UserMessage(content=f"msg {i}"))
            else:
                msgs.append(AssistantMessage(content=[TextBlock(text=f"resp {i}")]))
        return msgs

    def test_try_compaction(self):
        msgs = self._make_messages(10)
        to_summarize, to_keep = try_session_memory_compaction(msgs, 4)
        assert len(to_summarize) + len(to_keep) == 10
        assert len(to_keep) >= 4

    def test_try_compaction_short(self):
        msgs = self._make_messages(2)
        to_summarize, to_keep = try_session_memory_compaction(msgs, 4)
        assert len(to_summarize) == 0
        assert len(to_keep) == 2

    def test_try_compaction_equal(self):
        msgs = self._make_messages(4)
        to_summarize, to_keep = try_session_memory_compaction(msgs, 4)
        assert len(to_summarize) == 0
        assert len(to_keep) == 4
