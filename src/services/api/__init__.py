from .claude import StreamEvent, add_cache_breakpoints, call_model, tool_to_api_schema
from .errors import (
    MaxOutputTokensError,
    OverloadedError,
    PromptTooLongError,
    RateLimitError,
    categorize_retryable_api_error,
)
from .logging import NonNullableUsage, accumulate_usage, update_usage
from .provider_config import ProviderOverride, resolve_agent_provider
from .tool_normalization import normalize_tool_arguments

# ch04 round-4 GAP B: the parallel `retry.py` engine (with_retry/RetryOptions/
# RetryContext/CannotRetryError) and `FallbackTriggeredError` were deleted —
# the live retry + model-fallback lane is loop-internal (src/query/query.py;
# yield-based status, 529 counter, general 429/5xx/connection budget, jitter,
# provider.model fallback switch). Nothing raised FallbackTriggeredError and
# nothing called with_retry in production.

__all__ = [
    "MaxOutputTokensError",
    "NonNullableUsage",
    "OverloadedError",
    "PromptTooLongError",
    "ProviderOverride",
    "RateLimitError",
    "StreamEvent",
    "accumulate_usage",
    "add_cache_breakpoints",
    "call_model",
    "categorize_retryable_api_error",
    "normalize_tool_arguments",
    "resolve_agent_provider",
    "tool_to_api_schema",
    "update_usage",
]
