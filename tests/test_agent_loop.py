"""Test agent loop with mocked provider to verify tool invocation."""

import unittest
from unittest.mock import MagicMock
from pathlib import Path
import tempfile

from src.agent.conversation import Conversation
from src.providers.base import ChatResponse
from src.tool_system.defaults import build_default_registry
from src.tool_system.context import ToolContext
from src.tool_system.agent_loop import run_agent_loop, AgentLoopResult


class TestAgentLoop(unittest.TestCase):
    """Test agent loop logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        """Clean up test fixtures."""
        self.temp_dir.cleanup()

    def test_agent_loop_calls_tool(self):
        """Test agent loop correctly dispatches a tool call from mocked LLM."""
        conversation = Conversation()
        conversation.add_user_message("Create a file hello.py with content print('hello world')")

        # Mock provider
        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()

        # First response: tool use Write
        mock_tool_use = {
            "id": "toolu_123",
            "name": "Write",
            "input": {
                "file_path": str(self.workspace / "hello.py"),
                "content": "print('hello world')"
            }
        }
        mock_response1 = ChatResponse(
            content="I will create the file.",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[mock_tool_use],
        )

        # Second response: final text after tool result
        mock_response2 = ChatResponse(
            content="File created successfully!",
            model="test-model",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="stop",
            tool_uses=None,
        )

        mock_provider.chat.side_effect = [mock_response1, mock_response2]

        result = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.context,
            verbose=False,
        )

        # Verify final response
        self.assertIsInstance(result, AgentLoopResult)
        self.assertEqual(result.response_text, "File created successfully!")

        # Verify provider was called twice
        self.assertEqual(mock_provider.chat.call_count, 2)

        # Verify file was created
        hello_py = self.workspace / "hello.py"
        self.assertTrue(hello_py.exists())
        self.assertEqual(hello_py.read_text(), "print('hello world')")

    def test_agent_loop_creates_hello_world(self):
        """Test agent loop creates hello.py and writes print('hello world')."""
        conversation = Conversation()
        conversation.add_user_message("Create a file hello.py with content print('hello world')")

        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()

        # First response: tool use Write
        hello_path = self.workspace / "hello.py"
        mock_tool_write = {
            "id": "toolu_123",
            "name": "Write",
            "input": {
                "file_path": str(hello_path),
                "content": "print('hello world')"
            }
        }
        mock_response1 = ChatResponse(
            content="I will create the file.",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[mock_tool_write],
        )

        # Second response: final
        mock_response2 = ChatResponse(
            content="File created successfully!",
            model="test-model",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="stop",
            tool_uses=None,
        )

        mock_provider.chat.side_effect = [mock_response1, mock_response2]

        result = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.context,
            verbose=False,
        )

        self.assertIsInstance(result, AgentLoopResult)
        self.assertEqual(result.response_text, "File created successfully!")
        self.assertTrue(hello_path.exists())
        self.assertEqual(hello_path.read_text(), "print('hello world')")

    def test_agent_loop_stream_emits_final_text_chunks(self):
        """Streaming mode emits final response chunks without changing the result."""
        conversation = Conversation()
        conversation.add_user_message("Say hello")

        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()
        mock_provider.chat.return_value = ChatResponse(
            content="Hello from Clawcodex!",
            model="test-model",
            usage={"input_tokens": 3, "output_tokens": 4},
            finish_reason="stop",
            tool_uses=None,
        )

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.context,
            stream=True,
            verbose=False,
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "Hello from Clawcodex!")
        self.assertEqual(result.response_text, "Hello from Clawcodex!")
        self.assertEqual(mock_provider.chat.call_count, 1)
        self.assertEqual(len(conversation.messages), 2)
        self.assertEqual(conversation.messages[-1].role, "assistant")
        last_content = conversation.messages[-1].content
        if isinstance(last_content, list):
            self.assertEqual(last_content[0].text, "Hello from Clawcodex!")
        else:
            self.assertEqual(last_content, "Hello from Clawcodex!")

    def test_agent_loop_stream_only_emits_final_turn_text(self):
        """Streaming mode skips interim tool-planning text and emits the final answer only."""
        conversation = Conversation()
        conversation.add_user_message("Create a file hello.py with content print('hello world')")

        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()
        hello_path = self.workspace / "hello.py"
        mock_response1 = ChatResponse(
            content="I will create the file.",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_123",
                "name": "Write",
                "input": {
                    "file_path": str(hello_path),
                    "content": "print('hello world')",
                },
            }],
        )
        mock_response2 = ChatResponse(
            content="File created successfully!",
            model="test-model",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="stop",
            tool_uses=None,
        )
        mock_provider.chat.side_effect = [mock_response1, mock_response2]

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.context,
            stream=True,
            verbose=False,
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "File created successfully!")
        self.assertEqual(result.response_text, "File created successfully!")
        self.assertTrue(hello_path.exists())

    def test_agent_loop_stream_uses_structured_provider_streaming_for_tool_turns(self):
        """Structured provider streaming can emit pre-tool text and final text across turns."""
        conversation = Conversation()
        conversation.add_user_message("Create hello.py")

        provider = MagicMock()
        hello_path = self.workspace / "hello.py"

        stream_responses = [
            ChatResponse(
                content="I will create the file.",
                model="test-model",
                usage={"input_tokens": 10, "output_tokens": 20},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "toolu_123",
                    "name": "Write",
                    "input": {
                        "file_path": str(hello_path),
                        "content": "print('hello world')",
                    },
                }],
            ),
            ChatResponse(
                content="File created successfully!",
                model="test-model",
                usage={"input_tokens": 30, "output_tokens": 10},
                finish_reason="stop",
                tool_uses=None,
            ),
        ]

        def stream_side_effect(messages, tools=None, on_text_chunk=None, **kwargs):
            response = stream_responses.pop(0)
            if on_text_chunk is not None and response.content:
                on_text_chunk(response.content)
            return response

        provider.chat_stream_response.side_effect = stream_side_effect
        provider.chat.side_effect = AssertionError("chat() should not be used when structured streaming is available")

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            stream=True,
            verbose=False,
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "I will create the file.File created successfully!")
        self.assertEqual(result.response_text, "File created successfully!")
        self.assertEqual(provider.chat_stream_response.call_count, 2)
        self.assertTrue(hello_path.exists())

    def test_agent_loop_stream_falls_back_when_structured_streaming_is_unavailable(self):
        """If the provider lacks structured streaming, the stable synchronous path still works."""
        conversation = Conversation()
        conversation.add_user_message("Say hello")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Hello from fallback!",
            model="test-model",
            usage={"input_tokens": 2, "output_tokens": 3},
            finish_reason="stop",
            tool_uses=None,
        )

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            stream=True,
            verbose=False,
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "Hello from fallback!")
        self.assertEqual(result.response_text, "Hello from fallback!")
        provider.chat.assert_called_once()


class TestFilterIncompleteToolCalls(unittest.TestCase):
    """Test filter_incomplete_tool_calls from run_agent.py."""

    def test_empty_messages(self):
        from src.agent.run_agent import filter_incomplete_tool_calls
        assert filter_incomplete_tool_calls([]) == []

    def test_no_trailing_tool_use(self):
        from src.agent.run_agent import filter_incomplete_tool_calls
        from src.types.messages import AssistantMessage, UserMessage
        from src.types.content_blocks import TextBlock

        msgs = [
            UserMessage(content="hello"),
            AssistantMessage(content=[TextBlock(text="hi there")]),
        ]
        result = filter_incomplete_tool_calls(msgs)
        assert len(result) == 2

    def test_trailing_tool_use_removed(self):
        from src.agent.run_agent import filter_incomplete_tool_calls
        from src.types.messages import AssistantMessage, UserMessage
        from src.types.content_blocks import TextBlock, ToolUseBlock

        msgs = [
            UserMessage(content="hello"),
            AssistantMessage(content=[TextBlock(text="let me search")]),
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={"path": "/tmp/a"}),
            ]),
        ]
        result = filter_incomplete_tool_calls(msgs)
        # Trailing assistant with orphaned tool_use removed
        assert len(result) == 2

    def test_string_content_not_removed(self):
        from src.agent.run_agent import filter_incomplete_tool_calls
        from src.types.messages import AssistantMessage

        msgs = [
            AssistantMessage(content="just text, no tool_use"),
        ]
        result = filter_incomplete_tool_calls(msgs)
        assert len(result) == 1

    def test_matched_tool_use_is_kept(self):
        from src.agent.run_agent import filter_incomplete_tool_calls
        from src.types.messages import AssistantMessage, UserMessage
        from src.types.content_blocks import ToolUseBlock, ToolResultBlock

        msgs = [
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={"path": "/tmp/a"}),
            ]),
            UserMessage(content=[
                ToolResultBlock(tool_use_id="t1", content="ok"),
            ]),
        ]
        result = filter_incomplete_tool_calls(msgs)
        assert len(result) == 2

    def test_unmatched_tool_use_removed_even_if_not_trailing(self):
        from src.agent.run_agent import filter_incomplete_tool_calls
        from src.types.messages import AssistantMessage, UserMessage
        from src.types.content_blocks import ToolUseBlock, TextBlock

        msgs = [
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={"path": "/tmp/a"}),
            ]),
            UserMessage(content="no tool results here"),
            AssistantMessage(content=[TextBlock(text="final summary")]),
        ]
        result = filter_incomplete_tool_calls(msgs)
        assert len(result) == 2
        assert isinstance(result[0], UserMessage)
        assert isinstance(result[1], AssistantMessage)


class TestAgentDefinitions(unittest.TestCase):
    """Test agent definition module."""

    def test_get_built_in_agents(self):
        from src.agent.agent_definitions import get_built_in_agents

        agents = get_built_in_agents()
        assert len(agents) >= 3  # general-purpose, explore, plan
        types = {a.agent_type for a in agents}
        assert "general-purpose" in types
        assert "Explore" in types
        assert "Plan" in types

    def test_find_agent_by_type(self):
        from src.agent.agent_definitions import find_agent_by_type, get_built_in_agents

        agents = get_built_in_agents()
        gp = find_agent_by_type(agents, "general-purpose")
        assert gp is not None
        assert gp.agent_type == "general-purpose"
        assert gp.tools == ["*"]

    def test_find_agent_by_type_not_found(self):
        from src.agent.agent_definitions import find_agent_by_type, get_built_in_agents

        agents = get_built_in_agents()
        result = find_agent_by_type(agents, "nonexistent-agent")
        assert result is None

    def test_is_built_in_agent(self):
        from src.agent.agent_definitions import (
            is_built_in_agent,
            GENERAL_PURPOSE_AGENT,
            AgentDefinition,
        )

        assert is_built_in_agent(GENERAL_PURPOSE_AGENT) is True
        custom = AgentDefinition(
            agent_type="custom", when_to_use="custom", source="user"
        )
        assert is_built_in_agent(custom) is False


class TestAgentPrompt(unittest.TestCase):
    """Test agent prompt generation."""

    def test_get_agent_prompt(self):
        from src.agent.prompt import get_agent_prompt
        from src.agent.agent_definitions import get_built_in_agents

        agents = get_built_in_agents()
        prompt = get_agent_prompt(agents)

        assert "Available agent types" in prompt
        assert "general-purpose" in prompt
        assert "When NOT to use" in prompt
        assert "Writing the prompt" in prompt

    def test_format_agent_line(self):
        from src.agent.prompt import format_agent_line
        from src.agent.agent_definitions import EXPLORE_AGENT

        line = format_agent_line(EXPLORE_AGENT)
        assert "Explore" in line
        assert "Tools:" in line

    def test_get_agent_system_prompt_built_in(self):
        from src.agent.prompt import get_agent_system_prompt
        from src.agent.agent_definitions import GENERAL_PURPOSE_AGENT

        prompt = get_agent_system_prompt(GENERAL_PURPOSE_AGENT)
        assert "agent" in prompt.lower()
        assert len(prompt) > 50

    def test_get_agent_system_prompt_fork_inherits_parent(self):
        from src.agent.prompt import get_agent_system_prompt
        from src.agent.agent_definitions import FORK_AGENT

        parent_prompt = "You are the parent system prompt."
        prompt = get_agent_system_prompt(FORK_AGENT, parent_system_prompt=parent_prompt)
        assert prompt == parent_prompt


if __name__ == "__main__":
    unittest.main()
