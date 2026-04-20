from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from ..build_tool import Tool, ValidationResult, build_tool
from ..context import ToolContext
from ..errors import ToolInputError, ToolPermissionError
from ..protocol import ToolResult
from ..diff_utils import unified_diff_hunks
from src.permissions.types import (
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionPassthroughResult,
    PermissionResult,
)

# ---------------------------------------------------------------------------
# Prompt  (ported from TS FileWriteTool/prompt.ts)
# ---------------------------------------------------------------------------

_WRITE_TOOL_PROMPT = """\
Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.
- Prefer the Edit tool for modifying existing files \u2014 it only sends the diff. Only use this tool to create new files or for complete rewrites.
- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _expand_path(file_path: str) -> str:
    """Normalize ``~``, ``.``, and ``..`` to an absolute path string."""
    return str(Path(file_path).expanduser().resolve())


# ---------------------------------------------------------------------------
# backfill_observable_input  (ported from TS backfillObservableInput)
# ---------------------------------------------------------------------------

def _backfill_observable_input(tool_input: dict[str, Any]) -> None:
    """Expand *file_path* in-place so hook allowlists cannot be bypassed
    via ``~`` or relative paths.
    """
    fp = tool_input.get("file_path")
    if isinstance(fp, str):
        tool_input["file_path"] = _expand_path(fp)


# ---------------------------------------------------------------------------
# validate_input  (ported from TS validateInput -- Tier 1.1)
# ---------------------------------------------------------------------------

def _validate_input(tool_input: dict[str, Any], context: ToolContext) -> ValidationResult:
    """Pre-flight validation with distinct error codes:

    * *errorCode 0* -- notebook file (redirect to NotebookEdit)
    * *errorCode 2* -- file not read yet (or only partially read)
    * *errorCode 3* -- file modified since last read
    """
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str):
        return ValidationResult.fail("file_path must be a string")

    content = tool_input.get("content")
    if not isinstance(content, str):
        return ValidationResult.fail("content must be a string")

    # Reject .ipynb files -- redirect to NotebookEdit
    if file_path.lower().endswith(".ipynb"):
        return ValidationResult.fail(
            "Cannot write to Jupyter notebook (.ipynb) files with the Write tool. "
            "Use the NotebookEdit tool instead to modify notebook cells.",
            error_code=0,
        )

    # Resolve the path for filesystem checks
    try:
        path = context.ensure_allowed_path(file_path)
    except ToolPermissionError:
        # Permission errors are handled later by check_permissions / call
        return ValidationResult.ok()

    if not path.exists():
        # New file -- no staleness concern
        return ValidationResult.ok()

    status = context.file_read_status(path)
    if status == "not_read" or status == "partial":
        return ValidationResult.fail(
            "File has not been read yet. Read it first before writing to it.",
            error_code=2,
        )
    if status == "modified":
        return ValidationResult.fail(
            "File has been modified since read, either by the user or by a linter. "
            "Read it again before attempting to write it.",
            error_code=3,
        )
    return ValidationResult.ok()


# ---------------------------------------------------------------------------
# check_permissions
# ---------------------------------------------------------------------------

def _check_permissions(tool_input: dict[str, Any], context: ToolContext) -> PermissionResult:
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str):
        return PermissionPassthroughResult()

    # Path is already expanded by backfill_observable_input
    try:
        path = context.ensure_allowed_path(file_path)
    except ToolPermissionError:
        return PermissionPassthroughResult()

    if path.suffix.lower() in {".md", ".markdown"} and not context.allow_docs:
        return PermissionAskDecision(
            message="Writing documentation files is blocked unless allow_docs is enabled",
        )
    return PermissionPassthroughResult()


# ---------------------------------------------------------------------------
# map_result_to_api  (ported from TS mapToolResultToToolResultBlockParam)
# ---------------------------------------------------------------------------

def _map_result_to_api(output: Any, tool_use_id: str) -> dict[str, Any]:
    """Return concise human-readable messages instead of raw structured output."""
    if isinstance(output, dict):
        op_type = output.get("type")
        file_path = output.get("filePath", "")
        if op_type == "create":
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"File created successfully at: {file_path}",
            }
        if op_type == "update":
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"The file {file_path} has been updated successfully.",
            }

    # Fallback for unexpected shapes
    import json as _json
    if isinstance(output, str):
        content: str = output
    elif isinstance(output, dict):
        content = _json.dumps(output, ensure_ascii=False)
    else:
        content = str(output)
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }


# ---------------------------------------------------------------------------
# call
# ---------------------------------------------------------------------------

def _write_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    file_path = tool_input["file_path"]
    content = tool_input["content"]
    if not isinstance(file_path, str):
        raise ToolInputError("file_path must be a string")
    if not isinstance(content, str):
        raise ToolInputError("content must be a string")

    # Path is already expanded by backfill_observable_input
    path = context.ensure_allowed_path(file_path)

    original_file: str | None = None
    if path.exists():
        # validate_input already performed the rich staleness check.
        # Double-check here as a safety net in case validate_input was
        # bypassed or the file changed between validation and call.
        if not context.was_file_read_and_unchanged(path):
            raise ToolInputError(
                "File has been modified since read, either by the user or by a "
                "linter. Read it again before attempting to write it."
            )
        original_file = path.read_text(encoding="utf-8", errors="replace")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    context.mark_file_read(path)
    before_lines = (original_file or "").splitlines(keepends=True)
    after_lines = content.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=str(path),
            tofile=str(path),
            n=3,
            lineterm="",
        )
    )
    hunks = unified_diff_hunks(diff_lines)
    return ToolResult(
        name="Write",
        output={
            "type": "update" if original_file is not None else "create",
            "filePath": str(path),
            "content": content,
            "structuredPatch": hunks,
            "originalFile": original_file,
        },
    )


# ---------------------------------------------------------------------------
# Classifier helper
# ---------------------------------------------------------------------------

def _write_classifier_input(input_data: dict) -> str:
    fp = (input_data or {}).get("file_path", "")
    content = (input_data or {}).get("content", "")
    return f"{fp}: new content ({len(content)} chars)"


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

WriteTool: Tool = build_tool(
    name="Write",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to write (must be absolute, not relative)",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
        "required": ["file_path", "content"],
    },
    call=_write_call,
    prompt=_WRITE_TOOL_PROMPT,
    description="Write a file to the local filesystem.",
    map_result_to_api=_map_result_to_api,
    max_result_size_chars=100_000,
    strict=True,
    is_read_only=lambda _input: False,
    is_destructive=lambda _input: True,
    is_concurrency_safe=lambda _input: False,
    check_permissions=_check_permissions,
    validate_input=_validate_input,
    backfill_observable_input=_backfill_observable_input,
    get_path=lambda input_data: input_data.get("file_path", ""),
    user_facing_name=lambda input_data: f"Write: {(input_data or {}).get('file_path', '')}" if input_data else "Write",
    search_hint="write create file save",
    to_auto_classifier_input=_write_classifier_input,
    get_activity_description=lambda input_data: f"Writing {(input_data or {}).get('file_path', '')}" if input_data else None,
)
