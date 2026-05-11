"""Tests for the yield-based ``with_retry_stream`` generator (Phase D)."""
from __future__ import annotations

import unittest

from src.services.api.errors import OverloadedError, RateLimitError
from src.services.api.retry import (
    CannotRetryError,
    RetryOptions,
    RetryStatusMessage,
    _RetryResult,
    with_retry_stream,
)


class TestWithRetryStream(unittest.IsolatedAsyncioTestCase):
    async def test_success_on_first_attempt_yields_result_only(self) -> None:
        clients_made = 0

        async def get_client() -> object:
            nonlocal clients_made
            clients_made += 1
            return object()

        async def op(client, attempt, ctx) -> str:
            return "ok"

        results = []
        async for item in with_retry_stream(
            get_client, op, RetryOptions(max_retries=3),
        ):
            results.append(item)

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], _RetryResult)
        self.assertEqual(results[0].value, "ok")
        self.assertEqual(clients_made, 1)

    async def test_retry_on_rate_limit_yields_status(self) -> None:
        call_count = 0

        async def get_client() -> object:
            return object()

        async def op(client, attempt, ctx) -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                err = RateLimitError()
                err.headers = {"retry-after": "0.01"}  # type: ignore[attr-defined]
                raise err
            return "ok"

        statuses: list[RetryStatusMessage] = []
        final: object = None
        async for item in with_retry_stream(
            get_client, op, RetryOptions(max_retries=5),
        ):
            if isinstance(item, _RetryResult):
                final = item.value
                break
            statuses.append(item)

        self.assertEqual(final, "ok")
        self.assertEqual(len(statuses), 2)
        self.assertEqual(call_count, 3)
        for status in statuses:
            self.assertEqual(status.error_type, "rate_limit")
            self.assertGreater(status.wait_ms, 0)

    async def test_fallback_model_on_repeated_529(self) -> None:
        models_used: list[str] = []

        async def get_client() -> object:
            return object()

        async def op(client, attempt, ctx) -> str:
            models_used.append(ctx.model)
            if ctx.model == "fallback-model":
                return "fallback-ok"
            raise OverloadedError()

        results = []
        async for item in with_retry_stream(
            get_client, op,
            RetryOptions(
                max_retries=10,
                model="primary-model",
                fallback_model="fallback-model",
                initial_consecutive_529_errors=3,  # one more 529 triggers fallback
            ),
        ):
            results.append(item)

        # Expect: one RetryStatusMessage (fallback announcement), one
        # _RetryResult with the fallback result.
        self.assertIsInstance(results[-1], _RetryResult)
        self.assertEqual(results[-1].value, "fallback-ok")
        self.assertIn("fallback-model", models_used)

    async def test_cannot_retry_on_non_retryable_error(self) -> None:
        async def get_client() -> object:
            return object()

        async def op(client, attempt, ctx) -> str:
            raise ValueError("hard failure, not retryable")

        with self.assertRaises(CannotRetryError):
            async for _ in with_retry_stream(
                get_client, op, RetryOptions(max_retries=3),
            ):
                pass

    async def test_client_refreshed_after_stale_connection(self) -> None:
        """ECONNRESET-class failures (raised as ``ConnectionError``) flag the
        client for refresh on the next attempt — mirrors TS
        ``disableKeepAlive()`` + ``getClient()`` re-call at
        ``withRetry.ts:227-260``.
        """
        clients_made = 0

        async def get_client() -> object:
            nonlocal clients_made
            clients_made += 1
            return object()

        attempt_counter = 0

        async def op(client, attempt, ctx) -> str:
            nonlocal attempt_counter
            attempt_counter += 1
            if attempt_counter == 1:
                # Treated as retryable by categorize_retryable_api_error AND
                # flagged by _client_needs_refresh — so the next attempt
                # constructs a fresh client.
                raise ConnectionError("connection reset by peer")
            return "ok"

        async for _ in with_retry_stream(
            get_client, op, RetryOptions(max_retries=3),
        ):
            pass

        # Should have built the client twice: once initially, once after
        # the stale-connection retry.
        self.assertEqual(clients_made, 2)


if __name__ == "__main__":
    unittest.main()
