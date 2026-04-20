"""
Integration tests for WS-6 Compression Pipeline.

Tests the pipeline wired into the query loop with a mock provider,
verifying end-to-end behavior including autocompact triggering,
boundary marker creation, and conversation continuity after compaction.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import (
    Message,
    UserMessage,
    AssistantMessage,
    normalize_messages_for_api,
)
from src.providers.base import BaseProvider, ChatResponse
from src.compact_service.messages import (
    create_compact_boundary_message,
    is_compact_boundary_message,
    get_messages_after_boundary,
)
from src.services.compact.pipeline import (
    CompressionPipeline,
    PipelineConfig,
    run_compression_pipeline,
)
from src.services.compact.compact import (
    CompactContext,
    compact_conversation,
)
from src.services.compact.autocompact import (
    AutoCompactTracking,
    auto_compact_if_needed,
    should_auto_compact,
)
from src.services.compact.context_collapse import ContextCollapseStore
from src.services.compact.tool_result_budget import apply_tool_result_budget
from src.services.compact.snip_compact import snip_compact
from src.context_system.microcompact import (
    microcompact_typed_messages,
    microcompact_messages,
    strip_images_from_messages,
)


def _make_assistant_tool(tool_id: str, tool_name: str = "Read") -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[ToolUseBlock(id=tool_id, name=tool_name, input={"file_path": "test.txt"})],
    )


def _make_user_result(tool_id: str, content: str) -> UserMessage:
    return UserMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id=tool_id, content=content)],
    )


def _make_long_conversation(rounds: int = 20) -> list[Message]:
    """Build a long conversation simulating many tool-use rounds."""
    messages: list[Message] = [UserMessage(content="Help me analyze this codebase")]
    for i in range(rounds):
        messages.append(_make_assistant_tool(f"t{i}"))
        messages.append(_make_user_result(f"t{i}", f"File content for round {i}. " * 100))
    return messages


class TestEndToEndPipeline(unittest.TestCase):
    """End-to-end pipeline tests with varying conversation lengths."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.budget_dir = Path(self.tmpdir.name) / "budget"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_short_conversation_no_compression(self):
        """Short conversations pass through without compression."""
        messages = [
            UserMessage(content="Hello"),
            AssistantMessage(content=[TextBlock(text="Hi there!")]),
        ]
        result = asyncio.run(run_compression_pipeline(messages))
        self.assertEqual(len(result.messages), 2)
        self.assertEqual(result.tokens_saved, 0)
        self.assertEqual(result.layers_applied, [])

    def test_medium_conversation_layers_1_2_3(self):
        """Medium conversation triggers layers 1-3."""
        messages = _make_long_conversation(15)
        config = PipelineConfig(
            budget_dir=self.budget_dir,
            max_result_tokens=500,
            snip_keep_recent=3,
            mc_keep_recent=3,
            early_exit_tokens=999_999,  # Don't early-exit
        )
        result = asyncio.run(run_compression_pipeline(messages, config=config))
        self.assertGreater(result.tokens_saved, 0)
        # At least one of the first 3 layers should have triggered
        layer_set = set(result.layers_applied)
        self.assertTrue(layer_set & {"tool_result_budget", "snip_compact", "microcompact"})

    def test_long_conversation_all_layers(self):
        """Long conversation triggers autocompact when provider is set."""
        messages = _make_long_conversation(20)

        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Comprehensive summary of the coding session.",
            model="test",
            usage={"input_tokens": 200, "output_tokens": 100},
            finish_reason="stop",
        ))

        tracking = AutoCompactTracking()
        config = PipelineConfig(
            budget_dir=self.budget_dir,
            max_result_tokens=500,
            snip_keep_recent=3,
            mc_keep_recent=3,
            context_window=1_000,
            autocompact_threshold=0.1,
            autocompact_tracking=tracking,
            provider=provider,
            model="test-model",
            early_exit_tokens=999_999,
        )
        result = asyncio.run(run_compression_pipeline(
            messages, input_token_count=900_000, config=config,
        ))
        self.assertGreater(result.tokens_saved, 0)
        # Autocompact should have been attempted
        self.assertIn("autocompact", result.layers_applied)
        self.assertEqual(tracking.total_compactions, 1)


class TestPipelineWithBoundaryMarkers(unittest.TestCase):
    """Test that boundary markers work with the pipeline."""

    def test_boundary_markers_filtered_from_api_messages(self):
        """Boundary markers are not sent to the API."""
        boundary = create_compact_boundary_message(
            trigger="manual",
            pre_compact_token_count=5000,
        )
        messages = [
            UserMessage(content="Pre-boundary message"),
            AssistantMessage(content=[TextBlock(text="Response")]),
            boundary,
            UserMessage(content="Post-boundary message"),
        ]
        api_msgs = normalize_messages_for_api(messages)
        for msg in api_msgs:
            self.assertNotIn("compact_boundary", str(msg.get("content", "")))

    def test_get_messages_after_boundary(self):
        """After-boundary messages are correctly identified."""
        messages = [
            UserMessage(content="Old message"),
            AssistantMessage(content=[TextBlock(text="Old response")]),
        ]
        boundary = create_compact_boundary_message(trigger="auto")
        messages.append(boundary)
        messages.append(UserMessage(content="New message"))
        messages.append(AssistantMessage(content=[TextBlock(text="New response")]))

        after = get_messages_after_boundary(messages)
        self.assertEqual(len(after), 2)
        self.assertEqual(after[0].content, "New message")


class TestConversationContinuityAfterCompaction(unittest.TestCase):
    """Test that the conversation can continue after compaction."""

    def test_compact_then_continue(self):
        """Conversation state is valid after compaction."""
        messages = _make_long_conversation(5)

        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Summary: User was analyzing a codebase with Read tool.",
            model="test",
            usage={},
            finish_reason="stop",
        ))

        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=messages,
            trigger="manual",
        )
        result = asyncio.run(compact_conversation(ctx))

        # Build post-compaction conversation
        new_conversation = [result.boundary_marker] + result.summary_messages
        new_conversation.append(UserMessage(content="Continue the analysis"))

        # The conversation should be normalizable to API format
        api_msgs = normalize_messages_for_api(new_conversation)
        self.assertGreater(len(api_msgs), 0)
        # All API messages should have valid roles
        for msg in api_msgs:
            self.assertIn(msg["role"], {"user", "assistant"})


class TestBackwardCompatCompactService(unittest.TestCase):
    """Test that the old compact_service still works."""

    def test_old_compact_service_runs(self):
        """src/compact_service/service.py still works."""
        from src.compact_service.service import compact_conversation as old_compact

        from src.agent.conversation import Conversation
        conv = Conversation()
        conv.messages = [
            UserMessage(content="Hello world " * 50),
            AssistantMessage(content=[TextBlock(text="Hi there! " * 50)]),
            UserMessage(content="What about the code? " * 50),
        ]

        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Summary of conversation.",
            model="test",
            usage={},
            finish_reason="stop",
        ))

        result = asyncio.run(old_compact(conv, provider, "test-model"))
        # The mock LLM succeeds, so the summary is from the mock response
        self.assertIsNotNone(result.summary_text)
        self.assertGreater(len(result.summary_text), 0)

    def test_old_microcompact_backward_compat(self):
        """Dict-based microcompact_messages still works."""
        messages = [
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "file data " * 100},
                ],
            },
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t2", "name": "Read", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t2", "content": "recent data"},
                ],
            },
        ]
        result, saved = microcompact_messages(messages, keep_recent=1)
        self.assertIsInstance(result, list)
        # Should have cleared t1's result
        self.assertGreater(saved, 0)


class TestLayerInteractions(unittest.TestCase):
    """Test interactions between layers."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.budget_dir = Path(self.tmpdir.name) / "budget"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_budget_then_snip_then_microcompact(self):
        """All three lightweight layers can run sequentially."""
        messages = _make_long_conversation(20)

        # Layer 1
        messages, saved1 = apply_tool_result_budget(
            messages, self.budget_dir, max_result_tokens=500,
        )

        # Layer 2
        messages, saved2 = snip_compact(messages, keep_recent=3)

        # Layer 3
        messages, saved3 = microcompact_typed_messages(
            messages, keep_recent=3, force=True,
        )

        total_saved = saved1 + saved2 + saved3
        self.assertGreater(total_saved, 0)
        # Messages should still be a valid list
        self.assertIsInstance(messages, list)
        self.assertGreater(len(messages), 0)

    def test_context_collapse_with_microcompact(self):
        """Context collapse and microcompact work together."""
        messages = [
            UserMessage(content="Old task", uuid="u1"),
            AssistantMessage(
                content=[ToolUseBlock(id="t1", name="Read", input={})],
                uuid="a1",
            ),
            UserMessage(
                content=[ToolResultBlock(tool_use_id="t1", content="old file data " * 100)],
                uuid="u2",
            ),
            UserMessage(content="New task", uuid="u3"),
            AssistantMessage(
                content=[ToolUseBlock(id="t2", name="Read", input={})],
                uuid="a2",
            ),
            UserMessage(
                content=[ToolResultBlock(tool_use_id="t2", content="new data")],
                uuid="u4",
            ),
        ]

        # Apply context collapse
        store = ContextCollapseStore()
        store.add_commit(["u1", "a1", "u2"], "Summary of old task")
        collapsed = store.project_view(messages)

        # Then microcompact
        result, saved = microcompact_typed_messages(collapsed, keep_recent=1, force=True)
        self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main()
