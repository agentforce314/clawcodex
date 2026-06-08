"""Query middleware — 二开 request throttling and error policies.

Extracted from ``src/query/query.py`` so that the upstream query loop
remains free of orchestrator-specific rate-limiting and debugging
concerns.

Architecture::

    src/query/query.py                    ← upstream query loop (calls hooks below)
        ↑ import
    extensions/api/query_middleware.py    ← this module (二开 middleware)

Two public hooks are exposed:

* ``enforce_request_delay()`` — called before each provider API call to
  honour the ``CLAWCODEX_PROVIDER_REQUEST_DELAY_MS`` env var set by the
  orchestrator's ``AgentConfig.delay_between_requests_ms``.
* ``handle_rate_limit_error()`` — called when a provider raises an
  exception whose string contains ``"429"`` or ``"rate_limit"``.
  Returns an ``AssistantMessage`` tagged with ``_api_error =
  "rate_limit"`` so the query loop's error-classification path treats
  it as a retriable condition rather than a fatal model error.
"""

from __future__ import annotations

import os
import threading
import time


# ---------------------------------------------------------------------------
# Request-delay throttle
# ---------------------------------------------------------------------------

# Tracks the last provider API call timestamp (monotonic clock) for the
# delay_between_requests_ms mechanism. Module-level so the cooldown is
# enforced across all call sites in the same process. Initialised to 0
# so the first request is never delayed.
_last_provider_request_time: float = 0.0
_request_delay_lock = threading.Lock()


def enforce_request_delay() -> None:
    """Sleep if necessary to maintain the per-request minimum interval.

    Reads ``CLAWCODEX_PROVIDER_REQUEST_DELAY_MS`` from the environment
    (set by the orchestrator's ``AgentConfig.delay_between_requests_ms``).
    Under the hood uses a module-level monotonic-clock timestamp so the
    delay is measured wall-clock to wall-clock across all concurrent callers.
    """
    delay_ms_str = os.environ.get("CLAWCODEX_PROVIDER_REQUEST_DELAY_MS", "0")
    try:
        delay_ms = int(delay_ms_str)
    except (ValueError, TypeError):
        delay_ms = 0
    if delay_ms <= 0:
        return

    global _last_provider_request_time  # noqa: PLW0603
    now = time.monotonic()
    with _request_delay_lock:
        elapsed = now - _last_provider_request_time
        remaining = (delay_ms / 1000.0) - elapsed
        if remaining > 0 and _last_provider_request_time > 0:
            time.sleep(remaining)
        _last_provider_request_time = time.monotonic()


# ---------------------------------------------------------------------------
# Rate-limit error policy
# ---------------------------------------------------------------------------

def handle_rate_limit_error(error_str: str):
    """Return a tagged error message if *error_str* indicates rate-limiting.

    Returns ``None`` if the error is not a 429 / rate_limit error, so the
    caller can fall through to other error classifications.

    NOTE: This function constructs the ``AssistantMessage`` locally rather
    than importing ``_create_assistant_api_error_message`` from
    ``src.query.query`` to avoid circular imports (the query module imports
    this module at call time, not at module load).
    """
    if "429" not in error_str and "rate_limit" not in error_str.lower():
        return None
    # Lazy import to avoid circular dependency — this function is only
    # called on error, so the import cost is negligible.
    from src.types.messages import AssistantMessage
    err_msg = AssistantMessage(
        content="Rate limit exceeded. Please wait and retry.",
        isApiErrorMessage=True,
    )
    err_msg._api_error = "rate_limit"  # type: ignore[attr-defined]
    return err_msg


__all__ = [
    "enforce_request_delay",
    "handle_rate_limit_error",
]
