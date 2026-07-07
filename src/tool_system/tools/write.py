from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from ..build_tool import Tool, ValidationResult, build_tool
from ..context import ToolContext
from ..errors import ToolInputError, ToolPermissionError
from ..protocol import ToolResult
from ..diff_utils import (
    convert_leading_tabs_to_spaces,
    record_patch_line_totals,
    unified_diff_hunks,
)
from src.permissions.types import (
    PermissionAllowDecision,
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
    via ``~`` or relative paths. ch06 round-4 (critic m3): delegates to the
    shared Read/Edit helper so the three file tools can't drift."""
    from .read import _backfill_read_edit_path

    _backfill_read_edit_path(tool_input)


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

    # Resolve the path for filesystem checks. Auto-memory paths are
    # outside the workspace allowlist so ensure_allowed_path raises;
    # short-circuit to the expanded path so the staleness check below
    # still runs (otherwise auto-memory writes would silently bypass
    # the "read before write" invariant).
    if _is_auto_memory_write(file_path):
        path = Path(_expand_path(file_path))
    else:
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

def _is_auto_memory_write(file_path: str) -> bool:
    """Whether *file_path* is a write inside the auto-memory directory.

    Mirrors the TS write-tool carve-out at ``filesystem.ts``: writes to
    paths matched by ``isAutoMemPath()`` are allowed without further
    permission gating, *but only when ``hasAutoMemPathOverride()`` is
    false*. The override case means the SDK caller has wired memory
    themselves and has its own permission story (TS comment at
    ``paths.ts:262-272``).

    Note: TS does **not** gate this on ``isAutoMemoryEnabled()`` — the
    carve-out is purely path-shape, not behavior-flag. We mirror that:
    if a process has memory writes pending and then auto-memory gets
    disabled mid-session, the in-flight writes still resolve.
    """
    try:
        from src.memdir import (
            has_auto_mem_path_override,
            is_auto_mem_path,
        )
    except Exception:
        return False
    if has_auto_mem_path_override():
        return False
    try:
        return is_auto_mem_path(_expand_path(file_path))
    except Exception:
        return False


def _check_permissions(tool_input: dict[str, Any], context: ToolContext) -> PermissionResult:
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str):
        return PermissionPassthroughResult()

    # Memory carve-out: writes inside the auto-memory directory bypass
    # the workspace allowlist. Without this, the model would prompt the
    # user on every "save a memory" attempt.
    if _is_auto_memory_write(file_path):
        return PermissionPassthroughResult()

    # NB: no docs gate. The port used to raise an explicit ask for
    # ``.md``/``.markdown`` writes unless ``allow_docs`` — the original
    # Claude Code has no such permission gate, and being an explicit ask it
    # was structurally un-grantable (no session option, immune to
    # acceptEdits), so every markdown write re-prompted forever. Markdown
    # now flows like any other write.
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

    # call() receives the MODEL-ORIGINAL path (backfill is the
    # hooks/permissions audience only); both branches below self-expand.
    if _is_auto_memory_write(file_path):
        # Memory dir is outside the workspace allowlist; bypass it. The
        # auto-memory subsystem owns this path namespace and its own
        # safety rails (sanitize_path, NFC, trailing-sep prefix check
        # in is_auto_mem_path).
        path = Path(_expand_path(file_path))
    else:
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
    before_lines = convert_leading_tabs_to_spaces(original_file or "").splitlines(keepends=True)
    after_lines = convert_leading_tabs_to_spaces(content).splitlines(keepends=True)
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
    # New file: the original's write-create path passes an EXPLICITLY empty
    # patch (FileWriteTool.ts:408, countLinesChanged([], content)), so the
    # split(/\r?\n/) special case runs and a trailing newline counts one
    # extra (empty) segment. Passing the difflib hunks here instead would
    # undercount by that segment.
    record_patch_line_totals(
        [] if original_file is None else hunks,
        content if original_file is None else None,
    )
    return ToolResult(
        name="Write",
        output={
            "type": "update" if original_file is not None else "create",
            # MODEL-ORIGINAL path, not the resolved one: tool results embed
            # input fields verbatim (TS FileWriteTool.ts:377/:400 uses
            # file_path for data.filePath; fullFilePath is fs/logging only).
            "filePath": file_path,
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
