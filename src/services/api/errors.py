from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

API_ERROR_MESSAGE_PREFIX = "API Error"
PROMPT_TOO_LONG_ERROR_MESSAGE = "Prompt is too long"
# Surfaced when the provider rejects an image because the selected model
# does not accept image input (e.g. OpenRouter routing a request to a
# text-only DeepSeek endpoint and returning
# "No endpoints found that support image input" / 404).
#
# The user-facing wording follows the shape of TypeScript's friendly
# error messages at typescript/src/services/api/errors.ts (e.g.
# getPdfInvalidErrorMessage / getImageTooLargeErrorMessage) — clear
# about what happened and what the user can do. The TypeScript code
# has no dedicated handler for this specific capability rejection (the
# error would land in the catch-all at typescript/src/query.ts:1065),
# so this Python branch is genuinely new behaviour, not a port. We
# additionally strip the offending image from history (see
# QueryEngine.submit_message); TypeScript expects the user to manually
# "Double press esc" via the Ink MessageSelector, which the Rich REPL
# does not have.
IMAGE_UNSUPPORTED_ERROR_MESSAGE = (
    "The current model does not accept image input. The image has been "
    "removed from conversation history so subsequent requests will work. "
    "Switch to a vision-capable model to process images."
)


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


#: Exception type names meaning "the request never reached an HTTP verdict":
#: DNS/TLS/connect failures, socket timeouts, and mid-flight drops.
#:
#: Matched by MRO type name rather than ``isinstance`` so this module stays free
#: of provider-SDK imports. ``errors.py`` sits in the fast-path CLI import graph
#: (``clawcodex --help`` reaches it), and importing ``anthropic``/``openai``/
#: ``httpx`` just to run a classification check would put a heavy SDK on that
#: path -- the same reasoning that keeps ``providers/stream_retry.py``
#: dependency-free.
#:
#: The builtin ``ConnectionError``/``TimeoutError``/``OSError`` check is not
#: sufficient on its own, which is the bug this set fixes: the Anthropic and
#: OpenAI SDKs derive their transport errors from their own ``APIError`` base
#: (plain ``Exception``) and httpx derives its timeouts from
#: ``httpx.TransportError``. None of them subclass a builtin, and only
#: ``APIStatusError`` carries ``status_code``, so every one of them fell through
#: to ``retryable=False, error_type="unknown"``. That silently removed the
#: retries the interactive lane depends on: ``query.py`` passes
#: ``sdk_max_retries=0`` on foreground turns precisely so this manual lane owns
#: them, so a misclassification here left a foreground turn with *fewer*
#: attempts than a stock SDK client -- a 5s connect blip the SDK would have
#: absorbed silently became a hard, user-visible ``APITimeoutError``.
_TRANSPORT_ERROR_TYPES = frozenset({
    # anthropic + openai SDKs (identical class names in both)
    "APIConnectionError",
    "APITimeoutError",
    # httpx
    "TimeoutException",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "ConnectError",
    "ReadError",
    "WriteError",
    "RemoteProtocolError",
    # requests, for any future call path that uses it
    "ChunkedEncodingError",
})
#: Deliberately NOT listed: bare ``ProtocolError``. ``httpx.LocalProtocolError``
#: subclasses it and means *this client* emitted something illegal (malformed
#: header, bad body/method combination, invalid state transition). That is
#: deterministic -- retrying reproduces it ten times and then fails anyway.
#: ``RemoteProtocolError`` (the peer misbehaved, which a re-issue can survive) is
#: listed explicitly above, so nothing is lost by keeping the base name out.
#:
#: Also deliberately NOT listed: ``StreamIdleTimeout`` (or any ``TimeoutError``
#: base that would catch it). It reads like a timeout, so it looks like it
#: belongs -- but the provider's idle lane ALREADY re-attempts it up to
#: ``stream_idle_max_attempts``. Adding it here would stack this lane's budget on
#: top of that one: 3 x 11 = 33 attempts at a 90s idle deadline, a 45-minute
#: silent hang. It is a plain ``Exception`` today, which is what keeps the two
#: budgets from composing; keep it that way.
#:
#: Kept in sync by hand with ``_TRANSIENT_STREAM_DROP_TYPES`` in
#: ``src/providers/stream_retry.py``, which answers the narrower question "is
#: this a mid-stream drop worth re-issuing?" and so deliberately omits the
#: timeout family -- see the lane-ownership note there. Two sets, two questions;
#: divergence between them is exactly what produced this bug, so change them
#: together.


def is_transport_error(error: BaseException) -> bool:
    """True for a failure that produced no HTTP verdict, so retrying is sound.

    Walks the MRO so a *subclass* of a listed type matches too. That is the
    exact trap this guards: ``anthropic.APITimeoutError`` subclasses the listed
    ``APIConnectionError``, so a name-only check on ``type(error).__name__``
    misses it.

    Anything carrying a status is a server verdict, not a transport failure --
    those are classified by the status branches in
    :func:`categorize_retryable_api_error`, which run before this one.
    """
    if getattr(error, "status", getattr(error, "status_code", None)) is not None:
        return False
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return True
    return any(
        klass.__name__ in _TRANSPORT_ERROR_TYPES for klass in type(error).__mro__
    )


def is_media_size_error(raw: str) -> bool:
    return (
        ("image exceeds" in raw and "maximum" in raw)
        or ("image dimensions exceed" in raw and "many-image" in raw)
        or bool(re.search(r"maximum of \d+ PDF pages", raw))
    )


def is_image_unsupported_error(raw: str) -> bool:
    """Detect provider errors meaning "this model does not accept image input".

    Distinct from ``is_media_size_error`` (image too large): the model
    has zero image capability, so stripping or resizing won't help —
    history must be sanitized so the request can be re-issued text-only.

    Pattern set covers OpenRouter's real wording plus likely paraphrases
    from other OpenAI-compatible providers. Match is case-insensitive
    because providers paraphrase casing inconsistently.
    """
    low = raw.lower()
    return (
        "no endpoints found that support image" in low
        or "does not support image" in low
        or "doesn't support image" in low
        or "image input is not supported" in low
        or "image_input_not_supported" in low
        or "model does not accept image" in low
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

    if is_image_unsupported_error(str(error)):
        # Model-capability rejection — retrying won't help. The query
        # layer tags it ``image_unsupported`` and the engine strips
        # images from history; this classification keeps the retry
        # layer from looping on a permanent failure.
        return ErrorClassification(
            retryable=False,
            error_type="image_unsupported",
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

    if is_transport_error(error):
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
