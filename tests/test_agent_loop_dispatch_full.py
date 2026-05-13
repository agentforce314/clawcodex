"""Tests for Phase-4 ``agent_loop.py`` refactor.

Verifies the simpler agent loop now correctly:

* Routes tool calls through ``dispatch_full`` (full 13-step pipeline)
* Honors per-tool ``max_result_size_chars`` budgeting
* Propagates ``ToolResult.new_messages`` into the conversation
* Applies ``ToolResult.context_modifier`` so the next tool sees it
* Preserves the typed-shape ``output`` for sendusermessage /
  structuredoutput special-cases
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from src.agent.conversation import Conversation
from src.permissions.types import ToolPermissionContext
from src.providers.base import ChatResponse
from src.tool_system.agent_loop import run_agent_loop
from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolResult
from src.tool_system.registry import ToolRegistry


def _make_context() -> ToolContext:
    tmp = tempfile.mkdtemp()
    return ToolContext(
        workspace_root=Path(tmp),
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )


def _stub_provider(tool_uses_first_turn: list[dict[str, Any]],
                   final_text: str = "done"):
    """Build an Anthropic-shaped mock provider that returns one
    tool-use turn followed by a final text turn."""
    provider = MagicMock()
    provider.chat_stream_response.side_effect = NotImplementedError()

    r1 = ChatResponse(
        content="thinking",
        model="claude-test",
        usage={"input_tokens": 1, "output_tokens": 1},
        finish_reason="tool_use",
        tool_uses=tool_uses_first_turn,
    )
    r2 = ChatResponse(
        content=final_text,
        model="claude-test",
        usage={"input_tokens": 1, "output_tokens": 1},
        finish_reason="stop",
        tool_uses=None,
    )
    provider.chat.side_effect = [r1, r2]
    # Make isinstance(provider, AnthropicProvider) return True so the
    # agent_loop takes the Anthropic-shaped result path.
    from src.providers.anthropic_provider import AnthropicProvider
    provider.__class__ = AnthropicProvider
    return provider


class TestAgentLoopDispatchFull(unittest.TestCase):
    """``run_agent_loop`` routes each tool use through ``dispatch_full``."""

    def test_simple_tool_dispatched_and_result_in_conversation(self) -> None:
        called: list[dict[str, Any]] = []

        def _call(inp, _ctx):
            called.append(inp)
            return ToolResult(name="Echo", output={"echo": inp.get("text", "")})

        tool = build_tool(
            name="Echo",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            call=_call,
        )
        registry = ToolRegistry([tool])
        ctx = _make_context()
        provider = _stub_provider([
            {"id": "tu1", "name": "Echo", "input": {"text": "hi"}},
        ])

        conv = Conversation()
        conv.add_user_message("say hi")

        result = run_agent_loop(
            conversation=conv,
            provider=provider,
            tool_registry=registry,
            tool_context=ctx,
        )

        self.assertEqual(result.response_text, "done")
        self.assertEqual(len(called), 1)
        self.assertEqual(called[0]["text"], "hi")


class TestAgentLoopAggregateBudget(unittest.TestCase):
    """Per-tool budgeting now engages on the agent_loop path
    (was missing pre-Phase-4)."""

    def test_oversized_output_persisted_to_disk(self) -> None:
        # 80K output > 50K default threshold → must be persisted.
        big = "X" * 80_000

        def _call(_inp, _ctx):
            return ToolResult(name="Big", output=big)

        tool = build_tool(
            name="Big",
            input_schema={"type": "object", "properties": {}},
            call=_call,
            max_result_size_chars=50_000,
        )
        registry = ToolRegistry([tool])
        ctx = _make_context()
        provider = _stub_provider([
            {"id": "tu1", "name": "Big", "input": {}},
        ])

        conv = Conversation()
        conv.add_user_message("trigger big tool")

        run_agent_loop(
            conversation=conv,
            provider=provider,
            tool_registry=registry,
            tool_context=ctx,
        )

        # Walk the conversation to find the tool_result block.
        # Pre-Phase-4: would contain the raw 80K output.
        # Post-Phase-4: must contain the <persisted-output> wrapper.
        found_persisted = False
        for msg in conv.messages:
            if not isinstance(msg.content, list):
                continue
            for block in msg.content:
                content_val = getattr(block, "content", None)
                if isinstance(content_val, str) and "<persisted-output>" in content_val:
                    found_persisted = True
        self.assertTrue(
            found_persisted,
            "Oversized tool result was NOT routed through persistence — "
            "agent_loop is not engaging Step 11 budgeting",
        )


class TestAgentLoopContextModifier(unittest.TestCase):
    """``ToolResult.context_modifier`` mutates the context so the
    NEXT tool in the same turn sees it (serial semantics)."""

    def test_context_modifier_applied_between_tools(self) -> None:
        seen_states: list[bool] = []

        def _set_plan_mode(c: ToolContext) -> ToolContext:
            c.plan_mode = True
            return c

        def _planner_call(_inp, _ctx):
            return ToolResult(
                name="Planner",
                output={"ok": True},
                context_modifier=_set_plan_mode,
            )

        def _capture_call(_inp, c):
            seen_states.append(c.plan_mode)
            return ToolResult(name="Capture", output={"saw": c.plan_mode})

        planner = build_tool(
            name="Planner",
            input_schema={"type": "object", "properties": {}},
            call=_planner_call,
        )
        capture = build_tool(
            name="Capture",
            input_schema={"type": "object", "properties": {}},
            call=_capture_call,
        )
        registry = ToolRegistry([planner, capture])
        ctx = _make_context()
        provider = _stub_provider([
            {"id": "tu1", "name": "Planner", "input": {}},
            {"id": "tu2", "name": "Capture", "input": {}},
        ])

        conv = Conversation()
        conv.add_user_message("enter plan and capture")
        run_agent_loop(
            conversation=conv,
            provider=provider,
            tool_registry=registry,
            tool_context=ctx,
        )
        self.assertTrue(seen_states, "Capture tool didn't run")
        self.assertTrue(
            seen_states[0],
            "Capture saw plan_mode=False; modifier from previous tool "
            "in the same turn was not applied. agent_loop is not "
            "honoring context_modifier",
        )


class TestAgentLoopNewMessages(unittest.TestCase):
    """``ToolResult.new_messages`` are appended to the conversation."""

    def test_new_messages_appended(self) -> None:
        from src.types.messages import create_user_message

        extra = create_user_message(content="extra context note")

        def _agent_like_call(_inp, _ctx):
            return ToolResult(
                name="AgentLike",
                output={"transcript": "..."},
                new_messages=[extra],
            )

        tool = build_tool(
            name="AgentLike",
            input_schema={"type": "object", "properties": {}},
            call=_agent_like_call,
        )
        registry = ToolRegistry([tool])
        ctx = _make_context()
        provider = _stub_provider([
            {"id": "tu1", "name": "AgentLike", "input": {}},
        ])

        conv = Conversation()
        conv.add_user_message("invoke agent")
        run_agent_loop(
            conversation=conv,
            provider=provider,
            tool_registry=registry,
            tool_context=ctx,
        )

        # Walk conversation messages looking for the extra context note
        found_extra = False
        for msg in conv.messages:
            content = msg.content
            if isinstance(content, str) and "extra context note" in content:
                found_extra = True
                break
            if isinstance(content, list):
                for block in content:
                    text = (
                        block.get("text") if isinstance(block, dict)
                        else getattr(block, "text", None)
                    )
                    if isinstance(text, str) and "extra context note" in text:
                        found_extra = True
                        break
        self.assertTrue(
            found_extra,
            "ToolResult.new_messages not appended to the agent_loop conversation",
        )


class TestAgentLoopAttachmentPreservation(unittest.TestCase):
    """``new_messages`` carrying AttachmentMessage / SystemMessage
    must preserve subclass-specific fields (attachments, subtype,
    preventContinuation, etc.). The append path must use
    ``append_raw_message``, NOT ``add_message`` (which strips them).
    """

    def test_attachment_message_attachments_field_preserved(self) -> None:
        from src.types.messages import AttachmentMessage

        att = AttachmentMessage(
            content="",
            attachments=[{"type": "hook_stopped_continuation",
                          "hook_name": "PreToolUse:Edit"}],
        )

        def _call(_inp, _ctx):
            return ToolResult(
                name="HookExample",
                output={"ok": True},
                new_messages=[att],
            )

        tool = build_tool(
            name="HookExample",
            input_schema={"type": "object", "properties": {}},
            call=_call,
        )
        registry = ToolRegistry([tool])
        ctx = _make_context()
        provider = _stub_provider([
            {"id": "tu1", "name": "HookExample", "input": {}},
        ])

        conv = Conversation()
        conv.add_user_message("invoke")
        run_agent_loop(
            conversation=conv,
            provider=provider,
            tool_registry=registry,
            tool_context=ctx,
        )

        # Find the AttachmentMessage in the conversation
        found = None
        for msg in conv.messages:
            if isinstance(msg, AttachmentMessage):
                found = msg
                break
        self.assertIsNotNone(found, "AttachmentMessage not preserved as subclass")
        self.assertEqual(
            found.attachments,
            [{"type": "hook_stopped_continuation",
              "hook_name": "PreToolUse:Edit"}],
            "AttachmentMessage.attachments dropped — agent_loop used "
            "add_message instead of append_raw_message",
        )


class TestAgentLoopAggregateCounterReset(unittest.TestCase):
    """``tool_use_context.tool_result_chars_so_far`` must reset to 0
    at the top of each turn, not grow monotonically.

    Without the reset, a long session accumulates the counter and
    eventually every block triggers force-persistence regardless of
    its individual size.
    """

    def test_counter_reset_between_turns(self) -> None:
        # Tool returns a small output that should NOT be persisted
        # on its own. Two turns: turn 1 returns the output, turn 2
        # returns nothing (model emits no tool_use, ending the loop).
        def _call(_inp, _ctx):
            return ToolResult(name="Small", output="ok")

        tool = build_tool(
            name="Small",
            input_schema={"type": "object", "properties": {}},
            call=_call,
        )
        registry = ToolRegistry([tool])
        ctx = _make_context()
        # Simulate a high "from prior session" counter that would
        # force-persist on the next call if not reset.
        ctx.tool_result_chars_so_far = 250_000

        provider = _stub_provider([
            {"id": "tu1", "name": "Small", "input": {}},
        ])

        conv = Conversation()
        conv.add_user_message("trigger")
        run_agent_loop(
            conversation=conv,
            provider=provider,
            tool_registry=registry,
            tool_context=ctx,
        )

        # The tool returned "ok" — only a few chars. The counter
        # should reflect that small block's size (post-reset), not
        # 250K+small.
        self.assertLess(
            ctx.tool_result_chars_so_far, 1000,
            f"Counter not reset between turns; ended at "
            f"{ctx.tool_result_chars_so_far} — should be <1000 "
            f"after reset + one small block",
        )


class TestAgentLoopPreservesTypedOutput(unittest.TestCase):
    """The SendUserMessage / StructuredOutput special cases read
    ``.get("message")`` / ``.get("structured_output")`` on the tool
    output. ``dispatch_full.output`` preserves typed dict shape
    (NOT a stringified blob) so these branches keep working."""

    def test_sendusermessage_typed_output(self) -> None:
        def _call(_inp, _ctx):
            return ToolResult(
                name="SendUserMessage",
                output={"message": "user-visible text", "status": "sent"},
            )

        tool = build_tool(
            name="SendUserMessage",
            input_schema={"type": "object", "properties": {}},
            call=_call,
        )
        registry = ToolRegistry([tool])
        ctx = _make_context()
        provider = _stub_provider([
            {"id": "tu1", "name": "SendUserMessage", "input": {}},
        ], final_text="")  # Empty final text — last_user_visible_message takes over.

        conv = Conversation()
        conv.add_user_message("show the user something")
        result = run_agent_loop(
            conversation=conv,
            provider=provider,
            tool_registry=registry,
            tool_context=ctx,
        )

        # When the model's final response is empty, the loop falls back
        # to the last user-visible message. Verify the message field
        # was read out of the TYPED dict output (not a stringified
        # blob), proving dispatch_full surfaces the raw output.
        self.assertEqual(result.response_text, "user-visible text")


if __name__ == "__main__":
    unittest.main()
