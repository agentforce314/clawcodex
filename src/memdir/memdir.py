"""Auto-memory prompt builders and ``MEMORY.md`` index handling.

Ports the load-bearing pieces of `typescript/src/memdir/memdir.ts`:

- :func:`truncate_entrypoint_content` — enforces the 200-line / 25KB caps
  on ``MEMORY.md`` with a cap-aware warning naming which cap fired.
- :func:`ensure_memory_dir_exists` — idempotent mkdir so the prompt can
  truthfully say "the directory already exists".
- :func:`build_memory_lines` — the system-prompt section prose
  (parameterized for ``display_name`` and ``memory_dir`` so that an
  agent-memory variant can reuse it without refactoring later).
- :func:`build_memory_prompt` — assembles lines + ``MEMORY.md`` content.
- :func:`load_memory_prompt` — top-level dispatch invoked by the
  prompt-assembly chain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable

from .memory_types import (
    MEMORY_FRONTMATTER_EXAMPLE,
    TRUSTING_RECALL_SECTION,
    TYPES_SECTION_INDIVIDUAL,
    WHAT_NOT_TO_SAVE_SECTION,
    WHEN_TO_ACCESS_SECTION,
)
from .paths import (
    get_auto_mem_entrypoint,
    get_auto_mem_path,
    is_auto_memory_enabled,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ENTRYPOINT_NAME",
    "MAX_ENTRYPOINT_LINES",
    "MAX_ENTRYPOINT_BYTES",
    "DIR_EXISTS_GUIDANCE",
    "EntrypointTruncation",
    "truncate_entrypoint_content",
    "ensure_memory_dir_exists",
    "build_memory_lines",
    "build_memory_prompt",
    "load_memory_prompt",
]

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
# ~125 chars/line at 200 lines. p100 observed: 197KB under 200 lines.
MAX_ENTRYPOINT_BYTES = 25_000

_AUTO_MEM_DISPLAY_NAME = "auto memory"

DIR_EXISTS_GUIDANCE = (
    "This directory already exists — write to it directly with the "
    "Write tool (do not run mkdir or check for its existence)."
)


@dataclass(frozen=True)
class EntrypointTruncation:
    """Result of truncating ``MEMORY.md`` content."""

    content: str
    line_count: int
    byte_count: int
    was_line_truncated: bool
    was_byte_truncated: bool


def _format_file_size(byte_count: int) -> str:
    """Human-readable size string. Matches TS ``formatFileSize`` shape."""
    if byte_count < 1024:
        return f"{byte_count}B"
    if byte_count < 1024 * 1024:
        return f"{byte_count / 1024:.1f}KB"
    return f"{byte_count / (1024 * 1024):.1f}MB"


def truncate_entrypoint_content(raw: str) -> EntrypointTruncation:
    """Truncate ``MEMORY.md`` content to the line AND byte caps.

    Line-truncates first (natural boundary), then byte-truncates at the
    last newline before the byte cap so we never cut mid-line. When
    truncation fires, appends a warning naming which cap fired.
    """
    trimmed = raw.strip()
    content_lines = trimmed.split("\n") if trimmed else []
    line_count = len(content_lines)
    byte_count = len(trimmed.encode("utf-8"))

    was_line_truncated = line_count > MAX_ENTRYPOINT_LINES
    # The byte cap targets long lines specifically — check pre-line-trunc
    # so the warning is accurate.
    was_byte_truncated = byte_count > MAX_ENTRYPOINT_BYTES

    if not was_line_truncated and not was_byte_truncated:
        return EntrypointTruncation(
            content=trimmed,
            line_count=line_count,
            byte_count=byte_count,
            was_line_truncated=False,
            was_byte_truncated=False,
        )

    truncated = (
        "\n".join(content_lines[:MAX_ENTRYPOINT_LINES])
        if was_line_truncated
        else trimmed
    )

    encoded = truncated.encode("utf-8")
    if len(encoded) > MAX_ENTRYPOINT_BYTES:
        # Cut at the last newline before the byte cap. rfind on bytes
        # avoids splitting a multi-byte UTF-8 sequence at the cap boundary.
        cut_at = encoded.rfind(b"\n", 0, MAX_ENTRYPOINT_BYTES)
        if cut_at <= 0:
            cut_at = MAX_ENTRYPOINT_BYTES
        truncated = encoded[:cut_at].decode("utf-8", errors="ignore")

    if was_byte_truncated and not was_line_truncated:
        reason = (
            f"{_format_file_size(byte_count)} "
            f"(limit: {_format_file_size(MAX_ENTRYPOINT_BYTES)}) — "
            f"index entries are too long"
        )
    elif was_line_truncated and not was_byte_truncated:
        reason = f"{line_count} lines (limit: {MAX_ENTRYPOINT_LINES})"
    else:
        reason = (
            f"{line_count} lines and {_format_file_size(byte_count)}"
        )

    warning = (
        f"\n\n> WARNING: {ENTRYPOINT_NAME} is {reason}. Only part of it "
        f"was loaded. Keep index entries to one line under ~200 chars; "
        f"move detail into topic files."
    )

    return EntrypointTruncation(
        content=truncated + warning,
        line_count=line_count,
        byte_count=byte_count,
        was_line_truncated=was_line_truncated,
        was_byte_truncated=was_byte_truncated,
    )


def ensure_memory_dir_exists(memory_dir: str) -> None:
    """Idempotent mkdir for the memory directory.

    Synchronous on purpose: the prompt-assembly chain is sync, and the
    work is a single ``mkdir(parents=True, exist_ok=True)``. Errors other
    than EEXIST are logged at debug — the prompt is still returned, and
    the model's first ``Write`` will surface any real permission error.
    """
    try:
        Path(memory_dir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug(
            "ensure_memory_dir_exists failed for %s: %s", memory_dir, exc
        )


def _how_to_save_section(skip_index: bool) -> list[str]:
    if skip_index:
        return [
            "## How to save memories",
            "",
            "Write each memory to its own file (e.g., `user_role.md`, `feedback_testing.md`) using this frontmatter format:",
            "",
            *MEMORY_FRONTMATTER_EXAMPLE,
            "",
            "- Keep the name, description, and type fields in memory files up-to-date with the content",
            "- Organize memory semantically by topic, not chronologically",
            "- Update or remove memories that turn out to be wrong or outdated",
            "- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.",
        ]
    return [
        "## How to save memories",
        "",
        "Saving a memory is a two-step process:",
        "",
        f"**Step 1** — write the memory to its own file (e.g., `user_role.md`, `feedback_testing.md`) using this frontmatter format:",
        "",
        *MEMORY_FRONTMATTER_EXAMPLE,
        "",
        f"**Step 2** — add a pointer to that file in `{ENTRYPOINT_NAME}`. `{ENTRYPOINT_NAME}` is an index, not a memory — each entry should be one line, under ~150 characters: `- [Title](file.md) — one-line hook`. It has no frontmatter. Never write memory content directly into `{ENTRYPOINT_NAME}`.",
        "",
        f"- `{ENTRYPOINT_NAME}` is always loaded into your conversation context — lines after {MAX_ENTRYPOINT_LINES} will be truncated, so keep the index concise",
        "- Keep the name, description, and type fields in memory files up-to-date with the content",
        "- Organize memory semantically by topic, not chronologically",
        "- Update or remove memories that turn out to be wrong or outdated",
        "- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.",
    ]


def build_searching_past_context_section(auto_mem_dir: str) -> list[str]:
    """The "Searching past context" guidance (memdir.ts:375-407).

    MEMDIR-1: upstream gates this on ``tengu_coral_fern``, which the vendored
    GrowthBook stub's ``_openBuildDefaults`` sets to TRUE — so the reference
    build emits it for every user, and this port emits it unconditionally
    (no flag system here). TS picks shell-grep forms when the dedicated Grep
    tool is hidden (ant-native embedded search / REPL script mode); this
    port always ships the Grep tool, so the tool-invocation forms are used
    unconditionally. The transcript target is this port's saved-session
    store (``~/.clawcodex/sessions/``, ``*.json``) rather than the reference
    project-transcript dir.
    """
    sessions_dir = os.path.join(os.path.expanduser("~"), ".clawcodex", "sessions")
    mem_search = (
        f'Grep with pattern="<search term>" path="{auto_mem_dir}" glob="*.md"'
    )
    transcript_search = (
        f'Grep with pattern="<search term>" path="{sessions_dir}/" glob="*.json"'
    )
    return [
        "## Searching past context",
        "",
        "When looking for past context:",
        "1. Search topic files in your memory directory:",
        "```",
        mem_search,
        "```",
        "2. Session transcript logs (last resort — large files, slow):",
        "```",
        transcript_search,
        "```",
        "Use narrow search terms (error messages, file paths, function names) rather than broad keywords.",
        "",
    ]


def build_memory_lines(
    *,
    display_name: str,
    memory_dir: str,
    extra_guidelines: Iterable[str] | None = None,
    skip_index: bool = False,
) -> list[str]:
    """Build the typed-memory behavioral instructions.

    Parameterized so the eventual agent-memory variant can reuse this
    builder without a refactor (Slice D follow-up). Returns the section
    as a list of lines; callers join with ``"\\n"``.
    """
    lines: list[str] = [
        f"# {display_name}",
        "",
        f"You have a persistent, file-based memory system at `{memory_dir}`. {DIR_EXISTS_GUIDANCE}",
        "",
        "You should build up this memory system over time so that future conversations can have a complete picture of who the user is, how they'd like to collaborate with you, what behaviors to avoid or repeat, and the context behind the work the user gives you.",
        "",
        "If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.",
        "",
        *TYPES_SECTION_INDIVIDUAL,
        *WHAT_NOT_TO_SAVE_SECTION,
        "",
        *_how_to_save_section(skip_index),
        "",
        *WHEN_TO_ACCESS_SECTION,
        "",
        *TRUSTING_RECALL_SECTION,
        "",
        "## Memory and other forms of persistence",
        "Memory is one of several persistence mechanisms available to you as you assist the user in a given conversation. The distinction is often that memory can be recalled in future conversations and should not be used for persisting information that is only useful within the scope of the current conversation.",
        "- When to use or update a plan instead of memory: If you are about to start a non-trivial implementation task and would like to reach alignment with the user on your approach you should use a Plan rather than saving this information to memory. Similarly, if you already have a plan within the conversation and you have changed your approach persist that change by updating the plan rather than saving a memory.",
        "- When to use or update tasks instead of memory: When you need to break your work in current conversation into discrete steps or keep track of your progress use tasks instead of saving to memory. Tasks are great for persisting information about the work that needs to be done in the current conversation, but memory should be reserved for information that will be useful in future conversations.",
        "",
    ]

    if extra_guidelines:
        for guideline in extra_guidelines:
            if guideline:
                lines.append(guideline)
        lines.append("")

    # memdir.ts:263 — the searching-past-context guidance closes the section.
    lines.extend(build_searching_past_context_section(memory_dir))

    return lines


def build_memory_prompt(
    *,
    display_name: str,
    memory_dir: str,
    extra_guidelines: Iterable[str] | None = None,
) -> str:
    """Assemble the memory section, including ``MEMORY.md`` body.

    Reads ``MEMORY.md`` synchronously, runs through
    :func:`truncate_entrypoint_content`, and appends after the prose
    lines. Used by both auto-memory (this module's
    :func:`load_memory_prompt`) and the eventual agent-memory variant.
    """
    entrypoint_path = Path(memory_dir) / ENTRYPOINT_NAME
    entrypoint_content = ""
    try:
        entrypoint_content = entrypoint_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        entrypoint_content = ""

    lines = build_memory_lines(
        display_name=display_name,
        memory_dir=memory_dir,
        extra_guidelines=extra_guidelines,
    )

    if entrypoint_content.strip():
        truncation = truncate_entrypoint_content(entrypoint_content)
        lines.extend([f"## {ENTRYPOINT_NAME}", "", truncation.content])
    else:
        lines.extend(
            [
                f"## {ENTRYPOINT_NAME}",
                "",
                f"Your {ENTRYPOINT_NAME} is currently empty. When you save new memories, they will appear here.",
            ]
        )

    return "\n".join(lines)


def load_memory_prompt() -> str | None:
    """Top-level dispatch for the auto-memory system prompt section.

    Returns ``None`` when auto-memory is disabled. Otherwise creates
    the memory directory(ies) if they do not exist and returns the
    assembled prompt section.

    Dispatch order matches TS ``loadMemoryPrompt``:

    1. Auto-memory disabled → ``None``.
    2. Team memory enabled → combined private + team prompt.
    3. Else → single-directory auto-memory prompt.

    KAIROS daily-log mode is still deferred (Slice D in the refactor
    plan).
    """
    if not is_auto_memory_enabled():
        return None

    # Lazy import to avoid a circular import at module load time
    # (team_mem_prompts imports from this module).
    from .team_mem_paths import get_team_mem_path, is_team_memory_enabled

    if is_team_memory_enabled():
        from .team_mem_prompts import build_combined_memory_prompt

        # team_dir is nested under auto_dir, so creating team_dir with
        # parents=True creates auto_dir as a side effect.
        team_dir = get_team_mem_path()
        ensure_memory_dir_exists(team_dir)
        return build_combined_memory_prompt()

    auto_dir = get_auto_mem_path()
    ensure_memory_dir_exists(auto_dir)
    return build_memory_prompt(
        display_name=_AUTO_MEM_DISPLAY_NAME,
        memory_dir=auto_dir,
    )
