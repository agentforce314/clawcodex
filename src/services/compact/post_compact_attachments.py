"""
Post-compact file and plan attachments.

Port of the attachment creation logic from
``typescript/src/services/compact/compact.ts``:
- ``createPostCompactFileAttachments()``
- ``createPlanAttachmentIfNeeded()``
- ``createSkillAttachmentIfNeeded()``

Re-injects recently accessed files and plan state after compaction so the
model doesn't have to re-read them.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from ...token_estimation import rough_token_count_estimation, rough_token_count_estimation_for_messages
from ...types.content_blocks import TextBlock, ToolUseBlock, ToolResultBlock
from ...types.messages import Message, UserMessage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (match TS)
# ---------------------------------------------------------------------------
POST_COMPACT_MAX_FILES_TO_RESTORE = 5
POST_COMPACT_TOKEN_BUDGET = 50_000
POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000
POST_COMPACT_MAX_TOKENS_PER_SKILL = 5_000
POST_COMPACT_SKILLS_TOKEN_BUDGET = 25_000

SKILL_TRUNCATION_MARKER = (
    "\n\n[... skill content truncated for compaction; "
    "use Read on the skill path if you need the full text]"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expand_path(path: str) -> str:
    """Expand ~ and resolve to absolute path."""
    return os.path.abspath(os.path.expanduser(path))


def _should_exclude_from_post_compact_restore(
    filename: str,
    plan_file_path: str | None = None,
    memory_paths: set[str] | None = None,
) -> bool:
    """Check if a file should be excluded from post-compact restoration."""
    normalized = _expand_path(filename)

    if plan_file_path:
        try:
            if normalized == _expand_path(plan_file_path):
                return True
        except Exception:
            pass

    if memory_paths:
        if normalized in memory_paths:
            return True

    basename = os.path.basename(filename).lower()
    if basename in ("claude.md", ".claude.md", "claude_md"):
        return True
    if basename.endswith(".claude.md") or basename.endswith("claude.md"):
        return True

    return False


def _collect_read_tool_file_paths(messages: list[Message]) -> set[str]:
    """
    Collect file_path values from Read tool_use blocks in preserved messages.

    Skips Read results that are dedup stubs (FILE_UNCHANGED_STUB).
    """
    stub_ids: set[str] = set()
    for msg in messages:
        if msg.role != "user":
            continue
        content = msg.content
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, ToolResultBlock):
                if isinstance(block.content, str) and block.content.startswith(
                    "[File unchanged"
                ):
                    stub_ids.add(block.tool_use_id)
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                c = block.get("content", "")
                if isinstance(c, str) and c.startswith("[File unchanged"):
                    stub_ids.add(block.get("tool_use_id", ""))

    paths: set[str] = set()
    for msg in messages:
        if msg.role != "assistant":
            continue
        content = msg.content
        if not isinstance(content, list):
            continue
        for block in content:
            block_id = ""
            block_name = ""
            block_input: dict[str, Any] = {}

            if isinstance(block, ToolUseBlock):
                block_id = block.id
                block_name = block.name
                block_input = block.input if isinstance(block.input, dict) else {}
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                block_id = block.get("id", "")
                block_name = block.get("name", "")
                inp = block.get("input", {})
                block_input = inp if isinstance(inp, dict) else {}

            if block_name == "Read" and block_id not in stub_ids:
                fp = block_input.get("file_path", "")
                if isinstance(fp, str) and fp:
                    paths.add(_expand_path(fp))

    return paths


def _truncate_to_tokens(content: str, max_tokens: int) -> str:
    """Truncate content to roughly max_tokens, keeping the head."""
    if rough_token_count_estimation(content) <= max_tokens:
        return content
    char_budget = max_tokens * 4 - len(SKILL_TRUNCATION_MARKER)
    return content[:char_budget] + SKILL_TRUNCATION_MARKER


def _read_file_safe(filepath: str, max_tokens: int) -> str | None:
    """Read a file's content, capped at max_tokens. Returns None on error."""
    try:
        expanded = _expand_path(filepath)
        if not os.path.isfile(expanded):
            return None
        with open(expanded, "r", errors="replace") as f:
            content = f.read()
        if rough_token_count_estimation(content) > max_tokens:
            char_budget = max_tokens * 4
            content = content[:char_budget] + "\n[... file truncated for compaction]"
        return content
    except Exception:
        logger.debug("Failed to read file for post-compact restore: %s", filepath)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class FileAttachment:
    """A file restored after compaction."""
    filename: str
    content: str


def create_post_compact_file_attachments(
    read_file_state: dict[str, Any],
    max_files: int = POST_COMPACT_MAX_FILES_TO_RESTORE,
    preserved_messages: list[Message] | None = None,
    plan_file_path: str | None = None,
    memory_paths: set[str] | None = None,
) -> list[Message]:
    """
    Create attachment messages for recently accessed files to restore them
    after compaction.

    Port of ``createPostCompactFileAttachments`` from compact.ts.

    Args:
        read_file_state: Mapping of filename → {content, timestamp}.
        max_files: Maximum number of files to restore.
        preserved_messages: Messages kept post-compact; Read results here are skipped.
        plan_file_path: Path to the plan file (excluded from restore).
        memory_paths: Paths to memory/claude.md files (excluded from restore).

    Returns:
        List of UserMessage attachments with file content.
    """
    if not read_file_state:
        return []

    preserved_read_paths = (
        _collect_read_tool_file_paths(preserved_messages)
        if preserved_messages
        else set()
    )

    recent_files = sorted(
        (
            {"filename": fname, **state}
            for fname, state in read_file_state.items()
            if not _should_exclude_from_post_compact_restore(
                fname, plan_file_path, memory_paths
            )
            and _expand_path(fname) not in preserved_read_paths
        ),
        key=lambda f: f.get("timestamp", 0),
        reverse=True,
    )[:max_files]

    attachments: list[Message] = []
    used_tokens = 0

    for file_info in recent_files:
        filename = file_info["filename"]
        content = _read_file_safe(filename, POST_COMPACT_MAX_TOKENS_PER_FILE)
        if content is None:
            continue

        attachment_text = (
            f"[Post-compact file restore: {filename}]\n\n{content}"
        )
        attachment_tokens = rough_token_count_estimation(attachment_text)

        if used_tokens + attachment_tokens > POST_COMPACT_TOKEN_BUDGET:
            break

        used_tokens += attachment_tokens
        attachments.append(UserMessage(content=attachment_text, isMeta=True))

    return attachments


def create_plan_attachment_if_needed(
    plan_file_path: str | None = None,
) -> Message | None:
    """
    Create a plan file attachment if the plan file exists.

    Port of ``createPlanAttachmentIfNeeded`` from compact.ts.
    """
    if not plan_file_path:
        return None

    expanded = _expand_path(plan_file_path)
    if not os.path.isfile(expanded):
        return None

    try:
        with open(expanded, "r") as f:
            plan_content = f.read()
    except Exception:
        return None

    if not plan_content.strip():
        return None

    attachment_text = (
        f"[Post-compact plan restore: {plan_file_path}]\n\n{plan_content}"
    )
    return UserMessage(content=attachment_text, isMeta=True)


@dataclass
class SkillInfo:
    """An invoked skill to preserve across compaction."""
    name: str
    path: str
    content: str
    invoked_at: float = 0.0


def create_skill_attachment_if_needed(
    invoked_skills: list[SkillInfo] | None = None,
) -> Message | None:
    """
    Create attachment for invoked skills to preserve across compaction.

    Port of ``createSkillAttachmentIfNeeded`` from compact.ts.
    """
    if not invoked_skills:
        return None

    sorted_skills = sorted(invoked_skills, key=lambda s: s.invoked_at, reverse=True)

    used_tokens = 0
    skill_texts: list[str] = []

    for skill in sorted_skills:
        truncated = _truncate_to_tokens(skill.content, POST_COMPACT_MAX_TOKENS_PER_SKILL)
        tokens = rough_token_count_estimation(truncated)
        if used_tokens + tokens > POST_COMPACT_SKILLS_TOKEN_BUDGET:
            break
        used_tokens += tokens
        skill_texts.append(
            f"### Skill: {skill.name}\nPath: {skill.path}\n\n{truncated}"
        )

    if not skill_texts:
        return None

    attachment_text = (
        "[Post-compact skill restore]\n\n" + "\n\n---\n\n".join(skill_texts)
    )
    return UserMessage(content=attachment_text, isMeta=True)
