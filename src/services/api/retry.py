from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

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
