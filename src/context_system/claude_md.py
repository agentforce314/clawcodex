"""
Multi-level CLAUDE.md loading — aligned with typescript/src/utils/claudemd.ts.

Loading order (reverse priority — later entries take precedence):
  1. Managed memory (/etc/claude-code/CLAUDE.md)
  2. User memory (~/.claude/CLAUDE.md)
  3. Project memory (CLAUDE.md, .claude/CLAUDE.md, .claude/rules/*.md)
  4. Local memory (CLAUDE.local.md)

Files closer to CWD have higher priority (loaded later in the list).

The @include directive allows memory files to reference other files:
  @path, @./relative, @~/home, @/absolute
Included files are added after the including file.  Circular references
are prevented by tracking processed file paths.  Max depth = 5.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Sequence

from ..skills.frontmatter import parse_frontmatter
from .models import (
    MAX_INCLUDE_DEPTH,
    MAX_MEMORY_CHARACTER_COUNT,
    MEMORY_INSTRUCTION_PROMPT,
    TEXT_FILE_EXTENSIONS,
    MemoryFileInfo,
    MemoryType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level memoize cache for get_memory_files
# ---------------------------------------------------------------------------

_memory_files_cache: list[MemoryFileInfo] | None = None
_memory_files_cache_key: str | None = None


def clear_memory_file_caches() -> None:
    """Clear the memoized memory files cache (call after compact)."""
    global _memory_files_cache, _memory_files_cache_key
    _memory_files_cache = None
    _memory_files_cache_key = None


def reset_get_memory_files_cache() -> None:
    """Clear cache and mark for reload (mirrors TS resetGetMemoryFilesCache)."""
    clear_memory_file_caches()


# ---------------------------------------------------------------------------
# Configuration accessors (can be overridden for testing)
# ---------------------------------------------------------------------------

def _get_original_cwd() -> str:
    """Return the original CWD. Can be overridden via CLAUDE_CODE_ORIGINAL_CWD."""
    return os.environ.get("CLAUDE_CODE_ORIGINAL_CWD", os.getcwd())


def _is_bare_mode() -> bool:
    """Check if --bare mode is active."""
    return os.environ.get("CLAUDE_CODE_BARE_MODE", "").lower() in ("1", "true", "yes")


def _get_additional_directories() -> list[str]:
    """Get additional directories for CLAUDE.md discovery (--add-dir)."""
    val = os.environ.get("CLAUDE_CODE_ADDITIONAL_DIRECTORIES", "")
    if not val:
        return []
    return [d.strip() for d in val.split(os.pathsep) if d.strip()]


def _should_disable_claude_md() -> bool:
    """Mirrors TS shouldDisableClaudeMd logic."""
    if os.environ.get("CLAUDE_CODE_DISABLE_CLAUDE_MDS", "").lower() in ("1", "true", "yes"):
        return True
    if _is_bare_mode() and len(_get_additional_directories()) == 0:
        return True
    return False


# ---------------------------------------------------------------------------
# @include directive extraction
# ---------------------------------------------------------------------------

_INCLUDE_RE = re.compile(r"(?:^|\s)@((?:[^\s\\]|\\ )+)")

# Simple code fence detection (``` lines)
_CODE_FENCE_RE = re.compile(r"^`{3,}")


def _extract_include_paths(text: str, base_path: str) -> list[str]:
    """
    Extract @include paths from markdown text, skipping code blocks.

    Mirrors TS extractIncludePathsFromTokens but uses a simpler
    regex-based approach (no full markdown lexer dependency).
    """
    paths: set[str] = set()
    in_code_block = False

    for line in text.splitlines():
        stripped = line.strip()
        if _CODE_FENCE_RE.match(stripped):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        for match in _INCLUDE_RE.finditer(line):
            raw_path = match.group(1)
            if not raw_path:
                continue
            # Strip fragment identifiers
            hash_idx = raw_path.find("#")
            if hash_idx != -1:
                raw_path = raw_path[:hash_idx]
            if not raw_path:
                continue
            # Unescape spaces
            raw_path = raw_path.replace("\\ ", " ")
            resolved = _resolve_include_path(raw_path, base_path)
            if resolved:
                paths.add(resolved)

    return list(paths)


def _resolve_include_path(path_str: str, base_dir: str) -> str | None:
    """Resolve an @include path to an absolute path."""
    if path_str.startswith("~/"):
        return str(Path.home() / path_str[2:])
    if path_str.startswith("./"):
        return str(Path(base_dir) / path_str[2:])
    if path_str.startswith("/") and path_str != "/":
        return path_str
    # Bare relative path (no prefix) — treat as relative
    if path_str and re.match(r"^[a-zA-Z0-9._-]", path_str):
        return str(Path(base_dir) / path_str)
    return None


# ---------------------------------------------------------------------------
# Frontmatter path parsing
# ---------------------------------------------------------------------------

def _parse_frontmatter_paths(raw_content: str) -> tuple[str, list[str] | None]:
    """
    Parse frontmatter and extract `paths:` glob patterns.

    Returns (content_without_frontmatter, paths_or_None).
    """
    result = parse_frontmatter(raw_content)
    content = result.body
    paths_value = result.frontmatter.get("paths")
    if paths_value is None:
        return content, None
    if isinstance(paths_value, str):
        patterns = [p.strip() for p in paths_value.split(",") if p.strip()]
    elif isinstance(paths_value, list):
        patterns = [str(p).strip() for p in paths_value if str(p).strip()]
    else:
        return content, None
    # Remove /** suffix (same as TS)
    patterns = [p[:-3] if p.endswith("/**") else p for p in patterns]
    patterns = [p for p in patterns if p]
    # If all patterns are ** (match-all), treat as no globs
    if not patterns or all(p == "**" for p in patterns):
        return content, None
    return content, patterns


# ---------------------------------------------------------------------------
# HTML comment stripping
# ---------------------------------------------------------------------------

_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")


def strip_html_comments(content: str) -> str:
    """Strip block-level HTML comments from markdown content."""
    if "<!--" not in content:
        return content
    return _HTML_COMMENT_RE.sub("", content)


# ---------------------------------------------------------------------------
# Core file processing
# ---------------------------------------------------------------------------

def _parse_memory_file_content(
    raw_content: str,
    file_path: str,
    mem_type: MemoryType,
    include_base_path: str | None = None,
) -> tuple[MemoryFileInfo | None, list[str]]:
    """
    Parse raw content into a MemoryFileInfo.  Pure function — no I/O.

    Returns (info_or_None, include_paths).
    """
    ext = Path(file_path).suffix.lower()
    if ext and ext not in TEXT_FILE_EXTENSIONS:
        return None, []

    content_without_fm, paths = _parse_frontmatter_paths(raw_content)
    stripped = strip_html_comments(content_without_fm)
    final_content = stripped

    content_differs = final_content != raw_content

    include_paths: list[str] = []
    if include_base_path is not None:
        base_dir = str(Path(include_base_path).parent)
        include_paths = _extract_include_paths(content_without_fm, base_dir)

    info = MemoryFileInfo(
        path=file_path,
        type=mem_type,
        content=final_content,
        globs=paths if paths else None,
        content_differs_from_disk=content_differs,
        raw_content=raw_content if content_differs else None,
    )
    return info, include_paths


def _safe_read_file(file_path: str) -> str | None:
    """Read a file, returning None on any error (ENOENT, EACCES, etc.)."""
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except Exception:
        return None


async def process_memory_file(
    file_path: str,
    mem_type: MemoryType,
    processed_paths: set[str],
    include_external: bool = False,
    depth: int = 0,
    parent: str | None = None,
) -> list[MemoryFileInfo]:
    """
    Recursively process a memory file and its @include references.

    Returns list with main file first, then included files.
    Mirrors TS processMemoryFile from claudemd.ts.
    """
    normalized = os.path.normcase(os.path.realpath(file_path))
    if normalized in processed_paths or depth >= MAX_INCLUDE_DEPTH:
        return []

    processed_paths.add(normalized)

    raw_content = _safe_read_file(file_path)
    if raw_content is None or not raw_content.strip():
        return []

    info, include_paths = _parse_memory_file_content(
        raw_content, file_path, mem_type, include_base_path=file_path,
    )
    if info is None or not info.content.strip():
        return []

    if parent:
        info.parent = parent

    result: list[MemoryFileInfo] = [info]

    original_cwd = _get_original_cwd()
    for inc_path in include_paths:
        is_external = not _path_in_working_path(inc_path, original_cwd)
        if is_external and not include_external:
            continue
        included = await process_memory_file(
            inc_path, mem_type, processed_paths, include_external,
            depth + 1, file_path,
        )
        result.extend(included)

    return result


async def process_md_rules(
    rules_dir: str,
    mem_type: MemoryType,
    processed_paths: set[str],
    include_external: bool = False,
    conditional_rule: bool = False,
) -> list[MemoryFileInfo]:
    """
    Process all .md files in a .claude/rules/ directory and subdirectories.

    Mirrors TS processMdRules from claudemd.ts.
    """
    rules_path = Path(rules_dir)
    if not rules_path.is_dir():
        return []

    result: list[MemoryFileInfo] = []
    try:
        for entry in sorted(rules_path.rglob("*.md")):
            if not entry.is_file():
                continue
            files = await process_memory_file(
                str(entry), mem_type, processed_paths, include_external,
            )
            for f in files:
                if conditional_rule and not f.globs:
                    continue
                if not conditional_rule and f.globs:
                    continue
                result.append(f)
    except PermissionError:
        pass
    except Exception:
        logger.debug("Error processing rules dir %s", rules_dir, exc_info=True)

    return result


def _path_in_working_path(path: str, working_path: str) -> bool:
    """Check if path is within the working directory."""
    try:
        Path(path).resolve().relative_to(Path(working_path).resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# get_memory_files — main entry point (mirrors TS getMemoryFiles)
# ---------------------------------------------------------------------------

async def get_memory_files(
    cwd: str | None = None,
    force_include_external: bool = False,
) -> list[MemoryFileInfo]:
    """
    Load all memory files in priority order.

    Mirrors TS getMemoryFiles from claudemd.ts.
    Loading order: Managed → User → Project → Local
    Project/Local walk from root to CWD (closer = higher priority).

    Results are cached; call clear_memory_file_caches() to invalidate.
    """
    global _memory_files_cache, _memory_files_cache_key

    original_cwd = cwd or _get_original_cwd()
    cache_key = f"{original_cwd}:{force_include_external}"

    if _memory_files_cache is not None and _memory_files_cache_key == cache_key:
        return list(_memory_files_cache)

    result: list[MemoryFileInfo] = []
    processed_paths: set[str] = set()
    include_external = force_include_external

    home = str(Path.home())

    # 1. Managed memory (/etc/claude-code/CLAUDE.md)
    managed_path = os.path.join("/etc", "claude-code", "CLAUDE.md")
    result.extend(await process_memory_file(
        managed_path, "Managed", processed_paths, include_external,
    ))
    # Managed rules
    managed_rules_dir = os.path.join("/etc", "claude-code", ".claude", "rules")
    result.extend(await process_md_rules(
        managed_rules_dir, "Managed", processed_paths, include_external,
        conditional_rule=False,
    ))

    # 2. User memory (~/.claude/CLAUDE.md)
    user_claude_md = os.path.join(home, ".claude", "CLAUDE.md")
    result.extend(await process_memory_file(
        user_claude_md, "User", processed_paths, True,  # User can always include external
    ))
    # User rules (~/.claude/rules/*.md)
    user_rules_dir = os.path.join(home, ".claude", "rules")
    result.extend(await process_md_rules(
        user_rules_dir, "User", processed_paths, True,
        conditional_rule=False,
    ))

    # 3. Project and Local files — walk from root to CWD
    dirs: list[str] = []
    current_dir = os.path.realpath(original_cwd)
    root = os.path.splitdrive(current_dir)[0] + os.sep if os.name == "nt" else "/"

    while current_dir != root:
        dirs.append(current_dir)
        parent = os.path.dirname(current_dir)
        if parent == current_dir:
            break
        current_dir = parent

    # Process from root downward to CWD (reverse so CWD is last = highest priority)
    for d in reversed(dirs):
        # Project: CLAUDE.md
        project_path = os.path.join(d, "CLAUDE.md")
        result.extend(await process_memory_file(
            project_path, "Project", processed_paths, include_external,
        ))
        # Project: .claude/CLAUDE.md
        dot_claude_path = os.path.join(d, ".claude", "CLAUDE.md")
        result.extend(await process_memory_file(
            dot_claude_path, "Project", processed_paths, include_external,
        ))
        # Project: .claude/rules/*.md
        rules_dir = os.path.join(d, ".claude", "rules")
        result.extend(await process_md_rules(
            rules_dir, "Project", processed_paths, include_external,
            conditional_rule=False,
        ))
        # Local: CLAUDE.local.md
        local_path = os.path.join(d, "CLAUDE.local.md")
        result.extend(await process_memory_file(
            local_path, "Local", processed_paths, include_external,
        ))

    # 4. Additional directories (--add-dir)
    for add_dir in _get_additional_directories():
        project_path = os.path.join(add_dir, "CLAUDE.md")
        result.extend(await process_memory_file(
            project_path, "Project", processed_paths, include_external,
        ))
        dot_claude_path = os.path.join(add_dir, ".claude", "CLAUDE.md")
        result.extend(await process_memory_file(
            dot_claude_path, "Project", processed_paths, include_external,
        ))
        rules_dir = os.path.join(add_dir, ".claude", "rules")
        result.extend(await process_md_rules(
            rules_dir, "Project", processed_paths, include_external,
            conditional_rule=False,
        ))

    _memory_files_cache = result
    _memory_files_cache_key = cache_key

    return list(result)


# ---------------------------------------------------------------------------
# get_claude_mds — format memory files for injection (mirrors TS getClaudeMds)
# ---------------------------------------------------------------------------

def get_claude_mds(memory_files: list[MemoryFileInfo]) -> str:
    """
    Format memory files into the prompt string.

    Mirrors TS getClaudeMds from claudemd.ts.
    """
    memories: list[str] = []

    for f in memory_files:
        if not f.content or not f.content.strip():
            continue
        description = _get_memory_type_description(f.type)
        content = f.content.strip()
        memories.append(f"Contents of {f.path}{description}:\n\n{content}")

    if not memories:
        return ""

    return f"{MEMORY_INSTRUCTION_PROMPT}\n\n" + "\n\n".join(memories)


def _get_memory_type_description(mem_type: MemoryType) -> str:
    if mem_type == "Project":
        return " (project instructions, checked into the codebase)"
    elif mem_type == "Local":
        return " (user's private project instructions, not checked in)"
    elif mem_type == "User":
        return " (user's private global instructions for all projects)"
    elif mem_type == "Managed":
        return " (managed policy instructions)"
    return ""


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def get_large_memory_files(files: list[MemoryFileInfo]) -> list[MemoryFileInfo]:
    """Return memory files exceeding the recommended max size."""
    return [f for f in files if len(f.content) > MAX_MEMORY_CHARACTER_COUNT]


def is_memory_file_path(file_path: str) -> bool:
    """Check if a path looks like a memory file."""
    name = os.path.basename(file_path)
    if name in ("CLAUDE.md", "CLAUDE.local.md"):
        return True
    if name.endswith(".md") and (os.sep + ".claude" + os.sep + "rules" + os.sep) in file_path:
        return True
    return False
