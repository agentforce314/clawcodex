"""单个 Query tool round 的调度、上下文接管与结果收集。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from ..providers.base import BaseProvider
from ..tool_system.context import ToolContext
from ..types.content_blocks import ToolUseBlock
from ..types.messages import AssistantMessage, AttachmentMessage, Message, UserMessage


@dataclass
class ToolRoundState:
    context: ToolContext
    results: list[UserMessage] = field(default_factory=list)
    hook_stopped: bool = False


def _is_hook_stopped_continuation(message: Message | None) -> bool:
    if not isinstance(message, AttachmentMessage):
        return False
    attachments = getattr(message, "attachments", None) or []
    return any(
        isinstance(attachment, dict)
        and attachment.get("type") == "hook_stopped_continuation"
        for attachment in attachments
    )


async def execute_tool_round(
    *,
    tool_use_blocks: list[ToolUseBlock],
    assistant_messages: list[AssistantMessage],
    state: ToolRoundState,
    provider: BaseProvider,
) -> AsyncGenerator[Message, None]:
    """执行工具批次并按原顺序流式产出消息。

    ``state`` 在生成器完成后携带派生 context、严格 user-role results 和
    hook-stop 标记，避免通过第二套 loop 或延迟消息缓冲改变事件顺序。
    """

    from ..services.tool_execution.can_use_tool_adapter import build_can_use_tool
    from ..services.tool_execution.orchestrator import run_tools

    can_use_tool = build_can_use_tool(state.context)
    async for update in run_tools(
        tool_use_blocks,
        assistant_messages,
        can_use_tool,
        state.context,
    ):
        new_context = update.new_context
        if new_context is not None and new_context is not state.context:
            setattr(new_context, "_active_provider", provider)
            state.context = new_context
        message = update.message
        if message is None:
            continue
        if _is_hook_stopped_continuation(message):
            state.hook_stopped = True
        yield message
        if (
            isinstance(message, UserMessage)
            and getattr(message, "type", "user") == "user"
        ):
            state.results.append(message)
