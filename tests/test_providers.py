"""Tests for LLM providers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.providers import get_provider_class
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.glm_provider import GLMProvider  # legacy back-compat alias
from src.providers.zai_provider import ZaiProvider
from src.providers.openai_compatible import _convert_anthropic_messages_to_openai
from src.providers.openai_provider import OpenAIProvider
from src.providers.base import ChatMessage, ChatResponse


class TestChatMessage(unittest.TestCase):
    """Test ChatMessage dataclass."""

    def test_create_message(self):
        """Test creating a chat message."""
        msg = ChatMessage(role="user", content="Hello")
        self.assertEqual(msg.role, "user")
        self.assertEqual(msg.content, "Hello")

    def test_to_dict(self):
        """Test converting message to dict."""
        msg = ChatMessage(role="user", content="Hello")
        result = msg.to_dict()
        self.assertEqual(result, {"role": "user", "content": "Hello"})


class TestChatResponse(unittest.TestCase):
    """Test ChatResponse dataclass."""

    def test_create_response(self):
        """Test creating a chat response."""
        response = ChatResponse(
            content="Hello!",
            model="gpt-4",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="stop",
        )
        self.assertEqual(response.content, "Hello!")
        self.assertEqual(response.model, "gpt-4")
        self.assertIsNone(response.reasoning_content)

    def test_response_with_reasoning(self):
        """Test response with reasoning content."""
        response = ChatResponse(
            content="Answer",
            model="GLM-5.1",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="stop",
            reasoning_content="Reasoning process...",
        )
        self.assertEqual(response.reasoning_content, "Reasoning process...")


class TestAnthropicProvider(unittest.TestCase):
    """Test Anthropic provider."""

    def test_initialization(self):
        """Test provider initialization."""
        provider = AnthropicProvider(api_key="test_key")
        self.assertEqual(provider.model, "claude-sonnet-4-6")
        self.assertEqual(provider.api_key, "test_key")

    def test_custom_model(self):
        """Test provider with custom model."""
        provider = AnthropicProvider(api_key="test_key", model="claude-3-opus-20240229")
        self.assertEqual(provider.model, "claude-3-opus-20240229")

    def test_get_available_models(self):
        """Test getting available models."""
        provider = AnthropicProvider(api_key="test_key")
        models = provider.get_available_models()
        self.assertIn("claude-sonnet-4-20250514", models)
        self.assertIn("claude-3-5-sonnet-20241022", models)

    @patch("src.providers.anthropic_provider.anthropic.Anthropic")
    def test_chat(self, mock_anthropic):
        """Test synchronous chat."""
        # Setup mock
        mock_client = MagicMock()
        mock_response = MagicMock()
        # Mock text block with type and text attributes
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Hello!"
        mock_response.content = [mock_text_block]
        mock_response.model = "claude-sonnet-4-20250514"
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_response.stop_reason = "end_turn"
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        # Test
        provider = AnthropicProvider(api_key="test_key")
        messages = [ChatMessage(role="user", content="Hi")]
        response = provider.chat(messages)

        self.assertEqual(response.content, "Hello!")
        self.assertEqual(response.model, "claude-sonnet-4-20250514")
        self.assertEqual(response.finish_reason, "end_turn")

    @patch("src.providers.anthropic_provider.anthropic.Anthropic")
    def test_chat_accepts_dict_messages(self, mock_anthropic):
        """Test synchronous chat with dict messages."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        # Mock text block with type and text attributes
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Hello!"
        mock_response.content = [mock_text_block]
        mock_response.model = "claude-sonnet-4-20250514"
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_response.stop_reason = "end_turn"
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        provider = AnthropicProvider(api_key="test_key")
        messages = [{"role": "user", "content": "Hi"}]
        response = provider.chat(messages)

        self.assertEqual(response.content, "Hello!")
        mock_client.messages.create.assert_called_once()
        self.assertEqual(
            mock_client.messages.create.call_args.kwargs["messages"], messages
        )

    @patch("src.providers.anthropic_provider.anthropic.Anthropic")
    def test_chat_stream_response_with_tool_use(self, mock_anthropic):
        """Structured streaming returns final text and tool uses."""
        mock_client = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__enter__.return_value = mock_stream
        mock_stream.__exit__.return_value = False
        mock_stream.text_stream = iter(["Hello", " world"])

        final_response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello world"
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "toolu_1"
        tool_block.name = "Read"
        tool_block.input = {"file_path": "README.md"}
        final_response.content = [text_block, tool_block]
        final_response.model = "claude-sonnet-4-20250514"
        final_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        final_response.stop_reason = "tool_use"
        mock_stream.get_final_message.return_value = final_response

        mock_client.messages.stream.return_value = mock_stream
        mock_anthropic.return_value = mock_client

        provider = AnthropicProvider(api_key="test_key")
        chunks: list[str] = []
        response = provider.chat_stream_response(
            [ChatMessage(role="user", content="Hi")],
            tools=[{"name": "Read", "description": "", "input_schema": {"type": "object"}}],
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "Hello world")
        self.assertEqual(response.content, "Hello world")
        self.assertEqual(response.finish_reason, "tool_use")
        self.assertEqual(response.tool_uses[0]["name"], "Read")


class TestAnthropicUsageForwarding(unittest.TestCase):
    """WI-0.2 (ch17 Phase 0) — provider forwards prompt-cache credits + breakdown.

    Without this, downstream consumers (``src/tasks/progress.py``,
    ``src/agent/agent_tool_utils.py``, ``src/context_system/context_analyzer.py``)
    that read ``usage["cache_creation_input_tokens"]`` / etc. always
    observe 0 even when server-side prompt caching is engaged. The
    chapter line 61 anchor — "Token counting is anchored on the API's
    actual usage field ... accounting for prompt caching credits".
    """

    @staticmethod
    def _make_response(usage):
        response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "ok"
        response.content = [text_block]
        response.model = "claude-sonnet-4-20250514"
        response.usage = usage
        response.stop_reason = "end_turn"
        return response

    def test_cache_creation_input_tokens_forwarded(self):
        provider = AnthropicProvider(api_key="test")
        usage = MagicMock(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=1234, cache_read_input_tokens=0,
            cache_creation=None,
        )
        cr = provider._build_chat_response(self._make_response(usage))
        self.assertEqual(cr.usage["cache_creation_input_tokens"], 1234)

    def test_cache_read_input_tokens_forwarded(self):
        provider = AnthropicProvider(api_key="test")
        usage = MagicMock(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=9876,
            cache_creation=None,
        )
        cr = provider._build_chat_response(self._make_response(usage))
        self.assertEqual(cr.usage["cache_read_input_tokens"], 9876)

    def test_missing_cache_fields_default_to_zero(self):
        """Older SDK responses without cache fields → graceful 0."""
        class OldUsage:
            def __init__(self):
                self.input_tokens = 10
                self.output_tokens = 5
        provider = AnthropicProvider(api_key="test")
        cr = provider._build_chat_response(self._make_response(OldUsage()))
        self.assertEqual(cr.usage["cache_creation_input_tokens"], 0)
        self.assertEqual(cr.usage["cache_read_input_tokens"], 0)
        self.assertNotIn("cache_creation", cr.usage)

    def test_none_value_fields_default_to_zero(self):
        """SDK ``int | None`` → forward 0 instead of None."""
        provider = AnthropicProvider(api_key="test")
        usage = MagicMock(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=None, cache_read_input_tokens=None,
            cache_creation=None,
        )
        cr = provider._build_chat_response(self._make_response(usage))
        self.assertEqual(cr.usage["cache_creation_input_tokens"], 0)
        self.assertEqual(cr.usage["cache_read_input_tokens"], 0)

    def test_cache_creation_breakdown_forwarded(self):
        """``cache_creation`` sub-object's 5m/1h breakdown → nested dict."""
        provider = AnthropicProvider(api_key="test")
        cache_creation = MagicMock(
            ephemeral_5m_input_tokens=100, ephemeral_1h_input_tokens=200,
        )
        usage = MagicMock(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=300, cache_read_input_tokens=0,
            cache_creation=cache_creation,
        )
        cr = provider._build_chat_response(self._make_response(usage))
        self.assertEqual(
            cr.usage["cache_creation"],
            {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 200},
        )

    def test_existing_input_output_tokens_regression(self):
        provider = AnthropicProvider(api_key="test")
        usage = MagicMock(
            input_tokens=42, output_tokens=17,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
            cache_creation=None,
        )
        cr = provider._build_chat_response(self._make_response(usage))
        self.assertEqual(cr.usage["input_tokens"], 42)
        self.assertEqual(cr.usage["output_tokens"], 17)

    def test_missing_usage_object_safe_default(self):
        provider = AnthropicProvider(api_key="test")
        response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "ok"
        response.content = [text_block]
        response.model = "claude-sonnet-4-20250514"
        response.usage = None
        response.stop_reason = "end_turn"
        cr = provider._build_chat_response(response)
        self.assertEqual(cr.usage["input_tokens"], 0)
        self.assertEqual(cr.usage["output_tokens"], 0)
        self.assertEqual(cr.usage["cache_creation_input_tokens"], 0)
        self.assertEqual(cr.usage["cache_read_input_tokens"], 0)


class TestOpenAIProvider(unittest.TestCase):
    """Test OpenAI provider."""

    def test_initialization(self):
        """Test provider initialization."""
        provider = OpenAIProvider(api_key="test_key")
        self.assertEqual(provider.model, "gpt-5.4")

    def test_custom_model(self):
        """Test provider with custom model."""
        provider = OpenAIProvider(api_key="test_key", model="gpt-4-turbo")
        self.assertEqual(provider.model, "gpt-4-turbo")

    def test_get_available_models(self):
        """Test getting available models."""
        provider = OpenAIProvider(api_key="test_key")
        models = provider.get_available_models()
        self.assertIn("gpt-4", models)
        self.assertIn("gpt-4o", models)

    def test_converter_preserves_reasoning_with_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "reasoning_content": "keep-me",
                "content": [
                    {"type": "text", "text": "Thinking..."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "TaskCreate",
                        "input": {"title": "todo"},
                    },
                ],
            }
        ]
        converted = _convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(converted), 1)
        self.assertEqual(converted[0]["role"], "assistant")
        self.assertEqual(converted[0]["reasoning_content"], "keep-me")
        self.assertTrue(converted[0]["tool_calls"])

    @patch("src.providers.openai_provider.OpenAI")
    def test_chat(self, mock_openai):
        """Test synchronous chat."""
        # Setup mock
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.model = "gpt-4"
        mock_response.usage = MagicMock(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        )
        mock_response.choices[0].finish_reason = "stop"
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        # Test
        provider = OpenAIProvider(api_key="test_key")
        messages = [ChatMessage(role="user", content="Hi")]
        response = provider.chat(messages)

        self.assertEqual(response.content, "Hello!")
        self.assertEqual(response.model, "gpt-4")
        self.assertEqual(response.usage["total_tokens"], 15)

    @patch("src.providers.openai_provider.OpenAI")
    def test_chat_accepts_dict_messages(self, mock_openai):
        """Test synchronous chat with dict messages."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.model = "gpt-4"
        mock_response.usage = MagicMock(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        )
        mock_response.choices[0].finish_reason = "stop"
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        provider = OpenAIProvider(api_key="test_key")
        messages = [{"role": "user", "content": "Hi"}]
        response = provider.chat(messages)

        self.assertEqual(response.content, "Hello!")
        mock_client.chat.completions.create.assert_called_once()
        self.assertEqual(
            mock_client.chat.completions.create.call_args.kwargs["messages"], messages
        )

    @patch("src.providers.openai_provider.OpenAI")
    def test_chat_stream_response_rebuilds_tool_calls(self, mock_openai):
        """Streaming chunks are rebuilt into a final response with tool calls."""
        mock_client = MagicMock()

        chunk1 = MagicMock()
        chunk1.model = "gpt-4"
        chunk1.usage = None
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].finish_reason = None
        chunk1.choices[0].delta.content = "Hello"
        chunk1.choices[0].delta.reasoning_content = None
        chunk1.choices[0].delta.tool_calls = []

        tool_call_delta = MagicMock()
        tool_call_delta.index = 0
        tool_call_delta.id = "call_1"
        tool_call_delta.function = MagicMock(name="function")
        tool_call_delta.function.name = "Read"
        tool_call_delta.function.arguments = '{"file_path":"README.md"}'

        chunk2 = MagicMock()
        chunk2.model = "gpt-4"
        chunk2.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].finish_reason = "tool_calls"
        chunk2.choices[0].delta.content = None
        chunk2.choices[0].delta.reasoning_content = None
        chunk2.choices[0].delta.tool_calls = [tool_call_delta]

        mock_client.chat.completions.create.return_value = iter([chunk1, chunk2])
        mock_openai.return_value = mock_client

        provider = OpenAIProvider(api_key="test_key")
        chunks: list[str] = []
        response = provider.chat_stream_response(
            [ChatMessage(role="user", content="Hi")],
            tools=[{"name": "Read", "description": "", "input_schema": {"type": "object"}}],
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "Hello")
        self.assertEqual(response.content, "Hello")
        self.assertEqual(response.finish_reason, "tool_calls")
        self.assertEqual(response.tool_uses[0]["name"], "Read")
        self.assertEqual(response.usage["total_tokens"], 15)


class TestZaiProvider(unittest.TestCase):
    """Test Z.ai (GLM) provider.

    Z.ai's GLM Coding Plan is OpenAI-compatible, so the provider uses the
    OpenAI SDK pointed at the Z.ai base URL (mirrors DeepSeek/OpenAI).
    """

    def test_initialization(self):
        """Default model is the stable GLM-5.1, default base URL is the GLM Coding Plan."""
        provider = ZaiProvider(api_key="test_key")
        self.assertEqual(provider.model, "GLM-5.1")
        self.assertEqual(provider.base_url, "https://api.z.ai/api/coding/paas/v4")

    def test_custom_model(self):
        """Test provider with custom model (e.g. the GLM-5.2 preview)."""
        provider = ZaiProvider(api_key="test_key", model="GLM-5.2")
        self.assertEqual(provider.model, "GLM-5.2")

    def test_custom_base_url(self):
        """A configured base URL (e.g. the general API) overrides the default."""
        provider = ZaiProvider(
            api_key="test_key", base_url="https://api.z.ai/api/paas/v4"
        )
        self.assertEqual(provider.base_url, "https://api.z.ai/api/paas/v4")

    def test_get_available_models(self):
        """Test getting available models."""
        provider = ZaiProvider(api_key="test_key")
        models = provider.get_available_models()
        self.assertIn("GLM-5.1", models)
        self.assertIn("GLM-5.2", models)

    def test_legacy_glm_alias(self):
        """``GLMProvider`` is preserved as a back-compat alias of ``ZaiProvider``."""
        self.assertIs(GLMProvider, ZaiProvider)

    def test_model_alias_canonicalization(self):
        """Lowercase/aliased GLM ids are normalized to canonical ids on send.

        The Z.ai endpoint expects ``GLM-5.x``; a config value like ``glm-5.2``
        (the shape Z.ai users commonly write) must reach the API canonicalized.
        ``provider.model`` keeps the user's spelling for display.
        """
        provider = ZaiProvider(api_key="test_key", model="glm-5.2")
        self.assertEqual(provider.model, "glm-5.2")
        self.assertEqual(provider._get_model(), "GLM-5.2")
        # OpenRouter-style alias and the [1m] context suffix both resolve.
        self.assertEqual(
            ZaiProvider(api_key="k", model="zai-glm-5-1")._get_model(), "GLM-5.1"
        )
        self.assertEqual(
            ZaiProvider(api_key="k", model="glm-5.2[1m]")._get_model(), "GLM-5.2"
        )
        # Unknown / custom ids pass through untouched.
        self.assertEqual(
            ZaiProvider(api_key="k", model="custom-model")._get_model(), "custom-model"
        )

    @patch("src.providers.zai_provider.OpenAI")
    def test_chat(self, mock_openai):
        """Test synchronous chat."""
        # Setup mock
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.choices[0].message.reasoning_content = None
        mock_response.model = "GLM-5.1"
        mock_response.usage = MagicMock(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        )
        mock_response.choices[0].finish_reason = "stop"
        mock_client.chat.completions.create.return_value = mock_response
        # ``OpenAICompatibleProvider.client`` wraps the SDK client with
        # ``with_options(...)`` (bounded read timeout); return the same mock so
        # the configured ``create`` stub is the one actually exercised.
        mock_client.with_options.return_value = mock_client
        mock_openai.return_value = mock_client

        # Test
        provider = ZaiProvider(api_key="test_key")
        messages = [ChatMessage(role="user", content="Hi")]
        response = provider.chat(messages)

        self.assertEqual(response.content, "Hello!")
        self.assertEqual(response.model, "GLM-5.1")
        self.assertIsNone(response.reasoning_content)

    @patch("src.providers.zai_provider.OpenAI")
    def test_chat_with_reasoning(self, mock_openai):
        """GLM reasoning streams through ``reasoning_content`` and is surfaced."""
        # Setup mock
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Answer"
        mock_response.choices[0].message.reasoning_content = "Thinking..."
        mock_response.model = "GLM-5.1"
        mock_response.usage = MagicMock(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        )
        mock_response.choices[0].finish_reason = "stop"
        mock_client.chat.completions.create.return_value = mock_response
        mock_client.with_options.return_value = mock_client
        mock_openai.return_value = mock_client

        # Test
        provider = ZaiProvider(api_key="test_key")
        messages = [ChatMessage(role="user", content="Complex question")]
        response = provider.chat(messages)

        self.assertEqual(response.content, "Answer")
        self.assertEqual(response.reasoning_content, "Thinking...")


class TestGetProviderClass(unittest.TestCase):
    """Test get_provider_class function."""

    def test_get_anthropic_provider(self):
        """Test getting Anthropic provider class."""
        cls = get_provider_class("anthropic")
        self.assertEqual(cls, AnthropicProvider)

    def test_get_openai_provider(self):
        """Test getting OpenAI provider class."""
        cls = get_provider_class("openai")
        self.assertEqual(cls, OpenAIProvider)

    def test_get_zai_provider(self):
        """Test getting the Z.ai provider class by canonical id."""
        cls = get_provider_class("zai")
        self.assertEqual(cls, ZaiProvider)

    def test_get_zai_provider_via_legacy_glm_alias(self):
        """The pre-rename ``glm`` id still resolves to the Z.ai provider."""
        self.assertEqual(get_provider_class("glm"), ZaiProvider)

    def test_get_zai_provider_via_dotted_alias(self):
        """CodeWhale's ``z.ai`` / ``z-ai`` spellings resolve to the Z.ai provider."""
        self.assertEqual(get_provider_class("z.ai"), ZaiProvider)
        self.assertEqual(get_provider_class("z-ai"), ZaiProvider)

    def test_get_unknown_provider(self):
        """Test getting unknown provider."""
        with self.assertRaises(ValueError) as context:
            get_provider_class("unknown")

        self.assertIn("Unknown provider", str(context.exception))


if __name__ == "__main__":
    unittest.main()
