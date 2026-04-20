"""Tool hooks — mirrors TypeScript toolHooks.ts.

Pre/Post tool use hooks and permission resolution.
Hooks are user-defined scripts that run before/after tool execution.
Key invariant: hook 'allow' does NOT bypass settings deny/ask rules.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncGenerator

from src.types.messages import (
    AssistantMessage,
    Message,
    create_attachment_message,
    create_user_message,
)

if TYPE_CHECKING:
    from src.tool_system.build_tool import Tool
    from src.tool_system.context import ToolContext

logger = logging.getLogger(__name__)


@dataclass
class PreToolUseResult:
    type: str
    message: Any | None = None
    hook_permission_result: Any | None = None
    updated_input: dict[str, Any] | None = None
    should_prevent_continuation: bool = False
    stop_reason: str | None = None


async def run_pre_tool_use_hooks(
    tool_use_context: ToolContext,
    tool: Tool,
    processed_input: dict[str, Any],
    tool_use_id: str,
) -> AsyncGenerator[dict[str, Any], None]:
    try:
        from src.hooks.hook_executor import execute_pre_tool_hooks, has_hook_for_event

        if not has_hook_for_event("PreToolUse", tool_use_context):
            return

        async for result in execute_pre_tool_hooks(
            tool.name,
            tool_use_id,
            processed_input,
            tool_use_context,
        ):
            if result.get("blocking_error"):
                denial_message = f"Hook PreToolUse:{tool.name} blocked this tool: {result['blocking_error']}"
                yield {
                    "type": "hookPermissionResult",
                    "hookPermissionResult": {
                        "behavior": "deny",
                        "message": denial_message,
                        "decisionReason": {
                            "type": "hook",
                            "hookName": f"PreToolUse:{tool.name}",
                            "reason": denial_message,
                        },
                    },
                }

            if result.get("prevent_continuation"):
                yield {"type": "preventContinuation", "shouldPreventContinuation": True}
                if result.get("stop_reason"):
                    yield {"type": "stopReason", "stopReason": result["stop_reason"]}

            if result.get("permission_behavior") is not None:
                behavior = result["permission_behavior"]
                decision_reason = {
                    "type": "hook",
                    "hookName": f"PreToolUse:{tool.name}",
                    "reason": result.get("hook_permission_decision_reason"),
                }
                if behavior == "allow":
                    yield {
                        "type": "hookPermissionResult",
                        "hookPermissionResult": {
                            "behavior": "allow",
                            "updatedInput": result.get("updated_input"),
                            "decisionReason": decision_reason,
                        },
                    }
                elif behavior == "ask":
                    yield {
                        "type": "hookPermissionResult",
                        "hookPermissionResult": {
                            "behavior": "ask",
                            "updatedInput": result.get("updated_input"),
                            "message": result.get("hook_permission_decision_reason") or f"Hook PreToolUse:{tool.name} requires approval",
                            "decisionReason": decision_reason,
                        },
                    }
                else:
                    yield {
                        "type": "hookPermissionResult",
                        "hookPermissionResult": {
                            "behavior": behavior,
                            "message": result.get("hook_permission_decision_reason") or f"Hook PreToolUse:{tool.name} denied this tool",
                            "decisionReason": decision_reason,
                        },
                    }

            if result.get("updated_input") and result.get("permission_behavior") is None:
                yield {"type": "hookUpdatedInput", "updatedInput": result["updated_input"]}

            if result.get("additional_contexts"):
                yield {
                    "type": "additionalContext",
                    "message": {
                        "message": create_attachment_message({
                            "type": "hook_additional_context",
                            "content": result["additional_contexts"],
                            "hook_name": f"PreToolUse:{tool.name}",
                            "tool_use_id": tool_use_id,
                            "hook_event": "PreToolUse",
                        }),
                    },
                }

            if result.get("message"):
                yield {"type": "message", "message": {"message": result["message"]}}

            abort_ctrl = tool_use_context.abort_controller
            if abort_ctrl and abort_ctrl.signal.aborted:
                yield {
                    "type": "message",
                    "message": {
                        "message": create_attachment_message({
                            "type": "hook_cancelled",
                            "hook_name": f"PreToolUse:{tool.name}",
                            "tool_use_id": tool_use_id,
                            "hook_event": "PreToolUse",
                        }),
                    },
                }
                yield {"type": "stop"}
                return

    except Exception as e:
        logger.error("Pre-tool hook error: %s", e)
        yield {"type": "stop"}


async def run_post_tool_use_hooks(
    tool_use_context: ToolContext,
    tool: Tool,
    tool_use_id: str,
    tool_input: dict[str, Any],
    tool_response: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    try:
        from src.hooks.hook_executor import execute_post_tool_hooks, has_hook_for_event

        if not has_hook_for_event("PostToolUse", tool_use_context):
            return

        async for result in execute_post_tool_hooks(
            tool.name,
            tool_use_id,
            tool_input,
            tool_response,
            tool_use_context,
        ):
            if result.get("blocking_error"):
                yield {
                    "message": create_attachment_message({
                        "type": "hook_blocking_error",
                        "hook_name": f"PostToolUse:{tool.name}",
                        "tool_use_id": tool_use_id,
                        "hook_event": "PostToolUse",
                        "blocking_error": result["blocking_error"],
                    }),
                }

            if result.get("prevent_continuation"):
                yield {
                    "message": create_attachment_message({
                        "type": "hook_stopped_continuation",
                        "message": result.get("stop_reason") or "Execution stopped by PostToolUse hook",
                        "hook_name": f"PostToolUse:{tool.name}",
                        "tool_use_id": tool_use_id,
                        "hook_event": "PostToolUse",
                    }),
                }
                return

            if result.get("additional_contexts"):
                yield {
                    "message": create_attachment_message({
                        "type": "hook_additional_context",
                        "content": result["additional_contexts"],
                        "hook_name": f"PostToolUse:{tool.name}",
                        "tool_use_id": tool_use_id,
                        "hook_event": "PostToolUse",
                    }),
                }

            if result.get("updated_mcp_tool_output") and tool.is_mcp:
                yield {"updatedMCPToolOutput": result["updated_mcp_tool_output"]}

            if result.get("message"):
                yield {"message": result["message"]}

    except Exception as e:
        logger.error("Post-tool hook error: %s", e)


async def run_post_tool_use_failure_hooks(
    tool_use_context: ToolContext,
    tool: Tool,
    tool_use_id: str,
    processed_input: dict[str, Any],
    error: str,
) -> AsyncGenerator[dict[str, Any], None]:
    try:
        from src.hooks.hook_executor import execute_post_tool_failure_hooks, has_hook_for_event

        if not has_hook_for_event("PostToolUseFailure", tool_use_context):
            return

        async for result in execute_post_tool_failure_hooks(
            tool.name,
            tool_use_id,
            processed_input,
            error,
            tool_use_context,
        ):
            if result.get("blocking_error"):
                yield {
                    "message": create_attachment_message({
                        "type": "hook_blocking_error",
                        "hook_name": f"PostToolUseFailure:{tool.name}",
                        "tool_use_id": tool_use_id,
                        "hook_event": "PostToolUseFailure",
                        "blocking_error": result["blocking_error"],
                    }),
                }

            if result.get("additional_contexts"):
                yield {
                    "message": create_attachment_message({
                        "type": "hook_additional_context",
                        "content": result["additional_contexts"],
                        "hook_name": f"PostToolUseFailure:{tool.name}",
                        "tool_use_id": tool_use_id,
                        "hook_event": "PostToolUseFailure",
                    }),
                }

            if result.get("message"):
                yield {"message": result["message"]}

    except Exception as e:
        logger.error("Post-tool failure hook error: %s", e)


async def resolve_hook_permission_decision(
    hook_permission_result: Any,
    tool: Tool,
    tool_input: dict[str, Any],
    tool_use_context: ToolContext,
    can_use_tool: Any,
    assistant_message: AssistantMessage,
    tool_use_id: str,
) -> dict[str, Any]:
    if hook_permission_result is None:
        if can_use_tool is not None and callable(can_use_tool):
            try:
                import asyncio
                import inspect

                if inspect.iscoroutinefunction(can_use_tool):
                    decision = await can_use_tool(
                        tool, tool_input, tool_use_context, assistant_message, tool_use_id
                    )
                else:
                    decision = can_use_tool(
                        tool, tool_input, tool_use_context, assistant_message, tool_use_id
                    )
                if isinstance(decision, dict):
                    return decision
                if hasattr(decision, "behavior"):
                    return {"behavior": decision.behavior, "message": getattr(decision, "message", None)}
            except Exception as e:
                logger.debug("can_use_tool error: %s", e)
        return {"behavior": "allow"}

    if isinstance(hook_permission_result, dict):
        behavior = hook_permission_result.get("behavior")
    else:
        behavior = getattr(hook_permission_result, "behavior", None)

    if behavior == "allow":
        updated_input = (
            hook_permission_result.get("updatedInput")
            if isinstance(hook_permission_result, dict)
            else getattr(hook_permission_result, "updated_input", None)
        )
        hook_input = updated_input if updated_input is not None else tool_input

        requires_interaction = tool.requires_user_interaction and tool.requires_user_interaction()
        interaction_satisfied = requires_interaction and updated_input is not None
        require_can_use_tool = getattr(tool_use_context, "require_can_use_tool", False)

        if (requires_interaction and not interaction_satisfied) or require_can_use_tool:
            if can_use_tool is not None and callable(can_use_tool):
                import inspect
                if inspect.iscoroutinefunction(can_use_tool):
                    decision = await can_use_tool(
                        tool, hook_input, tool_use_context, assistant_message, tool_use_id
                    )
                else:
                    decision = can_use_tool(
                        tool, hook_input, tool_use_context, assistant_message, tool_use_id
                    )
                if isinstance(decision, dict):
                    return {**decision, "input": hook_input}
                if hasattr(decision, "behavior"):
                    return {"behavior": decision.behavior, "message": getattr(decision, "message", None), "input": hook_input}
            return {"behavior": "allow", "input": hook_input}

        try:
            from src.permissions.check import check_rule_based_permissions

            rule_check = check_rule_based_permissions(
                tool, hook_input, tool_use_context.permission_context, tool_use_context=tool_use_context
            )
            if rule_check is None:
                return {"behavior": "allow", "input": hook_input}
            if isinstance(rule_check, dict):
                return {**rule_check, "input": hook_input}
            if hasattr(rule_check, "behavior"):
                if rule_check.behavior == "deny":
                    return {"behavior": "deny", "message": getattr(rule_check, "message", "Denied by rule"), "input": hook_input}
                if rule_check.behavior == "ask" and can_use_tool is not None:
                    import inspect
                    if inspect.iscoroutinefunction(can_use_tool):
                        decision = await can_use_tool(
                            tool, hook_input, tool_use_context, assistant_message, tool_use_id
                        )
                    else:
                        decision = can_use_tool(
                            tool, hook_input, tool_use_context, assistant_message, tool_use_id
                        )
                    if isinstance(decision, dict):
                        return {**decision, "input": hook_input}
                    return {"behavior": getattr(decision, "behavior", "allow"), "input": hook_input}
        except ImportError:
            pass

        return {"behavior": "allow", "input": hook_input}

    if behavior == "deny":
        msg = (
            hook_permission_result.get("message")
            if isinstance(hook_permission_result, dict)
            else getattr(hook_permission_result, "message", "Permission denied by hook")
        )
        return {"behavior": "deny", "message": msg}

    if can_use_tool is not None and callable(can_use_tool):
        import inspect
        force_decision = hook_permission_result if behavior == "ask" else None
        try:
            if inspect.iscoroutinefunction(can_use_tool):
                decision = await can_use_tool(
                    tool, tool_input, tool_use_context, assistant_message, tool_use_id, force_decision
                )
            else:
                decision = can_use_tool(
                    tool, tool_input, tool_use_context, assistant_message, tool_use_id, force_decision
                )
            if isinstance(decision, dict):
                return decision
            if hasattr(decision, "behavior"):
                return {"behavior": decision.behavior, "message": getattr(decision, "message", None)}
        except Exception as e:
            logger.debug("can_use_tool error in ask path: %s", e)

    return {"behavior": "allow"}
