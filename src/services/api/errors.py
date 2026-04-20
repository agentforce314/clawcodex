from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

API_ERROR_MESSAGE_PREFIX = "API Error"
PROMPT_TOO_LONG_ERROR_MESSAGE = "Prompt is too long"


class PromptTooLongError(Exception):
    def __init__(
        self,
        message: str = PROMPT_TOO_LONG_ERROR_MESSAGE,
        actual_tokens: int | None = None,
        limit_tokens: int | None = None,
    ):
        super().__init__(message)
        self.actual_tokens = actual_tokens
        self.limit_tokens = limit_tokens

    @property
    def token_gap(self) -> int | None:
        if self.actual_tokens is not None and self.limit_tokens is not None:
            gap = self.actual_tokens - self.limit_tokens
            return gap if gap > 0 else None
        return None


class MaxOutputTokensError(Exception):
    def __init__(self, message: str = "Max output tokens reached"):
        super().__init__(message)


class RateLimitError(Exception):
    def __init__(
        self,
        message: str = "Rate limit exceeded",
        status: int = 429,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class OverloadedError(Exception):
    def __init__(self, message: str = "API overloaded", status: int = 529):
        super().__init__(message)
        self.status = status


class FallbackTriggeredError(Exception):
    def __init__(self, original_model: str, fallback_model: str):
        super().__init__(f"Model fallback triggered: {original_model} -> {fallback_model}")
        self.original_model = original_model
        self.fallback_model = fallback_model


class APIConnectionError(Exception):
    def __init__(self, message: str = "API connection error"):
        super().__init__(message)


class APITimeoutError(Exception):
    def __init__(self, message: str = "Request timed out"):
        super().__init__(message)


class InvalidAPIKeyError(Exception):
    def __init__(self, message: str = "Invalid API key"):
        super().__init__(message)


def parse_prompt_too_long_token_counts(raw_message: str) -> tuple[int | None, int | None]:
    match = re.search(r"prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)", raw_message, re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def is_prompt_too_long_error(error: Exception) -> bool:
    msg = str(error).lower()
    return "prompt is too long" in msg or "prompt_too_long" in msg


def is_rate_limit_error(error: Exception) -> bool:
    if isinstance(error, RateLimitError):
        return True
    status = getattr(error, "status", getattr(error, "status_code", None))
    return status == 429


def is_overloaded_error(error: Exception) -> bool:
    if isinstance(error, OverloadedError):
        return True
    status = getattr(error, "status", getattr(error, "status_code", None))
    return status == 529


def is_quota_exhausted(error: Exception) -> bool:
    msg = str(error).lower()
    status = getattr(error, "status", getattr(error, "status_code", None))
    return status == 429 and ("limit: 0" in msg or "exceeded your current quota" in msg)


def is_invalid_api_key(error: Exception) -> bool:
    status = getattr(error, "status", getattr(error, "status_code", None))
    return status == 401


def is_media_size_error(raw: str) -> bool:
    return (
        ("image exceeds" in raw and "maximum" in raw)
        or ("image dimensions exceed" in raw and "many-image" in raw)
        or bool(re.search(r"maximum of \d+ PDF pages", raw))
    )


@dataclass(frozen=True)
class ErrorClassification:
    retryable: bool
    error_type: str
    message: str


def categorize_retryable_api_error(error: Exception) -> ErrorClassification:
    if is_quota_exhausted(error):
        return ErrorClassification(
            retryable=False,
            error_type="quota_exhausted",
            message="API quota exhausted",
        )

    if is_invalid_api_key(error):
        return ErrorClassification(
            retryable=False,
            error_type="invalid_api_key",
            message="Invalid API key",
        )

    if is_prompt_too_long_error(error):
        return ErrorClassification(
            retryable=False,
            error_type="prompt_too_long",
            message=str(error),
        )

    if is_overloaded_error(error):
        return ErrorClassification(
            retryable=True,
            error_type="overloaded",
            message="API overloaded (529)",
        )

    if is_rate_limit_error(error):
        return ErrorClassification(
            retryable=True,
            error_type="rate_limit",
            message="Rate limited (429)",
        )

    status = getattr(error, "status", getattr(error, "status_code", None))
    if status and status >= 500:
        return ErrorClassification(
            retryable=True,
            error_type="server_error",
            message=f"Server error ({status})",
        )

    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return ErrorClassification(
            retryable=True,
            error_type="connection_error",
            message=str(error),
        )

    return ErrorClassification(
        retryable=False,
        error_type="unknown",
        message=str(error),
    )
