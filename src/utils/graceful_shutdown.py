"""SIGINT / SIGTERM / atexit-coordinated graceful shutdown.

Mirrors ``typescript/src/utils/gracefulShutdown.ts`` +
``typescript/src/utils/cleanupRegistry.ts``. Idempotent registration,
single-execution semantics across the atexit / signal-handler paths.

Plan reference: ``my-docs/ch02-bootstrap-refactoring-plan.md`` P1.2.

Design notes
------------
* **Single execution under racing handlers.** A SIGTERM can fire while
  atexit is already running, and ``sys.exit`` inside the signal handler
  triggers atexit a second time. ``_shutdown_started`` is the
  one-shot latch that ensures each cleanup runs exactly once regardless
  of which path triggers the drain.
* **Best-effort cleanups.** Exceptions inside individual cleanups are
  caught and logged to stderr; they never propagate. This matches the
  TS reference, which is explicit that "we never want a buggy cleanup
  to block process exit."
* **Thread safety.** ``register_cleanup`` may be called from worker
  threads (e.g., the api_preconnect worker). The lock protects the
  list during append/iterate; we snapshot the list under the lock,
  then run callbacks outside the lock to avoid deadlock if a cleanup
  itself re-registers.
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
from typing import Callable

__all__ = [
    "register_cleanup",
    "setup_graceful_shutdown",
    "graceful_shutdown_sync",
    "reset_for_test_only",
]


_cleanups: list[Callable[[], None]] = []
_cleanups_lock = threading.Lock()
_shutdown_started = False
_setup_done = False


def register_cleanup(fn: Callable[[], None]) -> None:
    """Register an idempotent cleanup to run on shutdown.

    Cleanups must be safe to call more than once (the drain may be
    re-entered between SIGTERM and atexit). Register order ≈ run order,
    but don't depend on it for correctness.
    """
    with _cleanups_lock:
        _cleanups.append(fn)


def setup_graceful_shutdown() -> None:
    """Install SIGINT / SIGTERM / atexit handlers. Idempotent.

    Mirrors TS ``setupGracefulShutdown``. Called from ``init()``.
    Safe to call multiple times — second call is a no-op.

    Signal handlers fail silently on non-main-thread invocation (test
    contexts run setup from worker threads); this matches TS's
    permissive registration.
    """
    global _setup_done
    if _setup_done:
        return
    _setup_done = True

    def _signal_handler(signum: int, _frame: object) -> None:
        # Encode the signal in the exit code: 128 + signal number is
        # the POSIX convention (e.g., SIGINT=2 → exit 130).
        graceful_shutdown_sync(128 + signum)

    try:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
        # SIGTSTP is the signal the foreground promotion machinery
        # races on — handle it gracefully so Ctrl+B from a raw TTY
        # still triggers clean background promotion.
        signal.signal(signal.SIGTSTP, _signal_handler)
    except ValueError:
        # signal.signal raises ValueError when called off the main
        # thread; this is OK in test contexts where setup may run
        # under pytest-xdist workers.
        pass

    atexit.register(_run_all_cleanups)


def _run_all_cleanups() -> None:
    """Drain registered cleanups exactly once.

    Re-entry guard: if the drain has already started (because SIGTERM
    fired first and called sys.exit → atexit), subsequent calls
    no-op. The list is snapshotted under the lock so a cleanup that
    re-registers (rare) doesn't iterate forever.
    """
    global _shutdown_started
    with _cleanups_lock:
        if _shutdown_started:
            return
        _shutdown_started = True
        pending = list(_cleanups)
    for fn in pending:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — best-effort
            try:
                name = getattr(fn, "__name__", repr(fn))
                sys.stderr.write(f"cleanup error in {name}: {exc}\n")
            except Exception:
                pass  # last-resort: never block exit on logging


def graceful_shutdown_sync(code: int = 0) -> None:
    """Run all cleanups then exit with ``code``.

    Mirrors TS ``gracefulShutdownSync``. Used by signal handlers and
    error paths that need a coordinated exit (not bare ``sys.exit``).
    """
    _run_all_cleanups()
    sys.exit(code)


def reset_for_test_only() -> None:
    """Wipe registered cleanups and the setup-done latch. Test-only.

    Gated by ``PYTEST_CURRENT_TEST`` so production callers cannot
    accidentally re-arm the signal handlers mid-session. Matches the
    discipline used by ``bootstrap.state.reset_state_for_tests``.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") is None:
        raise RuntimeError(
            "reset_for_test_only can only be called in tests"
        )
    global _shutdown_started, _setup_done
    with _cleanups_lock:
        _cleanups.clear()
        _shutdown_started = False
        _setup_done = False
