"""Auto-memory subsystem (`~/.claude/projects/<slug>/memory/`).

Mirrors `typescript/src/memdir/`. Slice A in the ch11 refactor:
path resolution, type taxonomy, prompt builders. Slices B (recall) and C
(safety net + write carve-out) build on top of these primitives.
"""

from __future__ import annotations

from .find_relevant_memories import (
    MAX_RELEVANT_MEMORIES,
    RelevantMemory,
    find_relevant_memories,
)
from .memdir import (
    DIR_EXISTS_GUIDANCE,
    ENTRYPOINT_NAME,
    EntrypointTruncation,
    MAX_ENTRYPOINT_BYTES,
    MAX_ENTRYPOINT_LINES,
    build_memory_lines,
    build_memory_prompt,
    ensure_memory_dir_exists,
    load_memory_prompt,
    truncate_entrypoint_content,
)
from .memory_age import (
    memory_age,
    memory_age_days,
    memory_freshness_note,
    memory_freshness_text,
)
from .memory_scan import (
    FRONTMATTER_MAX_LINES,
    MAX_DEPTH,
    MAX_MEMORY_FILES,
    MemoryHeader,
    format_memory_manifest,
    scan_memory_files,
)
from .memory_types import (
    MEMORY_DRIFT_CAVEAT,
    MEMORY_FRONTMATTER_EXAMPLE,
    MEMORY_TYPES,
    MemoryType,
    TRUSTING_RECALL_SECTION,
    TYPES_SECTION_INDIVIDUAL,
    WHAT_NOT_TO_SAVE_SECTION,
    WHEN_TO_ACCESS_SECTION,
    parse_memory_type,
)
from .paths import (
    find_canonical_git_root,
    get_auto_mem_daily_log_path,
    get_auto_mem_entrypoint,
    get_auto_mem_path,
    get_memory_base_dir,
    has_auto_mem_path_override,
    is_auto_mem_path,
    is_auto_memory_enabled,
    sanitize_path,
)

__all__ = [
    # paths
    "find_canonical_git_root",
    "get_auto_mem_daily_log_path",
    "get_auto_mem_entrypoint",
    "get_auto_mem_path",
    "get_memory_base_dir",
    "has_auto_mem_path_override",
    "is_auto_mem_path",
    "is_auto_memory_enabled",
    "sanitize_path",
    # types
    "MEMORY_DRIFT_CAVEAT",
    "MEMORY_FRONTMATTER_EXAMPLE",
    "MEMORY_TYPES",
    "MemoryType",
    "TRUSTING_RECALL_SECTION",
    "TYPES_SECTION_INDIVIDUAL",
    "WHAT_NOT_TO_SAVE_SECTION",
    "WHEN_TO_ACCESS_SECTION",
    "parse_memory_type",
    # memdir
    "DIR_EXISTS_GUIDANCE",
    "ENTRYPOINT_NAME",
    "EntrypointTruncation",
    "MAX_ENTRYPOINT_BYTES",
    "MAX_ENTRYPOINT_LINES",
    "build_memory_lines",
    "build_memory_prompt",
    "ensure_memory_dir_exists",
    "load_memory_prompt",
    "truncate_entrypoint_content",
    # scan / recall
    "FRONTMATTER_MAX_LINES",
    "MAX_DEPTH",
    "MAX_MEMORY_FILES",
    "MAX_RELEVANT_MEMORIES",
    "MemoryHeader",
    "RelevantMemory",
    "find_relevant_memories",
    "format_memory_manifest",
    "scan_memory_files",
    # staleness
    "memory_age",
    "memory_age_days",
    "memory_freshness_note",
    "memory_freshness_text",
]
