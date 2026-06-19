"""DeepSeek provider implementation.

DeepSeek exposes an OpenAI-compatible API at https://api.deepseek.com.
Current production models are ``deepseek-v4-pro`` and ``deepseek-v4-flash``;
the legacy aliases ``deepseek-chat`` / ``deepseek-reasoner`` are being
deprecated and resolve to the non-thinking / thinking modes of
``deepseek-v4-flash`` respectively.
"""

from __future__ import annotations

from typing import Any, Optional

try:
    from openai import OpenAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    OpenAI = None

from .openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek provider using the OpenAI SDK against the DeepSeek base URL."""

    DEFAULT_BASE_URL = "https://api.deepseek.com"

    #: Marks this provider as DeepSeek so the query layer relocates the
    #: per-request-volatile (REQUEST-scope) system sections to a trailing tail,
    #: keeping the request prefix byte-stable for DeepSeek's automatic prefix
    #: cache (see ``query._split_system_prompt_blocks``).
    is_deepseek = True

    def __init__(
        self, api_key: str, base_url: Optional[str] = None, model: Optional[str] = None
    ):
        """Initialize DeepSeek provider.

        Args:
            api_key: DeepSeek API key (sk-...)
            base_url: Base URL (optional, defaults to https://api.deepseek.com)
            model: Default model (default: deepseek-v4-pro)
        """
        super().__init__(
            api_key,
            base_url or self.DEFAULT_BASE_URL,
            model or "deepseek-v4-pro",
        )

    def _create_client(self) -> Any:
        """Create OpenAI SDK client pointed at DeepSeek."""
        if OpenAI is None:  # pragma: no cover
            raise ModuleNotFoundError(
                "openai package is not installed. Install optional dependencies to use DeepSeekProvider."
            )
        kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "base_url": self.base_url or self.DEFAULT_BASE_URL,
        }
        import os
        if os.environ.get("CLAWCODEX_SSL_VERIFY", "").lower() in ("0", "false", "no"):
            import httpx
            kwargs["http_client"] = httpx.Client(verify=False)
        return OpenAI(**kwargs)

    def _build_usage_dict(self, usage: Any) -> dict[str, Any]:
        """Add DeepSeek prompt-cache accounting onto the base usage dict.

        DeepSeek reports automatic prefix-cache utilisation in its ``usage``
        object — either as top-level ``prompt_cache_hit_tokens`` /
        ``prompt_cache_miss_tokens`` (DeepSeek's native shape) or, on some
        OpenAI-compatible gateways, nested under
        ``prompt_tokens_details.cached_tokens``. The base
        :meth:`OpenAICompatibleProvider._build_usage_dict` reports
        ``input_tokens`` as the FULL prompt (hit + miss) and drops the cache
        split, so cache hit-rate is invisible and cost cannot credit the hit.

        When a cache hit is present we re-map onto the **Anthropic
        convention** that ``cost_tracker.record_api_usage`` /
        ``services.pricing.compute_cost`` already understand:

        * ``input_tokens``            → cache MISS (uncached), priced at input
        * ``cache_read_input_tokens`` → cache HIT, priced at the cheap cache rate
        * ``cache_creation_input_tokens`` → 0 (DeepSeek has no cache-write charge)

        ``total_tokens`` and ``output_tokens`` are left untouched (still the
        full counts), and ``reasoning_tokens`` is surfaced when present. With
        no cache hit, the dict is identical to the base (all prompt tokens are
        uncached input). This override runs only for the DeepSeek provider;
        every other provider keeps the base behaviour.
        """
        result = super()._build_usage_dict(usage)
        if usage is None:
            return result

        def _field(name: str) -> int:
            value = getattr(usage, name, None)
            if value is None:
                extra = getattr(usage, "model_extra", None)
                if isinstance(extra, dict):
                    value = extra.get(name)
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        hit = _field("prompt_cache_hit_tokens")
        miss = _field("prompt_cache_miss_tokens")

        # OpenAI-compatible nested shape: prompt_tokens_details.cached_tokens.
        details = getattr(usage, "prompt_tokens_details", None)
        nested_cached = (
            getattr(details, "cached_tokens", None) if details is not None else None
        )
        if not hit and nested_cached:
            try:
                hit = int(nested_cached)
            except (TypeError, ValueError):
                hit = 0

        prompt_tokens = int(result.get("input_tokens", 0) or 0)
        if hit > 0:
            # Only an explicit/derivable cache hit triggers the re-map; with no
            # hit, the base dict (input_tokens = full prompt) is already correct.
            if not miss:
                miss = max(prompt_tokens - hit, 0)
            result["input_tokens"] = miss
            result["cache_read_input_tokens"] = hit
            result["cache_creation_input_tokens"] = 0

        cdetails = getattr(usage, "completion_tokens_details", None)
        reasoning = (
            getattr(cdetails, "reasoning_tokens", None) if cdetails is not None else None
        )
        if reasoning:
            try:
                result["reasoning_tokens"] = int(reasoning)
            except (TypeError, ValueError):
                pass
        return result

    def get_available_models(self) -> list[str]:
        """Return DeepSeek's current production models.

        ``deepseek-chat`` and ``deepseek-reasoner`` are kept for backward
        compatibility but DeepSeek has announced they will be deprecated.
        """
        return [
            # V4 series (current)
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            # Legacy aliases (being deprecated; map to v4-flash modes)
            "deepseek-chat",
            "deepseek-reasoner",
        ]
