"""
Compact service for /compact command.

Delegates to the unified compaction pipeline in
``src/services/compact/compact.py``. This module provides the
conversation-mutating wrapper that the /compact command handler expects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from ..agent.conversation import Conversation, Message
from ..providers.base import BaseProvider
from ..services.compact.compact import (
    compact_conversation as _pipeline_compact,
    CompactContext,
    CompactionResult as PipelineResult,
)
from .messages import is_compact_boundary_message

logger = logging.getLogger(__name__)


@dataclass
class CompactResult:
    """Result of a compaction operation (command-handler facing)."""
    boundary_message: Message
    summary_message: Message
    tokens_saved: int
    pre_compact_count: int
    post_compact_count: int
    summary_text: str
    trigger: str = "manual"
    user_display_message: Optional[str] = None


async def compact_conversation(
    conversation: Conversation,
    provider: BaseProvider,
    model: str,
    custom_instructions: Optional[str] = None,
    trigger: str = "manual",
    read_file_state: Optional[dict[str, Any]] = None,
    plan_file_path: Optional[str] = None,
    memory_paths: Optional[set[str]] = None,
) -> CompactResult:
    """
    Compact a conversation by summarizing older messages.

    Delegates to the unified pipeline in ``services/compact/compact.py``
    and applies the result to the conversation in place.

    Args:
        conversation: The live Conversation object (mutated in place)
        provider: The LLM provider for generating the summary
        model: Model name for the summary call
        custom_instructions: Optional user instructions for the summarizer
        trigger: "manual" or "auto"
        read_file_state: Mapping of filename → {content, timestamp} used to
            re-inject recently accessed files after compaction.
        plan_file_path: Path to the plan file to restore (and exclude from
            file restoration).
        memory_paths: Set of memory/CLAUDE.md paths to exclude from file
            restoration (they're loaded separately).

    Returns:
        CompactResult with boundary, summary, and metadata
    """
    pre_compact_count = len(conversation.messages)

    context = CompactContext(
        provider=provider,
        model=model,
        messages=list(conversation.messages),
        custom_instructions=custom_instructions,
        trigger=trigger,
        read_file_state=read_file_state,
        plan_file_path=plan_file_path,
        memory_paths=memory_paths,
    )

    pipeline_result = await _pipeline_compact(context)

    # Apply the pipeline result to the conversation in place
    boundary_indices = [
        i for i, m in enumerate(conversation.messages)
        if is_compact_boundary_message(m)
    ]

    if boundary_indices:
        insert_pos = max(boundary_indices) + 1
    else:
        insert_pos = 0

    new_messages: list[Message] = []
    if insert_pos > 0:
        new_messages.extend(conversation.messages[:insert_pos])
    new_messages.append(pipeline_result.boundary_marker)
    new_messages.extend(pipeline_result.summary_messages)
    new_messages.extend(pipeline_result.messages_to_keep)
    new_messages.extend(pipeline_result.attachments)

    conversation.messages = new_messages
    post_compact_count = len(conversation.messages)

    summary_text = ""
    for msg in pipeline_result.summary_messages:
        content = msg.content
        if isinstance(content, list):
            for block in content:
                if hasattr(block, "text"):
                    summary_text += block.text
        elif isinstance(content, str):
            summary_text += content

    return CompactResult(
        boundary_message=pipeline_result.boundary_marker,
        summary_message=(
            pipeline_result.summary_messages[0]
            if pipeline_result.summary_messages
            else pipeline_result.boundary_marker
        ),
        tokens_saved=pipeline_result.tokens_saved,
        pre_compact_count=pre_compact_count,
        post_compact_count=post_compact_count,
        summary_text=summary_text,
        trigger=trigger,
        user_display_message=pipeline_result.user_display_message,
    )
