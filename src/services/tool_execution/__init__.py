"""Tool execution services — streaming executor, orchestrator, and tool hooks."""

from __future__ import annotations

from .sync_adapter import (
    ToolDispatchResult,
    dispatch_full,
    make_stub_assistant_message,
)
from .tool_execution import (
    MessageUpdateLazy,
    classify_tool_error,
    run_tool_use,
)
from .tool_result_persistence import (
    DEFAULT_MAX_RESULT_SIZE_CHARS,
    PERSISTED_OUTPUT_CLOSING_TAG,
    PERSISTED_OUTPUT_TAG,
    PREVIEW_SIZE_BYTES,
    PersistedToolResult,
    PersistResult,
    PersistToolResultError,
    build_large_tool_result_message,
    generate_preview,
    get_persistence_threshold,
    is_persist_error,
    is_tool_result_content_empty,
    maybe_persist_large_tool_result,
    persist_tool_result,
    process_tool_result_block,
    resolve_tool_results_dir,
)

__all__ = [
    "DEFAULT_MAX_RESULT_SIZE_CHARS",
    "MessageUpdateLazy",
    "PERSISTED_OUTPUT_CLOSING_TAG",
    "PERSISTED_OUTPUT_TAG",
    "PREVIEW_SIZE_BYTES",
    "PersistResult",
    "PersistToolResultError",
    "PersistedToolResult",
    "ToolDispatchResult",
    "build_large_tool_result_message",
    "classify_tool_error",
    "dispatch_full",
    "generate_preview",
    "get_persistence_threshold",
    "is_persist_error",
    "is_tool_result_content_empty",
    "make_stub_assistant_message",
    "maybe_persist_large_tool_result",
    "persist_tool_result",
    "process_tool_result_block",
    "resolve_tool_results_dir",
    "run_tool_use",
]
