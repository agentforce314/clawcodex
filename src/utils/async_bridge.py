"""Run a coroutine to completion from synchronous code, loop or no loop.

Extracted from ``tool_system/registry._invoke_tool_call``'s async-tool bridge
(HOOKS-1 plan W2) so the permission-ask seam can drive the async hook
executor with the SAME semantics the dispatcher already uses for async
tools:

* No running loop in this thread → plain ``asyncio.run``.
* A loop IS running in this thread → drive the coroutine on a worker
  thread's own fresh loop (Python disallows nested ``run_until_complete``);
  block this thread until it finishes. Callers on such threads are already
  synchronous-by-contract (the registry dispatch path, the permission ask) —
  the bridge preserves that contract without deadlocking the loop thread's
  callbacks, which run on the loop, not here.

No timeout by design — mirror the dispatcher's posture: bounded work is the
coroutine's own job (hook execution carries TOOL_HOOK_EXECUTION_TIMEOUT_MS).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def run_coroutine_blocking(
    coro: Coroutine[Any, Any, T],
    *,
    thread_name: str = "async-bridge",
) -> T:
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is None:
        return asyncio.run(coro)

    holder: dict[str, Any] = {}
    done = threading.Event()

    def _runner() -> None:
        try:
            holder["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raise in the caller
            holder["error"] = exc
        finally:
            done.set()

    threading.Thread(target=_runner, daemon=True, name=thread_name).start()
    done.wait()
    if "error" in holder:
        raise holder["error"]  # type: ignore[misc]
    return holder["result"]  # type: ignore[no-any-return]
