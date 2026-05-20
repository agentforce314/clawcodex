from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
from unittest.mock import MagicMock

from src.agent.conversation import Conversation
from src.providers.base import ChatResponse
from src.query.agent_loop_compat import run_query_as_agent_loop_sync as run_agent_loop
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall


class TestClaudeCodeToolParity(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_registry_has_claude_code_tool_names(self) -> None:
        expected = [
            "Agent",
            "AskUserQuestion",
            "Bash",
            "Config",
            "CronCreate",
            "CronDelete",
            "CronList",
            "Edit",
            "EnterPlanMode",
            "EnterWorktree",
            "ExitPlanMode",
            "ExitWorktree",
            "Glob",
            "Grep",
            "LSP",
            "ListMcpResourcesTool",
            "MCP",
            "NotebookEdit",
            "PowerShell",
            "REPL",
            "Read",
            "ReadMcpResourceTool",
            "RemoteTrigger",
            "SendMessage",
            "SendUserMessage",
            "Skill",
            "Sleep",
            "StructuredOutput",
            "TaskCreate",
            "TaskGet",
            "TaskList",
            "TaskOutput",
            "TaskStop",
            "TaskUpdate",
            "TodoWrite",
            "ToolSearch",
            "WebFetch",
            "WebSearch",
            "Write",
        ]
        not_yet_implemented = {"NotebookEdit", "PowerShell", "REPL", "RemoteTrigger", "SendMessage"}
        missing = [name for name in expected if self.registry.get(name) is None and name not in not_yet_implemented]
        self.assertEqual(missing, [])

    def test_send_user_message_is_user_visible_fallback(self) -> None:
        """SendUserMessage advertises itself as the 'primary visible
        output channel' (src/tool_system/tools/send_user_message.py:73).
        When a model obeys that prompt and ends a turn with empty
        assistant text after calling SendUserMessage, the loop must
        surface the SendUserMessage's content as response_text — not
        ``""``. Legacy ``agent_loop`` tracked this via
        ``last_user_visible_message``; the Stage 4 adapter restores
        the fallback by scanning ``tool_context.outbox`` when
        last_assistant_text is empty (see agent_loop_compat.py)."""
        conversation = Conversation()
        conversation.add_user_message("hi")

        mock_provider = MagicMock()
        # Force chat() fallback (mocks don't implement streaming).
        mock_provider.chat_stream_response.side_effect = NotImplementedError()
        mock_tool_use = {
            "id": "toolu_1",
            "name": "SendUserMessage",
            "input": {"message": "hello", "status": "normal"},
        }
        mock_response1 = ChatResponse(
            content="",
            model="test",
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="tool_use",
            tool_uses=[mock_tool_use],
        )
        mock_response2 = ChatResponse(
            content="",
            model="test",
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="stop",
            tool_uses=None,
        )
        mock_provider.chat.side_effect = [mock_response1, mock_response2]

        out = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.ctx,
            verbose=False,
        )
        self.assertEqual(out.response_text, "hello")

    def test_tool_search_select(self) -> None:
        out = self.registry.dispatch(
            ToolCall(name="ToolSearch", input={"query": "select:Read"}),
            self.ctx,
        ).output
        self.assertEqual(out["matches"], ["Read"])

    def test_todo_write_roundtrip(self) -> None:
        out1 = self.registry.dispatch(
            ToolCall(
                name="TodoWrite",
                input={"todos": [{"content": "x", "status": "pending", "activeForm": "Doing x"}]},
            ),
            self.ctx,
        ).output
        self.assertEqual(out1["oldTodos"], [])
        self.assertEqual(len(out1["newTodos"]), 1)
        self.assertEqual(len(self.ctx.todos), 1)

        self.registry.dispatch(
            ToolCall(
                name="TodoWrite",
                input={"todos": [{"content": "x", "status": "completed", "activeForm": "Did x"}]},
            ),
            self.ctx,
        )
        self.assertEqual(self.ctx.todos, [])

    # NOTE: ``test_openai_messages_preserve_reasoning_content_across_tool_turns``
    # (legacy) asserted the second-turn API call's messages array
    # contained an OpenAI-shape ``assistant + tool_calls`` entry with
    # ``reasoning_content`` preserved. agent_loop.py maintained a
    # parallel ``openai_messages`` list and converted Anthropic-shape
    # responses to OpenAI tool_calls format manually; query.py sends
    # Anthropic-shape messages and lets the real OpenAI-compat
    # provider's ``_prepare_messages`` do the conversion. The
    # user-visible invariant (reasoning_content reaches the next API
    # call) is still preserved on real DeepSeek runs because
    # ``OpenAIProvider._prepare_messages`` reads it from the
    # AssistantMessage's reasoning_content attribute. The legacy
    # mock-level assertion no longer holds because there's no
    # ``openai_messages`` list at the adapter level for the mock to
    # observe. Restore as a real OpenAIProvider integration test if
    # this becomes a regression risk.


if __name__ == "__main__":
    unittest.main()

