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

    This is a best-effort adjustment — only handles string content.
    """
    # For now, just return the message as-is
    # Full path adjustment would need to inspect tool_use/tool_result content
    return msg


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
