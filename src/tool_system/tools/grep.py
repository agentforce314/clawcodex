"""Grep tool — ripgrep-backed search with Python fallback."""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

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
_MAX_COLUMN_LENGTH = 500


# -- Semantic coercion helpers ------------------------------------------------

def _semantic_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() not in ("false", "0", "no", "")
    return bool(value)


def _semantic_int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


# -- Glob pattern splitting ---------------------------------------------------

def _split_glob_patterns(glob: str) -> list[str]:
    """Split glob parameter into individual patterns, preserving brace expressions."""
    patterns: list[str] = []
    for raw in glob.split():
        if "{" in raw and "}" in raw:
            patterns.append(raw)
        else:
            patterns.extend(p for p in raw.split(",") if p)
    return patterns


# -- Python fallback search ---------------------------------------------------

def _iter_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _VCS_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def _matches_glob(path: Path, pattern: str) -> bool:
    return fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(str(path), pattern)


def _matches_type(path: Path, type_name: str) -> bool:
    ext = path.suffix.lower().lstrip(".")
    return ext == type_name.lower() if ext else False


def _truncate_line(line: str) -> str:
    if len(line) > _MAX_COLUMN_LENGTH:
        return line[:_MAX_COLUMN_LENGTH] + " [truncated]"
    return line


def _grep_fallback_python(
    pattern: str,
    base_path: Path,
    *,
    glob_pattern: str | None,
    type_name: str | None,
    output_mode: str,
    case_insensitive: bool,
    multiline: bool,
    show_line_numbers: bool,
    context_before: int,
    context_after: int,
    cwd: Path,
) -> dict[str, Any]:
    """Pure-Python fallback when ripgrep is not available."""
    flags = re.MULTILINE
    if case_insensitive:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.DOTALL
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        raise ToolInputError(f"invalid regex: {e}") from e

    files_to_search: list[Path] = []
    if base_path.is_file():
        files_to_search = [base_path]
    else:
        files_to_search = [p for p in _iter_files(base_path) if p.is_file()]

    if glob_pattern:
        patterns = _split_glob_patterns(glob_pattern)
        files_to_search = [
            p for p in files_to_search
            if any(_matches_glob(p, pat) for pat in patterns)
        ]
    if type_name:
        files_to_search = [p for p in files_to_search if _matches_type(p, type_name)]

    matched_files: list[Path] = []
    content_lines: list[str] = []
    total_matches = 0

    for file in files_to_search:
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if regex.search(text) is None:
            continue
        matched_files.append(file)

        if output_mode == "content":
            lines = text.splitlines()
            match_indices: list[int] = []
            for i, line in enumerate(lines):
                if regex.search(line) is not None:
                    match_indices.append(i)
                    total_matches += len(list(regex.finditer(line)))

            included: set[int] = set()
            for idx in match_indices:
                start = max(0, idx - context_before)
                end = min(len(lines), idx + context_after + 1)
                for j in range(start, end):
                    included.add(j)

            rel_path = to_relative_path(str(file), cwd)
            sorted_indices = sorted(included)
            prev = -2
            for i in sorted_indices:
                if prev >= 0 and i > prev + 1:
                    content_lines.append("--")
                line = _truncate_line(lines[i])
                line_no = i + 1
                if show_line_numbers:
                    content_lines.append(f"{rel_path}:{line_no}:{line}")
                else:
                    content_lines.append(f"{rel_path}:{line}")
                prev = i

        elif output_mode == "count":
            matches = len(list(regex.finditer(text)))
            total_matches += matches

    if output_mode == "content":
        return {
            "mode": "content",
            "content_lines": content_lines,
            "matched_files": matched_files,
            "total_matches": total_matches,
        }
    elif output_mode == "count":
        return {
            "mode": "count",
            "matched_files": matched_files,
            "total_matches": total_matches,
        }
    else:
        return {
            "mode": "files_with_matches",
            "matched_files": matched_files,
        }


# -- Ripgrep-based search -----------------------------------------------------

def _grep_via_ripgrep(
    pattern: str,
    base_path: str,
    *,
    glob_pattern: str | None,
    type_name: str | None,
    output_mode: str,
    case_insensitive: bool,
    multiline: bool,
    show_line_numbers: bool,
    context_before: int,
    context_after: int,
) -> list[str]:
    """Build ripgrep args and execute."""
    args = ["--hidden"]

    for d in _VCS_DIRS:
        args.extend(["--glob", f"!{d}"])

    args.extend(["--max-columns", str(_MAX_COLUMN_LENGTH)])

    if multiline:
        args.extend(["-U", "--multiline-dotall"])

    if case_insensitive:
        args.append("-i")

    if output_mode == "files_with_matches":
        args.append("-l")
    elif output_mode == "count":
        args.append("-c")

    if show_line_numbers and output_mode == "content":
        args.append("-n")

    if output_mode == "content":
        if context_after > 0 and context_before > 0 and context_after == context_before:
            args.extend(["-C", str(context_after)])
        else:
            if context_before > 0:
                args.extend(["-B", str(context_before)])
            if context_after > 0:
                args.extend(["-A", str(context_after)])

    if pattern.startswith("-"):
        args.extend(["-e", pattern])
    else:
        args.append(pattern)

    if type_name:
        args.extend(["--type", type_name])

    if glob_pattern:
        for pat in _split_glob_patterns(glob_pattern):
            args.extend(["--glob", pat])

    return ripgrep(args, base_path)


# -- Pagination ----------------------------------------------------------------

@dataclass(frozen=True)
class _Pagination:
    items: list[Any]
    applied_limit: int | None
    applied_offset: int


def _paginate(items: list[Any], *, head_limit: int | None, offset: int) -> _Pagination:
    if head_limit == 0:
        return _Pagination(items=items[offset:], applied_limit=None, applied_offset=offset)
    effective_limit = head_limit if head_limit is not None else 250
    sliced = items[offset : offset + effective_limit]
    truncated = len(items) - offset > effective_limit
    return _Pagination(
        items=sliced,
        applied_limit=effective_limit if truncated else None,
        applied_offset=offset,
    )


# -- Result formatting for API ------------------------------------------------

def _format_limit_info(applied_limit: int | None, applied_offset: int) -> str:
    parts = []
    if applied_limit is not None:
        parts.append(f"limit: {applied_limit}")
    if applied_offset > 0:
        parts.append(f"offset: {applied_offset}")
    return ", ".join(parts)


def _plural(n: int, word: str) -> str:
    return word if n == 1 else word + "s"


def _map_result_to_api(result: Any, tool_use_id: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(result)}

    mode = result.get("mode", "files_with_matches")
    applied_limit = result.get("appliedLimit")
    applied_offset = result.get("appliedOffset", 0)
    limit_info = _format_limit_info(applied_limit, applied_offset)

    if mode == "content":
        content = result.get("content", "No matches found")
        if limit_info:
            content = f"{content}\n\n[Showing results with pagination = {limit_info}]"
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}

    if mode == "count":
        raw = result.get("content", "No matches found")
        matches = result.get("numMatches", 0)
        files = result.get("numFiles", 0)
        suffix = f" with pagination = {limit_info}" if limit_info else ""
        summary = f"\n\nFound {matches} total {_plural(matches, 'occurrence')} across {files} {_plural(files, 'file')}.{suffix}"
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": raw + summary}

    # files_with_matches
    num_files = result.get("numFiles", 0)
    if num_files == 0:
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": "No files found"}
    filenames = result.get("filenames", [])
    header = f"Found {num_files} {_plural(num_files, 'file')}"
    if limit_info:
        header += f" {limit_info}"
    content = f"{header}\n" + "\n".join(filenames)
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


# -- Main tool call ------------------------------------------------------------

def _grep_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    pattern = tool_input["pattern"]
    if not isinstance(pattern, str) or pattern == "":
        raise ToolInputError("pattern must be a non-empty string")

    base = tool_input.get("path")
    glob_pattern = tool_input.get("glob")
    type_name = tool_input.get("type")
    output_mode = tool_input.get("output_mode", "files_with_matches")
    if output_mode not in {"content", "files_with_matches", "count"}:
        raise ToolInputError("invalid output_mode")

    head_limit = _semantic_int(tool_input.get("head_limit"))
    offset = _semantic_int(tool_input.get("offset"), default=0) or 0
    if head_limit is not None and head_limit < 0:
        raise ToolInputError("head_limit must be an integer >= 0")
    if offset < 0:
        raise ToolInputError("offset must be an integer >= 0")

    case_insensitive = _semantic_bool(tool_input.get("-i", False))
    multiline = _semantic_bool(tool_input.get("multiline", False))
    show_line_numbers = _semantic_bool(tool_input.get("-n", True)) if output_mode == "content" else False

    # Resolve context lines: context/-C take precedence over -B/-A
    ctx_val = _semantic_int(tool_input.get("context"))
    ctx_c = _semantic_int(tool_input.get("-C"))
    ctx_before = _semantic_int(tool_input.get("-B"), default=0) or 0
    ctx_after = _semantic_int(tool_input.get("-A"), default=0) or 0
    if ctx_val is not None:
        ctx_before = ctx_after = ctx_val
    elif ctx_c is not None:
        ctx_before = ctx_after = ctx_c

    base_path = context.cwd if base is None else context.ensure_allowed_path(base)
    if not base_path.exists():
        hint = suggest_path_under_cwd(str(base_path), context.cwd) if base else None
        msg = f"path does not exist: {base_path}"
        if hint:
            msg += f'. Did you mean "{hint}"?'
        raise ToolInputError(msg)

    cwd = context.cwd
    use_ripgrep = find_ripgrep() is not None

    if use_ripgrep:
        try:
            results = _grep_via_ripgrep(
                pattern,
                str(base_path),
                glob_pattern=glob_pattern,
                type_name=type_name,
                output_mode=output_mode,
                case_insensitive=case_insensitive,
                multiline=multiline,
                show_line_numbers=show_line_numbers,
                context_before=ctx_before,
                context_after=ctx_after,
            )
        except RipgrepTimeoutError as e:
            results = e.partial_results
        except RipgrepUnavailableError:
            use_ripgrep = False

    if not use_ripgrep:
        fb = _grep_fallback_python(
            pattern,
            base_path,
            glob_pattern=glob_pattern,
            type_name=type_name,
            output_mode=output_mode,
            case_insensitive=case_insensitive,
            multiline=multiline,
            show_line_numbers=show_line_numbers,
            context_before=ctx_before,
            context_after=ctx_after,
            cwd=cwd,
        )
        return _build_result_from_fallback(fb, head_limit=head_limit, offset=offset, cwd=cwd)

    return _build_result_from_ripgrep(
        results, output_mode=output_mode, head_limit=head_limit, offset=offset, cwd=cwd,
    )


def _build_result_from_ripgrep(
    results: list[str],
    *,
    output_mode: str,
    head_limit: int | None,
    offset: int,
    cwd: Path,
) -> ToolResult:
    if output_mode == "content":
        paged = _paginate(results, head_limit=head_limit, offset=offset)
        rel_lines = []
        for line in paged.items:
            colon = line.find(":")
            if colon > 0:
                file_path = line[:colon]
                rest = line[colon:]
                rel_lines.append(to_relative_path(file_path, cwd) + rest)
            else:
                rel_lines.append(line)
        content = "\n".join(rel_lines)
        output: dict[str, Any] = {
            "mode": "content",
            "numFiles": 0,
            "filenames": [],
            "content": content,
            "numLines": len(rel_lines),
            "appliedOffset": paged.applied_offset,
        }
        if paged.applied_limit is not None:
            output["appliedLimit"] = paged.applied_limit
        return ToolResult(name="Grep", output=output)

    if output_mode == "count":
        paged = _paginate(results, head_limit=head_limit, offset=offset)
        total_matches = 0
        file_count = 0
        count_lines = []
        for line in paged.items:
            colon = line.rfind(":")
            if colon > 0:
                file_path = line[:colon]
                count_str = line[colon + 1:]
                count_lines.append(to_relative_path(file_path, cwd) + ":" + count_str)
                try:
                    total_matches += int(count_str)
                    file_count += 1
                except ValueError:
                    pass
            else:
                count_lines.append(line)
        content = "\n".join(count_lines)
        output = {
            "mode": "count",
            "numFiles": file_count,
            "filenames": [],
            "content": content,
            "numMatches": total_matches,
            "appliedOffset": paged.applied_offset,
        }
        if paged.applied_limit is not None:
            output["appliedLimit"] = paged.applied_limit
        return ToolResult(name="Grep", output=output)

    # files_with_matches — sort by mtime
    file_stats: list[tuple[str, float]] = []
    for f in results:
        try:
            mtime = os.path.getmtime(f)
        except OSError:
            mtime = 0.0
        file_stats.append((f, mtime))

    file_stats.sort(key=lambda x: (-x[1], x[0]))
    sorted_files = [f for f, _ in file_stats]

    paged = _paginate(sorted_files, head_limit=head_limit, offset=offset)
    rel_files = [to_relative_path(f, cwd) for f in paged.items]

    output = {
        "mode": "files_with_matches",
        "numFiles": len(rel_files),
        "filenames": rel_files,
        "appliedOffset": paged.applied_offset,
    }
    if paged.applied_limit is not None:
        output["appliedLimit"] = paged.applied_limit
    return ToolResult(name="Grep", output=output)


def _build_result_from_fallback(
    fb: dict[str, Any],
    *,
    head_limit: int | None,
    offset: int,
    cwd: Path,
) -> ToolResult:
    mode = fb["mode"]

    if mode == "content":
        content_lines = fb["content_lines"]
        paged = _paginate(content_lines, head_limit=head_limit, offset=offset)
        matched_files = fb["matched_files"]
        output: dict[str, Any] = {
            "mode": "content",
            "numFiles": len(matched_files),
            "filenames": [to_relative_path(str(p), cwd) for p in matched_files],
            "content": "\n".join(paged.items),
            "numLines": len(paged.items),
            "appliedOffset": paged.applied_offset,
        }
        if paged.applied_limit is not None:
            output["appliedLimit"] = paged.applied_limit
        return ToolResult(name="Grep", output=output)

    if mode == "count":
        matched_files = fb["matched_files"]
        filenames = [to_relative_path(str(p), cwd) for p in matched_files]
        paged = _paginate(filenames, head_limit=head_limit, offset=offset)
        output = {
            "mode": "count",
            "numFiles": len(matched_files),
            "filenames": paged.items,
            "content": "\n".join(f"{f}:{fb['total_matches']}" for f in paged.items),
            "numMatches": fb["total_matches"],
            "appliedOffset": paged.applied_offset,
        }
        if paged.applied_limit is not None:
            output["appliedLimit"] = paged.applied_limit
        return ToolResult(name="Grep", output=output)

    # files_with_matches — sort by mtime
    matched_files = fb["matched_files"]
    file_stats: list[tuple[Path, float]] = []
    for f in matched_files:
        try:
            mtime = f.stat().st_mtime
        except OSError:
            mtime = 0.0
        file_stats.append((f, mtime))

    file_stats.sort(key=lambda x: (-x[1], str(x[0])))
    sorted_files = [to_relative_path(str(f), cwd) for f, _ in file_stats]

    paged = _paginate(sorted_files, head_limit=head_limit, offset=offset)
    output = {
        "mode": "files_with_matches",
        "numFiles": len(paged.items),
        "filenames": paged.items,
        "appliedOffset": paged.applied_offset,
    }
    if paged.applied_limit is not None:
        output["appliedLimit"] = paged.applied_limit
    return ToolResult(name="Grep", output=output)


# -- Prompt -------------------------------------------------------------------

_GREP_PROMPT = """A powerful search tool built on ripgrep

  Usage:
  - ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.
  - Supports full regex syntax (e.g., "log.*Error", "function\\\\s+\\\\w+")
  - Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
  - Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
  - Use Agent tool for open-ended searches requiring multiple rounds
  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\\\{\\\\}` to find `interface{}` in Go code)
  - Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\\\{[\\\\s\\\\S]*?field`, use `multiline: true`
"""


# -- Tool definition -----------------------------------------------------------

GrepTool: Tool = build_tool(
    name="Grep",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for in file contents",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in (rg PATH). Defaults to current working directory.",
            },
            "glob": {
                "type": "string",
                "description": 'Glob pattern to filter files (e.g. "*.js", "*.{ts,tsx}") - maps to rg --glob',
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": 'Output mode: "content" shows matching lines (supports -A/-B/-C context, -n line numbers, head_limit), "files_with_matches" shows file paths (supports head_limit), "count" shows match counts (supports head_limit). Defaults to "files_with_matches".',
            },
            "-B": {
                "type": "number",
                "description": 'Number of lines to show before each match (rg -B). Requires output_mode: "content", ignored otherwise.',
            },
            "-A": {
                "type": "number",
                "description": 'Number of lines to show after each match (rg -A). Requires output_mode: "content", ignored otherwise.',
            },
            "-C": {
                "type": "number",
                "description": "Alias for context.",
            },
            "context": {
                "type": "number",
                "description": 'Number of lines to show before and after each match (rg -C). Requires output_mode: "content", ignored otherwise.',
            },
            "-n": {
                "type": "boolean",
                "description": 'Show line numbers in output (rg -n). Requires output_mode: "content", ignored otherwise. Defaults to true.',
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search (rg -i)",
            },
            "type": {
                "type": "string",
                "description": "File type to search (rg --type). Common types: js, py, rust, go, java, etc. More efficient than include for standard file types.",
            },
            "head_limit": {
                "type": "number",
                "description": 'Limit output to first N lines/entries, equivalent to "| head -N". Works across all output modes: content (limits output lines), files_with_matches (limits file paths), count (limits count entries). Defaults to 250 when unspecified. Pass 0 for unlimited (use sparingly - large result sets waste context).',
            },
            "offset": {
                "type": "number",
                "description": 'Skip first N lines/entries before applying head_limit, equivalent to "| tail -n +N | head -N". Works across all output modes. Defaults to 0.',
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline mode where . matches newlines and patterns can span lines (rg -U --multiline-dotall). Default: false.",
            },
        },
        "required": ["pattern"],
    },
    call=_grep_call,
    prompt=_GREP_PROMPT,
    description="A powerful search tool built on ripgrep",
    map_result_to_api=_map_result_to_api,
    strict=True,
    max_result_size_chars=20_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    search_hint="grep search regex find content",
    to_auto_classifier_input=lambda input_data: (input_data or {}).get("pattern", ""),
    is_search_or_read_command=lambda _input: SearchOrReadResult(is_search=True),
    get_activity_description=lambda input_data: f"Searching for {(input_data or {}).get('pattern', '')!r}" if input_data else None,
)
