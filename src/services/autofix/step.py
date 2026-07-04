"""autoFix post-tool step — port of the toolHooks.ts:196-253 autoFix block.

Runs as a SIBLING of ``run_post_tool_use_hooks`` at the orchestrator (NOT
inside it — that function early-returns when no user PostToolUse hook is
configured, tool_hooks.py:166, which is the common case for an autoFix
user; putting the step there would strand it — the teammate-hooks
split-gate class). This step runs regardless of user hooks, gated only on
the ``settings.autoFix`` opt-in.

D1 (see the plan): TS's tool-name set never matches real tool names, so the
reference feature never fires; this port activates the author's intent
(``{Edit, Write}``) — a documented, opt-in-gated divergence.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from src.types.messages import create_attachment_message

from .config import load_auto_fix_config
from .hook import (
    build_auto_fix_context,
    build_max_retries_context,
    should_run_auto_fix,
)
from .runner import run_auto_fix_check

logger = logging.getLogger(__name__)

# chain_id → consecutive auto-fix attempt count (the TS autoFixRetryCount
# analog). Cleared on a clean run so a later unrelated failure starts fresh.
_auto_fix_retry_count: dict[str, int] = {}


def _chain_key(tool_use_context: Any) -> str:
    qt = getattr(tool_use_context, "query_tracking", None)
    return (getattr(qt, "chain_id", "") if qt else "") or "default"


def _attachment(content: str, tool_name: str, tool_use_id: str) -> dict[str, Any]:
    return {
        "message": create_attachment_message({
            "type": "hook_additional_context",
            "content": [content],
            "hook_name": f"AutoFix:{tool_name}",
            "tool_use_id": tool_use_id,
            "hook_event": "PostToolUse",
        })
    }


async def run_auto_fix_step(
    tool_use_context: Any,
    tool_name: str,
    tool_use_id: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield the ``<auto_fix_feedback>`` attachment(s) after a file edit, or
    nothing. Guarded — an autoFix failure never breaks the tool loop."""
    try:
        config = load_auto_fix_config()
        if not should_run_auto_fix(tool_name, config) or config is None:
            return

        chain_key = _chain_key(tool_use_context)
        current = _auto_fix_retry_count.get(chain_key, 0)
        if current >= config.max_retries:
            yield _attachment(
                build_max_retries_context(config.max_retries), tool_name, tool_use_id
            )
            return

        abort_ctrl = getattr(tool_use_context, "abort_controller", None)
        abort_signal = getattr(abort_ctrl, "signal", None) if abort_ctrl else None
        cwd = getattr(tool_use_context, "cwd", None) or "."

        result = await run_auto_fix_check(
            lint=config.lint,
            test=config.test,
            timeout_ms=config.timeout_ms,
            cwd=str(cwd),
            abort_signal=abort_signal,
        )
        context = build_auto_fix_context(result)
        if context:
            _auto_fix_retry_count[chain_key] = current + 1
            yield _attachment(context, tool_name, tool_use_id)
        else:
            # Clean run — reset the counter (toolHooks.ts:247) so a later
            # unrelated failing edit is not prematurely capped.
            _auto_fix_retry_count.pop(chain_key, None)
    except Exception:  # noqa: BLE001 — autoFix must never break the tool loop
        logger.debug("autofix step failed", exc_info=True)
