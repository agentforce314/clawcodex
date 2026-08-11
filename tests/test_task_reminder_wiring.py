"""The task reminder actually reaches the conversation.

Unit coverage lives in test_task_reminder.py; this drives the real
``run_query_as_agent_loop`` and asserts the reminder is appended to the
outgoing messages and persisted via ``on_attachment`` — the wiring in
agent_loop_compat, which no unit test touches.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import pytest

from src.bootstrap.state import set_is_interactive
from src.providers.base import ChatResponse
from src.query.agent_loop_compat import run_query_as_agent_loop
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import AssistantMessage, UserMessage

MARKER = "The task tools haven't been used recently"


class _Provider:
    """Non-streaming fake that records what the model was sent."""

    def __init__(self) -> None:
        self.base_url = None
        self.model = "claude-test"
        self.seen_messages: list[Any] = []

    def chat_stream_response(self, *_args: Any, **_kwargs: Any) -> ChatResponse:
        raise NotImplementedError  # force the non-stream path

    def chat(self, messages: Any = None, *args: Any, **kwargs: Any) -> ChatResponse:
        self.seen_messages = list(messages or kwargs.get("messages") or [])

        return ChatResponse(
            content="done",
            model="test",
            usage={"input_tokens": 0, "output_tokens": 0},
            finish_reason="end_turn",
            tool_uses=None,
        )


def _text(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(getattr(b, "text", ""))
            for b in content
        )
    return ""


@pytest.fixture()
def interactive():
    set_is_interactive(True)
    yield
    set_is_interactive(False)


def test_reminder_lands_in_the_conversation_and_persists(interactive) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        context = ToolContext(workspace_root=Path(tmp))
        provider = _Provider()
        persisted: list[Any] = []

        # 11 turn pairs with no task-tool usage → both counters past 10.
        history: list[Any] = []
        for i in range(11):
            history.append(UserMessage(content=f"step {i}"))
            history.append(AssistantMessage(content=[{"type": "text", "text": "ok"}]))
        history.append(UserMessage(content="continue"))

        asyncio.run(
            run_query_as_agent_loop(
                initial_messages=history,
                provider=provider,  # type: ignore[arg-type]
                tool_registry=build_default_registry(),
                tool_context=context,
                system_prompt="",
                max_turns=2,
                on_attachment=persisted.append,
            )
        )

        sent = [m for m in provider.seen_messages if MARKER in _text(m)]
        assert sent, "reminder never reached the model"
        assert "<system-reminder>" in _text(sent[0])

        kept = [m for m in persisted if MARKER in _text(m)]
        assert kept, "reminder was not persisted via on_attachment"


def test_persisted_reminder_is_meta_not_a_prompt(interactive) -> None:
    """The attachment must persist as ``isMeta`` — dropping the flag made the
    reminder count as a real user turn (stats odometer) and a /rewind
    boundary. Drives the same ``Conversation.add_message`` seam the two
    ``on_attachment`` lambdas use."""
    from src.agent.conversation import Conversation
    from src.server.agent_server import _count_prompt_turns
    from src.types.messages import create_user_message

    conv = Conversation()
    conv.add_message("user", "a real prompt")
    reminder = create_user_message(content=f"<system-reminder>\n{MARKER} …\n</system-reminder>", isMeta=True)
    conv.add_message(reminder.role, reminder.content, isMeta=getattr(reminder, "isMeta", False))

    assert getattr(conv.messages[-1], "isMeta", False) is True
    assert _count_prompt_turns(conv.messages) == 1

    # And it survives the save/load round-trip the throttle scan depends on.
    restored = Conversation.from_dict(conv.to_dict())
    assert getattr(restored.messages[-1], "isMeta", False) is True
    assert _count_prompt_turns(restored.messages) == 1


def test_no_reminder_on_a_short_conversation(interactive) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        context = ToolContext(workspace_root=Path(tmp))
        provider = _Provider()

        asyncio.run(
            run_query_as_agent_loop(
                initial_messages=[UserMessage(content="hi")],
                provider=provider,  # type: ignore[arg-type]
                tool_registry=build_default_registry(),
                tool_context=context,
                system_prompt="",
                max_turns=2,
            )
        )

        assert not any(MARKER in _text(m) for m in provider.seen_messages)
