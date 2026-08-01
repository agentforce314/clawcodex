import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import AssistantMessage, SystemMessage, UserMessage
from src.utils.abort_controller import AbortController

from src.query.query import QueryParams, StreamEvent, query


def _run(coro):
    return asyncio.run(coro)


class TestQueryLoopSingleTurn(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_single_turn_no_tools(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Hello, world!",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        messages = [UserMessage(content="Hi")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        assistants = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertEqual(len(assistants), 1)

        content = assistants[0].content
        if isinstance(content, list):
            text = "".join(b.text for b in content if isinstance(b, TextBlock))
        else:
            text = content
        self.assertIn("Hello", text)

    def test_multi_turn_with_tools(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        tool_use_response = ChatResponse(
            content="I'll create the file.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_001",
                "name": "Write",
                "input": {
                    "file_path": str(self.workspace / "test.txt"),
                    "content": "hello",
                },
            }],
        )

        final_response = ChatResponse(
            content="File created!",
            model="test",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="end_turn",
            tool_uses=None,
        )

        provider.chat.side_effect = [tool_use_response, final_response]

        messages = [UserMessage(content="Create test.txt")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        assistants = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertGreaterEqual(len(assistants), 1)

        tool_results = [
            m for m in collected
            if isinstance(m, UserMessage) and isinstance(m.content, list)
            and any(isinstance(b, ToolResultBlock) for b in m.content)
        ]
        self.assertGreaterEqual(len(tool_results), 1)

    def test_multi_turn_replays_reasoning_content_for_followup(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        first = ChatResponse(
            content="I'll handle this",
            model="deepseek-v4-pro",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            reasoning_content="thinking trace from provider",
            tool_uses=[{
                "id": "toolu_001",
                "name": "Write",
                "input": {
                    "file_path": str(self.workspace / "reasoning.txt"),
                    "content": "hello",
                },
            }],
        )
        second = ChatResponse(
            content="Done",
            model="deepseek-v4-pro",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider.chat.side_effect = [first, second]

        params = QueryParams(
            messages=[UserMessage(content="Create file")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )

        async def run():
            async for _msg in query(params):
                pass

        _run(run())

        self.assertEqual(provider.chat.call_count, 2)
        second_call_messages = provider.chat.call_args_list[1].args[0]
        assistant_with_tool_use = next(
            msg for msg in second_call_messages
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), list)
        )
        self.assertEqual(
            assistant_with_tool_use.get("reasoning_content"),
            "thinking trace from provider",
        )

    def test_max_turns_limit(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        tool_use_response = ChatResponse(
            content="Working...",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_001",
                "name": "Write",
                "input": {
                    "file_path": str(self.workspace / "test.txt"),
                    "content": "hello",
                },
            }],
        )

        provider.chat.return_value = tool_use_response

        messages = [UserMessage(content="Create test.txt")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=2,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        max_turns_msgs = [
            m for m in collected
            if isinstance(m, SystemMessage) and getattr(m, "subtype", None) == "max_turns_reached"
        ]
        self.assertEqual(len(max_turns_msgs), 1)


class TestQueryLoopAbort(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_abort_before_response(self):
        abort = AbortController()
        abort.abort("test_abort")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Should not see this",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        messages = [UserMessage(content="Hi")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=abort,
            max_turns=10,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        interruptions = [
            m for m in collected
            if isinstance(m, UserMessage) and m.isMeta
        ]
        self.assertGreaterEqual(len(interruptions), 1)


class TestQueryLoopImageSizeError(unittest.TestCase):
    """ImageSizeError raised by ``BaseProvider._prepare_messages`` must be
    translated by ``_call_model_sync`` into a media_size assistant error
    rather than crashing the query loop. Pins the contract between the
    provider-layer pre-API guard and the higher-level recovery path."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_image_size_error_from_provider_yields_media_size_assistant_message(self):
        from src.utils.image_validation import ImageSizeError

        provider = MagicMock()
        # Both streaming and non-streaming code paths share _prepare_messages,
        # so both surface ImageSizeError. Simulate that here.
        oversize_exc = ImageSizeError([(6 * 1024 * 1024, 5 * 1024 * 1024)])
        provider.chat_stream_response.side_effect = oversize_exc
        provider.chat.side_effect = oversize_exc

        messages = [UserMessage(content="Describe this oversized image")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        assistants = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertEqual(len(assistants), 1)
        err = assistants[0]
        # The assistant message must be flagged as an API-error and carry
        # the ``media_size`` classification so the reactive-compact recovery
        # path can match on it.
        self.assertTrue(getattr(err, "isApiErrorMessage", False))
        self.assertEqual(getattr(err, "_api_error", None), "media_size")
        content = err.content
        text = content if isinstance(content, str) else "".join(
            b.text for b in content if isinstance(b, TextBlock)
        )
        self.assertIn("Media too large", text)


if __name__ == "__main__":
    unittest.main()


class TestQueryLoopImageCountError(unittest.TestCase):
    """A "too MANY images" provider error must enter the media-recovery lane.

    An agent that reads image files in a loop — video frames, a screenshot
    series, a page-by-page scan — accumulates one image block per Read, and
    every block rides along on every later request. Nothing bounds that:
    compaction is the only thing that strips images, and it is triggered by
    the TOKEN budget, so on a million-token model it may never fire before the
    provider's image cap is hit.

    Observed on terminal-bench 2.1 (video-processing, 2026-08-01): 82 image
    Reads, then ``Exceeded maximum number of images (50) allowed in the
    request.`` The classifier did not recognise the wording, so it fell
    through to the generic handler and killed a 22-minute run outright
    instead of compacting once and retrying.

    The recovery machinery already existed and was tested — only the
    classifier's pattern set was missing this phrasing.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_with_error(self, error_text: str):
        provider = MagicMock()
        exc = Exception(error_text)
        provider.chat_stream_response.side_effect = exc
        provider.chat.side_effect = exc
        params = QueryParams(
            messages=[UserMessage(content="describe the frames")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )
        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())
        return collected

    def test_image_count_error_is_classified_as_media_size(self):
        """THE regression guard — the exact string that killed the trial."""
        collected = self._run_with_error(
            "Exceeded maximum number of images (50) allowed in the request."
        )
        assistants = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertTrue(assistants, "expected an assistant error message")
        tagged = [
            m for m in assistants
            if getattr(m, "_api_error", None) == "media_size"
        ]
        self.assertTrue(
            tagged,
            "the image-COUNT error must carry the media_size tag so the "
            "reactive-compact lane (which strips images) can recover; "
            f"got tags {[getattr(m, '_api_error', None) for m in assistants]}",
        )

    def test_image_count_error_does_not_end_as_a_plain_model_error(self):
        """Unclassified, it reached ``Terminal(model_error)`` and the run died.

        Classified, an exhausted recovery lands on ``image_error`` instead —
        which ``EARLY_STOP_SUBTYPES`` maps to a non-success result subtype, so
        the caller can tell a media problem from an arbitrary crash.
        """
        from src.query.query import run_query
        from src.query.transitions import EARLY_STOP_SUBTYPES

        provider = MagicMock()
        exc = Exception("Exceeded maximum number of images (50) allowed in the request.")
        provider.chat_stream_response.side_effect = exc
        provider.chat.side_effect = exc
        params = QueryParams(
            messages=[UserMessage(content="describe the frames")],
            system_prompt="s",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )
        _msgs, terminal = _run(run_query(params))
        self.assertNotEqual(
            terminal.reason, "model_error",
            "a recognised media error must not exit as a generic model_error",
        )
        self.assertEqual(terminal.reason, "image_error")
        self.assertIn(
            "image_error", EARLY_STOP_SUBTYPES,
            "image_error must map to a non-success subtype so the caller can "
            "distinguish it from a clean completion",
        )

    def test_size_errors_still_recognised(self):
        """The pre-existing SIZE patterns must keep working."""
        collected = self._run_with_error("image exceeds the maximum allowed size")
        tagged = [
            m for m in collected
            if isinstance(m, AssistantMessage)
            and getattr(m, "_api_error", None) == "media_size"
        ]
        self.assertTrue(tagged)

    def test_unrelated_errors_are_not_swept_in(self):
        """Widening the pattern set must not capture other failures — a
        prompt-too-long has its own recovery, and a generic server error has
        none, so mislabelling either would route it wrongly."""
        for text, expect_tag in (
            ("prompt is too long: 137500 tokens > 135000 maximum", "prompt_too_long"),
            ("The server had an error processing your request", None),
        ):
            with self.subTest(text=text):
                collected = self._run_with_error(text)
                tags = {
                    getattr(m, "_api_error", None)
                    for m in collected
                    if isinstance(m, AssistantMessage)
                }
                self.assertNotIn("media_size", tags, f"{text!r} misclassified")
                if expect_tag:
                    self.assertIn(expect_tag, tags)
