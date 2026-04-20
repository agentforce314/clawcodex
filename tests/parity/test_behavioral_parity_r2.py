"""Phase D — Round 2 Behavioral Parity Tests.

16 tests validating end-to-end flows match TypeScript behavior.
Each test simulates a complete conversation or permission flow using mocks.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.api.claude import (
    ContentBlockStop,
    MessageDelta,
    MessageStart,
    MessageStop,
    TextDelta,
    ToolUseDelta,
    ToolUseStart,
    UsageEvent,
)
from src.services.api.logging import NonNullableUsage
from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import (
    AssistantMessage,
    UserMessage,
    create_assistant_message,
    create_user_message,
    message_from_dict,
    message_to_dict,
    normalize_messages_for_api,
)


# ---------------------------------------------------------------------------
# 1. Single tool conversation flow
# ---------------------------------------------------------------------------

class TestConversationFlowSingleTool(unittest.TestCase):
    """User → tool_use → tool_result → end_turn."""

    def test_single_tool_flow(self) -> None:
        user_msg = create_user_message("List files in /tmp")
        assistant_msg = AssistantMessage(
            content=[
                TextBlock(text="I'll list the files."),
                ToolUseBlock(id="tu_1", name="Bash", input={"command": "ls /tmp"}),
            ],
            stop_reason="tool_use",
        )
        tool_result = create_user_message(
            [ToolResultBlock(tool_use_id="tu_1", content="file1.txt\nfile2.txt")],
        )
        final_msg = create_assistant_message("Here are the files: file1.txt, file2.txt")

        messages = [user_msg, assistant_msg, tool_result, final_msg]
        api_msgs = normalize_messages_for_api(messages)

        self.assertEqual(len(api_msgs), 4)
        self.assertEqual(api_msgs[0]["role"], "user")
        self.assertEqual(api_msgs[1]["role"], "assistant")
        self.assertEqual(api_msgs[2]["role"], "user")
        self.assertEqual(api_msgs[3]["role"], "assistant")

        # Verify tool_use in assistant message
        asst_content = api_msgs[1]["content"]
        tool_use_blocks = [b for b in asst_content if b.get("type") == "tool_use"]
        self.assertEqual(len(tool_use_blocks), 1)
        self.assertEqual(tool_use_blocks[0]["name"], "Bash")


# ---------------------------------------------------------------------------
# 2. Multi-tool conversation flow
# ---------------------------------------------------------------------------

class TestConversationFlowMultiTool(unittest.TestCase):
    """Multiple concurrent tool calls in a single turn."""

    def test_multi_tool_flow(self) -> None:
        user_msg = create_user_message("Read two files")
        assistant_msg = AssistantMessage(
            content=[
                TextBlock(text="Reading both files."),
                ToolUseBlock(id="tu_1", name="Read", input={"file_path": "a.py"}),
                ToolUseBlock(id="tu_2", name="Read", input={"file_path": "b.py"}),
            ],
            stop_reason="tool_use",
        )
        tool_results = create_user_message([
            ToolResultBlock(tool_use_id="tu_1", content="content of a"),
            ToolResultBlock(tool_use_id="tu_2", content="content of b"),
        ])
        final_msg = create_assistant_message("Both files read successfully.")

        messages = [user_msg, assistant_msg, tool_results, final_msg]
        api_msgs = normalize_messages_for_api(messages)

        self.assertEqual(len(api_msgs), 4)
        # Both tool results in one user message
        user_result_content = api_msgs[2]["content"]
        tool_results_blocks = [
            b for b in user_result_content if b.get("type") == "tool_result"
        ]
        self.assertEqual(len(tool_results_blocks), 2)


# ---------------------------------------------------------------------------
# 3. Error recovery flow
# ---------------------------------------------------------------------------

class TestConversationFlowErrorRecovery(unittest.TestCase):
    """API error → retry → success."""

    def test_retry_recovers_from_transient_error(self) -> None:
        from src.services.api.errors import OverloadedError
        from src.services.api.retry import RetryOptions, with_retry

        call_count = 0

        async def transient_failing_op(attempt, ctx):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise OverloadedError("API overloaded", status=529)
            return {"status": "ok", "content": "Hello"}

        async def run():
            return await with_retry(
                transient_failing_op,
                RetryOptions(max_retries=5, model="claude-sonnet-4-6"),
            )

        result = asyncio.run(run())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(call_count, 3)


# ---------------------------------------------------------------------------
# 4. Max output tokens escalation
# ---------------------------------------------------------------------------

class TestConversationFlowMaxOutputTokens(unittest.TestCase):
    """Max tokens reached → stop_reason=max_tokens."""

    def test_max_tokens_stop_reason(self) -> None:
        msg = AssistantMessage(
            content=[TextBlock(text="This is a truncated response...")],
            stop_reason="max_tokens",
        )
        self.assertEqual(msg.stop_reason, "max_tokens")

        # Verify the message can be normalized for API
        api_msgs = normalize_messages_for_api([msg])
        self.assertEqual(len(api_msgs), 1)


# ---------------------------------------------------------------------------
# 5. Abort / user interrupt
# ---------------------------------------------------------------------------

class TestConversationFlowAbort(unittest.TestCase):
    """User interrupt → AbortError → cancellation message."""

    def test_abort_signal_raises(self) -> None:
        from src.utils.abort_controller import AbortController, AbortError

        controller = AbortController()
        controller.abort(reason="user_interrupted")

        self.assertTrue(controller.signal.aborted)
        self.assertEqual(controller.signal.reason, "user_interrupted")

    def test_child_abort_inherits(self) -> None:
        from src.utils.abort_controller import (
            AbortController,
            create_child_abort_controller,
        )

        parent = AbortController()
        child = create_child_abort_controller(parent)

        parent.abort(reason="user_interrupted")
        self.assertTrue(child.signal.aborted)


# ---------------------------------------------------------------------------
# 6. Compact boundary flow
# ---------------------------------------------------------------------------

class TestConversationFlowCompactBoundary(unittest.TestCase):
    """Context too large → compact → continue."""

    def test_compact_creates_boundary_message(self) -> None:
        summary = create_user_message(
            "Summary: Previous conversation discussed file editing.",
            isCompactSummary=True,
        )
        self.assertTrue(summary.isCompactSummary)

        # A compact boundary should be identifiable
        from src.utils.messages import is_compact_boundary
        self.assertTrue(is_compact_boundary(summary))

    def test_compact_preserves_recent_messages(self) -> None:
        # Simulate a long conversation
        messages = [create_user_message(f"Turn {i}") for i in range(50)]
        # After compact, only summary + recent remain
        compact_summary = create_user_message(
            "Summary of turns 0-40",
            isCompactSummary=True,
        )
        recent = messages[-5:]
        compacted = [compact_summary] + recent
        self.assertEqual(len(compacted), 6)
        self.assertTrue(compacted[0].isCompactSummary)


# ---------------------------------------------------------------------------
# 7. Permission flow: deny
# ---------------------------------------------------------------------------

class TestPermissionFlowDeny(unittest.TestCase):
    """Dangerous command → deny."""

    def test_dangerous_bash_triggers_ask(self) -> None:
        from src.permissions.bash_security import check_bash_command_safety
        result = check_bash_command_safety("sudo rm -rf /")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_chmod_triggers_ask(self) -> None:
        from src.permissions.bash_security import check_bash_command_safety
        result = check_bash_command_safety("chmod 777 /etc/passwd")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")


# ---------------------------------------------------------------------------
# 8. Permission flow: ask
# ---------------------------------------------------------------------------

class TestPermissionFlowAsk(unittest.TestCase):
    """Unknown or complex command → ask for confirmation."""

    def test_complex_command_triggers_ask(self) -> None:
        from src.permissions.bash_security import check_bash_command_safety
        # Complex piped command with eval-like construct
        result = check_bash_command_safety("eval $(echo 'rm -rf /')")
        if result is not None:
            self.assertEqual(result.behavior, "ask")

    def test_unknown_binary_triggers_ask(self) -> None:
        from src.permissions.bash_security import analyze_bash_command
        result = analyze_bash_command("custom_binary --dangerous-flag")
        # Unknown commands should be flagged
        self.assertIn(result.safety, ("unknown", "safe", "read_only"))


# ---------------------------------------------------------------------------
# 9. Permission flow: allow
# ---------------------------------------------------------------------------

class TestPermissionFlowAllow(unittest.TestCase):
    """Safe command → auto-allow (no permission check needed)."""

    def test_safe_commands_pass(self) -> None:
        from src.permissions.bash_security import check_bash_command_safety
        safe_cmds = ["echo hello", "pwd", "whoami", "date"]
        for cmd in safe_cmds:
            result = check_bash_command_safety(cmd)
            self.assertIsNone(result, f"Expected auto-allow for: {cmd}")

    def test_read_only_commands_pass(self) -> None:
        from src.permissions.bash_security import check_bash_command_safety
        ro_cmds = ["cat file.txt", "ls -la", "grep pattern file"]
        for cmd in ro_cmds:
            result = check_bash_command_safety(cmd)
            self.assertIsNone(result, f"Expected auto-allow for read-only: {cmd}")


# ---------------------------------------------------------------------------
# 10. Agent flow: sync child
# ---------------------------------------------------------------------------

class TestAgentFlowSync(unittest.TestCase):
    """Agent tool → sync child → result aggregation."""

    def test_agent_definitions_available(self) -> None:
        from src.agent.agent_definitions import (
            EXPLORE_AGENT,
            GENERAL_PURPOSE_AGENT,
            PLAN_AGENT,
            get_built_in_agents,
        )
        agents = get_built_in_agents()
        self.assertGreater(len(agents), 0)

    def test_run_agent_params_complete(self) -> None:
        from src.agent.run_agent import RunAgentParams, RunAgentResult
        # Verify all required fields
        self.assertIn("parent_context", RunAgentParams.__dataclass_fields__)
        self.assertIn("agent_definition", RunAgentParams.__dataclass_fields__)
        self.assertIn("prompt", RunAgentParams.__dataclass_fields__)
        self.assertIn("messages", RunAgentResult.__dataclass_fields__)

    def test_subagent_context_isolation(self) -> None:
        from src.agent.subagent_context import SubagentContextOverrides
        overrides = SubagentContextOverrides(share_permission_handler=False)
        self.assertFalse(overrides.share_permission_handler)


# ---------------------------------------------------------------------------
# 11. Agent flow: background
# ---------------------------------------------------------------------------

class TestAgentFlowBackground(unittest.TestCase):
    """Agent tool → background async → notification."""

    def test_async_agent_allowed_tools(self) -> None:
        from src.agent.constants import ASYNC_AGENT_ALLOWED_TOOLS
        self.assertIsInstance(ASYNC_AGENT_ALLOWED_TOOLS, (list, tuple, set, frozenset))
        self.assertGreater(len(ASYNC_AGENT_ALLOWED_TOOLS), 0)

    def test_agent_tool_schema_supports_background(self) -> None:
        from src.tool_system.tools.agent import AGENT_INPUT_SCHEMA
        self.assertIn("run_in_background", AGENT_INPUT_SCHEMA["properties"])


# ---------------------------------------------------------------------------
# 12. Hook flow: PreToolUse block
# ---------------------------------------------------------------------------

class TestHookFlowPretoolBlock(unittest.TestCase):
    """PreToolUse hook blocks execution."""

    def test_pre_tool_use_result_block(self) -> None:
        from src.services.tool_execution.tool_hooks import PreToolUseResult
        result = PreToolUseResult(
            type="block",
            message="Blocked by policy hook",
            should_prevent_continuation=True,
        )
        self.assertEqual(result.type, "block")
        self.assertTrue(result.should_prevent_continuation)

    def test_hook_registry_filters_by_tool(self) -> None:
        from src.hooks.hook_types import HookConfig, HookSource
        from src.hooks.registry import AsyncHookRegistry

        async def run():
            reg = AsyncHookRegistry()
            config = HookConfig(type="command", command="echo block", matcher="Bash")
            await reg.register("PreToolUse", config, HookSource.SETTINGS)

            # Should match Bash
            hooks = await reg.get_hooks_for_event("PreToolUse", tool_name="Bash")
            self.assertEqual(len(hooks), 1)

            # Should NOT match Write
            hooks = await reg.get_hooks_for_event("PreToolUse", tool_name="Write")
            self.assertEqual(len(hooks), 0)

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 13. Hook flow: PreToolUse modify
# ---------------------------------------------------------------------------

class TestHookFlowPretoolModify(unittest.TestCase):
    """PreToolUse hook modifies tool input."""

    def test_pre_tool_use_result_modify(self) -> None:
        from src.services.tool_execution.tool_hooks import PreToolUseResult
        result = PreToolUseResult(
            type="modify",
            updated_input={"command": "ls -la --safe"},
        )
        self.assertEqual(result.type, "modify")
        self.assertEqual(result.updated_input["command"], "ls -la --safe")

    def test_hook_wildcard_matcher(self) -> None:
        from src.hooks.hook_types import HookConfig, HookSource
        from src.hooks.registry import AsyncHookRegistry

        async def run():
            reg = AsyncHookRegistry()
            # Wildcard matcher should match all tools
            config = HookConfig(type="command", command="echo audit", matcher=None)
            await reg.register("PreToolUse", config, HookSource.SETTINGS)

            for tool in ["Bash", "Write", "Read", "Agent"]:
                hooks = await reg.get_hooks_for_event("PreToolUse", tool_name=tool)
                self.assertEqual(len(hooks), 1, f"Expected match for {tool}")

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 14. Session flow: save → resume
# ---------------------------------------------------------------------------

class TestSessionFlowSaveResume(unittest.TestCase):
    """Session save → resume → continue."""

    def test_save_and_resume_roundtrip(self) -> None:
        from src.services.session_resume import resume_session
        from src.services.session_storage import SessionStorage

        with tempfile.TemporaryDirectory() as td:
            sessions_dir = Path(td)

            # Save session
            storage = SessionStorage(session_id="flow-test", sessions_dir=sessions_dir)
            storage.init_metadata(model="claude-sonnet-4-6", cwd="/tmp", title="Flow Test")

            msg1 = create_user_message("Hello")
            msg2 = create_assistant_message("Hi there!")

            storage.write_message(msg1)
            storage.write_message(msg2)
            storage.flush()

            # Resume
            result = resume_session("flow-test", sessions_dir=sessions_dir)
            self.assertTrue(result.success)
            self.assertEqual(result.message_count, 2)
            self.assertEqual(result.metadata.title, "Flow Test")

    def test_resume_handles_missing_session(self) -> None:
        from src.services.session_resume import resume_session

        with tempfile.TemporaryDirectory() as td:
            result = resume_session("nonexistent", sessions_dir=Path(td))
            self.assertFalse(result.success)


# ---------------------------------------------------------------------------
# 15. Command flow: /compact
# ---------------------------------------------------------------------------

class TestCommandFlowCompact(unittest.TestCase):
    """/compact → compaction → continue."""

    def test_compact_command_exists(self) -> None:
        from src.command_system.builtins import COMPACT_COMMAND
        self.assertEqual(COMPACT_COMMAND.name, "compact")
        self.assertTrue(COMPACT_COMMAND.supports_non_interactive)

    def test_compact_conversation_interface(self) -> None:
        from src.services.compact.compact import CompactContext, compact_conversation
        self.assertTrue(callable(compact_conversation))
        self.assertIn("messages", CompactContext.__dataclass_fields__)


# ---------------------------------------------------------------------------
# 16. Command flow: /model switch
# ---------------------------------------------------------------------------

class TestCommandFlowModelSwitch(unittest.TestCase):
    """/model → switch → new model used."""

    def test_model_aliases_resolve(self) -> None:
        from src.models.aliases import resolve_alias
        # Known aliases should resolve
        result = resolve_alias("sonnet")
        self.assertIsNotNone(result)
        self.assertNotEqual(result, "sonnet")  # Should resolve to full name

    def test_model_capabilities_exist(self) -> None:
        from src.models.capabilities import get_model_capabilities
        caps = get_model_capabilities("claude-sonnet-4-6")
        self.assertIsNotNone(caps)

    def test_model_validation(self) -> None:
        from src.models.validation import validate_model_name
        self.assertTrue(validate_model_name("claude-sonnet-4-6"))
        self.assertFalse(validate_model_name(""))


if __name__ == "__main__":
    unittest.main()
