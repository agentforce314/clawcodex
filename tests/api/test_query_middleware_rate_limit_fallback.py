"""Contract test for F-48 ``extensions/api/query_middleware.py``.

The fork extracted the rate-limit / throttling logic out of
``src/query/query.py`` into ``extensions/api/query_middleware.py`` so the
upstream query loop stays free of orchestrator-specific concerns. This
test pins the contract between the call sites in ``src/query/query.py``
and the middleware, so a future refactor of either side cannot silently
change behaviour.

Coverage:
* ``handle_rate_limit_error`` returns ``AssistantMessage(_api_error="rate_limit")``
  when the error string mentions ``"429"`` (HTTP status) or contains
  ``"rate_limit"`` (case-insensitive substring). It returns ``None`` for
  unrelated errors.
* ``enforce_request_delay`` honours
  ``CLAWCODEX_PROVIDER_REQUEST_DELAY_MS``. A no-op when the env var is
  unset / zero. The contract is "the first call is never delayed" so
  tests pre-warm the timer with a zero-delay call before measuring.
"""

from __future__ import annotations

import os
import time

import pytest

from extensions.api.query_middleware import (
    enforce_request_delay,
    handle_rate_limit_error,
)


# ---------------------------------------------------------------------------
# handle_rate_limit_error — must match the in-line behaviour of
# ``src/query/query.py:641-654`` byte-for-byte (same predicate:
# ``"429" in error_str or "rate_limit" in error_str.lower()``).
# ---------------------------------------------------------------------------


def test_handle_rate_limit_error_429_returns_tagged_message():
    msg = handle_rate_limit_error("HTTP 429 Too Many Requests")
    assert msg is not None
    assert getattr(msg, "_api_error", None) == "rate_limit"
    assert "rate limit" in msg.content.lower() or "429" in msg.content.lower()


def test_handle_rate_limit_error_phrase_returns_tagged_message():
    # The predicate matches the literal substring "rate_limit" (with
    # underscore, lowercased), not the natural-language phrase
    # "rate limit". A real provider error string uses the snake_case
    # token in its identifier / type.
    msg = handle_rate_limit_error("anthropic_api_rate_limit_exceeded")
    assert msg is not None
    assert getattr(msg, "_api_error", None) == "rate_limit"


def test_handle_rate_limit_error_is_case_insensitive():
    # Predicate lowercases its target — RATE_LIMIT must still trigger.
    msg = handle_rate_limit_error("provider returned RATE_LIMIT retry-after=30")
    assert msg is not None
    assert getattr(msg, "_api_error", None) == "rate_limit"


def test_handle_rate_limit_error_unrelated_returns_none():
    assert handle_rate_limit_error("connection reset by peer") is None
    # 'prompt is too long' must not collide with the rate-limit path —
    # it's a separate classification in src/query/query.py.
    assert handle_rate_limit_error("prompt is too long") is None
    # Auth errors must not be confused for rate-limit either.
    assert handle_rate_limit_error("401 unauthorized") is None
    assert handle_rate_limit_error("internal server error 500") is None


def test_handle_rate_limit_error_assistant_message_shape():
    """The returned message must be an AssistantMessage with the
    ``isApiErrorMessage`` flag so the query loop's image-unsupported
    recovery path doesn't accidentally re-strip it."""
    from src.types.messages import AssistantMessage
    msg = handle_rate_limit_error("429")
    assert isinstance(msg, AssistantMessage)
    assert msg.isApiErrorMessage is True


# ---------------------------------------------------------------------------
# enforce_request_delay — must throttle to at least the requested
# interval between calls, and must be a no-op when no delay is set.
# ---------------------------------------------------------------------------


def test_enforce_request_delay_no_env_var_is_immediate(monkeypatch):
    monkeypatch.delenv("CLAWCODEX_PROVIDER_REQUEST_DELAY_MS", raising=False)
    start = time.perf_counter()
    enforce_request_delay()
    elapsed = time.perf_counter() - start
    # 10ms tolerance for clock noise.
    assert elapsed < 0.01, f"expected no delay, got {elapsed:.3f}s"


def test_enforce_request_delay_zero_ms_is_immediate(monkeypatch):
    monkeypatch.setenv("CLAWCODEX_PROVIDER_REQUEST_DELAY_MS", "0")
    start = time.perf_counter()
    enforce_request_delay()
    elapsed = time.perf_counter() - start
    assert elapsed < 0.01, f"expected no delay, got {elapsed:.3f}s"


def test_enforce_request_delay_enforces_interval(monkeypatch):
    """Set 100ms throttle, pre-warm with a 0-delay call so the second
    call has a recorded ``_last_provider_request_time``, then measure.
    90ms tolerance for clock noise / scheduling jitter on CI."""
    # Reset the module-level timer by patching it through env-var
    # toggling. A direct poke is fragile (the lock + global live in the
    # middleware), so we rely on the documented contract: zero ms never
    # delays, and the first non-zero call IS delayed (the contract is
    # "first request is never delayed" — but ``_last_provider_request_time``
    # was last touched by the zero-delay warm-up, so the second call IS
    # throttled). The pre-warm call must run at least one full interval
    # before the measurement call for the throttle to fire.
    monkeypatch.setenv("CLAWCODEX_PROVIDER_REQUEST_DELAY_MS", "100")
    # Pre-warm: first call, ``_last_provider_request_time`` is 0.0, so
    # the contract says "never delay the first call" and we go through
    # fast. But this ALSO touches ``_last_provider_request_time`` to now().
    enforce_request_delay()
    # Sleep just under the interval so the next call definitely has
    # remaining > 0.
    time.sleep(0.05)
    start = time.perf_counter()
    enforce_request_delay()
    elapsed = time.perf_counter() - start
    # We slept 50ms; remaining is 50ms; so enforce must have slept ~50ms
    # (with jitter). Allow 30ms floor for clock skew.
    assert elapsed >= 0.03, f"expected ≥30ms throttle, got {elapsed:.3f}s"


def test_enforce_request_delay_invalid_env_value_does_not_crash(monkeypatch):
    """A malformed env var must default to 0 (no delay) rather than
    raising. Catches the case where an orchestrator writes a non-integer
    into the env."""
    monkeypatch.setenv("CLAWCODEX_PROVIDER_REQUEST_DELAY_MS", "not-a-number")
    enforce_request_delay()  # must not raise
