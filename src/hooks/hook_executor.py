"""Hook execution engine — mirrors TypeScript utils/hooks.ts.

Core hook execution with shell command protocol, JSON stdin/stdout,
exit code semantics, timeout handling, and settings-driven configuration.

Exit code semantics:
- 0: success (stdout parsed as JSON for decisions)
- 2: blocking error (stderr or stdout used as error message)
- other non-zero: non-blocking error (logged, execution continues)

Security invariant: hooks cannot lower security level.
Hook 'allow' does not bypass settings deny/ask rules.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, AsyncGenerator
from uuid import uuid4

from src.hooks.hook_types import (
    TOOL_HOOK_EXECUTION_TIMEOUT_MS,
    HookConfig,
    HookEvent,
    HookResult,
)
from src.types.messages import (
    create_attachment_message,
    create_progress_message,
)

logger = logging.getLogger(__name__)


def _get_hooks_from_settings(tool_use_context: Any) -> dict[str, list[HookConfig]]:
    try:
        options = getattr(tool_use_context, "options", None)
        if options is None:
            return {}
        hooks_config = getattr(options, "hooks", None)
        if hooks_config is None:
            return {}
        if isinstance(hooks_config, dict):
            result: dict[str, list[HookConfig]] = {}
            for event_name, hook_list in hooks_config.items():
                if isinstance(hook_list, list):
                    configs = []
                    for h in hook_list:
                        if isinstance(h, dict):
                            configs.append(HookConfig(
                                type=h.get("type", "command"),
                                command=h.get("command", ""),
                                timeout=h.get("timeout"),
                                matcher=h.get("matcher"),
                            ))
                        elif isinstance(h, HookConfig):
                            configs.append(h)
                    result[event_name] = configs
            return result
    except Exception:
        pass
    return {}


def has_hook_for_event(event: str, tool_use_context: Any) -> bool:
    hooks = _get_hooks_from_settings(tool_use_context)
    return bool(hooks.get(event))


def _matches_tool(matcher: str | None, tool_name: str) -> bool:
    if matcher is None:
        return True
    if matcher == tool_name:
        return True
    if matcher.endswith("*"):
        prefix = matcher[:-1]
        return tool_name.startswith(prefix)
    if matcher.startswith("*"):
        suffix = matcher[1:]
        return tool_name.endswith(suffix)
    return matcher == tool_name


async def _execute_command_hook(
    hook: HookConfig,
    stdin_data: dict[str, Any],
    abort_signal: Any | None = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
) -> HookResult:
    command = hook.command
    if not command:
        return HookResult()

    effective_timeout = (hook.timeout or timeout_ms) / 1000.0
    start_time = time.monotonic()

    try:
        stdin_json = json.dumps(stdin_data, default=str)

        process = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "CLAUDE_HOOK_EVENT": stdin_data.get("hook_event", "")},
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(stdin_json.encode()),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except Exception:
                pass
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return HookResult(
                blocking_error=f"Hook timed out after {duration_ms}ms",
                exit_code=-1,
                duration_ms=duration_ms,
                command=command,
            )

        duration_ms = int((time.monotonic() - start_time) * 1000)
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        exit_code = process.returncode or 0

        if abort_signal and abort_signal.aborted:
            return HookResult(
                exit_code=exit_code,
                duration_ms=duration_ms,
                command=command,
            )

        if exit_code == 2:
            error_msg = stderr or stdout or "Hook exited with code 2"
            return HookResult(
                blocking_error=error_msg,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                command=command,
            )

        if exit_code != 0:
            return HookResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                command=command,
            )

        result = HookResult(
            exit_code=0,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            command=command,
        )

        if stdout:
            try:
                parsed = json.loads(stdout)
                if isinstance(parsed, dict):
                    decision = parsed.get("decision")
                    if decision in ("allow", "deny", "ask"):
                        result.permission_behavior = decision
                        result.hook_permission_decision_reason = parsed.get("reason")
                    if parsed.get("updatedInput"):
                        result.updated_input = parsed["updatedInput"]
                    if parsed.get("preventContinuation"):
                        result.prevent_continuation = True
                        result.stop_reason = parsed.get("stopReason")
                    if parsed.get("additionalContexts"):
                        result.additional_contexts = parsed["additionalContexts"]
                    if parsed.get("updatedMCPToolOutput"):
                        result.updated_mcp_tool_output = parsed["updatedMCPToolOutput"]
            except json.JSONDecodeError:
                pass

        return result

    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return HookResult(
            blocking_error=f"Hook execution error: {e}",
            exit_code=-1,
            duration_ms=duration_ms,
            command=command,
        )


async def _run_hooks_for_event(
    event: str,
    tool_name: str | None,
    stdin_data: dict[str, Any],
    tool_use_context: Any,
    abort_signal: Any | None = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
) -> AsyncGenerator[dict[str, Any], None]:
    hooks = _get_hooks_from_settings(tool_use_context)
    event_hooks = hooks.get(event, [])

    tool_use_id = stdin_data.get("tool_use_id", str(uuid4()))
    parent_tool_use_id = ""

    for hook in event_hooks:
        if tool_name and not _matches_tool(hook.matcher, tool_name):
            continue

        yield {
            "message": create_progress_message(
                toolUseID=tool_use_id,
                parentToolUseID=parent_tool_use_id,
                data={"command": hook.command, "prompt_text": None},
            ),
        }

        result = await _execute_command_hook(
            hook,
            {**stdin_data, "hook_event": event},
            abort_signal=abort_signal,
            timeout_ms=timeout_ms,
        )

        if result.blocking_error:
            yield {"blocking_error": {"blocking_error": result.blocking_error, "command": result.command}}

        if result.permission_behavior is not None:
            yield {
                "permission_behavior": result.permission_behavior,
                "hook_permission_decision_reason": result.hook_permission_decision_reason,
                "updated_input": result.updated_input,
            }

        if result.prevent_continuation:
            yield {
                "prevent_continuation": True,
                "stop_reason": result.stop_reason,
            }

        if result.additional_contexts:
            yield {"additional_contexts": result.additional_contexts}

        if result.updated_mcp_tool_output is not None:
            yield {"updated_mcp_tool_output": result.updated_mcp_tool_output}

        if result.updated_input and result.permission_behavior is None:
            yield {"updated_input": result.updated_input}

        if result.exit_code is not None and result.exit_code != 0 and result.exit_code != 2:
            yield {
                "message": create_attachment_message({
                    "type": "hook_non_blocking_error",
                    "hook_event": event,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration_ms": result.duration_ms,
                    "command": result.command,
                }),
            }
        elif result.exit_code == 0:
            yield {
                "message": create_attachment_message({
                    "type": "hook_success",
                    "hook_event": event,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration_ms": result.duration_ms,
                    "command": result.command,
                }),
            }


async def execute_pre_tool_hooks(
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, Any],
    tool_use_context: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    stdin_data = {
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "tool_input": tool_input,
    }

    abort_signal = None
    abort_ctrl = getattr(tool_use_context, "abort_controller", None)
    if abort_ctrl:
        abort_signal = abort_ctrl.signal

    async for result in _run_hooks_for_event(
        "PreToolUse",
        tool_name,
        stdin_data,
        tool_use_context,
        abort_signal=abort_signal,
    ):
        yield result


async def execute_post_tool_hooks(
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, Any],
    tool_response: Any,
    tool_use_context: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    stdin_data = {
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "tool_input": tool_input,
        "tool_response": str(tool_response) if not isinstance(tool_response, (str, dict, list)) else tool_response,
    }

    abort_signal = None
    abort_ctrl = getattr(tool_use_context, "abort_controller", None)
    if abort_ctrl:
        abort_signal = abort_ctrl.signal

    async for result in _run_hooks_for_event(
        "PostToolUse",
        tool_name,
        stdin_data,
        tool_use_context,
        abort_signal=abort_signal,
    ):
        yield result


async def execute_post_tool_failure_hooks(
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, Any],
    error: str,
    tool_use_context: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    stdin_data = {
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "tool_input": tool_input,
        "error": error,
    }

    abort_signal = None
    abort_ctrl = getattr(tool_use_context, "abort_controller", None)
    if abort_ctrl:
        abort_signal = abort_ctrl.signal

    async for result in _run_hooks_for_event(
        "PostToolUseFailure",
        tool_name,
        stdin_data,
        tool_use_context,
        abort_signal=abort_signal,
    ):
        yield result


async def execute_stop_hooks(
    *,
    permission_mode: str | None = None,
    abort_signal: Any | None = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
    stop_hook_active: bool = False,
    subagent_id: str | None = None,
    tool_use_context: Any = None,
    messages: list[Any] | None = None,
    agent_type: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    event = "SubagentStop" if subagent_id else "Stop"

    stdin_data: dict[str, Any] = {
        "permission_mode": permission_mode,
        "stop_hook_active": stop_hook_active,
    }
    if subagent_id:
        stdin_data["subagent_id"] = subagent_id
    if agent_type:
        stdin_data["agent_type"] = agent_type

    async for result in _run_hooks_for_event(
        event,
        None,
        stdin_data,
        tool_use_context,
        abort_signal=abort_signal,
        timeout_ms=timeout_ms,
    ):
        yield result
