"""Callback hook executor — synchronous in-process callable.

Phase-9 / WI-9.1. Closes gap analysis #12.

Callback hooks invoke a Python callable directly — no subprocess, no
LLM call, no HTTP. The chapter's "fast path": SDK consumers and TUI
subscribers that want to react to hook events without the cost or
configuration overhead of the other types. The TS chapter cites a
≈70% latency reduction vs command hooks; the Python equivalent is
even bigger because we skip the subprocess + JSON serialization /
deserialization round-trip that command hooks pay.

**Registration.** Callback hooks are programmatic — they're never
loaded from settings.json (a JSON config can't carry a Python
callable). The expected registration path is:

  await add_session_hook(
      registry=registry,
      session_id=session_id,
      event="PreToolUse",
      matcher="Bash",
      hook=HookConfig(
          type="callback",
          callback_ref=lambda event_data: HookResult(
              additional_contexts=["audit log entry"],
          ),
      ),
  )

**Sync vs. async callables.** Both are accepted. The executor checks
``inspect.iscoroutinefunction``; async callables are awaited. Sync
callables run in-line on the event loop — the chapter assumes
callbacks are *fast*, so blocking the loop briefly is acceptable.
Slow callbacks should use ``asyncio.to_thread`` internally.

**Return value contract.** The callable may return either:
  * A ``HookResult`` instance — used directly (decision routing,
    additional_contexts, etc. all flow through aggregation).
  * ``None`` — treated as "exit_code=0, no decision" (the no-op
    success case).

**Exception isolation.** Callbacks that raise are caught at the
executor boundary; the exception becomes ``blocking_error`` on the
HookResult. The aggregator treats this like any blocking_error from
other hook types — first-non-None-wins precedence — and the rest of
the executor pipeline is unaffected.
"""

from __future__ import annotations

import inspect
import logging
import time
from typing import Any

from .hook_types import HookConfig, HookResult

logger = logging.getLogger(__name__)


async def execute_callback_hook(
    hook: HookConfig,
    stdin_data: dict[str, Any],
) -> HookResult:
    """Run a callback hook by invoking its registered Python callable.

    Returns a ``HookResult`` matching the dispatch contract used by
    other hook executors (command / http / prompt / agent). The
    callable may return ``HookResult`` or ``None``; ``None`` becomes a
    no-op-success result. Callable exceptions are converted to
    ``blocking_error`` so the executor pipeline stays robust.
    """
    callback = hook.callback_ref
    if callback is None:
        return HookResult(
            blocking_error=(
                "Callback hook has no callback_ref configured. "
                "Programmatic registration only (not loadable from "
                "settings.json); see add_session_hook."
            ),
            exit_code=-1,
        )

    if not callable(callback):
        return HookResult(
            blocking_error=(
                f"Callback hook's callback_ref is not callable "
                f"(got {type(callback).__name__})"
            ),
            exit_code=-1,
        )

    start_time = time.monotonic()
    try:
        if inspect.iscoroutinefunction(callback):
            outcome = await callback(stdin_data)
        else:
            outcome = callback(stdin_data)
    except Exception as exc:
        # Exception isolation per chapter §"Hook Event Emission" /
        # §"Subscriber Error Isolation": one bad callback must not
        # break the executor pipeline. Convert to blocking_error so
        # the aggregator can route the failure normally.
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.warning(
            "callback hook raised; converting to blocking_error", exc_info=True,
        )
        return HookResult(
            blocking_error=f"Callback hook raised: {exc}",
            exit_code=-1,
            duration_ms=duration_ms,
        )

    duration_ms = int((time.monotonic() - start_time) * 1000)

    if outcome is None:
        # No-op success — callback chose to do nothing actionable.
        return HookResult(exit_code=0, duration_ms=duration_ms)

    if isinstance(outcome, HookResult):
        # Annotate the duration if the callback didn't set it (most
        # callbacks won't bother — they shouldn't have to).
        if outcome.duration_ms is None:
            outcome.duration_ms = duration_ms
        return outcome

    # Anything else — treat as a programmer error in the callback. We
    # don't try to coerce arbitrary values into HookResult shapes;
    # surface the mistake.
    return HookResult(
        blocking_error=(
            f"Callback hook returned an unsupported type "
            f"{type(outcome).__name__}; expected HookResult | None"
        ),
        exit_code=-1,
        duration_ms=duration_ms,
    )
