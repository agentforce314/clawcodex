"""
Compression pipeline for context management.

Implements the 5-layer compression pipeline matching TypeScript
``typescript/src/services/compact/``:

1. tool_result_budget — Persist large tool results to disk
2. snip_compact       — Trim old tool results (preserve structure)
3. microcompact       — Compress intermediate tool calls
4. context_collapse   — Read-time projection via collapse store
5. autocompact        — Full LLM summarization (last resort)

The pipeline runs cheap → expensive; if earlier layers free enough tokens,
later layers are no-ops.
"""

from __future__ import annotations

from .pipeline import CompressionPipeline, CompressionResult, run_compression_pipeline
from .tool_result_budget import apply_tool_result_budget
from .snip_compact import snip_compact
from .context_collapse import ContextCollapseStore, CollapseCommit
from .autocompact import (
    should_auto_compact,
    auto_compact_if_needed,
    get_effective_context_window_size,
    get_auto_compact_threshold,
    is_auto_compact_enabled,
    calculate_token_warning_state,
    AutoCompactTracking,
)
from .compact import compact_conversation, CompactionResult, truncate_head_for_ptl_retry
from .grouping import group_messages_by_api_round, ApiRound
from .prompt import get_compact_prompt, format_compact_summary
from .compact_warning import (
    suppress_compact_warning,
    clear_compact_warning_suppression,
    is_compact_warning_suppressed,
)
from .post_compact_cleanup import run_post_compact_cleanup
from .post_compact_attachments import (
    create_post_compact_file_attachments,
    create_plan_attachment_if_needed,
    create_skill_attachment_if_needed,
    POST_COMPACT_MAX_FILES_TO_RESTORE,
    POST_COMPACT_TOKEN_BUDGET,
    POST_COMPACT_MAX_TOKENS_PER_FILE,
)

__all__ = [
    "CompressionPipeline",
    "CompressionResult",
    "run_compression_pipeline",
    "apply_tool_result_budget",
    "snip_compact",
    "ContextCollapseStore",
    "CollapseCommit",
    "should_auto_compact",
    "auto_compact_if_needed",
    "get_effective_context_window_size",
    "get_auto_compact_threshold",
    "is_auto_compact_enabled",
    "calculate_token_warning_state",
    "AutoCompactTracking",
    "compact_conversation",
    "CompactionResult",
    "truncate_head_for_ptl_retry",
    "group_messages_by_api_round",
    "ApiRound",
    "get_compact_prompt",
    "format_compact_summary",
    "suppress_compact_warning",
    "clear_compact_warning_suppression",
    "is_compact_warning_suppressed",
    "run_post_compact_cleanup",
    "create_post_compact_file_attachments",
    "create_plan_attachment_if_needed",
    "create_skill_attachment_if_needed",
    "POST_COMPACT_MAX_FILES_TO_RESTORE",
    "POST_COMPACT_TOKEN_BUDGET",
    "POST_COMPACT_MAX_TOKENS_PER_FILE",
]
