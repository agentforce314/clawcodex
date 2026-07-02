from __future__ import annotations

import unittest

from src.services.api.errors import (
    APIConnectionError,
    APITimeoutError,
    ErrorClassification,
    IMAGE_UNSUPPORTED_ERROR_MESSAGE,
    InvalidAPIKeyError,
    MaxOutputTokensError,
    OverloadedError,
    PromptTooLongError,
    RateLimitError,
    categorize_retryable_api_error,
    is_image_unsupported_error,
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


class TestIsImageUnsupportedError(unittest.TestCase):
    # Pin every substring the classifier matches so a downstream regex
    # refactor can't silently narrow the set and reintroduce the
    # context-stuck bug for any one provider's wording.
    def test_openrouter_404_phrase(self) -> None:
        self.assertTrue(
            is_image_unsupported_error(
                "Error code: 404 - {'error': {'message': "
                "'No endpoints found that support image input', 'code': 404}}"
            )
        )

    def test_paraphrase_does_not_support(self) -> None:
        self.assertTrue(
            is_image_unsupported_error("This model does not support image input")
        )

    def test_paraphrase_doesnt_support(self) -> None:
        self.assertTrue(
            is_image_unsupported_error("Selected model doesn't support image content")
        )

    def test_paraphrase_image_input_is_not_supported(self) -> None:
        self.assertTrue(
            is_image_unsupported_error("Image input is not supported by deepseek-v4")
        )

    def test_paraphrase_snake_case_code(self) -> None:
        self.assertTrue(is_image_unsupported_error("error: image_input_not_supported"))

    def test_paraphrase_model_does_not_accept(self) -> None:
        self.assertTrue(
            is_image_unsupported_error("Model does not accept image content blocks")
        )

    def test_case_insensitive(self) -> None:
        self.assertTrue(
            is_image_unsupported_error("NO ENDPOINTS FOUND THAT SUPPORT IMAGE INPUT")
        )

    # Negative cases — make sure we don't accidentally classify adjacent
    # but distinct errors (PTL, media-size, generic 404) as
    # image_unsupported, which would route them away from their own
    # recovery paths.
    def test_negative_prompt_too_long(self) -> None:
        self.assertFalse(
            is_image_unsupported_error("prompt is too long: 250000 tokens > 200000")
        )

    def test_negative_media_size(self) -> None:
        self.assertFalse(
            is_image_unsupported_error("image exceeds the maximum allowed size")
        )

    def test_negative_generic_404(self) -> None:
        self.assertFalse(is_image_unsupported_error("404 Not Found"))

    def test_negative_empty(self) -> None:
        self.assertFalse(is_image_unsupported_error(""))


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

    def test_image_unsupported_not_retryable(self) -> None:
        # Pins the contract used by the engine's strip-and-recover path:
        # the retry layer must NOT loop on a permanent capability error.
        err = Exception("No endpoints found that support image input")
        result = categorize_retryable_api_error(err)
        self.assertFalse(result.retryable)
        self.assertEqual(result.error_type, "image_unsupported")


class TestImageUnsupportedErrorMessage(unittest.TestCase):
    def test_message_mentions_strip_and_alternative(self) -> None:
        # Pins user-facing wording: must (1) say the image was removed
        # so the user understands why future turns "forget" the image,
        # and (2) suggest a class of alternative model (kept provider-
        # neutral so the suggestion doesn't go stale when model names
        # change).
        self.assertIn("removed from conversation history", IMAGE_UNSUPPORTED_ERROR_MESSAGE)
        self.assertIn("vision-capable", IMAGE_UNSUPPORTED_ERROR_MESSAGE)


if __name__ == "__main__":
    unittest.main()
