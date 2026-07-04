"""autoFix hook helpers — port of autoFixHook.ts.

``AUTO_FIX_TOOLS`` maps TS's internal ``file_edit``/``file_write`` to the
port's file-mutation registry tool names
(``permissions/check.py:_FILE_EDIT_TOOLS``).
"""

from __future__ import annotations

from .config import AutoFixConfig
from .runner import AutoFixResult

# The author's INTENT (critic M4): TS's dead `{file_edit, file_write}` was
# meant to match FileEditTool (`Edit`) + FileWriteTool (`Write`). NOT the
# 4-element _FILE_EDIT_TOOLS — TS deliberately excluded NotebookEdit and has
# no MultiEdit tool; over-broadening would diverge past the intent. See the
# plan's D1: this port ACTIVATES a feature TS never fires (a documented,
# opt-in-gated divergence that fixes the reference's dead tool-name set).
AUTO_FIX_TOOLS = frozenset({"Edit", "Write"})


def should_run_auto_fix(tool_name: str, config: AutoFixConfig | None) -> bool:
    if config is None:
        return False
    return tool_name in AUTO_FIX_TOOLS


def build_auto_fix_context(result: AutoFixResult) -> str | None:
    """The ``<auto_fix_feedback>`` block, VERBATIM (autoFixHook.ts:16-24).
    None when there are no errors / no summary."""
    if not result.has_errors or not result.error_summary:
        return None
    return (
        "<auto_fix_feedback>\n"
        "AUTO-FIX: The file you just edited has errors. Please fix them:\n\n"
        f"{result.error_summary}\n\n"
        "Please fix these errors in the files you just edited. "
        "Do not ask the user — just apply the fix.\n"
        "</auto_fix_feedback>"
    )


def build_max_retries_context(max_retries: int) -> str:
    """The max-retries-reached ``<auto_fix_feedback>`` (toolHooks.ts:216-220),
    verbatim."""
    return (
        "<auto_fix_feedback>\n"
        f"AUTO-FIX: Maximum retry limit ({max_retries}) reached. "
        "Skipping further auto-fix attempts. Please review the errors "
        "manually.\n"
        "</auto_fix_feedback>"
    )
