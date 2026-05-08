"""XML tag constants for chapter-10 task notifications.

Mirrors ``typescript/src/constants/xml.ts``. Single source of truth for
the tag names that appear in ``<task-notification>`` envelopes the model
sees in its conversation flow. Keep names byte-identical to the TS
source so prompt-cache stability holds across TS↔Python interop and
snapshot tests pin the surface.
"""
from __future__ import annotations

from typing import Final

TASK_NOTIFICATION_TAG: Final[str] = "task-notification"
TASK_ID_TAG: Final[str] = "task-id"
TOOL_USE_ID_TAG: Final[str] = "tool-use-id"
OUTPUT_FILE_TAG: Final[str] = "output-file"
STATUS_TAG: Final[str] = "status"
SUMMARY_TAG: Final[str] = "summary"
RESULT_TAG: Final[str] = "result"
USAGE_TAG: Final[str] = "usage"
TOTAL_TOKENS_TAG: Final[str] = "total_tokens"
TOOL_USES_TAG: Final[str] = "tool_uses"
DURATION_MS_TAG: Final[str] = "duration_ms"
WORKTREE_TAG: Final[str] = "worktree"
WORKTREE_PATH_TAG: Final[str] = "worktree-path"
WORKTREE_BRANCH_TAG: Final[str] = "worktree-branch"


__all__ = [
    "TASK_NOTIFICATION_TAG",
    "TASK_ID_TAG",
    "TOOL_USE_ID_TAG",
    "OUTPUT_FILE_TAG",
    "STATUS_TAG",
    "SUMMARY_TAG",
    "RESULT_TAG",
    "USAGE_TAG",
    "TOTAL_TOKENS_TAG",
    "TOOL_USES_TAG",
    "DURATION_MS_TAG",
    "WORKTREE_TAG",
    "WORKTREE_PATH_TAG",
    "WORKTREE_BRANCH_TAG",
]
