"""Tool execution — mirrors TypeScript toolExecution.ts.

Core function: run_tool_use() — async generator yielding MessageUpdateLazy.
Handles tool lookup, input validation, permission checks, pre/post hooks,
tool execution with progress, error handling, and result mapping.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable

from src.types.messages import (
    CANCEL_MESSAGE,
    AssistantMessage,
    Message,
    create_progress_message,
    create_user_message,
)
from src.utils.abort_controller import AbortError

if TYPE_CHECKING:
    from src.tool_system.build_tool import Tool, Tools
    from src.tool_system.context import ToolContext

logger = logging.getLogger(__name__)

HOOK_TIMING_DISPLAY_THRESHOLD_MS = 500


@dataclass
class ContextModifier:
    tool_use_id: str
    modify_context: Callable[[Any], Any]


@dataclass
class MessageUpdateLazy:
    message: Message | None = None
    context_modifier: ContextModifier | None = None


async def run_tool_use(
    tool_use: Any,
    assistant_message: AssistantMessage,
    can_use_tool: Any,
    tool_use_context: ToolContext,
) -> AsyncGenerator[MessageUpdateLazy, None]:
    from src.tool_system.build_tool import find_tool_by_name
    from src.tool_system.registry import get_all_base_tools

    tool_name = tool_use.name
    tool = find_tool_by_name(tool_use_context.options.tools, tool_name)

    if tool is None:
        try:
            from src.tool_system.registry import ToolRegistry
            fallback_registry = ToolRegistry(tool_use_context.options.tools)
            tool = fallback_registry.get(tool_name)
        except Exception:
            pass

    if tool is None:
        logger.debug("Unknown tool %s: %s", tool_name, tool_use.id)
        yield MessageUpdateLazy(
            message=create_user_message(
                content=[{
                    "type": "tool_result",
                    "content": f"<tool_use_error>Error: No such tool available: {tool_name}</tool_use_error>",
                    "is_error": True,
                    "tool_use_id": tool_use.id,
                }],
                toolUseResult=f"Error: No such tool available: {tool_name}",
            ),
        )
        return

    tool_input = tool_use.input if isinstance(tool_use.input, dict) else {}

    try:
        abort_ctrl = tool_use_context.abort_controller
        if abort_ctrl and abort_ctrl.signal.aborted:
            content = _create_tool_result_stop(tool_use.id)
            yield MessageUpdateLazy(
                message=create_user_message(
                    content=[content],
                    toolUseResult=CANCEL_MESSAGE,
                ),
            )
            return

        async for update in _streamed_check_permissions_and_call_tool(
            tool,
            tool_use.id,
            tool_input,
            tool_use_context,
            can_use_tool,
            assistant_message,
        ):
            yield update
    except Exception as error:
        logger.error("Error in run_tool_use: %s", error)
        error_msg = str(error)
        tool_info = f" ({tool.name})" if tool else ""
        detailed = f"Error calling tool{tool_info}: {error_msg}"
        yield MessageUpdateLazy(
            message=create_user_message(
                content=[{
                    "type": "tool_result",
                    "content": f"<tool_use_error>{detailed}</tool_use_error>",
                    "is_error": True,
                    "tool_use_id": tool_use.id,
                }],
                toolUseResult=detailed,
            ),
        )


async def _streamed_check_permissions_and_call_tool(
    tool: Tool,
    tool_use_id: str,
    tool_input: dict[str, Any],
    tool_use_context: ToolContext,
    can_use_tool: Any,
    assistant_message: AssistantMessage,
) -> AsyncGenerator[MessageUpdateLazy, None]:
    results = await _check_permissions_and_call_tool(
        tool,
        tool_use_id,
        tool_input,
        tool_use_context,
        can_use_tool,
        assistant_message,
    )
    for result in results:
        yield result


async def _check_permissions_and_call_tool(
    tool: Tool,
    tool_use_id: str,
    tool_input: dict[str, Any],
    tool_use_context: ToolContext,
    can_use_tool: Any,
    assistant_message: AssistantMessage,
) -> list[MessageUpdateLazy]:
    resulting_messages: list[MessageUpdateLazy] = []
    processed_input = tool_input

    if tool.validate_input is not None:
        try:
            validation = tool.validate_input(processed_input, tool_use_context)
            if hasattr(validation, "result") and not validation.result:
                msg = getattr(validation, "message", "Validation failed")
                resulting_messages.append(MessageUpdateLazy(
                    message=create_user_message(
                        content=[{
                            "type": "tool_result",
                            "content": f"<tool_use_error>{msg}</tool_use_error>",
                            "is_error": True,
                            "tool_use_id": tool_use_id,
                        }],
                        toolUseResult=f"Error: {msg}",
                    ),
                ))
                return resulting_messages
        except Exception as e:
            logger.debug("Validation error for %s: %s", tool.name, e)

    should_prevent_continuation = False
    stop_reason: str | None = None
    hook_permission_result = None

    try:
        from src.services.tool_execution.tool_hooks import run_pre_tool_use_hooks

        async for result in run_pre_tool_use_hooks(
            tool_use_context,
            tool,
            processed_input,
            tool_use_id,
        ):
            result_type = result.get("type") if isinstance(result, dict) else getattr(result, "type", None)

            if result_type == "message":
                msg = result.get("message") if isinstance(result, dict) else getattr(result, "message", None)
                if msg:
                    resulting_messages.append(msg if isinstance(msg, MessageUpdateLazy) else MessageUpdateLazy(message=msg))
            elif result_type == "hookPermissionResult":
                hook_permission_result = result.get("hookPermissionResult") if isinstance(result, dict) else getattr(result, "hook_permission_result", None)
            elif result_type == "hookUpdatedInput":
                processed_input = result.get("updatedInput") if isinstance(result, dict) else getattr(result, "updated_input", processed_input)
            elif result_type == "preventContinuation":
                should_prevent_continuation = True
            elif result_type == "stopReason":
                stop_reason = result.get("stopReason") if isinstance(result, dict) else getattr(result, "stop_reason", None)
            elif result_type == "stop":
                resulting_messages.append(MessageUpdateLazy(
                    message=create_user_message(
                        content=[_create_tool_result_stop(tool_use_id)],
                        toolUseResult=f"Error: {stop_reason or 'stopped'}",
                    ),
                ))
                return resulting_messages
    except Exception as e:
        logger.debug("Pre-tool hook error: %s", e)

    permission_decision = await _resolve_permission(
        hook_permission_result,
        tool,
        processed_input,
        tool_use_context,
        can_use_tool,
        assistant_message,
        tool_use_id,
    )

    if permission_decision.get("behavior") != "allow":
        error_message = permission_decision.get("message", "Permission denied")
        if should_prevent_continuation and not error_message:
            error_message = f"Execution stopped by PreToolUse hook{': ' + stop_reason if stop_reason else ''}"

        resulting_messages.append(MessageUpdateLazy(
            message=create_user_message(
                content=[{
                    "type": "tool_result",
                    "content": error_message,
                    "is_error": True,
                    "tool_use_id": tool_use_id,
                }],
                toolUseResult=f"Error: {error_message}",
            ),
        ))
        return resulting_messages

    updated_input = permission_decision.get("updatedInput")
    if updated_input is not None:
        processed_input = updated_input

    start_time = time.monotonic()

    try:
        from src.tool_system.context import ToolContext as TC

        call_context = tool_use_context
        call_context.tool_use_id = tool_use_id
        call_context.user_modified = permission_decision.get("userModified", False)

        result = await _call_tool(tool, processed_input, call_context)

        duration_ms = int((time.monotonic() - start_time) * 1000)

        tool_result_block = tool.map_result_to_api(result.data, tool_use_id)

        resulting_messages.append(MessageUpdateLazy(
            message=create_user_message(
                content=[tool_result_block],
                toolUseResult=result.data if not tool_use_context.agent_id else None,
            ),
            context_modifier=ContextModifier(
                tool_use_id=tool_use_id,
                modify_context=result.context_modifier,
            ) if result.context_modifier else None,
        ))

        try:
            from src.services.tool_execution.tool_hooks import run_post_tool_use_hooks

            async for hook_result in run_post_tool_use_hooks(
                tool_use_context,
                tool,
                tool_use_id,
                processed_input,
                result.data,
            ):
                if isinstance(hook_result, dict) and "message" in hook_result:
                    resulting_messages.append(MessageUpdateLazy(message=hook_result["message"]))
                elif isinstance(hook_result, MessageUpdateLazy):
                    resulting_messages.append(hook_result)
        except Exception as e:
            logger.debug("Post-tool hook error: %s", e)

        if result.new_messages:
            for msg in result.new_messages:
                resulting_messages.append(MessageUpdateLazy(message=msg))

        if should_prevent_continuation:
            from src.types.messages import create_attachment_message
            resulting_messages.append(MessageUpdateLazy(
                message=create_attachment_message({
                    "type": "hook_stopped_continuation",
                    "message": stop_reason or "Execution stopped by hook",
                    "hook_name": f"PreToolUse:{tool.name}",
                    "tool_use_id": tool_use_id,
                    "hook_event": "PreToolUse",
                }),
            ))

        return resulting_messages

    except AbortError:
        content = _create_tool_result_stop(tool_use_id)
        resulting_messages.append(MessageUpdateLazy(
            message=create_user_message(
                content=[content],
                toolUseResult=CANCEL_MESSAGE,
            ),
        ))
        return resulting_messages

    except Exception as error:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.error("Tool %s error (%dms): %s", tool.name, duration_ms, error)

        error_content = _format_error(error)

        resulting_messages.append(MessageUpdateLazy(
            message=create_user_message(
                content=[{
                    "type": "tool_result",
                    "content": error_content,
                    "is_error": True,
                    "tool_use_id": tool_use_id,
                }],
                toolUseResult=f"Error: {error_content}",
            ),
        ))

        try:
            from src.services.tool_execution.tool_hooks import run_post_tool_use_failure_hooks

            async for hook_result in run_post_tool_use_failure_hooks(
                tool_use_context,
                tool,
                tool_use_id,
                processed_input,
                error_content,
            ):
                if isinstance(hook_result, dict) and "message" in hook_result:
                    resulting_messages.append(MessageUpdateLazy(message=hook_result["message"]))
        except Exception:
            pass

        return resulting_messages


async def _call_tool(tool: Tool, tool_input: dict[str, Any], context: ToolContext) -> Any:
    from src.tool_system.protocol import ToolResult

    call_fn = tool.call
    import asyncio
    import inspect

    if inspect.iscoroutinefunction(call_fn):
        result = await call_fn(tool_input, context)
    else:
        result = call_fn(tool_input, context)

    if not isinstance(result, ToolResult):
        result = ToolResult(name=tool.name, output=result)

    return result


async def _resolve_permission(
    hook_permission_result: Any,
    tool: Tool,
    tool_input: dict[str, Any],
    tool_use_context: ToolContext,
    can_use_tool: Any,
    assistant_message: AssistantMessage,
    tool_use_id: str,
) -> dict[str, Any]:
    from src.services.tool_execution.tool_hooks import resolve_hook_permission_decision

    return await resolve_hook_permission_decision(
        hook_permission_result,
        tool,
        tool_input,
        tool_use_context,
        can_use_tool,
        assistant_message,
        tool_use_id,
    )


def _create_tool_result_stop(tool_use_id: str) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "content": CANCEL_MESSAGE,
        "is_error": True,
        "tool_use_id": tool_use_id,
    }


def _format_error(error: Exception) -> str:
    if isinstance(error, AbortError):
        return CANCEL_MESSAGE
    msg = str(error)
    if not msg:
        msg = type(error).__name__
    return f"<tool_use_error>{msg}</tool_use_error>"


def classify_tool_error(error: Exception) -> str:
    if hasattr(error, "telemetry_message"):
        return str(error.telemetry_message)[:200]
    if isinstance(error, OSError) and hasattr(error, "errno"):
        import errno as errno_mod
        code = errno_mod.errorcode.get(error.errno, "")
        if code:
            return f"Error:{code}"
    name = getattr(error, "name", None) or type(error).__name__
    if name and name != "Error" and len(name) > 3:
        return name[:60]
    return "Error"
