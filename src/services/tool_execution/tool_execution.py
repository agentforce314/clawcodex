"""Tool execution — mirrors TypeScript toolExecution.ts.

Core function: run_tool_use() — async generator yielding MessageUpdateLazy.
Handles tool lookup, input validation, permission checks, pre/post hooks,
tool execution with progress, error handling, and result mapping.
"""

from __future__ import annotations

import json
import logging
import math
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

    tool_name = tool_use.name
    tool = find_tool_by_name(tool_use_context.options.tools, tool_name)

    if tool is None:
        # Old-transcript names and pool-hidden tools resolve through the
        # FULL base-tool list (TS toolExecution.ts:335-341:
        # findToolByName(getAllBaseTools(), toolName)). Not a permission
        # bypass: resolution still runs downstream for the resolved tool.
        try:
            from src.tool_system.defaults import build_default_registry

            tool = find_tool_by_name(
                build_default_registry().list_tools(), tool_name
            )
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
        # ``abort_controller`` is non-optional on ``ToolContext`` — the
        # historical ``if abort_ctrl and …`` guard masked the field-is-None
        # hazard class that broke ESC propagation into subagents.
        if tool_use_context.abort_controller.signal.aborted:
            if _is_user_cancelled_abort(tool_use_context):
                # ESC tripped before dispatch: REJECT_MESSAGE so the
                # model sees an unambiguous "user rejected" signal (TS
                # StreamingToolExecutor.ts:153-205 user_interrupted).
                yield MessageUpdateLazy(
                    message=_build_user_cancelled_message(tool_use.id),
                )
                return
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

    # ----- Step 3 — Schema validation with deferred-tool recovery hint.
    # Mirrors typescript/src/services/tools/toolExecution.ts schema check at
    # the head of checkPermissionsAndCallTool. Skipped when the tool has no
    # input_schema (defensive — every Tool should declare one).
    if getattr(tool, "input_schema", None):
        try:
            from src.tool_system.schema_validation import (
                build_schema_not_sent_hint,
                validate_json_schema,
            )

            validate_json_schema(
                processed_input, tool.input_schema, root_name=tool.name,
            )
        except Exception as schema_err:  # ToolInputError or any subclass
            msg = str(schema_err)
            if getattr(tool, "should_defer", False):
                msg = msg + build_schema_not_sent_hint(tool)
            # ch06 round-4 PR-A GAP C — the ``InputValidationError:`` prefix
            # (TS toolExecution.ts:726). Load-bearing: the tool-failure-loop
            # guard categorizes on ``\bInputValidationError\b`` (ch01
            # round-3 port), so without the prefix repeated schema failures
            # are miscategorized as generic errors and the path-based /
            # signature-based trip logic doesn't recognize them.
            resulting_messages.append(MessageUpdateLazy(
                message=create_user_message(
                    content=[{
                        "type": "tool_result",
                        "content": (
                            f"<tool_use_error>InputValidationError: "
                            f"{msg}</tool_use_error>"
                        ),
                        "is_error": True,
                        "tool_use_id": tool_use_id,
                    }],
                    toolUseResult=f"InputValidationError: {msg}",
                ),
            ))
            return resulting_messages

    # ----- Step 4 — Semantic validation (`validate_input`).
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

    # ----- Step 6 — Input Backfill (clone, not mutate).
    # call() receives the MODEL-ORIGINAL input: tool results embed input
    # fields verbatim (e.g. "File created successfully at: {path}"), and
    # changing them alters the serialized transcript. The cloned,
    # backfilled input is the hooks/permissions audience only.
    # Mirrors typescript/src/services/tools/toolExecution.ts:838-853.
    call_input = processed_input
    backfilled_clone: dict[str, Any] | None = None
    if tool.backfill_observable_input is not None:
        try:
            backfilled = dict(processed_input)
            tool.backfill_observable_input(backfilled)
            backfilled_clone = backfilled
            processed_input = backfilled
        except Exception as e:
            logger.debug("backfill_observable_input error for %s: %s", tool.name, e)

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

        # If processed_input still points at the backfill clone, no
        # hook/permission replaced it — pass the pre-backfill call_input so
        # call() sees the model's original field values. Hook/permission
        # flows may return a fresh object derived from the backfilled clone
        # (e.g. via schema re-parse): if its file_path matches the
        # backfill-expanded value, restore the model's original so the tool
        # result string embeds the path the model emitted. Other
        # modifications flow through unchanged. Mirrors
        # typescript/src/services/tools/toolExecution.ts:1212-1237.
        if (
            backfilled_clone is not None
            and processed_input is not call_input
            and isinstance(processed_input, dict)
            and "file_path" in processed_input
            and isinstance(call_input, dict)
            and "file_path" in call_input
            and processed_input.get("file_path")
            == backfilled_clone.get("file_path")
        ):
            call_input = {**processed_input, "file_path": call_input["file_path"]}
        elif processed_input is not backfilled_clone:
            call_input = processed_input

        result = await _call_tool(tool, call_input, call_context)

        # Post-tool override: a tool that observed the abort and returned
        # (e.g. bash's interrupted payload) reads as a generic failure;
        # replace it so the resume turn sees an unambiguous "user
        # rejected" signal. Mirrors TS StreamingToolExecutor.ts:332-345;
        # ported from the retired slim lane (ch07 unification).
        if _is_user_cancelled_abort(tool_use_context):
            resulting_messages.append(MessageUpdateLazy(
                message=_build_user_cancelled_message(tool_use_id),
            ))
            return resulting_messages

        duration_ms = int((time.monotonic() - start_time) * 1000)

        # Step 11 — Result Budgeting. Per-tool max_result_size_chars is
        # honored here: empty content is replaced with a "(<tool> completed
        # with no output)" marker, oversized content is persisted to disk
        # and replaced with a <persisted-output> wrapper that includes a
        # preview, image content reaches the model intact.
        from src.services.tool_execution.tool_result_persistence import (
            process_tool_result_block,
            resolve_tool_results_dir,
        )

        tool_results_dir = resolve_tool_results_dir(tool_use_context)
        # WI-5.1: thread the per-message aggregate counter through. The
        # function consults it to decide whether to force-persist a
        # block that would push the running total past the cap. We then
        # update the counter with the post-decision block size via
        # ``compute_block_chars``.
        from src.services.tool_execution.tool_result_persistence import (
            compute_block_chars,
        )
        tool_result_block = process_tool_result_block(
            tool,
            result.data,
            tool_use_id,
            tool_results_dir=tool_results_dir,
            aggregate_chars_so_far=tool_use_context.tool_result_chars_so_far,
        )
        # Non-finite-threshold tools (Read) are excluded from the aggregate
        # — TS skip-set semantics (query.ts:419-423,
        # toolResultStorage.ts:841-851): skipped tools neither get replaced
        # nor count toward the budget. process_tool_result_block still runs
        # for them (empty-content marker applies to ALL tools).
        if math.isfinite(tool.max_result_size_chars):
            tool_use_context.tool_result_chars_so_far += compute_block_chars(
                tool_result_block,
            )

        # Build the in-process ToolResultBlock (dataclass, not the raw
        # dict): preserves multimodal list content end-to-end (images/PDF
        # blocks reach the API as blocks, not JSON-stringified text) and
        # carries dict outputs as metadata["tool_output"] for display
        # consumers (repl/core._format_tool_result_preview). The wire
        # serialization strips metadata via content_block_to_dict.
        from src.types.content_blocks import ToolResultBlock

        raw_block_content = tool_result_block.get("content", "")
        if not isinstance(raw_block_content, (str, list)):
            raw_block_content = str(raw_block_content)
        block_metadata: dict[str, Any] = {}
        if isinstance(result.output, dict):
            block_metadata["tool_output"] = result.output
        result_block_obj = ToolResultBlock(
            tool_use_id=tool_use_id,
            content=raw_block_content,
            is_error=bool(getattr(result, "is_error", False)),
            metadata=block_metadata,
        )

        resulting_messages.append(MessageUpdateLazy(
            message=create_user_message(
                content=[result_block_obj],
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

    except AbortError as abort_err:
        # Two contracts at once (ported from the retired slim lane;
        # pinned by tests/test_esc_reject_message_dispatch.py and
        # test_agent_loop_does_not_swallow_abort_error_as_tool_error):
        # 1. tool_use/tool_result pairing stays intact — return a
        #    result, don't raise (an orphaned tool_use 400s next call).
        # 2. No follow-up API turn — when the signal isn't already
        #    tripped, trip it so the loop's post-tools abort gate exits.
        if _is_user_cancelled_abort(tool_use_context):
            resulting_messages.append(MessageUpdateLazy(
                message=_build_user_cancelled_message(tool_use_id),
            ))
            return resulting_messages
        try:
            tool_use_context.abort_controller.abort("tool_raised_abort_error")
        except Exception:
            pass
        resulting_messages.append(MessageUpdateLazy(
            message=create_user_message(
                content=[{
                    "type": "tool_result",
                    "content": f"Error: Tool execution aborted ({abort_err})",
                    "is_error": True,
                    "tool_use_id": tool_use_id,
                }],
                toolUseResult=f"Error: Tool execution aborted ({abort_err})",
            ),
        ))
        return resulting_messages

    except Exception as error:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        # Step 14 — Error Handling with telemetry-safe classification.
        # classify_tool_error returns a stable, mangling-safe string so
        # downstream telemetry/log scrapers can group errors by class
        # without leaking unredacted error messages. Mirrors
        # classifyToolError in typescript/src/services/tools/toolExecution.ts:151.
        classified = classify_tool_error(error)
        logger.error(
            "Tool %s failed (%dms) classified=%s: %s",
            tool.name, duration_ms, classified, error,
        )

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
        # Offload sync tools to a worker thread so they don't block the
        # event loop. Without this, a long Bash command holds the loop
        # for its entire duration — ESC key presses and abort_controller
        # listeners can't fire because no other task gets to run. Mirrors
        # the responsiveness TS gets "for free" from Node's
        # callback-driven I/O.
        result = await asyncio.to_thread(call_fn, tool_input, context)

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


def _is_user_cancelled_abort(tool_use_context: Any) -> bool:
    """True iff the abort signal fired with a user-initiated reason.

    ``sibling_error`` (streaming-executor parallel cascade) and
    ``streaming_fallback`` (discarded executor) are NOT user-rejected
    signals — surfacing REJECT_MESSAGE for them would mask the real
    failure. Every other reason (``user_interrupt`` from ESC,
    ``interrupt`` reserved for TS parity, ``tool_raised_abort_error``)
    collapses into the user-cancelled bucket. Moved from the retired
    query.py slim lane at ch07 unification; see that lane's docstring
    history for the TS interruptBehavior divergence note.
    """
    ctrl = tool_use_context.abort_controller
    if not ctrl.signal.aborted:
        return False
    return ctrl.signal.reason not in ("sibling_error", "streaming_fallback")


def _build_user_cancelled_message(tool_use_id: str) -> Any:
    """Synthetic REJECT_MESSAGE tool_result for a user abort.

    The bash tool's interrupted path emits ``<error>Command was aborted
    before completion</error>`` which the model reads as a generic
    failure and retries; REJECT_MESSAGE makes the cancellation
    unambiguous. Mirrors TS ``createSyntheticErrorMessage`` for
    ``user_interrupted`` (StreamingToolExecutor.ts:153-205).
    """
    from src.types.content_blocks import ToolResultBlock
    from src.types.messages import REJECT_MESSAGE

    return create_user_message(
        content=[
            ToolResultBlock(
                tool_use_id=tool_use_id,
                content=REJECT_MESSAGE,
                is_error=True,
            )
        ],
        toolUseResult="User rejected tool use",
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
