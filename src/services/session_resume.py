"""Session resume — read JSONL, reconstruct typed Messages.

Matches TypeScript session/resume.ts. Handles:
- Malformed line recovery
- Orphaned permissions (tool_use without tool_result)
- Cross-project path adjustment
- Snip boundary handling
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..types.messages import (
    Message,
    UserMessage,
    AssistantMessage,
    message_from_dict,
    create_user_message,
    SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
)
from ..types.content_blocks import ToolResultBlock
from .session_storage import SessionStorage, SessionMetadata

logger = logging.getLogger(__name__)


def resume_session(
    session_id: str,
    *,
    sessions_dir: Path | None = None,
    current_cwd: str | None = None,
) -> ResumeResult:
    """Resume a session by reading its JSONL transcript.

    Returns a ResumeResult with messages, metadata, and any warnings.
    """
    storage = SessionStorage(session_id=session_id, sessions_dir=sessions_dir)
    metadata = storage.get_metadata()

    if metadata is None:
        return ResumeResult(
            messages=[],
            metadata=None,
            warnings=["Session not found or metadata missing"],
            success=False,
        )

    # Read raw entries (handles malformed lines)
    entries = storage.read_transcript()

    # Convert to typed messages
    messages: list[Message] = []
    warnings: list[str] = []
    skipped = 0

    for i, entry in enumerate(entries):
        try:
            msg = message_from_dict(entry)

            # Cross-project path adjustment
            if current_cwd and metadata.cwd and current_cwd != metadata.cwd:
                msg = _adjust_paths(msg, metadata.cwd, current_cwd)

            messages.append(msg)
        except Exception as e:
            skipped += 1
            warnings.append(f"Skipped entry {i}: {e}")

    if skipped > 0:
        warnings.append(f"Total skipped entries: {skipped}")

    # Handle orphaned permissions (tool_use without tool_result)
    messages, orphan_warnings = _fix_orphaned_tool_uses(messages)
    warnings.extend(orphan_warnings)

    # Handle snip boundaries
    messages = _handle_snip_boundaries(messages)

    # S-R4-CK: run consistency check on the final message chain
    consistency_warnings = _check_chain_consistency(messages)
    warnings.extend(consistency_warnings)

    return ResumeResult(
        messages=messages,
        metadata=metadata,
        warnings=warnings,
        success=True,
    )


class ResumeResult:
    """Result of a session resume operation."""

    def __init__(
        self,
        messages: list[Message],
        metadata: SessionMetadata | None,
        warnings: list[str],
        success: bool = True,
    ) -> None:
        self.messages = messages
        self.metadata = metadata
        self.warnings = warnings
        self.success = success

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


def _fix_orphaned_tool_uses(messages: list[Message]) -> tuple[list[Message], list[str]]:
    """Fix orphaned tool_use blocks (assistant tool_use without corresponding user tool_result).

    Adds synthetic tool_result for any unmatched tool_use.
    """
    warnings: list[str] = []

    # Collect all tool_use IDs from assistant messages
    tool_use_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, AssistantMessage) and isinstance(msg.content, list):
            for block in msg.content:
                if getattr(block, "type", None) == "tool_use":
                    tid = getattr(block, "id", "")
                    if tid:
                        tool_use_ids.add(tid)

    # Collect all tool_result IDs from user messages
    tool_result_ids: set[str] = set()
    for msg in messages:
        if msg.role == "user" and isinstance(msg.content, list):
            for block in msg.content:
                btype = getattr(block, "type", None)
                if btype == "tool_result":
                    tid = getattr(block, "tool_use_id", "")
                    if tid:
                        tool_result_ids.add(tid)

    # Find orphans
    orphaned = tool_use_ids - tool_result_ids
    if not orphaned:
        return messages, warnings

    warnings.append(f"Found {len(orphaned)} orphaned tool_use(s), adding synthetic results")

    # Add synthetic tool_result messages
    result_messages = list(messages)

    # Find the last assistant message with orphaned tool_use and add result after it
    for i in range(len(result_messages) - 1, -1, -1):
        msg = result_messages[i]
        if isinstance(msg, AssistantMessage) and isinstance(msg.content, list):
            msg_orphans = []
            for block in msg.content:
                if getattr(block, "type", None) == "tool_use":
                    tid = getattr(block, "id", "")
                    if tid in orphaned:
                        msg_orphans.append(tid)

            if msg_orphans:
                synthetic_blocks = [
                    ToolResultBlock(
                        type="tool_result",
                        tool_use_id=tid,
                        content=SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                        is_error=False,
                    )
                    for tid in msg_orphans
                ]
                synthetic_msg = create_user_message(content=synthetic_blocks, isMeta=True)
                result_messages.insert(i + 1, synthetic_msg)
                for tid in msg_orphans:
                    orphaned.discard(tid)

    return result_messages, warnings


def _adjust_paths(msg: Message, old_cwd: str, new_cwd: str) -> Message:
    """Adjust relative paths when resuming from a different directory.

    Best-effort: rewrites relative file paths in tool_use arguments and
    tool_result content that start with *old_cwd* so they point to the
    equivalent location under *new_cwd*.  Absolute paths and paths outside
    *old_cwd* are left untouched.

    Handles tool arguments with keys like ``path``, ``file_path``,
    ``directory``, ``working_dir`` that contain a relative file path.
    Falls back to a general string-replace of the old prefix as a safety
    net for messages whose structure isn't explicitly mapped.
    """
    if old_cwd == new_cwd:
        return msg

    # ---- Tool-use arguments with known path keys ----
    PATH_KEYS: set[str] = {"path", "file_path", "directory", "working_dir", "dir", "target_dir"}

    if isinstance(msg, AssistantMessage) and isinstance(msg.content, list):
        for block in msg.content:
            btype = getattr(block, "type", None)
            if btype == "tool_use":
                inp = getattr(block, "input", None) or {}
                if not isinstance(inp, dict):
                    continue
                modified = False
                for key, val in inp.items():
                    if key in PATH_KEYS and isinstance(val, str) and val.startswith(old_cwd):
                        inp[key] = val.replace(old_cwd, new_cwd, 1)
                        modified = True
                if modified:
                    try:
                        block.input = inp  # mutate in-place
                    except Exception:
                        pass

    # ---- Tool-result content with path references ----
    if msg.role == "user" and isinstance(msg.content, list):
        for block in msg.content:
            btype = getattr(block, "type", None)
            if btype == "tool_result":
                content = getattr(block, "content", None)
                if isinstance(content, str) and old_cwd in content:
                    try:
                        block.content = content.replace(old_cwd, new_cwd)
                    except Exception:
                        pass
                elif isinstance(content, list):
                    _rewrite_content_list(content, old_cwd, new_cwd)

    # ---- Fallback: rewrite content text ----
    content = getattr(msg, "content", None) or ""
    if isinstance(content, str) and old_cwd in content:
        try:
            msg.content = content.replace(old_cwd, new_cwd)  # type: ignore[assignment]
        except Exception:
            pass
    elif isinstance(content, list):
        _rewrite_content_list(content, old_cwd, new_cwd)

    return msg


def _rewrite_content_list(blocks: list, old_cwd: str, new_cwd: str) -> None:
    """Rewrite path text inside a content-block list in-place."""
    for item in blocks:
        if isinstance(item, str) and old_cwd in item:
            try:
                blocks[blocks.index(item)] = item.replace(old_cwd, new_cwd)
            except Exception:
                pass
        elif isinstance(item, dict):
            for key, val in item.items():
                if isinstance(val, str) and old_cwd in val:
                    try:
                        item[key] = val.replace(old_cwd, new_cwd)
                    except Exception:
                        pass


def _handle_snip_boundaries(messages: list[Message]) -> list[Message]:
    """Handle snip boundary markers in the message list.

    If there's a compact boundary, only keep messages after the last one.
    """
    last_boundary_idx = -1
    for i, msg in enumerate(messages):
        if msg.isCompactSummary:
            last_boundary_idx = i

    if last_boundary_idx >= 0:
        # Keep the boundary and everything after
        return messages[last_boundary_idx:]

    return messages


def _check_chain_consistency(messages: list[Message]) -> list[str]:
    """Validate the resume message chain for structural consistency (S-R4-CK).

    Checks:
    1. Message ordering follows user → assistant → user → assistant ...
    2. Each assistant message has non-empty content or tool_use blocks.
    3. No consecutive messages with the same role.

    Returns a list of warning strings (empty = consistent).
    """
    warnings: list[str] = []
    if not messages:
        return warnings

    prev_role: str | None = None
    for i, msg in enumerate(messages):
        role = getattr(msg, "role", None) or ""
        if not role:
            warnings.append(f"Message {i}: missing role")
            continue

        # Check consecutive same role
        if prev_role == role:
            warnings.append(
                f"Message {i}: consecutive '{role}' messages (prev was message {i - 1})"
            )

        # Check ordering: user → assistant → user → assistant
        if prev_role == "user" and role == "user":
            warnings.append(
                f"Message {i}: two consecutive user messages; "
                "expected assistant between them"
            )
        if prev_role == "assistant" and role == "assistant":
            warnings.append(
                f"Message {i}: two consecutive assistant messages; "
                "expected user between them"
            )

        # Check assistant messages have content
        if role == "assistant":
            content = getattr(msg, "content", None)
            if not content:
                warnings.append(f"Message {i}: assistant message has empty content")
            elif isinstance(content, list) and len(content) == 0:
                warnings.append(f"Message {i}: assistant message has empty content list")
            else:
                # Check that the content has at least text or tool_use
                has_text = False
                has_tool_use = False
                if isinstance(content, list):
                    for block in content:
                        btype = getattr(block, "type", None) if not isinstance(block, dict) else block.get("type")
                        if btype == "text":
                            has_text = True
                        elif btype == "tool_use":
                            has_tool_use = True
                if not has_text and not has_tool_use:
                    # Single string content is fine
                    if not isinstance(content, str) or not content.strip():
                        warnings.append(
                            f"Message {i}: assistant message has no text or tool_use content"
                        )

        prev_role = role

    # Check that the chain starts with a user message
    first_role = getattr(messages[0], "role", None) or ""
    if first_role != "user":
        warnings.append(
            f"Chain starts with '{first_role}' message (expected 'user')"
        )

    # Check that the chain ends with an assistant message
    last_role = getattr(messages[-1], "role", None) or ""
    if last_role not in ("assistant", "user"):
        warnings.append(
            f"Chain ends with '{last_role}' message (expected 'assistant' or 'user')"
        )

    return warnings


__all__ = [
    "resume_session",
    "ResumeResult",
    "_fix_orphaned_tool_uses",
    "_adjust_paths",
    "_check_chain_consistency",
]
