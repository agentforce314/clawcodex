"""
Tests for the compression pipeline orchestrator.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import Message, UserMessage, AssistantMessage
from src.providers.base import ChatResponse
from src.services.compact.pipeline import (
    CompressionPipeline,
    CompressionResult,
    PipelineConfig,
    run_compression_pipeline,
)
from src.services.compact.context_collapse import ContextCollapseStore
from src.services.compact.autocompact import (
    AutoCompactTracking,
    get_auto_compact_threshold,
)


def _make_assistant(tool_id: str, tool_name: str = "Read") -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[ToolUseBlock(id=tool_id, name=tool_name, input={})],
    )


def _make_user_result(tool_id: str, content: str) -> UserMessage:
    return UserMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id=tool_id, content=content)],
    )


def _make_simple_messages(count: int = 4) -> list[Message]:
    """Create a simple conversation with count rounds of tool use."""
    messages: list[Message] = []
    for i in range(count):
        messages.append(_make_assistant(f"t{i}"))
        messages.append(_make_user_result(f"t{i}", f"Result {i} " * 50))
    return messages


class TestCompressionPipelineEmpty(unittest.TestCase):
    """Tests for pipeline with empty / minimal input."""

    def test_empty_messages(self):
        result = asyncio.run(run_compression_pipeline([]))
        self.assertEqual(result.messages, [])
        self.assertEqual(result.tokens_saved, 0)
        self.assertEqual(result.layers_applied, [])

    def test_single_message(self):
        messages = [UserMessage(content="Hello")]
        result = asyncio.run(run_compression_pipeline(messages))
        self.assertEqual(len(result.messages), 1)
        self.assertEqual(result.tokens_saved, 0)


class TestCompressionPipelineLayers(unittest.TestCase):
    """Tests for individual layers triggering in the pipeline."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.budget_dir = Path(self.tmpdir.name) / "budget"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_layer1_tool_result_budget(self):
        """Layer 1 triggers when tool results exceed threshold."""
        messages = [
            _make_assistant("t1"),
            _make_user_result("t1", "x" * 50_000),  # ~12,500 tokens
        ]
        config = PipelineConfig(
            budget_dir=self.budget_dir,
            max_result_tokens=1_000,
            snip_keep_recent=100,  # high to prevent snip
            mc_keep_recent=100,    # high to prevent mc
        )
        result = asyncio.run(run_compression_pipeline(messages, config=config))
        self.assertIn("tool_result_budget", result.layers_applied)
        self.assertGreater(result.tokens_saved, 0)

    def test_layer2_snip_compact_is_noop(self):
        """Layer 2 is a no-op stub (matches TS snipCompact.ts)."""
        messages = _make_simple_messages(15)
        config = PipelineConfig(
            budget_dir=self.budget_dir,
            max_result_tokens=999_999,
            snip_keep_recent=2,
            mc_keep_recent=100,
        )
        result = asyncio.run(run_compression_pipeline(messages, config=config))
        self.assertNotIn("snip_compact", result.layers_applied)

    def test_layer3_microcompact(self):
        """Layer 3 triggers when compactable tool results exist."""
        messages = _make_simple_messages(10)
        config = PipelineConfig(
            budget_dir=self.budget_dir,
            max_result_tokens=999_999,
            snip_keep_recent=100,  # high to skip layer 2
            mc_enabled=True,
            mc_keep_recent=2,
        )
        result = asyncio.run(run_compression_pipeline(messages, config=config))
        self.assertIn("microcompact", result.layers_applied)
        self.assertGreater(result.tokens_saved, 0)

    def test_layer4_context_collapse(self):
        """Layer 4 applies context collapse when store has commits."""
        messages = [
            UserMessage(content="Old query", uuid="u1"),
            AssistantMessage(content=[TextBlock(text="Old answer")], uuid="a1"),
            UserMessage(content="New query", uuid="u2"),
        ]
        store = ContextCollapseStore()
        store.add_commit(["u1", "a1"], "Summary of old exchange")

        config = PipelineConfig(
            budget_dir=self.budget_dir,
            max_result_tokens=999_999,
            snip_keep_recent=100,
            mc_keep_recent=100,
            collapse_store=store,
        )
        result = asyncio.run(run_compression_pipeline(messages, config=config))
        self.assertIn("context_collapse", result.layers_applied)

    def test_layer5_autocompact_not_triggered_below_threshold(self):
        """Layer 5 does not trigger below token threshold."""
        messages = _make_simple_messages(3)
        provider = MagicMock()
        config = PipelineConfig(
            budget_dir=self.budget_dir,
            max_result_tokens=999_999,
            snip_keep_recent=100,
            mc_keep_recent=100,
            context_window=200_000,
            autocompact_threshold=0.8,
            provider=provider,
            model="test-model",
        )
        result = asyncio.run(run_compression_pipeline(
            messages, input_token_count=5_000, config=config,
        ))
        self.assertNotIn("autocompact", result.layers_applied)
        provider.chat_async.assert_not_called()


class TestCompressionPipelineEarlyExit(unittest.TestCase):
    """Tests for pipeline early exit when enough tokens are freed."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.budget_dir = Path(self.tmpdir.name) / "budget"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_early_exit_skips_later_layers(self):
        """If layer 1 frees enough tokens, later layers are skipped."""
        messages = [
            _make_assistant("t1"),
            _make_user_result("t1", "x" * 200_000),  # massive result
        ]
        config = PipelineConfig(
            budget_dir=self.budget_dir,
            max_result_tokens=100,
            early_exit_tokens=1_000,
            snip_keep_recent=1,
            mc_keep_recent=1,
        )
        result = asyncio.run(run_compression_pipeline(messages, config=config))
        self.assertIn("tool_result_budget", result.layers_applied)
        # snip and microcompact should NOT be in layers because of early exit
        self.assertNotIn("snip_compact", result.layers_applied)
        self.assertNotIn("microcompact", result.layers_applied)


class TestCompressionPipelineAutocompact(unittest.TestCase):
    """Tests for layer 5 wiring: token threshold + attachment forwarding.

    Guards against the inert-by-default bug where the query loop passed
    input_token_count=0, which fell below MIN_INPUT_TOKENS_FOR_AUTOCOMPACT
    and short-circuited autocompact forever.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.budget_dir = Path(self.tmpdir.name) / "budget"

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_provider(self, summary: str = "Compacted summary"):
        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content=summary,
            model="test",
            usage={"input_tokens": 100, "output_tokens": 50},
            finish_reason="stop",
        ))
        return provider

    def test_layer5_autocompact_fires_above_threshold(self):
        """Layer 5 fires from the pipeline when input tokens exceed threshold."""
        messages = _make_simple_messages(3)
        provider = self._make_provider()
        config = PipelineConfig(
            budget_dir=self.budget_dir,
            max_result_tokens=999_999,
            snip_keep_recent=100,
            mc_keep_recent=100,
            context_window=200_000,
            autocompact_threshold=0.8,
            provider=provider,
            model="test-model",
        )
        threshold = get_auto_compact_threshold(200_000)
        result = asyncio.run(run_compression_pipeline(
            messages, input_token_count=threshold + 100, config=config,
        ))
        self.assertIn("autocompact", result.layers_applied)
        self.assertIsNotNone(result.autocompact_result)
        self.assertEqual(result.autocompact_result.trigger, "auto")
        provider.chat_async.assert_called()

    def test_layer5_forwards_read_file_state_to_attachments(self):
        """read_file_state on PipelineConfig produces post-compact file attachments."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("print('hello')")
            f.flush()
            tmp_path = f.name

        try:
            messages = _make_simple_messages(3)
            provider = self._make_provider()
            config = PipelineConfig(
                budget_dir=self.budget_dir,
                max_result_tokens=999_999,
                snip_keep_recent=100,
                mc_keep_recent=100,
                context_window=200_000,
                autocompact_threshold=0.8,
                provider=provider,
                model="test-model",
                read_file_state={tmp_path: {"timestamp": time.time()}},
            )
            threshold = get_auto_compact_threshold(200_000)
            result = asyncio.run(run_compression_pipeline(
                messages, input_token_count=threshold + 100, config=config,
            ))
            self.assertIn("autocompact", result.layers_applied)
            self.assertIsNotNone(result.autocompact_result)
            attachments = result.autocompact_result.attachments
            self.assertGreaterEqual(len(attachments), 1)
            self.assertTrue(any(
                tmp_path in m.content for m in attachments
                if isinstance(m.content, str)
            ))
        finally:
            os.unlink(tmp_path)


class TestCompressionPipelineConvenienceFunction(unittest.TestCase):
    """Tests for the run_compression_pipeline convenience function."""

    def test_default_config(self):
        """Works with default config (no provider = no autocompact)."""
        messages = _make_simple_messages(3)
        result = asyncio.run(run_compression_pipeline(messages))
        self.assertIsInstance(result, CompressionResult)

    def test_with_explicit_config(self):
        config = PipelineConfig(snip_keep_recent=1, mc_keep_recent=1)
        messages = _make_simple_messages(5)
        result = asyncio.run(run_compression_pipeline(messages, config=config))
        self.assertIsInstance(result, CompressionResult)


if __name__ == "__main__":
    unittest.main()
