from __future__ import annotations

import unittest

from src.services.api.errors import (
    APIConnectionError,
    APITimeoutError,
    ErrorClassification,
    FallbackTriggeredError,
    InvalidAPIKeyError,
    MaxOutputTokensError,
    OverloadedError,
    PromptTooLongError,
    RateLimitError,
    categorize_retryable_api_error,
    is_invalid_api_key,
    is_media_size_error,
    is_overloaded_error,
    is_prompt_too_long_error,
    is_quota_exhausted,
    is_rate_limit_error,
    parse_prompt_too_long_token_counts,
)


class TestPromptTooLongError(unittest.TestCase):
    def test_default_message(self) -> None:
        err = PromptTooLongError()
        self.assertIn("Prompt is too long", str(err))

    def test_token_gap_computed(self) -> None:
        err = PromptTooLongError(actual_tokens=250000, limit_tokens=200000)
        self.assertEqual(err.token_gap, 50000)

    def test_token_gap_none_when_no_tokens(self) -> None:
        err = PromptTooLongError()
        self.assertIsNone(err.token_gap)

    def test_token_gap_none_when_under_limit(self) -> None:
        err = PromptTooLongError(actual_tokens=100, limit_tokens=200)
        self.assertIsNone(err.token_gap)


class TestRateLimitError(unittest.TestCase):
    def test_default_status(self) -> None:
        err = RateLimitError()
        self.assertEqual(err.status, 429)

    def test_retry_after(self) -> None:
        err = RateLimitError(retry_after=30.0)
        self.assertEqual(err.retry_after, 30.0)


class TestOverloadedError(unittest.TestCase):
    def test_default_status(self) -> None:
        err = OverloadedError()
        self.assertEqual(err.status, 529)


class TestFallbackTriggeredError(unittest.TestCase):
    def test_stores_models(self) -> None:
        err = FallbackTriggeredError("claude-opus-4-20250514", "claude-sonnet-4-20250514")
        self.assertEqual(err.original_model, "claude-opus-4-20250514")
        self.assertEqual(err.fallback_model, "claude-sonnet-4-20250514")


class TestParsePromptTooLongTokenCounts(unittest.TestCase):
    def test_parses_token_counts(self) -> None:
        msg = "prompt is too long: 250000 tokens > 200000"
        actual, limit = parse_prompt_too_long_token_counts(msg)
        self.assertEqual(actual, 250000)
        self.assertEqual(limit, 200000)

    def test_returns_none_for_no_match(self) -> None:
        actual, limit = parse_prompt_too_long_token_counts("some random error")
        self.assertIsNone(actual)
        self.assertIsNone(limit)


class TestIsPromptTooLongError(unittest.TestCase):
    def test_prompt_too_long_error_detected(self) -> None:
        self.assertTrue(is_prompt_too_long_error(PromptTooLongError()))

    def test_generic_error_with_message(self) -> None:
        self.assertTrue(is_prompt_too_long_error(Exception("prompt is too long")))

    def test_unrelated_error(self) -> None:
        self.assertFalse(is_prompt_too_long_error(Exception("network error")))


class TestIsRateLimitError(unittest.TestCase):
    def test_rate_limit_error_instance(self) -> None:
        self.assertTrue(is_rate_limit_error(RateLimitError()))

    def test_error_with_429_status(self) -> None:
        err = Exception("rate limited")
        err.status = 429  # type: ignore[attr-defined]
        self.assertTrue(is_rate_limit_error(err))

    def test_unrelated_error(self) -> None:
        self.assertFalse(is_rate_limit_error(Exception("foo")))


class TestIsOverloadedError(unittest.TestCase):
    def test_overloaded_error_instance(self) -> None:
        self.assertTrue(is_overloaded_error(OverloadedError()))

    def test_error_with_529_status(self) -> None:
        err = Exception("overloaded")
        err.status = 529  # type: ignore[attr-defined]
        self.assertTrue(is_overloaded_error(err))


class TestIsQuotaExhausted(unittest.TestCase):
    def test_429_with_limit_zero(self) -> None:
        err = Exception("limit: 0")
        err.status = 429  # type: ignore[attr-defined]
        self.assertTrue(is_quota_exhausted(err))

    def test_429_without_quota_message(self) -> None:
        err = Exception("too many requests")
        err.status = 429  # type: ignore[attr-defined]
        self.assertFalse(is_quota_exhausted(err))


class TestIsInvalidApiKey(unittest.TestCase):
    def test_401_status(self) -> None:
        err = Exception("unauthorized")
        err.status = 401  # type: ignore[attr-defined]
        self.assertTrue(is_invalid_api_key(err))

    def test_non_401(self) -> None:
        err = Exception("unauthorized")
        err.status = 403  # type: ignore[attr-defined]
        self.assertFalse(is_invalid_api_key(err))


class TestIsMediaSizeError(unittest.TestCase):
    def test_image_exceeds(self) -> None:
        self.assertTrue(is_media_size_error("image exceeds the maximum allowed"))

    def test_pdf_pages(self) -> None:
        self.assertTrue(is_media_size_error("maximum of 100 PDF pages"))

    def test_unrelated(self) -> None:
        self.assertFalse(is_media_size_error("some other error"))


class TestCategorizeRetryableApiError(unittest.TestCase):
    def test_rate_limit_retryable(self) -> None:
        result = categorize_retryable_api_error(RateLimitError())
        self.assertTrue(result.retryable)
        self.assertEqual(result.error_type, "rate_limit")

    def test_overloaded_retryable(self) -> None:
        result = categorize_retryable_api_error(OverloadedError())
        self.assertTrue(result.retryable)
        self.assertEqual(result.error_type, "overloaded")

    def test_prompt_too_long_not_retryable(self) -> None:
        result = categorize_retryable_api_error(PromptTooLongError())
        self.assertFalse(result.retryable)
        self.assertEqual(result.error_type, "prompt_too_long")

    def test_quota_exhausted_not_retryable(self) -> None:
        err = Exception("limit: 0")
        err.status = 429  # type: ignore[attr-defined]
        result = categorize_retryable_api_error(err)
        self.assertFalse(result.retryable)

    def test_invalid_api_key_not_retryable(self) -> None:
        err = Exception("unauthorized")
        err.status = 401  # type: ignore[attr-defined]
        result = categorize_retryable_api_error(err)
        self.assertFalse(result.retryable)
        self.assertEqual(result.error_type, "invalid_api_key")

    def test_server_error_retryable(self) -> None:
        err = Exception("internal server error")
        err.status = 500  # type: ignore[attr-defined]
        result = categorize_retryable_api_error(err)
        self.assertTrue(result.retryable)
        self.assertEqual(result.error_type, "server_error")

    def test_connection_error_retryable(self) -> None:
        result = categorize_retryable_api_error(ConnectionError("reset"))
        self.assertTrue(result.retryable)
        self.assertEqual(result.error_type, "connection_error")

    def test_unknown_error_not_retryable(self) -> None:
        result = categorize_retryable_api_error(ValueError("bad value"))
        self.assertFalse(result.retryable)
        self.assertEqual(result.error_type, "unknown")


if __name__ == "__main__":
    unittest.main()
