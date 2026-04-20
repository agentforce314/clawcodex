"""Stop hooks — mirrors TypeScript query/stopHooks.ts.

Handles end-of-turn hook execution, background tasks, and abort handling.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from ..types.messages import (
    AssistantMessage,
    Message,
    create_attachment_message,
    create_stop_hook_summary_message,
    create_system_message,
    create_user_interruption_message,
    create_user_message,
)

logger = logging.getLogger(__name__)


@dataclass
class StopHookInfo:
    command: str = ""
    prompt_text: str | None = None
    duration_ms: int | None = None


@dataclass
class StopHookResult:
    blocking_errors: list[Message] = field(default_factory=list)
    prevent_continuation: bool = False


async def handle_stop_hooks(
    messages_for_query: list[Message],
    assistant_messages: list[AssistantMessage],
    system_prompt: str,
    tool_use_context: Any,
    query_source: str,
    stop_hook_active: bool | None = None,
    user_context: dict[str, str] | None = None,
    system_context: dict[str, str] | None = None,
) -> StopHookResult:
    emitted: list[Message] = []
    result = StopHookResult()

    async for msg_or_result in _handle_stop_hooks_generator(
        messages_for_query,
        assistant_messages,
        system_prompt,
        tool_use_context,
        query_source,
        stop_hook_active,
        result,
    ):
        emitted.append(msg_or_result)

    return result


async def handle_stop_hooks_streaming(
    messages_for_query: list[Message],
    assistant_messages: list[AssistantMessage],
    system_prompt: str,
    tool_use_context: Any,
    query_source: str,
    stop_hook_active: bool | None = None,
) -> AsyncGenerator[Message | StopHookResult, None]:
    result = StopHookResult()

    async for msg in _handle_stop_hooks_generator(
        messages_for_query,
        assistant_messages,
        system_prompt,
        tool_use_context,
        query_source,
        stop_hook_active,
        result,
    ):
        yield msg

    yield result


async def _handle_stop_hooks_generator(
    messages_for_query: list[Message],
    assistant_messages: list[AssistantMessage],
    system_prompt: str,
    tool_use_context: Any,
    query_source: str,
    stop_hook_active: bool | None,
    result_out: StopHookResult,
) -> AsyncGenerator[Message, None]:
    hook_start_time = time.time()

    try:
        from src.hooks.hook_executor import execute_stop_hooks, has_hook_for_event

        abort_ctrl = getattr(tool_use_context, "abort_controller", None)
        permission_mode = _get_permission_mode(tool_use_context)
        agent_id = getattr(tool_use_context, "agent_id", None)
        agent_type = getattr(tool_use_context, "agent_type", None)

        if not has_hook_for_event("Stop", tool_use_context):
            return

        blocking_errors: list[Message] = []
        stop_hook_tool_use_id = ""
        hook_count = 0
        prevented_continuation = False
        stop_reason = ""
        has_output = False
        hook_errors: list[str] = []
        hook_infos: list[StopHookInfo] = []

        all_messages = [*messages_for_query, *assistant_messages]

        async for hook_result in execute_stop_hooks(
            permission_mode=permission_mode,
            abort_signal=abort_ctrl.signal if abort_ctrl else None,
            stop_hook_active=stop_hook_active or False,
            subagent_id=agent_id,
            tool_use_context=tool_use_context,
            messages=all_messages,
            agent_type=agent_type,
        ):
            if hook_result.get("message"):
                msg = hook_result["message"]
                yield msg

                if hasattr(msg, "type") and msg.type == "progress":
                    if hasattr(msg, "toolUseID") and msg.toolUseID:
                        stop_hook_tool_use_id = msg.toolUseID
                        hook_count += 1
                    progress_data = getattr(msg, "data", None)
                    if isinstance(progress_data, dict) and progress_data.get("command"):
                        hook_infos.append(StopHookInfo(
                            command=progress_data["command"],
                            prompt_text=progress_data.get("prompt_text"),
                        ))

                if hasattr(msg, "type") and msg.type == "attachment":
                    attachments = getattr(msg, "attachments", [])
                    for attachment in attachments:
                        hook_event = attachment.get("hook_event", "")
                        if hook_event in ("Stop", "SubagentStop"):
                            att_type = attachment.get("type", "")
                            if att_type == "hook_non_blocking_error":
                                hook_errors.append(
                                    attachment.get("stderr") or f"Exit code {attachment.get('exit_code')}"
                                )
                                has_output = True
                            elif att_type == "hook_error_during_execution":
                                hook_errors.append(attachment.get("content", ""))
                                has_output = True
                            elif att_type == "hook_success":
                                if (
                                    (attachment.get("stdout") or "").strip()
                                    or (attachment.get("stderr") or "").strip()
                                ):
                                    has_output = True

            if hook_result.get("blocking_error"):
                error_info = hook_result["blocking_error"]
                error_message = _get_stop_hook_message(error_info)
                user_msg = create_user_message(
                    content=error_message,
                    isMeta=True,
                )
                blocking_errors.append(user_msg)
                yield user_msg
                has_output = True
                hook_errors.append(
                    error_info.get("blocking_error", "")
                    if isinstance(error_info, dict)
                    else str(error_info)
                )

            if hook_result.get("prevent_continuation"):
                prevented_continuation = True
                stop_reason = hook_result.get("stop_reason") or "Stop hook prevented continuation"
                yield create_attachment_message({
                    "type": "hook_stopped_continuation",
                    "message": stop_reason,
                    "hook_name": "Stop",
                    "tool_use_id": stop_hook_tool_use_id,
                    "hook_event": "Stop",
                })

            if abort_ctrl and abort_ctrl.signal.aborted:
                yield create_user_interruption_message(tool_use=False)
                result_out.blocking_errors = []
                result_out.prevent_continuation = True
                return

        if hook_count > 0:
            yield create_stop_hook_summary_message(
                hook_count=hook_count,
                hook_infos=[
                    {"command": h.command, "prompt_text": h.prompt_text, "duration_ms": h.duration_ms}
                    for h in hook_infos
                ],
                hook_errors=hook_errors,
                prevented_continuation=prevented_continuation,
                stop_reason=stop_reason,
                has_output=has_output,
                suggestion_type="suggestion",
                tool_use_id=stop_hook_tool_use_id,
            )

        if prevented_continuation:
            result_out.blocking_errors = []
            result_out.prevent_continuation = True
            return

        if blocking_errors:
            result_out.blocking_errors = blocking_errors
            result_out.prevent_continuation = False
            return

    except Exception as error:
        duration_ms = int((time.time() - hook_start_time) * 1000)
        logger.error("Stop hook error (%dms): %s", duration_ms, error)
        yield create_system_message(
            f"Stop hook failed: {error}",
            "warning",
        )


def _get_permission_mode(tool_use_context: Any) -> str | None:
    try:
        app_state = tool_use_context.get_app_state() if hasattr(tool_use_context, "get_app_state") else None
        if app_state:
            return getattr(getattr(app_state, "tool_permission_context", None), "mode", None)
    except Exception:
        pass
    perm_ctx = getattr(tool_use_context, "permission_context", None)
    if perm_ctx:
        return getattr(perm_ctx, "mode", None)
    return None


def _get_stop_hook_message(error_info: Any) -> str:
    if isinstance(error_info, dict):
        blocking_error = error_info.get("blocking_error", "")
        return f"Stop hook blocked: {blocking_error}"
    if isinstance(error_info, str):
        return f"Stop hook blocked: {error_info}"
    return f"Stop hook blocked: {error_info}"
