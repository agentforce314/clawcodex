from __future__ import annotations

from .builder import build_context_prompt
from .prompt_assembly import (
    append_system_context,
    clear_context_caches,
    fetch_system_prompt_parts,
    get_system_context,
    get_user_context,
    prepend_user_context,
)
from .claude_md import (
    clear_memory_file_caches,
    get_claude_mds,
    get_memory_files,
    reset_get_memory_files_cache,
)
from .git_context import (
    GitContextSnapshot,
    clear_git_caches,
    collect_git_context,
    format_git_status,
    get_is_git,
)
from .models import (
    MemoryFileInfo,
    MemoryType,
    SystemPromptParts,
)

__all__ = [
    # Legacy (backward compat)
    "build_context_prompt",
    # Prompt assembly (WS-5)
    "append_system_context",
    "clear_context_caches",
    "fetch_system_prompt_parts",
    "get_system_context",
    "get_user_context",
    "prepend_user_context",
    # CLAUDE.md
    "clear_memory_file_caches",
    "get_claude_mds",
    "get_memory_files",
    "reset_get_memory_files_cache",
    # Git context
    "GitContextSnapshot",
    "clear_git_caches",
    "collect_git_context",
    "format_git_status",
    "get_is_git",
    # Models
    "MemoryFileInfo",
    "MemoryType",
    "SystemPromptParts",
]
