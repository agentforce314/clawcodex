"""OpenAI Codex provider backed by ChatGPT OAuth tokens."""

from __future__ import annotations

from typing import Any, Optional

try:
    from openai import OpenAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    OpenAI = None

from src.auth.codex_oauth import CODEX_BASE_URL, resolve_codex_runtime_credentials

from .codex_models import CODEX_FALLBACK_MODELS, get_codex_model_ids
from .openai_compatible import OpenAICompatibleProvider


class OpenAICodexProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        api_key: str = "",
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        super().__init__(api_key, base_url or CODEX_BASE_URL, model or CODEX_FALLBACK_MODELS[0])

    def _create_client(self) -> Any:
        if OpenAI is None:  # pragma: no cover
            raise ModuleNotFoundError(
                "openai package is not installed. Install optional dependencies to use OpenAICodexProvider."
            )
        credentials = resolve_codex_runtime_credentials()
        self.api_key = credentials.api_key
        self.base_url = self.base_url or credentials.base_url
        kwargs: dict[str, Any] = {"api_key": self.api_key, "base_url": self.base_url}
        import os
        if os.environ.get("CLAWCODEX_SSL_VERIFY", "").lower() in ("0", "false", "no"):
            import httpx
            kwargs["http_client"] = httpx.Client(verify=False)
        return OpenAI(**kwargs)

    @property
    def client(self) -> Any:
        credentials = resolve_codex_runtime_credentials()
        if credentials.api_key != self.api_key:
            self.api_key = credentials.api_key
            self._client = None
        if self.base_url != credentials.base_url:
            self.base_url = credentials.base_url
            self._client = None
        return super().client

    def get_available_models(self) -> list[str]:
        try:
            credentials = resolve_codex_runtime_credentials(refresh_if_expiring=True)
        except Exception:
            return list(CODEX_FALLBACK_MODELS)
        return get_codex_model_ids(credentials.api_key)
