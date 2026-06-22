"""Z.ai (GLM) provider implementation.

Z.ai serves the GLM Coding Plan over an OpenAI-compatible
``/chat/completions`` API at ``https://api.z.ai/api/coding/paas/v4`` (the
general API lives at ``https://api.z.ai/api/paas/v4``). The provider therefore
reuses the OpenAI SDK pointed at the Z.ai base URL — the same shape as
:class:`~src.providers.deepseek_provider.DeepSeekProvider` — rather than a
vendor-specific SDK.

Canonical id ``zai`` (aliases ``z-ai`` / ``z_ai`` / ``z.ai``, plus the
pre-rename ``glm``), default model ``GLM-5.1`` with ``GLM-5.2`` as an opt-in
preview. GLM models stream their chain-of-thought through ``reasoning_content``,
which the shared ``OpenAICompatibleProvider`` already surfaces.
"""

from __future__ import annotations

from typing import Any, Optional

try:
    from openai import OpenAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    OpenAI = None

from .openai_compatible import OpenAICompatibleProvider


# Canonical Z.ai GLM model ids, keyed by accepted lowercase aliases. The Z.ai
# endpoint expects the capitalized ``GLM-5.x`` ids, so a config value like
# ``glm-5.2`` (or the OpenRouter-style ``zai-glm-5-2``) is normalized before the
# request. Unknown / custom model ids pass through unchanged.
_GLM_MODEL_ALIASES: dict[str, str] = {
    "glm-5.1": "GLM-5.1",
    "glm-5-1": "GLM-5.1",
    "zai-glm-5.1": "GLM-5.1",
    "zai-glm-5-1": "GLM-5.1",
    "glm-5.2": "GLM-5.2",
    "glm-5-2": "GLM-5.2",
    "zai-glm-5.2": "GLM-5.2",
    "zai-glm-5-2": "GLM-5.2",
}


def _canonical_glm_model(model: str) -> str:
    """Map an accepted GLM alias to its canonical Z.ai id; pass others through."""
    return _GLM_MODEL_ALIASES.get(model.strip().lower(), model)


class ZaiProvider(OpenAICompatibleProvider):
    """Z.ai GLM Coding Plan provider using the OpenAI SDK against the Z.ai base URL."""

    DEFAULT_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
    DEFAULT_MODEL = "GLM-5.1"

    def __init__(
        self, api_key: str, base_url: Optional[str] = None, model: Optional[str] = None
    ):
        """Initialize the Z.ai provider.

        Args:
            api_key: Z.ai API key (``ZAI_API_KEY`` / ``Z_AI_API_KEY``).
            base_url: Base URL (optional, defaults to the GLM Coding Plan endpoint).
            model: Default model (default: ``GLM-5.1``; ``GLM-5.2`` is an opt-in preview).
        """
        super().__init__(
            api_key,
            base_url or self.DEFAULT_BASE_URL,
            model or self.DEFAULT_MODEL,
        )

    def _get_model(self, **kwargs) -> str:
        """Resolve the model id, normalizing known GLM aliases to canonical ids.

        ``OpenAICompatibleProvider`` sends ``_get_model(...)`` verbatim to the
        endpoint, so this is where ``glm-5.2`` becomes ``GLM-5.2``. The base
        implementation still strips the ``[1m]`` context-opt-in suffix first.
        """
        model = super()._get_model(**kwargs)
        return _canonical_glm_model(model) if model else model

    def _create_client(self) -> Any:
        """Create an OpenAI SDK client pointed at Z.ai.

        The read timeout that prevents a stalled stream from freezing the event
        loop is applied centrally by ``OpenAICompatibleProvider.client`` (via
        ``_apply_client_timeout``) for every provider, so it isn't set here.
        """
        if OpenAI is None:  # pragma: no cover
            raise ModuleNotFoundError(
                "openai package is not installed. Install optional dependencies to use ZaiProvider."
            )
        kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "base_url": self.base_url or self.DEFAULT_BASE_URL,
        }
        # Support SSL verification bypass for corporate/internal endpoints.
        import os
        if os.environ.get("CLAWCODEX_SSL_VERIFY", "").lower() in ("0", "false", "no"):
            import httpx
            kwargs["http_client"] = httpx.Client(verify=False)
        return OpenAI(**kwargs)

    def get_available_models(self) -> list[str]:
        """Return Z.ai's GLM Coding Plan models.

        ``GLM-5.1`` is the stable default; ``GLM-5.2`` is an opt-in preview
        (set ``model = "GLM-5.2"`` to try it).
        """
        return [
            "GLM-5.1",
            "GLM-5.2",
        ]
