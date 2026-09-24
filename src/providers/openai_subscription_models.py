"""Account-scoped model discovery for the ChatGPT Codex endpoint.

The public API catalog and another client's model cache do not establish
what a Clawcodex OAuth login can use. Read this login's catalog instead.
Picker reads never wait for HTTP; login can explicitly refresh synchronously.
Successful requests also establish availability: the catalog can lag rollout.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from src.auth.openai_subscription import (
    ORIGINATOR,
    SubscriptionCredentials,
    credentials_path,
    load_credentials,
)

from .openai_responses import EFFORT_LADDER, SUBSCRIPTION_MODELS

MODELS_ENDPOINT = "https://chatgpt.com/backend-api/codex/models"
# Catalog protocol version verified against the Codex endpoint. This is not
# Clawcodex's package version (which the endpoint doesn't understand).
#
# It is also a GATE, not just a label: the backend hides every model whose
# ``minimal_client_version`` exceeds it. At 0.149.1 the catalog stopped at
# gpt-5.6; gpt-6-astra needs 0.153.0 and gpt-6-sol / gpt-6-luna need 0.155.0
# (probed 2026-09-24). Bump it only after the new models' wire has been
# checked live — the pin is what keeps an unverified model out of the picker.
CATALOG_CLIENT_VERSION = "0.155.1"
TTL_SECONDS = 300
RETRY_SECONDS = 30
VERIFICATION_TTL_SECONDS = 86_400

_lock = threading.Lock()
_in_flight: set[tuple[Path, str]] = set()
_retry_after: dict[tuple[Path, str], float] = {}


def _scope(credentials: SubscriptionCredentials) -> str:
    # Partition even logins to the same account: entitlements may differ by
    # token/workspace. Never persist the credentials themselves in this cache.
    value = credentials.account_id + "\0" + credentials.access_token
    return hashlib.sha256(value.encode()).hexdigest()


def _read_entry(path: Path, scope: str) -> dict:
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if entry.get("scope") == scope and entry.get("version") == 1:
            return entry
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {"version": 1, "scope": scope}


def _read_cache(path: Path, scope: str) -> tuple[list[str] | None, float]:
    entry = _read_entry(path, scope)
    try:
        models = entry["models"]
        if isinstance(models, list) and all(isinstance(m, str) and m for m in models):
            return models, float(entry["fetched_at"])
    except (ValueError, TypeError, KeyError):
        pass
    return None, 0


def _write_entry(path: Path, entry: dict) -> None:
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".openai-models-")
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(entry, stream)
        os.replace(tmp, path)
    except OSError:
        pass  # Discovery/cache failures must not break requests or the picker.
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _recent_models(entry: dict, key: str, ttl_seconds: float) -> dict[str, float]:
    recorded = entry.get(key)
    if not isinstance(recorded, dict):
        return {}
    now = time.time()
    return {
        model: timestamp for model, timestamp in recorded.items()
        if isinstance(model, str) and model
        and isinstance(timestamp, (int, float))
        and 0 <= now - timestamp < ttl_seconds
    }


def record_subscription_model(
    credentials: SubscriptionCredentials, model: str, *, available: bool = True,
) -> None:
    """Remember actual request results for this login, not guessed model IDs."""
    if not model:
        return
    path = credentials_path().with_name("openai-models-cache.json")
    with _lock:
        entry = _read_entry(path, _scope(credentials))
        verified = _recent_models(entry, "verified_models", VERIFICATION_TTL_SECONDS)
        rejected = _recent_models(entry, "rejected_models", TTL_SECONDS)
        if available:
            verified[model] = time.time()
            rejected.pop(model, None)
        else:
            verified.pop(model, None)
            rejected[model] = time.time()
        entry["verified_models"] = verified
        entry["rejected_models"] = rejected
        _write_entry(path, entry)


def _wire_efforts(raw: object) -> list[str] | None:
    """A catalog entry's ``supported_reasoning_levels`` reduced to values the
    wire accepts (``ultra`` is advertised but 400s — see EFFORT_LADDER)."""
    if not isinstance(raw, list):
        return None
    levels = [
        entry.get("effort") for entry in raw
        if isinstance(entry, dict) and entry.get("effort") in EFFORT_LADDER
    ]
    return levels or None


def _fetch_catalog(
    credentials: SubscriptionCredentials,
) -> tuple[list[str], dict[str, list[str]]] | None:
    import httpx

    headers = {
        "Authorization": f"Bearer {credentials.access_token}",
        "originator": ORIGINATOR,
        "Accept": "application/json",
    }
    if credentials.account_id:
        headers["chatgpt-account-id"] = credentials.account_id
    verify = os.environ.get("CLAWCODEX_SSL_VERIFY", "").lower() not in ("0", "false", "no")
    try:
        response = httpx.get(
            MODELS_ENDPOINT,
            params={"client_version": CATALOG_CLIENT_VERSION},
            headers=headers,
            timeout=2.0,
            verify=verify,
        )
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            return None
        listed = [
            model for model in raw
            if isinstance(model, dict)
            and model.get("visibility") == "list"
            and isinstance(model.get("slug"), str) and model["slug"]
        ]
        # Preserve backend order, including subscription-only models (their
        # supported_in_api flag is false). Hidden/internal models stay hidden.
        slugs = list(dict.fromkeys(model["slug"] for model in listed))
        efforts: dict[str, list[str]] = {}
        for model in listed:
            levels = _wire_efforts(model.get("supported_reasoning_levels"))
            if levels:
                efforts.setdefault(model["slug"], levels)
        return slugs, efforts
    except (httpx.HTTPError, ValueError):
        return None


def _refresh(path: Path, scope: str, credentials: SubscriptionCredentials) -> None:
    key = (path, scope)
    try:
        catalog = _fetch_catalog(credentials)
        if catalog is None:
            return
        models, efforts = catalog
        with _lock:
            # Re-read after HTTP so an in-flight successful request cannot
            # lose its verification when discovery finishes later.
            entry = _read_entry(path, scope)
            entry.update(models=models, efforts=efforts, fetched_at=time.time())
            _write_entry(path, entry)
    finally:
        with _lock:
            _in_flight.discard(key)
            _retry_after[key] = time.monotonic() + RETRY_SECONDS


def get_subscription_models(
    credentials: SubscriptionCredentials | None = None,
    *, background: bool = True, force: bool = False,
) -> list[str]:
    credentials = credentials or load_credentials()
    if credentials is None:
        return []
    path = credentials_path().with_name("openai-models-cache.json")
    scope = _scope(credentials)
    models, fetched_at = _read_cache(path, scope)
    key = (path, scope)
    if (force or time.time() - fetched_at >= TTL_SECONDS) and not credentials.needs_refresh:
        with _lock:
            refresh = key not in _in_flight and (
                force or time.monotonic() >= _retry_after.get(key, 0)
            )
            if refresh:
                _in_flight.add(key)
        if refresh:
            if background:
                threading.Thread(
                    target=_refresh, args=(path, scope, credentials),
                    daemon=True, name="openai-subscription-models",
                ).start()
            else:
                _refresh(path, scope, credentials)
                models, _ = _read_cache(path, scope)
    # Actual successful requests outrank an incomplete discovery catalog.
    # Verification is scoped to this login and survives catalog refreshes.
    entry = _read_entry(path, scope)
    verified = _recent_models(entry, "verified_models", VERIFICATION_TTL_SECONDS)
    # A stale catalog or fallback must not immediately restore a model that
    # this login just rejected. Allow another attempt after one cache TTL.
    rejected = _recent_models(entry, "rejected_models", TTL_SECONDS)
    listed = list(models) if models is not None else list(SUBSCRIPTION_MODELS)
    return [model for model in dict.fromkeys([*verified, *listed]) if model not in rejected]


def _static_effort_levels(model: str) -> tuple[str, ...]:
    """Fallback when this login's catalog has no entry for ``model``.

    Probed live on the ChatGPT backend 2026-09-24: gpt-6-* and gpt-5.6-* take
    ``max``; gpt-5.5 takes ``xhigh`` and 400s on ``max``. Anything older keeps
    the low/medium/high ceiling measured 2026-07-25, the safe direction —
    under-offering hides a level, over-offering 400s every request. Shaped
    like the catalog's own lists (which never include ``none``), so a level
    behaves the same whether or not this login's catalog is cached yet.
    """
    m = (model or "").lower()
    if m.startswith(("gpt-6", "gpt-5.6")):
        return ("low", "medium", "high", "xhigh", "max")
    if m.startswith("gpt-5.5"):
        return ("low", "medium", "high", "xhigh")
    return ("minimal", "low", "medium", "high")


def get_subscription_effort_levels(
    model: str, credentials: SubscriptionCredentials | None = None,
) -> tuple[str, ...]:
    """The reasoning levels this login's catalog advertises for ``model``.

    Cache-only: it runs on the request path and in the /model picker, so it
    must never wait on HTTP. The catalog's list is trusted as-is apart from
    ``ultra`` (dropped at fetch time, see ``_wire_efforts``); it matched every
    live probe on 2026-09-24.

    The cache is scoped per access token, so after a token refresh the
    per-model list is missed until the next catalog refresh and the static
    table answers instead. That is safe while the table matches the catalog;
    keep the two in step when a new generation lands.
    """
    credentials = credentials or load_credentials()
    if credentials is not None:
        path = credentials_path().with_name("openai-models-cache.json")
        efforts = _read_entry(path, _scope(credentials)).get("efforts")
        levels = efforts.get(model) if isinstance(efforts, dict) else None
        if isinstance(levels, list) and levels:
            wire = [lvl for lvl in EFFORT_LADDER if lvl in levels]
            if wire:
                return tuple(wire)
    return _static_effort_levels(model)
