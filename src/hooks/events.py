"""Hook event emission stream.

Phase-6 / WI-6.1. Mirrors TS ``typescript/src/utils/hooks/hookEvents.ts``.

Subscribers (UI / SDK / telemetry) register handlers that receive
notifications when hooks fire. The chapter §"Hook Event Emission" calls
this out as the integration surface for "hook running" spinners,
permission-decision attribution UIs, and structured telemetry pipes.

Emission seams (post-Phase 4):

  * ``emit_hook_started`` — fired before each hook executes (sub-process
    spawn, HTTP POST, LLM call). Lets subscribers show a per-hook
    spinner with the command/source.
  * ``emit_hook_response`` — fired after each hook returns. Carries the
    hook's exit code, duration, and ``blocking_error`` if any. Pairs
    with ``emit_hook_started`` for matched start/stop bracketing.
  * ``emit_hook_aggregated`` — fired ONCE per ``_run_hooks_for_event``
    invocation, after Phase-4 aggregation. Carries the
    ``AggregatedHookResult`` so subscribers see the final decision +
    full ``contributing_reasons`` attribution without re-deriving from
    individual responses. New in Phase 6.

**Error isolation contract (chapter requirement).** A subscriber that
raises must NOT break the executor or other subscribers. Each handler is
called inside a try/except; failures are logged at WARNING and dispatch
continues to the next subscriber.

**Concurrency.** Subscriber state is module-level. Adds and removes are
brief sync mutations under a ``threading.Lock`` (the executor is async
but emits are synchronous-from-the-emitter's-perspective; subscribers
wishing to do async work create their own task internally). The lock
is short-held — only the list copy / append / remove. Dispatch iterates
a snapshot of the handler list, so a handler can deregister itself
safely.

**Idempotent unregister.** ``register_hook_event_handler`` returns a
deregister function. Calling it twice is a no-op; the second call
returns without error. Tests pin this property because subscribers
typically wrap registration in context managers and want exception-safe
cleanup.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Literal

from .hook_types import HookEvent, HookSource, HookType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subscriber API
# ---------------------------------------------------------------------------

# Handlers receive a dict-shaped payload. Using a dict (rather than typed
# event objects) keeps the surface flexible for SDK consumers that may
# want to forward payloads as-is over a wire protocol. The ``type`` key
# is the discriminator: ``hook_started`` / ``hook_response`` /
# ``hook_aggregated``.
HookEventHandler = Callable[[dict[str, Any]], None]


_handlers_lock = threading.Lock()
_handlers: list[HookEventHandler] = []
_enabled: bool = True


def register_hook_event_handler(handler: HookEventHandler) -> Callable[[], None]:
    """Register a subscriber. Returns an idempotent deregister function.

    The returned function is safe to call multiple times; a second call
    is a no-op. This matches the "context-manager-cleanup" pattern most
    subscribers use.
    """
    with _handlers_lock:
        _handlers.append(handler)

    deregistered = {"done": False}

    def _deregister() -> None:
        if deregistered["done"]:
            return
        deregistered["done"] = True
        with _handlers_lock:
            try:
                _handlers.remove(handler)
            except ValueError:
                # Already removed (e.g., via clear_hook_event_state).
                pass

    return _deregister


def set_all_hook_events_enabled(enabled: bool) -> None:
    """Globally enable/disable emission. Useful for tests that want to
    silence the stream without unregistering individual subscribers,
    and for runtime configuration (e.g., disabling for performance-
    sensitive code paths).
    """
    global _enabled
    _enabled = enabled


def clear_hook_event_state() -> None:
    """Remove all subscribers + reset the enabled flag. Test fixtures
    use this between cases to guarantee isolation.
    """
    global _enabled
    with _handlers_lock:
        _handlers.clear()
    _enabled = True


class LazyJsonPayload(dict):
    """Phase-9 / WI-9.2 — dict subclass with a memoized ``json``
    property for subscribers that need a wire-format serialization.

    Two performance properties this wrapper preserves:

      1. **Zero serialization cost when no subscriber wants JSON.**
         If subscribers all consume the dict directly (the common case
         for in-process consumers — TUI / SDK callbacks), ``json`` is
         never called and ``json.dumps`` never runs.
      2. **Single serialization shared across all subscribers that
         want JSON.** If three subscribers (telemetry pipe + audit log
         + remote forwarder) each access ``payload.json``, the
         serialization happens exactly once — the result is cached on
         first access.

    Inheriting from ``dict`` keeps full back-compat with subscribers
    that read fields directly via ``payload["type"]`` /
    ``payload.get("event")``. The lazy property is opt-in.

    Memoization is thread-safe via ``threading.Lock`` — multiple
    subscribers accessing ``payload.json`` concurrently will see one
    serialization pass, not N races. The lock is per-instance (each
    event has its own); contention is bounded by the number of
    subscribers requesting JSON for a single event.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Cached JSON; ``None`` is the not-yet-computed sentinel. Empty
        # string would be a valid payload (empty dict serializes to
        # ``"{}"`` not ``""``), so ``None`` is unambiguous.
        self._json_cache: str | None = None
        self._json_lock = threading.Lock()

    @property
    def json(self) -> str:
        """Return the JSON-serialized payload, computing on first
        access and caching for subsequent calls.

        Subscribers that don't need JSON pay zero serialization cost.
        Subscribers that DO need JSON pay it exactly once across all
        consumers of this event.
        """
        if self._json_cache is not None:
            return self._json_cache
        with self._json_lock:
            # Double-check after lock acquisition (another thread may
            # have populated the cache while we waited).
            if self._json_cache is None:
                self._json_cache = json.dumps(dict(self), default=str)
            return self._json_cache


def _dispatch(event: dict[str, Any]) -> None:
    """Fan out one event to all current subscribers.

    Iterates a snapshot of the handler list so a subscriber can
    deregister itself or others mid-iteration safely. Each handler is
    called inside a try/except; failures are logged at WARNING and
    don't break the dispatch loop.

    The event is wrapped in a ``LazyJsonPayload`` (Phase-9 / WI-9.2):
    subscribers can either read fields directly via dict access (zero
    serialization cost) or request a memoized JSON serialization via
    ``event.json``. The wrapper is transparent to existing subscribers
    that treat the event as a plain dict.
    """
    if not _enabled:
        return
    # Wrap with lazy-JSON memoization. ``LazyJsonPayload`` IS a dict,
    # so all existing subscribers continue to work unchanged.
    payload = LazyJsonPayload(event)
    # Snapshot under the lock so concurrent register/unregister doesn't
    # race the iteration. Lock release is fast — we copy the small list,
    # then iterate without holding it (so subscribers can safely call
    # register/deregister recursively).
    with _handlers_lock:
        snapshot = list(_handlers)
    for h in snapshot:
        try:
            h(payload)
        except Exception:
            # Subscriber crashes do NOT break the executor or other
            # subscribers. Logged at WARNING (not exception, to avoid
            # massive tracebacks for buggy subscribers).
            logger.warning(
                "hook event handler raised; continuing", exc_info=True,
            )


# ---------------------------------------------------------------------------
# Emission API — called from src/hooks/hook_executor.py
# ---------------------------------------------------------------------------


def emit_hook_started(
    *,
    hook_id: str,
    event: HookEvent | str,
    hook_type: HookType | str = "command",
    command: str | None = None,
    source: HookSource | str | None = None,
    tool_use_id: str = "",
) -> None:
    """Fire a ``hook_started`` event before a hook executes.

    Pairs with ``emit_hook_response`` to bracket the hook's run. The
    ``hook_id`` ties the start to the matching response — typically a
    composition of (event, command-hash, sequence) so concurrent hooks
    don't collide.
    """
    _dispatch({
        "type": "hook_started",
        "hook_id": hook_id,
        "event": str(event),
        "hook_type": str(hook_type),
        "command": command,
        "source": str(source) if source is not None else None,
        "tool_use_id": tool_use_id,
    })


def emit_hook_response(
    *,
    hook_id: str,
    event: HookEvent | str,
    exit_code: int | None,
    duration_ms: int | None,
    blocking_error: str | None = None,
    permission_behavior: str | None = None,
    command: str | None = None,
) -> None:
    """Fire a ``hook_response`` event after a hook returns.

    The ``permission_behavior`` field is per-hook (the hook's own
    decision), NOT the aggregated decision. Subscribers that want the
    aggregated decision listen for ``hook_aggregated`` instead.
    """
    _dispatch({
        "type": "hook_response",
        "hook_id": hook_id,
        "event": str(event),
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "blocking_error": blocking_error,
        "permission_behavior": permission_behavior,
        "command": command,
    })


def emit_hook_aggregated(
    *,
    event: HookEvent | str,
    aggregated: Any,
) -> None:
    """Fire a ``hook_aggregated`` event after Phase-4 aggregation.

    ``aggregated`` is the ``AggregatedHookResult`` from
    ``src.hooks.aggregation``. Subscribers can read
    ``contributing_reasons`` for full per-hook attribution without
    needing to track ``hook_response`` events themselves.

    Fires once per ``_run_hooks_for_event`` invocation that produced at
    least one result. Skipped when the executor's collected results list
    is empty (no hooks fired → no aggregation → no event).
    """
    _dispatch({
        "type": "hook_aggregated",
        "event": str(event),
        "aggregated": aggregated,
    })
