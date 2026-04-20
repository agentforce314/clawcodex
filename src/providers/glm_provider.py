"""GLM (Zhipu AI) provider implementation."""

from __future__ import annotations

from typing import Any, Optional

try:
    from zhipuai import ZhipuAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    ZhipuAI = None

from .openai_compatible import OpenAICompatibleProvider


class GLMProvider(OpenAICompatibleProvider):
    """GLM (Zhipu AI) provider using Zhipu SDK.

    GLM models on z.ai require the 'zai/' prefix in model names.
    """

    def __init__(
        self, api_key: str, base_url: Optional[str] = None, model: Optional[str] = None
    ):
        """Initialize GLM provider.

        Args:
            api_key: Zhipu AI API key
            base_url: Base URL (optional)
            model: Default model (default: zai/glm-5)
        """
        super().__init__(api_key, base_url, model or "zai/glm-5")

    def _create_client(self) -> Any:
        """Create Zhipu AI SDK client."""
        if ZhipuAI is None:  # pragma: no cover
            raise ModuleNotFoundError(
                "zhipuai package is not installed. Install optional dependencies to use GLMProvider."
            )
        return ZhipuAI(api_key=self.api_key)

    def get_available_models(self) -> list[str]:
        """Get list of available GLM models.

        Returns:
            List of model names (with zai/ prefix for z.ai API)
        """
        return [
            # GLM-5 series (latest, requires zai/ prefix)
            "zai/glm-5",
            "zai/glm-5-turbo",
            # GLM-4 series (standard, zai/ prefix)
            "zai/glm-4",
            "zai/glm-4-plus",
            "zai/glm-4-air",
            "zai/glm-4-flash",
            "zai/glm-4.5",
            "zai/glm-4.6",
            "zai/glm-4.7",
            # GLM-3 series (legacy)
            "zai/glm-3-turbo",
        ]
