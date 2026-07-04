"""Live model discovery for dynamic-catalog providers.

INTEG-1 (integrations-folder parity) — the port of
``typescript/src/integrations/discoveryService.ts`` + ``discoveryCache.ts``:
providers whose model lists cannot be known statically (ollama's installed
models are machine-local; vLLM/SGLang serve whatever was launched;
OpenRouter's hosted list churns) get their lists from the endpoint itself,
merged over the static registry list, with a persistent TTL cache.

Non-blocking contract (deliberate divergence from TS's blocking
fetch-with-TTL, documented in the plan): ``discovered_models`` returns
IMMEDIATELY — fresh-cache ∪ static, or static alone — and refreshes a
stale/missing entry on a single-flight background thread so the NEXT call
is fresh. ``get_available_models()`` sits on the agent-server's control
paths (init/list replies); a blocking network call there would stall the
client whenever a local endpoint is down. Cost: the first-ever call on a
cold cache shows the static list once.

Failures never propagate: fetch/parse/cache errors degrade to
stale-or-static (the TS error path's behavior).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
DISCOVERY_TTL_S = 86_400  # 1 day — TS gateways/ollama.ts discoveryCacheTtl '1d'
FETCH_TIMEOUT_S = 2.0

_refresh_locks_guard = threading.Lock()
_refresh_in_flight: set[str] = set()
# Serializes the cache read-modify-write in _refresh (critic round: the
# in-flight guard alone left a cross-KEY lost-update window — two providers
# refreshing concurrently could clobber each other's entry; TS serializes
# via withDiscoveryCacheLock). In-process only; a cross-process race
# self-heals (one refetch).
_cache_write_lock = threading.Lock()


def _cache_path() -> Path:
    """Under the canonical config home (critic round: reuse
    config.GLOBAL_CONFIG_DIR — test fixtures re-point it — instead of a
    hardcoded ~/.clawcodex)."""
    from src.config import GLOBAL_CONFIG_DIR

    return Path(GLOBAL_CONFIG_DIR) / "model-discovery-cache.json"


def _cache_key(provider_id: str, base_url: str) -> str:
    digest = hashlib.sha1((base_url or "").encode("utf-8")).hexdigest()[:12]
    return f"{provider_id}:{digest}"


def _read_cache() -> dict[str, Any]:
    """The entries map; corrupt/missing/wrong-version files are empty, never
    an error (discoveryCache's tolerance)."""
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("version") == CACHE_VERSION:
            entries = raw.get("entries")
            if isinstance(entries, dict):
                return entries
    except Exception:  # noqa: BLE001
        pass
    return {}


def _write_cache(entries: dict[str, Any]) -> None:
    """Atomic write (tempfile + os.replace in the same dir) so concurrent
    readers always see valid JSON — the discoveryCache pattern."""
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": CACHE_VERSION, "entries": entries})
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".model-discovery-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except Exception:  # noqa: BLE001 — cache persistence is best-effort
        logger.debug("model discovery: cache write failed", exc_info=True)


def _http_get_json(url: str, *, api_key: str | None, timeout: float) -> Any:
    request = urllib.request.Request(url)
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — provider endpoint the user configured
        return json.loads(response.read().decode("utf-8", errors="replace"))


def fetch_openai_compatible_models(
    base_url: str, api_key: str | None = None, *, timeout: float = FETCH_TIMEOUT_S,
) -> list[str] | None:
    """GET {base}/models → data[].id (the fetchOpenAICompatibleModelsRaw
    analog). None on any failure."""
    try:
        payload = _http_get_json(
            base_url.rstrip("/") + "/models", api_key=api_key, timeout=timeout,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return None
        models = [
            str(item["id"]) for item in data
            if isinstance(item, dict) and item.get("id")
        ]
        return models or None
    except Exception:  # noqa: BLE001
        logger.debug("model discovery: openai-compatible fetch failed for %s", base_url, exc_info=True)
        return None


def fetch_ollama_models(
    base_url: str, *, timeout: float = FETCH_TIMEOUT_S,
) -> list[str] | None:
    """GET {root}/api/tags → models[].name. The registry's ollama base URL
    ends in ``/v1`` (the OpenAI-compat surface); tags lives at the server
    ROOT, so a trailing /v1 is stripped."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    try:
        payload = _http_get_json(root + "/api/tags", api_key=None, timeout=timeout)
        models_raw = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models_raw, list):
            return None
        models = [
            str(item["name"]) for item in models_raw
            if isinstance(item, dict) and item.get("name")
        ]
        return models or None
    except Exception:  # noqa: BLE001
        logger.debug("model discovery: ollama tags fetch failed for %s", base_url, exc_info=True)
        return None


def _fetch_for_kind(
    kind: str, base_url: str, api_key: str | None, timeout: float,
) -> list[str] | None:
    if kind == "ollama":
        return fetch_ollama_models(base_url, timeout=timeout)
    if kind == "openai-compatible":
        return fetch_openai_compatible_models(base_url, api_key, timeout=timeout)
    return None


def _merge(
    discovered: list[str] | None, static_models: list[str], mode: str,
) -> list[str]:
    """The two TS catalog-source semantics (critic MAJOR — a single
    discovered-first union defeated both target providers):

    * ``dynamic`` (ollama/vllm/sglang; gateways/ollama.ts source:'dynamic'):
      the endpoint is the truth — discovered REPLACES static when non-empty;
      static is only the no-discovery fallback. This is what actually kills
      the bogus ``deepseek-coder:1.3b`` stub once a real list arrives.
    * ``hybrid`` (openrouter; gateways/openrouter.ts source:'hybrid'):
      STATIC-first merge, discovered appended case-insensitively deduped
      (discoveryService.ts:194-212 mergeCatalogEntries) — curation keeps its
      order; the live tail extends it.
    """
    discovered_list = discovered or []
    if mode == "dynamic":
        return list(discovered_list) if discovered_list else list(static_models)
    merged = list(static_models)
    seen = {name.lower() for name in merged}
    for name in discovered_list:
        if name.lower() not in seen:
            seen.add(name.lower())
            merged.append(name)
    return merged


def _refresh(
    key: str, kind: str, base_url: str, api_key: str | None, timeout: float,
) -> None:
    try:
        discovered = _fetch_for_kind(kind, base_url, api_key, timeout)
        if discovered:
            with _cache_write_lock:
                entries = _read_cache()
                entries[key] = {"models": discovered, "fetched_at": time.time()}
                _write_cache(entries)
    finally:
        with _refresh_locks_guard:
            _refresh_in_flight.discard(key)


def discovered_models(
    provider_id: str,
    base_url: str,
    api_key: str | None,
    kind: str,
    static_models: tuple[str, ...] | list[str],
    *,
    mode: str = "dynamic",
    ttl_s: float = DISCOVERY_TTL_S,
    timeout: float = FETCH_TIMEOUT_S,
    background: bool = True,
) -> list[str]:
    """The non-blocking entry point (module docstring). ``mode`` selects the
    catalog-source semantics (see ``_merge``): "dynamic" (default —
    endpoint-authoritative) or "hybrid" (curated-static-first).
    ``background=False`` forces a synchronous refresh — for tests and
    explicit-refresh callers."""
    static_list = list(static_models)
    if not base_url:
        return static_list

    key = _cache_key(provider_id, base_url)
    entries = _read_cache()
    entry = entries.get(key)
    fresh = (
        isinstance(entry, dict)
        and isinstance(entry.get("models"), list)
        and (time.time() - float(entry.get("fetched_at", 0))) < ttl_s
    )

    if not fresh:
        with _refresh_locks_guard:
            already = key in _refresh_in_flight
            if not already:
                _refresh_in_flight.add(key)
        if not already:
            if background:
                threading.Thread(
                    target=_refresh,
                    args=(key, kind, base_url, api_key, timeout),
                    daemon=True,
                    name=f"model-discovery:{provider_id}",
                ).start()
            else:
                _refresh(key, kind, base_url, api_key, timeout)
                entries = _read_cache()
                entry = entries.get(key)

    cached = entry.get("models") if isinstance(entry, dict) else None
    return _merge(cached if isinstance(cached, list) else None, static_list, mode)
