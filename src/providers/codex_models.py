"""Model discovery for OpenAI Codex ChatGPT OAuth."""

from __future__ import annotations

from typing import Any

import httpx

CODEX_FALLBACK_MODELS = [
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
]
CODEX_MODELS_URL = "https://chatgpt.com/backend-api/codex/models?client_version=1.0.0"


def get_codex_model_ids(access_token: str, *, timeout_seconds: float = 10.0) -> list[str]:
    if not access_token.strip():
        return list(CODEX_FALLBACK_MODELS)
    try:
        response = httpx.get(
            CODEX_MODELS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout_seconds,
        )
        if response.status_code != 200:
            return list(CODEX_FALLBACK_MODELS)
        models = _extract_models(response.json())
    except Exception:
        return list(CODEX_FALLBACK_MODELS)
    return models or list(CODEX_FALLBACK_MODELS)


def _extract_models(payload: Any) -> list[str]:
    candidates: Any = payload
    if isinstance(payload, dict):
        candidates = payload.get("models") or payload.get("data") or []
    if not isinstance(candidates, list):
        return []
    model_ids: list[str] = []
    for item in candidates:
        if isinstance(item, str):
            model_id = item
            hidden = False
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("name") or item.get("slug")
            hidden = bool(item.get("hidden") or item.get("hide"))
        else:
            continue
        if isinstance(model_id, str) and model_id and not hidden:
            model_ids.append(model_id)
    return list(dict.fromkeys(model_ids))
