from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
from unittest.mock import MagicMock

from src.agent.conversation import Conversation
from src.providers.base import ChatResponse
from src.tool_system.agent_loop import run_agent_loop
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
        conversation = Conversation()
        conversation.add_user_message("hi")

        mock_provider = MagicMock()
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


if __name__ == "__main__":
    unittest.main()

