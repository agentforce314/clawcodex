from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Awaitable, Callable, TypeVar

from .errors import (
    FallbackTriggeredError,
    OverloadedError,
    RateLimitError,
    categorize_retryable_api_error,
    is_overloaded_error,
    is_quota_exhausted,
    is_rate_limit_error,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 10
BASE_DELAY_MS = 500
MAX_529_RETRIES = 3


@dataclass
class RetryContext:
    model: str = ""
    max_tokens_override: int | None = None
    thinking_enabled: bool = False
    fast_mode: bool = False


class CannotRetryError(Exception):
    def __init__(self, original_error: Exception, retry_context: RetryContext):
        super().__init__(str(original_error))
        self.original_error = original_error
        self.retry_context = retry_context


@dataclass
class RetryOptions:
    max_retries: int = DEFAULT_MAX_RETRIES
    model: str = ""
    fallback_model: str | None = None
    thinking_enabled: bool = False
    fast_mode: bool = False
    signal: Any = None
    query_source: str | None = None
    initial_consecutive_529_errors: int = 0


@dataclass
class RetryStatusMessage:
    message: str
    attempt: int
    error_type: str
    wait_ms: int = 0


def _compute_backoff_ms(attempt: int, base_delay_ms: int = BASE_DELAY_MS) -> int:
    delay = base_delay_ms * (2 ** (attempt - 1))
    jitter = random.uniform(0, delay * 0.25)
    return int(delay + jitter)


def _parse_retry_after(error: Exception) -> float | None:
    headers = getattr(error, "headers", None) or getattr(error, "response_headers", None)
    if headers:
        retry_after = None
        if isinstance(headers, dict):
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
        elif hasattr(headers, "get"):
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after is not None:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
    return None


async def with_retry(
    operation: Callable[..., Awaitable[T]],
    options: RetryOptions,
    on_status: Callable[[RetryStatusMessage], None] | None = None,
) -> T:
    retry_context = RetryContext(
        model=options.model,
        thinking_enabled=options.thinking_enabled,
        fast_mode=options.fast_mode,
    )

    consecutive_529_errors = options.initial_consecutive_529_errors
    last_error: Exception | None = None

    for attempt in range(1, options.max_retries + 2):
        if options.signal and getattr(options.signal, "aborted", False):
            raise asyncio.CancelledError("Aborted")

        try:
            result = await operation(attempt, retry_context)
            return result
        except Exception as error:
            last_error = error

            classification = categorize_retryable_api_error(error)

            if not classification.retryable:
                raise CannotRetryError(error, retry_context) from error

            if is_quota_exhausted(error):
                raise CannotRetryError(error, retry_context) from error

            if is_overloaded_error(error):
                consecutive_529_errors += 1
                if consecutive_529_errors > MAX_529_RETRIES:
                    if options.fallback_model and options.fallback_model != options.model:
                        retry_context.model = options.fallback_model
                        consecutive_529_errors = 0
                        if on_status:
                            on_status(RetryStatusMessage(
                                message=f"Falling back to {options.fallback_model} after {MAX_529_RETRIES} overloaded errors",
                                attempt=attempt,
                                error_type="fallback",
                            ))
                        continue
                    raise CannotRetryError(error, retry_context) from error
            else:
                consecutive_529_errors = 0

            if attempt > options.max_retries:
                raise CannotRetryError(error, retry_context) from error

            retry_after = _parse_retry_after(error)
            if retry_after is not None:
                wait_ms = int(retry_after * 1000)
            else:
                wait_ms = _compute_backoff_ms(attempt)

            if on_status:
                on_status(RetryStatusMessage(
                    message=f"Retry attempt {attempt}: {classification.error_type} — waiting {wait_ms}ms",
                    attempt=attempt,
                    error_type=classification.error_type,
                    wait_ms=wait_ms,
                ))

            await asyncio.sleep(wait_ms / 1000.0)

    if last_error:
        raise CannotRetryError(last_error, retry_context) from last_error

    raise RuntimeError("with_retry exhausted without result")


@dataclass
class _RetryResult:
    """Sentinel wrapper that lets ``with_retry_stream`` yield a final value.

    Python async generators don't surface a generator's ``return`` value
    the way TS does. So ``with_retry_stream`` yields ``RetryStatusMessage``
    objects between attempts, then yields ``_RetryResult(value=...)`` as
    its last item before returning. Callers distinguish the two by type.
    """
    value: Any


async def with_retry_stream(
    get_client: Callable[[], Awaitable[Any]],
    operation: Callable[[Any, int, RetryContext], Awaitable[T]],
    options: RetryOptions,
) -> AsyncGenerator[Any, None]:
    """Yield-based retry wrapper.

    Mirrors TS ``withRetry`` (services/api/withRetry.ts:179-536). Unlike the
    plain ``with_retry`` (callback-based) above, this variant yields
    ``RetryStatusMessage`` objects between attempts so the caller (typically
    a streaming pipeline) can surface retry progress to the UI as part of
    the same event stream — no side-channel notifications, no callbacks.

    The generator's final yielded item is always a ``_RetryResult`` wrapping
    the operation's return value. Callers iterate, surface status messages,
    and unwrap the final result::

        async for item in with_retry_stream(get_client, op, opts):
            if isinstance(item, _RetryResult):
                value = item.value
                break
            else:
                yield item  # RetryStatusMessage → surface to UI

    Differences from TS:
    - The ``get_client`` factory is called on the first attempt and then
      again after auth-related errors or ``ECONNRESET``/``EPIPE``. The
      client is cached between attempts.
    - There is no separate "persistent retry" mode (TS ``UNATTENDED_RETRY``
      / ``CLAUDE_CODE_UNATTENDED_RETRY``). The unattended path is internal-
      only on the TS side and not part of the Chapter 4 contract.
    """
    retry_context = RetryContext(
        model=options.model,
        thinking_enabled=options.thinking_enabled,
        fast_mode=options.fast_mode,
    )

    consecutive_529_errors = options.initial_consecutive_529_errors
    last_error: Exception | None = None
    client: Any = None

    for attempt in range(1, options.max_retries + 2):
        if options.signal and getattr(options.signal, "aborted", False):
            raise asyncio.CancelledError("Aborted")

        # Refresh the client on the first attempt and after errors that
        # invalidate it (auth failures, stale-connection signatures).
        if client is None or _client_needs_refresh(last_error):
            client = await get_client()

        try:
            result = await operation(client, attempt, retry_context)
            yield _RetryResult(value=result)
            return
        except Exception as error:
            last_error = error
            classification = categorize_retryable_api_error(error)

            if not classification.retryable:
                raise CannotRetryError(error, retry_context) from error

            if is_quota_exhausted(error):
                raise CannotRetryError(error, retry_context) from error

            if is_overloaded_error(error):
                consecutive_529_errors += 1
                if consecutive_529_errors > MAX_529_RETRIES:
                    if (
                        options.fallback_model
                        and options.fallback_model != retry_context.model
                    ):
                        retry_context.model = options.fallback_model
                        consecutive_529_errors = 0
                        yield RetryStatusMessage(
                            message=(
                                f"Server overloaded — falling back to "
                                f"{options.fallback_model}"
                            ),
                            attempt=attempt,
                            error_type="fallback",
                        )
                        continue
                    raise CannotRetryError(error, retry_context) from error
            else:
                consecutive_529_errors = 0

            if attempt > options.max_retries:
                raise CannotRetryError(error, retry_context) from error

            retry_after = _parse_retry_after(error)
            if retry_after is not None:
                wait_ms = int(retry_after * 1000)
            else:
                wait_ms = _compute_backoff_ms(attempt)

            yield RetryStatusMessage(
                message=f"Retry attempt {attempt}: {classification.error_type}",
                attempt=attempt,
                error_type=classification.error_type,
                wait_ms=wait_ms,
            )

            await asyncio.sleep(wait_ms / 1000.0)

    if last_error:
        raise CannotRetryError(last_error, retry_context) from last_error
    raise RuntimeError("with_retry_stream exhausted without result")


def _client_needs_refresh(error: Exception | None) -> bool:
    """Should the next attempt construct a fresh client?

    Mirrors the auth/stale-connection refresh trigger in TS
    ``withRetry.ts:241-260``. We refresh on:

    - HTTP 401 (auth expired / invalid)
    - HTTP 403 (token revoked / Bedrock auth)
    - ``ECONNRESET`` / ``EPIPE`` (stale keep-alive socket)

    A ``None`` error (first attempt) doesn't need refresh — that's the
    initial client construction. The caller handles that case before
    calling this helper.
    """
    if error is None:
        return False
    status = getattr(error, "status", getattr(error, "status_code", None))
    if status in (401, 403):
        return True
    if isinstance(error, (ConnectionError, OSError)):
        # ECONNRESET / EPIPE / broken-pipe family
        return True
    return False
