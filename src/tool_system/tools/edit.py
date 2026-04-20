"""FileEditTool -- exact string replacements with quote normalization and desanitization."""

from __future__ import annotations

import difflib
import os
import unicodedata
from pathlib import Path
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..diff_utils import unified_diff_hunks
from ..errors import ToolInputError, ToolPermissionError
from ..protocol import ToolResult
from src.permissions.types import (
    PermissionAskDecision,
    PermissionPassthroughResult,
    PermissionResult,
)


# -- Quote normalization -------------------------------------------------------

_LEFT_SINGLE_CURLY = "\u2018"
_RIGHT_SINGLE_CURLY = "\u2019"
_LEFT_DOUBLE_CURLY = "\u201c"
_RIGHT_DOUBLE_CURLY = "\u201d"


def _normalize_quotes(s: str) -> str:
    return (
        s.replace(_LEFT_SINGLE_CURLY, "'")
        .replace(_RIGHT_SINGLE_CURLY, "'")
        .replace(_LEFT_DOUBLE_CURLY, '"')
        .replace(_RIGHT_DOUBLE_CURLY, '"')
    )


def _is_opening_context(chars: list[str], index: int) -> bool:
    if index == 0:
        return True
    prev = chars[index - 1]
    return prev in (" ", "\t", "\n", "\r", "(", "[", "{", "\u2014", "\u2013")


def _apply_curly_double_quotes(s: str) -> str:
    chars = list(s)
    result: list[str] = []
    for i, ch in enumerate(chars):
        if ch == '"':
            result.append(_LEFT_DOUBLE_CURLY if _is_opening_context(chars, i) else _RIGHT_DOUBLE_CURLY)
        else:
            result.append(ch)
    return "".join(result)


def _apply_curly_single_quotes(s: str) -> str:
    chars = list(s)
    result: list[str] = []
    for i, ch in enumerate(chars):
        if ch == "'":
            prev = chars[i - 1] if i > 0 else ""
            nxt = chars[i + 1] if i < len(chars) - 1 else ""
            prev_letter = bool(unicodedata.category(prev).startswith("L")) if prev else False
            next_letter = bool(unicodedata.category(nxt).startswith("L")) if nxt else False
            if prev_letter and next_letter:
                result.append(_RIGHT_SINGLE_CURLY)
            else:
                result.append(_LEFT_SINGLE_CURLY if _is_opening_context(chars, i) else _RIGHT_SINGLE_CURLY)
        else:
            result.append(ch)
    return "".join(result)


def _find_actual_string(file_content: str, search_string: str) -> str | None:
    if search_string in file_content:
        return search_string
    normalized_search = _normalize_quotes(search_string)
    normalized_file = _normalize_quotes(file_content)
    idx = normalized_file.find(normalized_search)
    if idx != -1:
        return file_content[idx : idx + len(search_string)]
    return None


def _preserve_quote_style(old_string: str, actual_old_string: str, new_string: str) -> str:
    if old_string == actual_old_string:
        return new_string
    has_double = _LEFT_DOUBLE_CURLY in actual_old_string or _RIGHT_DOUBLE_CURLY in actual_old_string
    has_single = _LEFT_SINGLE_CURLY in actual_old_string or _RIGHT_SINGLE_CURLY in actual_old_string
    if not has_double and not has_single:
        return new_string
    result = new_string
    if has_double:
        result = _apply_curly_double_quotes(result)
    if has_single:
        result = _apply_curly_single_quotes(result)
    return result


# -- Desanitization ------------------------------------------------------------

_DESANITIZATIONS: list[tuple[str, str]] = [
    ("<fnr>", "<function_results>"),
    ("<n>", "<name>"),
    ("</n>", "</name>"),
    ("<o>", "<output>"),
    ("</o>", "</output>"),
    ("<e>", "<error>"),
    ("</e>", "</error>"),
    ("<s>", "<system>"),
    ("</s>", "</system>"),
    ("<r>", "<result>"),
    ("</r>", "</result>"),
    ("< " + "META_START >", "<" + "META_START>"),
    ("< " + "META_END >", "<" + "META_END>"),
    ("< " + "EOT >", "<" + "EOT>"),
    ("< " + "META >", "<" + "META>"),
    ("< " + "SOS >", "<" + "SOS>"),
    ("\n\nH:", "\n\nHuman:"),
    ("\n\nA:", "\n\nAssistant:"),
]


def _desanitize_match_string(match_string: str) -> tuple[str, list[tuple[str, str]]]:
    result = match_string
    applied: list[tuple[str, str]] = []
    for from_str, to_str in _DESANITIZATIONS:
        before = result
        result = result.replace(from_str, to_str)
        if before != result:
            applied.append((from_str, to_str))
    return result, applied


# -- Smart edit application ----------------------------------------------------

def _apply_edit(original: str, old_string: str, new_string: str, replace_all: bool) -> str:
    if new_string != "":
        if replace_all:
            return original.replace(old_string, new_string)
        idx = original.index(old_string)
        return original[:idx] + new_string + original[idx + len(old_string):]

    strip_trailing_newline = (
        not old_string.endswith("\n") and (old_string + "\n") in original
    )
    target = old_string + "\n" if strip_trailing_newline else old_string
    if replace_all:
        return original.replace(target, "")
    idx = original.index(target)
    return original[:idx] + original[idx + len(target):]


# -- Trailing whitespace stripping ---------------------------------------------

def _strip_trailing_whitespace(s: str) -> str:
    import re
    return re.sub(r"[ \t]+(\r\n|\n|\r)", r"\1", s)


# -- Validation ----------------------------------------------------------------

_MAX_FILE_SIZE = 1 << 30  # 1 GiB


def _find_similar_file(file_path: str, cwd: Path) -> str | None:
    name = os.path.basename(file_path)
    if not name:
        return None
    for root, _dirs, files in os.walk(cwd):
        if any(d in root for d in (".git", "node_modules", "__pycache__")):
            continue
        for f in files:
            if f == name:
                return os.path.join(root, f)
        depth = root.replace(str(cwd), "").count(os.sep)
        if depth > 3:
            _dirs.clear()
    return None


# -- Permissions ---------------------------------------------------------------

def _check_permissions(tool_input: dict[str, Any], context: ToolContext) -> PermissionResult:
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str):
        return PermissionPassthroughResult()
    try:
        path = context.ensure_allowed_path(file_path)
    except ToolPermissionError:
        return PermissionPassthroughResult()
    if path.suffix.lower() in {".md", ".markdown"} and not context.allow_docs:
        return PermissionAskDecision(
            message="Editing documentation files is blocked unless allow_docs is enabled",
        )
    return PermissionPassthroughResult()


# -- Result formatting ---------------------------------------------------------

def _map_result_to_api(result: Any, tool_use_id: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(result)}
    file_path = result.get("filePath", "")
    edit_type = result.get("type", "update")
    if edit_type == "create":
        msg = f"The file {file_path} has been created successfully."
    else:
        msg = f"The file {file_path} has been updated successfully."
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": msg}


# -- Main call -----------------------------------------------------------------

def _edit_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    file_path = tool_input["file_path"]
    old_string = tool_input["old_string"]
    new_string = tool_input["new_string"]
    replace_all = bool(tool_input.get("replace_all", False))

    if not isinstance(file_path, str) or not file_path:
        raise ToolInputError("file_path must be a non-empty string")
    if not isinstance(old_string, str):
        raise ToolInputError("old_string must be a string")
    if not isinstance(new_string, str):
        raise ToolInputError("new_string must be a string")
    if old_string == new_string:
        raise ToolInputError("old_string and new_string must differ")

    path = context.ensure_allowed_path(file_path)

    # Reject .ipynb files
    if path.suffix.lower() == ".ipynb":
        raise ToolInputError("Cannot edit .ipynb files with Edit tool. Use the NotebookEdit tool instead.")

    # File creation (empty old_string)
    if old_string == "":
        if path.exists():
            raise ToolInputError("old_string is empty but file already exists -- use non-empty old_string to edit")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_string, encoding="utf-8")
        context.mark_file_read(path)
        return ToolResult(
            name="Edit",
            output={
                "type": "create",
                "filePath": str(path),
                "content": new_string,
                "structuredPatch": [],
            },
        )

    # File existence and type checks
    if not path.exists():
        hint = _find_similar_file(file_path, context.cwd)
        msg = f"file does not exist: {path}"
        if hint:
            msg += f'. Did you mean "{hint}"?'
        raise ToolInputError(msg)
    if not path.is_file():
        raise ToolInputError(f"path is not a file: {path}")

    # File size guard
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size > _MAX_FILE_SIZE:
        raise ToolInputError(f"file is too large ({size} bytes, max {_MAX_FILE_SIZE})")

    # Staleness check
    if not context.was_file_read_and_unchanged(path):
        raise ToolInputError("file must be read first and be unchanged since last read")

    original = path.read_text(encoding="utf-8", errors="replace")
    is_markdown = path.suffix.lower() in (".md", ".mdx")

    # Strip trailing whitespace on new_string (except markdown)
    if not is_markdown and new_string:
        new_string = _strip_trailing_whitespace(new_string)

    # Step 1: Try exact match
    actual_old = _find_actual_string(original, old_string)

    # Step 2: Try desanitization if no match
    desanitized_applied: list[tuple[str, str]] = []
    if actual_old is None:
        desan_old, desanitized_applied = _desanitize_match_string(old_string)
        if desan_old in original:
            actual_old = desan_old
            # Apply same desanitization to new_string
            for from_str, to_str in desanitized_applied:
                new_string = new_string.replace(from_str, to_str)

    if actual_old is None:
        raise ToolInputError("old_string not found in file")

    # Preserve quote style if normalization was used
    if actual_old != old_string and not desanitized_applied:
        new_string = _preserve_quote_style(old_string, actual_old, new_string)

    count = original.count(actual_old)
    if count > 1 and not replace_all:
        raise ToolInputError(f"old_string found {count} times -- set replace_all=true or provide more context")

    updated = _apply_edit(original, actual_old, new_string, replace_all)

    path.write_text(updated, encoding="utf-8")
    context.mark_file_read(path)

    before_lines = original.splitlines(keepends=True)
    after_lines = updated.splitlines(keepends=True)
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
        name="Edit",
        output={
            "type": "update",
            "filePath": str(path),
            "content": updated,
            "structuredPatch": hunks,
        },
    )


# -- Prompt --------------------------------------------------------------------

_EDIT_PROMPT = """Performs exact string replacements in files.

Usage:
- You must use your `Read` tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file.
- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: line number + tab. Everything after that is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.
- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance."""


def _edit_classifier_input(input_data: dict) -> str:
    fp = (input_data or {}).get("file_path", "")
    old = (input_data or {}).get("old_string", "")
    new = (input_data or {}).get("new_string", "")
    return f"{fp}: {old!r} -> {new!r}"


# -- Tool definition -----------------------------------------------------------

EditTool: Tool = build_tool(
    name="Edit",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to modify",
            },
            "old_string": {
                "type": "string",
                "description": "The text to replace",
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace it with (must be different from old_string)",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences of old_string (default false)",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    },
    call=_edit_call,
    prompt=_EDIT_PROMPT,
    description="Performs exact string replacements in files.",
    map_result_to_api=_map_result_to_api,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: False,
    is_destructive=lambda _input: True,
    is_concurrency_safe=lambda _input: False,
    check_permissions=_check_permissions,
    get_path=lambda input_data: input_data.get("file_path", ""),
    user_facing_name=lambda input_data: f"Edit: {(input_data or {}).get('file_path', '')}" if input_data else "Edit",
    search_hint="edit modify replace change file",
    to_auto_classifier_input=_edit_classifier_input,
    get_activity_description=lambda input_data: f"Editing {(input_data or {}).get('file_path', '')}" if input_data else None,
)
