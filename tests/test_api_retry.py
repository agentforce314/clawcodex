from __future__ import annotations

import asyncio
import unittest

from src.services.api.retry import (
    CannotRetryError,
    RetryContext,
    RetryOptions,
    RetryStatusMessage,
    _compute_backoff_ms,
    _parse_retry_after,
    with_retry,
)
from src.services.api.errors import (
    OverloadedError,
    RateLimitError,
)


class TestComputeBackoffMs(unittest.TestCase):
    def test_first_attempt(self) -> None:
        delay = _compute_backoff_ms(1, base_delay_ms=500)
        self.assertGreaterEqual(delay, 500)
        self.assertLessEqual(delay, 625)

    def test_second_attempt(self) -> None:
        delay = _compute_backoff_ms(2, base_delay_ms=500)
        self.assertGreaterEqual(delay, 1000)
        self.assertLessEqual(delay, 1250)

    def test_third_attempt(self) -> None:
        delay = _compute_backoff_ms(3, base_delay_ms=500)
        self.assertGreaterEqual(delay, 2000)
        self.assertLessEqual(delay, 2500)


class TestParseRetryAfter(unittest.TestCase):
    def test_dict_headers(self) -> None:
        err = Exception("rate limited")
        err.headers = {"retry-after": "30"}  # type: ignore[attr-defined]
        self.assertEqual(_parse_retry_after(err), 30.0)

    def test_capitalized_header(self) -> None:
        err = Exception("rate limited")
        err.headers = {"Retry-After": "15.5"}  # type: ignore[attr-defined]
        self.assertEqual(_parse_retry_after(err), 15.5)

    def test_no_headers(self) -> None:
        err = Exception("rate limited")
        self.assertIsNone(_parse_retry_after(err))

    def test_invalid_value(self) -> None:
        err = Exception("rate limited")
        err.headers = {"retry-after": "not-a-number"}  # type: ignore[attr-defined]
        self.assertIsNone(_parse_retry_after(err))


class TestWithRetry(unittest.TestCase):
    def test_success_on_first_try(self) -> None:
        async def _run() -> None:
            call_count = 0

            async def operation(attempt: int, ctx: RetryContext) -> str:
                nonlocal call_count
                call_count += 1
                return "ok"

            result = await with_retry(operation, RetryOptions(max_retries=3))
            self.assertEqual(result, "ok")
            self.assertEqual(call_count, 1)

        asyncio.run(_run())

    def test_retry_on_rate_limit(self) -> None:
        async def _run() -> None:
            call_count = 0

            async def operation(attempt: int, ctx: RetryContext) -> str:
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    err = RateLimitError()
                    err.headers = {"retry-after": "0.01"}  # type: ignore[attr-defined]
                    raise err
                return "ok"

            result = await with_retry(operation, RetryOptions(max_retries=5))
            self.assertEqual(result, "ok")
            self.assertEqual(call_count, 3)

        asyncio.run(_run())

    def test_cannot_retry_on_non_retryable(self) -> None:
        async def _run() -> None:
            async def operation(attempt: int, ctx: RetryContext) -> str:
                raise ValueError("bad value")

            with self.assertRaises(CannotRetryError):
                await with_retry(operation, RetryOptions(max_retries=3))

        asyncio.run(_run())

    def test_fallback_model_on_529(self) -> None:
        async def _run() -> None:
            models_used: list[str] = []

            async def operation(attempt: int, ctx: RetryContext) -> str:
                models_used.append(ctx.model)
                if ctx.model == "fallback-model":
                    return "ok"
                raise OverloadedError()

            result = await with_retry(
                operation,
                RetryOptions(
                    max_retries=10,
                    model="primary-model",
                    fallback_model="fallback-model",
                    initial_consecutive_529_errors=2,
                ),
            )
            self.assertEqual(result, "ok")
            self.assertIn("fallback-model", models_used)

        asyncio.run(_run())

    def test_on_status_callback(self) -> None:
        async def _run() -> None:
            statuses: list[RetryStatusMessage] = []

            async def operation(attempt: int, ctx: RetryContext) -> str:
                if attempt < 2:
                    err = RateLimitError()
                    err.headers = {"retry-after": "0.01"}  # type: ignore[attr-defined]
                    raise err
                return "ok"

            result = await with_retry(
                operation,
                RetryOptions(max_retries=5),
                on_status=lambda msg: statuses.append(msg),
            )
            self.assertEqual(result, "ok")
            self.assertEqual(len(statuses), 1)
            self.assertEqual(statuses[0].attempt, 1)

        asyncio.run(_run())

    def test_exhaust_retries(self) -> None:
        async def _run() -> None:
            async def operation(attempt: int, ctx: RetryContext) -> str:
                err = RateLimitError()
                err.headers = {"retry-after": "0.01"}  # type: ignore[attr-defined]
                raise err

            with self.assertRaises(CannotRetryError):
                await with_retry(operation, RetryOptions(max_retries=2))

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
