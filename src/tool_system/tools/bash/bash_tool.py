"""Core bash tool definition -- execution, permissions, and result mapping."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

_HARDCODED_DANGEROUS_PATTERNS = [
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\b\s+if=", re.IGNORECASE),
    re.compile(r"\brm\b.*\s+-rf\s+/\s*$", re.IGNORECASE),
    re.compile(r"\brm\b.*\s+-rf\s+/\s+"),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.IGNORECASE),
]

from ...build_tool import SearchOrReadResult, Tool, ValidationResult, build_tool
from ...context import ToolContext
from ...errors import ToolInputError, ToolPermissionError
from ...protocol import ToolResult
from src.permissions.bash_security import check_bash_command_safety
from src.permissions.types import PermissionPassthroughResult, PermissionResult

from .background import spawn_background_bash
from .command_semantics import interpret_command_result
from .destructive_warnings import get_destructive_command_warning
from .prompt import get_bash_prompt, get_default_timeout_ms, get_max_timeout_ms
from .read_only_validation import is_command_read_only
from .search_classification import (
    SearchOrReadResult as _SearchOrRead,
    is_search_or_read_command,
    is_silent_command,
)
from .sleep_detection import detect_blocked_sleep_pattern
from .utils import strip_empty_lines, strip_leading_blank_lines, truncate_output

BASH_TOOL_NAME = "Bash"

TOOL_SUMMARY_MAX_LENGTH = 80


def _try_extract_cd(command: str) -> Path | None:
    stripped = command.strip()
    if not stripped.startswith("cd "):
        return None
    try:
        parts = shlex.split(stripped, posix=True)
    except ValueError:
        return None
    if len(parts) >= 2 and parts[0] == "cd":
        return Path(parts[1])
    return None


def _bash_check_permissions(
    tool_input: dict[str, Any],
    context: ToolContext,
) -> PermissionResult:
    command = (tool_input or {}).get("command", "")
    if not command:
        return PermissionPassthroughResult()

    cwd_str = str(context.cwd) if context.cwd else None
    result = check_bash_command_safety(command, cwd=cwd_str)
    if result is not None:
        return result

    return PermissionPassthroughResult()


def _bash_validate_input(
    tool_input: dict[str, Any],
    context: ToolContext,
) -> ValidationResult:
    command = (tool_input or {}).get("command", "")
    sleep_pattern = detect_blocked_sleep_pattern(command)
    if sleep_pattern is not None:
        return ValidationResult.fail(
            f"Blocked: {sleep_pattern}. Run blocking commands in the background "
            "with run_in_background: true -- you'll get a completion notification "
            "when done. If you genuinely need a delay (rate limiting, deliberate "
            "pacing), keep it under 2 seconds.",
            error_code=10,
        )
    return ValidationResult.ok()


def _bash_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    command = tool_input["command"]
    if not isinstance(command, str) or not command.strip():
        raise ToolInputError("command must be a non-empty string")
    if "\x00" in command:
        raise ToolInputError("command contains NUL byte")

    # Defense-in-depth: block obviously dangerous commands even when called
    # directly (bypassing the registry's check_permissions flow).
    for pat in _HARDCODED_DANGEROUS_PATTERNS:
        if pat.search(command):
            raise ToolPermissionError("refusing to run potentially dangerous command")

    explicit_cwd = tool_input.get("cwd")
    if explicit_cwd is not None:
        if not isinstance(explicit_cwd, str) or not explicit_cwd.startswith("/"):
            raise ToolInputError("cwd must be an absolute path when provided")
        cwd = context.ensure_allowed_path(explicit_cwd)
    else:
        cwd = context.cwd or context.workspace_root

    # ``run_in_background: true`` detaches the command so the agent can keep
    # coordinating while a long-running job (dev server, build, long test
    # suite, ...) makes progress. Mirrors
    # ``typescript/src/tools/BashTool/BashTool.tsx`` ``spawnBackgroundTask``
    # behaviour: we return immediately with a task id and let the model poll
    # the output via ``TaskOutput``.
    if tool_input.get("run_in_background"):
        bg_output = spawn_background_bash(
            command=command,
            cwd=cwd,
            description=tool_input.get("description"),
            context=context,
        )
        return ToolResult(name=BASH_TOOL_NAME, output=bg_output)

    cd_target = _try_extract_cd(command)
    if (
        cd_target is not None
        and command.strip().startswith("cd ")
        and len(command.strip().splitlines()) == 1
    ):
        if not cd_target.is_absolute():
            next_dir = (cwd / cd_target).expanduser().resolve()
        else:
            next_dir = cd_target.expanduser().resolve()
        next_dir = context.ensure_allowed_path(next_dir)
        if not next_dir.exists() or not next_dir.is_dir():
            return ToolResult(
                name=BASH_TOOL_NAME,
                output={"error": f"directory does not exist: {next_dir}"},
                is_error=True,
            )
        context.cwd = next_dir
        return ToolResult(
            name=BASH_TOOL_NAME,
            output={"cwd": str(context.cwd), "stdout": "", "stderr": ""},
        )

    # Resolve timeout: prefer explicit timeout (ms), fall back to timeout_s (legacy), then default
    timeout_ms = tool_input.get("timeout")
    if timeout_ms is not None:
        max_ms = get_max_timeout_ms()
        if not isinstance(timeout_ms, (int, float)) or timeout_ms < 1000:
            raise ToolInputError("timeout must be at least 1000 ms")
        if timeout_ms > max_ms:
            raise ToolInputError(f"timeout must not exceed {max_ms} ms")
        timeout_s = int(timeout_ms / 1000)
    else:
        timeout_s = tool_input.get("timeout_s")
        if timeout_s is None:
            timeout_s = int(get_default_timeout_ms() / 1000)
        if not isinstance(timeout_s, int) or timeout_s < 1 or timeout_s > 600:
            raise ToolInputError("timeout_s must be an integer between 1 and 600")

    # Persist cwd across invocations (port of ``typescript/src/utils/Shell.ts``,
    # which writes PWD to ``cwdFilePath`` after every command and calls
    # ``setCwdState()``). We wrap the user's command so that a trailing ``pwd``
    # writes the shell's final directory into a tempfile, and read it back to
    # update ``context.cwd``. This way ``cd demos && ls`` (compound) or a
    # ``pushd`` inside a script correctly moves the persistent CWD forward
    # instead of being discarded with the subprocess.
    import os as _os
    import tempfile as _tempfile

    cwd_fd, cwd_path = _tempfile.mkstemp(prefix="clawcodex-bash-cwd-", suffix=".txt")
    _os.close(cwd_fd)
    try:
        wrapped = f"{{ {command}\n}}; __rc=$?; pwd > {shlex.quote(cwd_path)} 2>/dev/null; exit $__rc"
        try:
            completed = subprocess.run(
                ["bash", "-lc", wrapped],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=BASH_TOOL_NAME,
                output={
                    "cwd": str(cwd),
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout_s} seconds",
                    "interrupted": True,
                },
                is_error=True,
            )

        # If the command succeeded in changing directory, promote the new cwd
        # into the shared ToolContext so follow-up Bash invocations start
        # there. Errors (e.g. command exited mid-flight before ``pwd`` ran)
        # fall through quietly — we keep the prior cwd.
        try:
            with open(cwd_path, "r", encoding="utf-8") as handle:
                final_cwd_text = handle.read().strip()
        except OSError:
            final_cwd_text = ""
    finally:
        try:
            _os.unlink(cwd_path)
        except OSError:
            pass

    if final_cwd_text:
        try:
            new_cwd = context.ensure_allowed_path(final_cwd_text)
            if new_cwd.exists() and new_cwd.is_dir():
                context.cwd = new_cwd
                cwd = new_cwd
        except ToolPermissionError:
            # cd'd outside the allowed roots — don't track it but don't fail
            # the call either (matches the TS behavior where the process can
            # roam freely but the UI cwd clamps to the workspace).
            pass

    stdout = truncate_output(completed.stdout or "")
    stderr = truncate_output(completed.stderr or "")

    interpretation = interpret_command_result(
        command, completed.returncode, completed.stdout or "", completed.stderr or "",
    )

    output: dict[str, Any] = {
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }

    if interpretation.message:
        output["returnCodeInterpretation"] = interpretation.message
    if is_silent_command(command):
        output["noOutputExpected"] = True

    return ToolResult(
        name=BASH_TOOL_NAME,
        output=output,
        is_error=interpretation.is_error,
    )


def _bash_map_result_to_api(output: Any, tool_use_id: str) -> dict[str, Any]:
    if isinstance(output, dict):
        # ``run_in_background: true`` responses carry a task id + a canned
        # message instead of stdout/stderr -- hand it through verbatim so the
        # model sees something actionable.
        if output.get("backgroundTaskId") and not output.get("stdout"):
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": output.get("message", "")
                or f"Background task started: {output['backgroundTaskId']}",
            }
        stdout = output.get("stdout", "")
        stderr = output.get("stderr", "")
        interpretation = output.get("returnCodeInterpretation")
        interrupted = output.get("interrupted", False)

        processed_stdout = strip_leading_blank_lines(stdout).rstrip() if stdout else ""

        parts: list[str] = []
        if processed_stdout:
            parts.append(processed_stdout)

        error_parts: list[str] = []
        if stderr and stderr.strip():
            error_parts.append(stderr.strip())
        if interrupted:
            error_parts.append("<error>Command was aborted before completion</error>")
        if error_parts:
            parts.append("\n".join(error_parts))

        if interpretation:
            parts.append(interpretation)

        content = "\n".join(parts) if parts else ""

        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
            "is_error": interrupted,
        }

    if isinstance(output, str):
        content_val: str | list[dict[str, Any]] = output
    else:
        content_val = json.dumps(output) if output else ""

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content_val,
    }


def _bash_prompt_fn() -> str:
    return get_bash_prompt()


def _bash_search_or_read(input_data: dict) -> SearchOrReadResult:
    cmd = (input_data or {}).get("command", "")
    result = is_search_or_read_command(cmd)
    return SearchOrReadResult(
        is_search=result.is_search,
        is_read=result.is_read,
        is_list=result.is_list,
    )


def _bash_classifier_input(input_data: dict) -> str:
    return (input_data or {}).get("command", "")


def _bash_activity(input_data: dict | None) -> str | None:
    if not input_data:
        return "Running command"
    cmd = input_data.get("command", "")
    desc = input_data.get("description")
    if desc:
        return f"Running {desc}"
    return f"Running {cmd[:60]}" if cmd else "Running command"


def _bash_user_facing_name(input_data: dict | None) -> str:
    if not input_data:
        return "Bash"
    return f"Bash: {(input_data.get('command', '') or '')[:50]}" if input_data else "Bash"


def _bash_tool_use_summary(input_data: dict | None) -> str | None:
    if not input_data:
        return None
    desc = input_data.get("description")
    if desc:
        return desc[:TOOL_SUMMARY_MAX_LENGTH]
    cmd = input_data.get("command", "")
    return cmd[:TOOL_SUMMARY_MAX_LENGTH] if cmd else None


BashTool: Tool = build_tool(
    name=BASH_TOOL_NAME,
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory (absolute path)",
            },
            "timeout_s": {
                "type": "integer",
                "description": "Timeout in seconds (1-600)",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in milliseconds",
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command does in active voice."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Set to true to run this command in the background.",
            },
        },
        "required": ["command"],
    },
    call=_bash_call,
    prompt=_bash_prompt_fn,
    description="Execute a shell command.",
    max_result_size_chars=30_000,
    map_result_to_api=_bash_map_result_to_api,
    check_permissions=_bash_check_permissions,
    validate_input=_bash_validate_input,
    is_read_only=lambda _input: is_command_read_only((_input or {}).get("command", "")),
    is_concurrency_safe=lambda _input: is_command_read_only((_input or {}).get("command", "")),
    is_destructive=lambda _input: not is_command_read_only((_input or {}).get("command", "")),
    user_facing_name=_bash_user_facing_name,
    search_hint="shell terminal execute run command",
    to_auto_classifier_input=_bash_classifier_input,
    is_search_or_read_command=_bash_search_or_read,
    get_activity_description=_bash_activity,
    get_tool_use_summary=_bash_tool_use_summary,
)
