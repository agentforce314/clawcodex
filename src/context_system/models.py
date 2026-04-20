"""
Context system data models — aligned with TypeScript Claude Code architecture.

TypeScript references:
  - claudemd.ts: MemoryFileInfo, MemoryType
  - context.ts: getUserContext, getSystemContext (return dict[str, str])
  - queryContext.ts: fetchSystemPromptParts return type
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


# ---------------------------------------------------------------------------
# Memory types — mirrors TS MemoryType
# ---------------------------------------------------------------------------

MemoryType = Literal["Managed", "User", "Project", "Local"]

# ---------------------------------------------------------------------------
# MemoryFileInfo — mirrors TS MemoryFileInfo from claudemd.ts
# ---------------------------------------------------------------------------

@dataclass
class MemoryFileInfo:
    """A loaded memory/instruction file (CLAUDE.md, rules, etc.)."""

    path: str
    type: MemoryType
    content: str
    parent: str | None = None           # Path of the file that @included this one
    globs: list[str] | None = None      # Glob patterns from frontmatter `paths:`
    content_differs_from_disk: bool = False
    raw_content: str | None = None


# ---------------------------------------------------------------------------
# SystemPromptParts — return type of fetch_system_prompt_parts()
# Mirrors TS fetchSystemPromptParts from queryContext.ts
# ---------------------------------------------------------------------------

@dataclass
class SystemPromptParts:
    """Assembled pieces for the API cache-key prefix."""

    default_system_prompt: list[str]
    user_context: dict[str, str]     # e.g. {"claudeMd": "...", "currentDate": "..."}
    system_context: dict[str, str]   # e.g. {"gitStatus": "..."}


# ---------------------------------------------------------------------------
# Max constants — mirrors TS constants
# ---------------------------------------------------------------------------

MAX_MEMORY_CHARACTER_COUNT = 40_000
MAX_STATUS_CHARS = 2_000
MAX_INCLUDE_DEPTH = 5
MEMORY_INSTRUCTION_PROMPT = (
    "Codebase and user instructions are shown below. "
    "Be sure to adhere to these instructions. "
    "IMPORTANT: These instructions OVERRIDE any default behavior "
    "and you MUST follow them exactly as written."
)

# Text file extensions allowed for @include directives (mirrors TS TEXT_FILE_EXTENSIONS)
TEXT_FILE_EXTENSIONS: frozenset[str] = frozenset([
    ".md", ".txt", ".text",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".csv",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".mts", ".cts",
    ".py", ".pyi", ".pyw",
    ".rb", ".erb", ".rake",
    ".go", ".rs",
    ".java", ".kt", ".kts", ".scala",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx",
    ".cs", ".swift",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".env", ".ini", ".cfg", ".conf", ".config", ".properties",
    ".sql", ".graphql", ".gql", ".proto",
    ".vue", ".svelte", ".astro",
    ".ejs", ".hbs", ".pug", ".jade",
    ".php", ".pl", ".pm", ".lua", ".r", ".R", ".dart",
    ".ex", ".exs", ".erl", ".hrl",
    ".clj", ".cljs", ".cljc", ".edn",
    ".hs", ".lhs", ".elm", ".ml", ".mli",
    ".f", ".f90", ".f95", ".for",
    ".cmake", ".make", ".makefile", ".gradle", ".sbt",
    ".rst", ".adoc", ".asciidoc", ".org", ".tex", ".latex",
    ".lock", ".log", ".diff", ".patch",
])


# ---------------------------------------------------------------------------
# Legacy types — kept for backward compat with workspace_snapshot.py
# ---------------------------------------------------------------------------

@dataclass
class WorkspaceSnapshot:
    workspace_root: Path
    current_directory: Path
    top_level_entries: tuple[str, ...]
    key_files: tuple[str, ...]
    python_file_count: int
    test_file_count: int
