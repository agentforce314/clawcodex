"""Glob tool — ripgrep-backed file pattern matching with stdlib fallback."""

from __future__ import annotations

import glob as globlib
import os
from pathlib import Path
from typing import Any

from ..build_tool import SearchOrReadResult, Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult
from ..utils.path_utils import suggest_path_under_cwd, to_relative_path
from ..utils.ripgrep import (
    RipgrepTimeoutError,
    RipgrepUnavailableError,
    find_ripgrep,
    ripgrep,
)


_VCS_DIRS = {".git", ".svn", ".hg", ".bzr", ".jj", ".sl"}


def _glob_via_ripgrep(
    pattern: str, base_dir: str
) -> list[str]:
    """Use ripgrep --files --glob for fast file discovery."""
    args = ["--files", "--hidden", "--glob", pattern]
    for d in _VCS_DIRS:
        args.extend(["--glob", f"!{d}"])
    return ripgrep(args, base_dir)


def _glob_fallback(pattern: str, base_dir: Path) -> list[str]:
    """Stdlib glob.glob fallback when ripgrep is unavailable."""
    full_pattern = str(base_dir / pattern)
    matches = [Path(p) for p in globlib.glob(full_pattern, recursive=True)]
    return [str(p) for p in matches if p.is_file()]


def _sort_by_mtime(files: list[str]) -> list[str]:
    """Sort files by modification time (most recent first), filename as tiebreaker."""
    stats: list[tuple[str, float]] = []
    for f in files:
        try:
            mtime = os.path.getmtime(f)
        except OSError:
            mtime = 0.0
        stats.append((f, mtime))
    stats.sort(key=lambda x: (-x[1], x[0]))
    return [f for f, _ in stats]


def _map_result_to_api(result: Any, tool_use_id: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(result)}

    filenames = result.get("filenames", [])
    truncated = result.get("truncated", False)

    if not filenames:
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": "No files found"}

    content = "\n".join(filenames)
    if truncated:
        content += "\n\n(Results are truncated. Consider using a more specific path or pattern.)"
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


def _glob_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    pattern = tool_input["pattern"]
    base = tool_input.get("path")
    limit = tool_input.get("limit", 100)
    if not isinstance(pattern, str) or not pattern:
        raise ToolInputError("pattern must be a non-empty string")
    if base is not None and (not isinstance(base, str) or not base):
        raise ToolInputError("path must be a non-empty string when provided")
    if not isinstance(limit, int) or limit < 1 or limit > 10_000:
        raise ToolInputError("limit must be an integer between 1 and 10000")

    base_dir = context.cwd if base is None else context.ensure_allowed_path(base)
    if not base_dir.exists():
        hint = suggest_path_under_cwd(str(base_dir), context.cwd) if base else None
        msg = f"path does not exist: {base_dir}"
        if hint:
            msg += f'. Did you mean "{hint}"?'
        raise ToolInputError(msg)
    if not base_dir.is_dir():
        raise ToolInputError(f"path is not a directory: {base_dir}")

    cwd = context.cwd
    use_ripgrep = find_ripgrep() is not None

    if use_ripgrep:
        try:
            files = _glob_via_ripgrep(pattern, str(base_dir))
        except RipgrepTimeoutError as e:
            files = e.partial_results
        except (RipgrepUnavailableError, RuntimeError):
            use_ripgrep = False

    if not use_ripgrep:
        files = _glob_fallback(pattern, base_dir)

    files = _sort_by_mtime(files)
    truncated = len(files) > limit
    files = files[:limit]

    rel_files = [to_relative_path(f, cwd) for f in files]

    return ToolResult(
        name="Glob",
        output={
            "filenames": rel_files,
            "numFiles": len(rel_files),
            "truncated": truncated,
        },
    )


_GLOB_PROMPT = """- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead"""


GlobTool: Tool = build_tool(
    name="Glob",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in. If not specified, the current working directory will be used. IMPORTANT: Omit this field to use the default directory. DO NOT enter \"undefined\" or \"null\" - simply omit it for the default behavior. Must be a valid directory path if provided.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return. Defaults to 100.",
            },
        },
        "required": ["pattern"],
    },
    call=_glob_call,
    prompt=_GLOB_PROMPT,
    description="Fast file pattern matching tool.",
    map_result_to_api=_map_result_to_api,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    search_hint="glob find files pattern match",
    to_auto_classifier_input=lambda input_data: (input_data or {}).get("pattern", ""),
    is_search_or_read_command=lambda _input: SearchOrReadResult(is_search=True),
    get_activity_description=lambda input_data: f"Searching for {(input_data or {}).get('pattern', '')}" if input_data else None,
)
