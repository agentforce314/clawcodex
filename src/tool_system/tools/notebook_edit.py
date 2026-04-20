"""NotebookEdit tool — edit cells in Jupyter notebooks (.ipynb files).

Supports three edit modes:
- replace: Replace the source of an existing cell
- insert: Insert a new cell after a specified cell (or at the beginning)
- delete: Delete a cell
"""

from __future__ import annotations

import json
import random
import re
import string
from pathlib import Path
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError, ToolPermissionError
from ..protocol import ToolResult
from src.permissions.types import (
    PermissionAskDecision,
    PermissionPassthroughResult,
    PermissionResult,
)


IPYNB_INDENT = 1
_CELL_ID_RE = re.compile(r"^cell-(\d+)$")


def _parse_cell_id(cell_id: str) -> int | None:
    m = _CELL_ID_RE.match(cell_id)
    return int(m.group(1)) if m else None


def _generate_cell_id() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def _find_cell_index(cells: list[dict], cell_id: str) -> int | None:
    for i, cell in enumerate(cells):
        if cell.get("id") == cell_id:
            return i
    idx = _parse_cell_id(cell_id)
    if idx is not None and 0 <= idx < len(cells):
        return idx
    return None


def _make_cell(
    cell_type: str,
    source: str,
    language: str,
    nb_version: int,
) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "source": source,
        "metadata": {},
    }
    if nb_version >= 5:
        cell["id"] = _generate_cell_id()
    if cell_type == "code":
        cell["outputs"] = []
        cell["execution_count"] = None
    return cell


def _check_permissions(tool_input: dict[str, Any], context: ToolContext) -> PermissionResult:
    notebook_path = tool_input.get("notebook_path")
    if not isinstance(notebook_path, str):
        return PermissionPassthroughResult()
    try:
        context.ensure_allowed_path(notebook_path)
    except ToolPermissionError:
        return PermissionPassthroughResult()
    return PermissionPassthroughResult()


def _map_result_to_api(result: Any, tool_use_id: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(result)}

    if "error" in result:
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": result["error"],
            "is_error": True,
        }

    edit_mode = result.get("edit_mode", "replace")
    cell_id = result.get("cell_id", "")
    new_source = result.get("new_source", "")

    if edit_mode == "delete":
        msg = f"Deleted cell {cell_id}"
    elif edit_mode == "insert":
        preview = new_source[:100] + "..." if len(new_source) > 100 else new_source
        msg = f"Inserted cell {cell_id} with {preview}"
    else:
        preview = new_source[:100] + "..." if len(new_source) > 100 else new_source
        msg = f"Updated cell {cell_id} with {preview}"

    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": msg}


def _notebook_edit_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    notebook_path = tool_input.get("notebook_path", "")
    new_source = tool_input.get("new_source", "")
    cell_id = tool_input.get("cell_id")
    cell_type = tool_input.get("cell_type")
    edit_mode = tool_input.get("edit_mode", "replace")

    if not isinstance(notebook_path, str) or not notebook_path:
        raise ToolInputError("notebook_path must be a non-empty string")
    if not isinstance(new_source, str):
        raise ToolInputError("new_source must be a string")

    path = context.ensure_allowed_path(notebook_path)

    if path.suffix.lower() != ".ipynb":
        raise ToolInputError(
            f"Not a notebook file: {path}. Use the Edit tool for non-.ipynb files."
        )

    if edit_mode not in ("replace", "insert", "delete"):
        raise ToolInputError(
            f"Invalid edit_mode: {edit_mode}. Must be 'replace', 'insert', or 'delete'."
        )

    if edit_mode == "insert" and not cell_type:
        raise ToolInputError(
            "cell_type is required when edit_mode is 'insert'. "
            "Specify 'code' or 'markdown'."
        )

    if cell_type and cell_type not in ("code", "markdown"):
        raise ToolInputError(
            f"Invalid cell_type: {cell_type}. Must be 'code' or 'markdown'."
        )

    if not path.exists():
        raise ToolInputError(f"Notebook file does not exist: {path}")

    if not context.was_file_read_and_unchanged(path):
        raise ToolInputError(
            "Notebook must be read first and be unchanged since last read"
        )

    original_content = path.read_text(encoding="utf-8", errors="replace")

    try:
        notebook = json.loads(original_content)
    except json.JSONDecodeError as e:
        raise ToolInputError(f"Invalid notebook JSON: {e}")

    cells = notebook.get("cells", [])

    nbformat_major = notebook.get("nbformat", 4)
    nbformat_minor = notebook.get("nbformat_minor", 0)
    nb_version = nbformat_major * 10 + nbformat_minor

    language = "python"
    lang_info = notebook.get("metadata", {}).get("language_info", {})
    if isinstance(lang_info, dict) and "name" in lang_info:
        language = lang_info["name"]

    # Resolve cell index
    target_index: int | None = None
    resolved_cell_id = cell_id

    if cell_id is not None:
        target_index = _find_cell_index(cells, cell_id)
        if target_index is None and edit_mode != "insert":
            raise ToolInputError(
                f"Cell not found: {cell_id}. "
                f"Available cells: {len(cells)} (use cell-0 through cell-{len(cells) - 1})"
            )
        if target_index is None and edit_mode == "insert":
            raise ToolInputError(
                f"Cannot insert after non-existent cell: {cell_id}"
            )
    else:
        target_index = 0

    if edit_mode == "delete":
        if target_index is None or target_index >= len(cells):
            raise ToolInputError(f"Cell index out of bounds: {target_index}")
        del cells[target_index]
        resolved_cell_id = cell_id or f"cell-{target_index}"

    elif edit_mode == "insert":
        insert_index = (target_index + 1) if cell_id is not None else 0
        new_cell = _make_cell(
            cell_type=cell_type or "code",
            source=new_source,
            language=language,
            nb_version=nb_version,
        )
        cells.insert(insert_index, new_cell)
        resolved_cell_id = new_cell.get("id", f"cell-{insert_index}")

    else:
        if target_index is not None and target_index == len(cells):
            new_cell = _make_cell(
                cell_type=cell_type or "code",
                source=new_source,
                language=language,
                nb_version=nb_version,
            )
            cells.append(new_cell)
            edit_mode = "insert"
            resolved_cell_id = new_cell.get("id", f"cell-{target_index}")
        elif target_index is not None and target_index < len(cells):
            cell = cells[target_index]
            cell["source"] = new_source
            if cell_type:
                cell["cell_type"] = cell_type
            if cell.get("cell_type") == "code":
                cell["outputs"] = []
                cell["execution_count"] = None
            resolved_cell_id = cell.get("id", cell_id or f"cell-{target_index}")
        else:
            raise ToolInputError(f"Cell index out of bounds: {target_index}")

    notebook["cells"] = cells
    updated_content = json.dumps(notebook, indent=IPYNB_INDENT, ensure_ascii=False) + "\n"

    path.write_text(updated_content, encoding="utf-8")
    context.mark_file_read(path)

    return ToolResult(
        name="NotebookEdit",
        output={
            "new_source": new_source,
            "cell_id": resolved_cell_id,
            "cell_type": cell_type or (cells[target_index]["cell_type"] if edit_mode == "replace" and target_index is not None and target_index < len(cells) else "code"),
            "language": language,
            "edit_mode": edit_mode,
            "notebook_path": str(path),
            "original_file": original_content,
            "updated_file": updated_content,
        },
    )


_NOTEBOOK_EDIT_PROMPT = """Completely replaces the contents of a specific cell in a Jupyter notebook (.ipynb file) with new source. Jupyter notebooks are interactive documents that combine code, text, and visualizations, commonly used for data analysis and scientific computing. The notebook_path parameter must be an absolute path, not a relative path. The cell_number is 0-indexed. Use edit_mode=insert to add a new cell at the index specified by cell_number. Use edit_mode=delete to delete the cell at the index specified by cell_number."""


NotebookEditTool: Tool = build_tool(
    name="NotebookEdit",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "notebook_path": {
                "type": "string",
                "description": "The absolute path to the Jupyter notebook file to edit (must be absolute, not relative)",
            },
            "cell_id": {
                "type": "string",
                "description": (
                    "The ID of the cell to edit. When inserting a new cell, "
                    "the new cell will be inserted after the cell with this ID, "
                    "or at the beginning if not specified."
                ),
            },
            "new_source": {
                "type": "string",
                "description": "The new source for the cell",
            },
            "cell_type": {
                "type": "string",
                "enum": ["code", "markdown"],
                "description": (
                    "The type of the cell (code or markdown). If not specified, "
                    "it defaults to the current cell type. "
                    "If using edit_mode=insert, this is required."
                ),
            },
            "edit_mode": {
                "type": "string",
                "enum": ["replace", "insert", "delete"],
                "description": (
                    "The type of edit to make (replace, insert, delete). Defaults to replace."
                ),
            },
        },
        "required": ["notebook_path", "new_source"],
    },
    call=_notebook_edit_call,
    prompt=_NOTEBOOK_EDIT_PROMPT,
    description="Replace the contents of a specific cell in a Jupyter notebook.",
    map_result_to_api=_map_result_to_api,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: False,
    is_destructive=lambda _input: True,
    is_concurrency_safe=lambda _input: False,
    check_permissions=_check_permissions,
    get_path=lambda input_data: (input_data or {}).get("notebook_path", ""),
    user_facing_name=lambda input_data: f"Edit Notebook: {(input_data or {}).get('notebook_path', '')}" if input_data else "Edit Notebook",
    search_hint="edit Jupyter notebook cells ipynb",
    to_auto_classifier_input=lambda input_data: f"{(input_data or {}).get('notebook_path', '')} {(input_data or {}).get('edit_mode', 'replace')}: {(input_data or {}).get('new_source', '')}",
    get_activity_description=lambda input_data: f"Editing notebook {(input_data or {}).get('notebook_path', '')}" if input_data else None,
)
