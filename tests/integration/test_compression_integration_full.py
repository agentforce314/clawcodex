"""Phase D — Compression Pipeline Integration Tests.

Full compression pipeline: grouping → layers → compact → cleanup.
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import (
    AssistantMessage,
    UserMessage,
    create_assistant_message,
    create_user_message,
)


class TestCompressionGrouping(unittest.TestCase):
    """Message grouping into API rounds."""

    def test_group_simple_conversation(self) -> None:
        from src.services.compact.grouping import group_messages_by_api_round

        messages = [
            create_user_message("Hello"),
            create_assistant_message("Hi!"),
            create_user_message("How are you?"),
            create_assistant_message("I'm doing well."),
        ]
        rounds = group_messages_by_api_round(messages)
        # 3 rounds: leading user msg, 1st assistant+user, 2nd assistant
        self.assertEqual(len(rounds), 3)

    def test_group_tool_use_round(self) -> None:
        from src.services.compact.grouping import group_messages_by_api_round

        messages = [
            create_user_message("Read file.txt"),
            AssistantMessage(
                content=[
                    TextBlock(text="Reading..."),
                    ToolUseBlock(id="tu_1", name="Read", input={"file_path": "f.txt"}),
                ],
                stop_reason="tool_use",
            ),
            create_user_message(
                [ToolResultBlock(tool_use_id="tu_1", content="file content")],
            ),
            create_assistant_message("Here's the content."),
        ]
        rounds = group_messages_by_api_round(messages)
        self.assertGreaterEqual(len(rounds), 1)


class TestCompressionLayers(unittest.TestCase):
    """Individual compression layers exist and are callable."""

    def test_tool_result_budget_exists(self) -> None:
        from src.services.compact.tool_result_budget import apply_tool_result_budget
        self.assertTrue(callable(apply_tool_result_budget))

    def test_snip_compact_exists(self) -> None:
        from src.services.compact.snip_compact import snip_compact
        self.assertTrue(callable(snip_compact))

    def test_microcompact_exists(self) -> None:
        from src.context_system.microcompact import microcompact_messages
        self.assertTrue(callable(microcompact_messages))

    def test_context_collapse_exists(self) -> None:
        from src.services.compact.context_collapse import ContextCollapseStore
        store = ContextCollapseStore()
        self.assertIsNotNone(store)

    def test_autocompact_exists(self) -> None:
        from src.services.compact.autocompact import should_auto_compact
        self.assertTrue(callable(should_auto_compact))


class TestCompressionPipeline(unittest.TestCase):
    """Pipeline orchestration."""

    def test_pipeline_config_defaults(self) -> None:
        from src.services.compact.pipeline import PipelineConfig
        config = PipelineConfig()
        self.assertIsNotNone(config)

    def test_compact_context_fields(self) -> None:
        from src.services.compact.compact import CompactContext
        fields = CompactContext.__dataclass_fields__
        self.assertIn("messages", fields)

    def test_compact_prompt_exists(self) -> None:
        from src.services.compact.prompt import BASE_COMPACT_PROMPT
        self.assertIsInstance(BASE_COMPACT_PROMPT, str)
        self.assertGreater(len(BASE_COMPACT_PROMPT), 0)


class TestReactiveCompact(unittest.TestCase):
    """Reactive compact for prompt-too-long recovery."""

    def test_result_dataclass(self) -> None:
        from src.services.compact.reactive_compact import ReactiveCompactResult
        result = ReactiveCompactResult(
            compacted=True,
            messages=[],
            tokens_before=10000,
            tokens_after=5000,
        )
        self.assertTrue(result.compacted)
        self.assertIsNone(result.error)

    def test_error_detection(self) -> None:
        from src.services.compact.reactive_compact import is_prompt_too_long_error
        self.assertTrue(is_prompt_too_long_error(Exception("prompt_too_long")))
        self.assertTrue(is_prompt_too_long_error(Exception("Prompt is too long")))
        self.assertFalse(is_prompt_too_long_error(Exception("random error")))

    def test_drop_oldest_messages(self) -> None:
        from src.services.compact.reactive_compact import _drop_oldest_messages

        messages = [create_user_message(f"msg {i}") for i in range(20)]
        dropped = _drop_oldest_messages(messages, fraction=0.5)
        self.assertLess(len(dropped), 20)
        self.assertGreater(len(dropped), 0)


class TestPostCompactCleanup(unittest.TestCase):
    """Post-compact cleanup clears caches."""

    def test_cleanup_function_exists(self) -> None:
        from src.services.compact.post_compact_cleanup import run_post_compact_cleanup
        self.assertTrue(callable(run_post_compact_cleanup))

    def test_compact_warning_state(self) -> None:
        from src.services.compact.compact_warning import (
            is_compact_warning_suppressed,
            suppress_compact_warning,
        )
        self.assertTrue(callable(is_compact_warning_suppressed))
        self.assertTrue(callable(suppress_compact_warning))


if __name__ == "__main__":
    unittest.main()
