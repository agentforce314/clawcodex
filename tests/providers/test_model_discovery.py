"""INTEG-1 — live model discovery (port of discoveryService/discoveryCache).

Plan: my-docs/get-parity-by-folder/integrations-refactoring-plan.md.
The contract under test: non-blocking (cache-or-static immediately +
single-flight background refresh), never-empty merge, corruption-tolerant
atomic cache, and the spec/openrouter wiring.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

import src.providers.model_discovery as md


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(md, "_cache_path", lambda: tmp_path / "cache.json")
    # Reset single-flight state between tests.
    with md._refresh_locks_guard:
        md._refresh_in_flight.clear()
    yield


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def test_openai_compatible_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        md, "_http_get_json",
        lambda url, *, api_key, timeout: {"data": [{"id": "m1"}, {"id": "m2"}, {"bad": 1}]},
    )
    assert md.fetch_openai_compatible_models("http://x/v1") == ["m1", "m2"]


def test_openai_compatible_parser_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(md, "_http_get_json", lambda url, *, api_key, timeout: {"nope": 1})
    assert md.fetch_openai_compatible_models("http://x/v1") is None


def test_ollama_parser_strips_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def _fake(url, *, api_key, timeout):
        seen.append(url)
        return {"models": [{"name": "llama3:8b"}, {"name": "qwen3:4b"}]}

    monkeypatch.setattr(md, "_http_get_json", _fake)
    models = md.fetch_ollama_models("http://localhost:11434/v1")
    assert models == ["llama3:8b", "qwen3:4b"]
    # /api/tags lives at the server ROOT — the /v1 surface must be stripped.
    assert seen == ["http://localhost:11434/api/tags"]


def test_fetch_errors_return_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(url, *, api_key, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(md, "_http_get_json", _boom)
    assert md.fetch_openai_compatible_models("http://x/v1") is None
    assert md.fetch_ollama_models("http://x/v1") is None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_fresh_cache_is_served_without_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    key = md._cache_key("ollama", "http://x/v1")
    md._write_cache({key: {"models": ["cached:1"], "fetched_at": time.time()}})
    fetches: list = []
    monkeypatch.setattr(md, "_fetch_for_kind", lambda *a: fetches.append(a) or ["live"])
    out = md.discovered_models("ollama", "http://x/v1", None, "ollama", ("static:1",))
    # dynamic mode: the endpoint is authoritative — discovered REPLACES static.
    assert out == ["cached:1"]
    assert fetches == [], "fresh cache must not trigger a fetch"


def test_stale_cache_refreshes_and_next_call_is_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = md._cache_key("ollama", "http://x/v1")
    md._write_cache({key: {"models": ["old:1"], "fetched_at": time.time() - 999_999}})
    monkeypatch.setattr(md, "_fetch_for_kind", lambda *a: ["new:1"])
    first = md.discovered_models(
        "ollama", "http://x/v1", None, "ollama", ("static:1",), background=False,
    )
    # background=False → synchronous refresh → already fresh; dynamic mode
    # replaces the static stub outright.
    assert first == ["new:1"]
    second = md.discovered_models("ollama", "http://x/v1", None, "ollama", ("static:1",))
    assert second == ["new:1"]


def test_corrupt_cache_treated_empty(tmp_path: Path) -> None:
    md._cache_path().write_text("{ not json", encoding="utf-8")
    assert md._read_cache() == {}
    md._cache_path().write_text(json.dumps({"version": 999, "entries": {"k": {}}}), encoding="utf-8")
    assert md._read_cache() == {}


def test_atomic_write_leaves_valid_json() -> None:
    md._write_cache({"k": {"models": ["a"], "fetched_at": 1.0}})
    raw = json.loads(md._cache_path().read_text(encoding="utf-8"))
    assert raw["version"] == md.CACHE_VERSION
    assert raw["entries"]["k"]["models"] == ["a"]
    # No stray temp files.
    stray = [p for p in md._cache_path().parent.iterdir() if p.name.startswith(".model-discovery-")]
    assert stray == []


# ---------------------------------------------------------------------------
# Merge semantics
# ---------------------------------------------------------------------------


def test_merge_dynamic_replaces_hybrid_curates() -> None:
    # dynamic: endpoint-authoritative; static only as the no-discovery fallback.
    assert md._merge(["b", "a"], ["a", "c"], "dynamic") == ["b", "a"]
    assert md._merge(None, ["s1"], "dynamic") == ["s1"]
    assert md._merge([], ["s1"], "dynamic") == ["s1"]
    # hybrid: STATIC-first (curation order kept), discovered appended with
    # case-insensitive dedup (mergeCatalogEntries, discoveryService.ts:194-212).
    assert md._merge(["b", "A", "d"], ["a", "c"], "hybrid") == ["a", "c", "b", "d"]
    assert md._merge(None, ["s1"], "hybrid") == ["s1"]


def test_fetch_failure_falls_back_to_static(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(md, "_fetch_for_kind", lambda *a: None)
    out = md.discovered_models(
        "ollama", "http://x/v1", None, "ollama", ("static:1",), background=False,
    )
    assert out == ["static:1"]


def test_no_base_url_returns_static() -> None:
    assert md.discovered_models("p", "", None, "ollama", ("s",)) == ["s"]


# ---------------------------------------------------------------------------
# Single-flight
# ---------------------------------------------------------------------------


def test_single_flight_one_refresh_for_concurrent_stale_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list = []

    def _slow_fetch(*a):
        calls.append(a)
        started.set()
        release.wait(timeout=5)
        return ["live:1"]

    monkeypatch.setattr(md, "_fetch_for_kind", _slow_fetch)
    md.discovered_models("ollama", "http://x/v1", None, "ollama", ("s",))
    started.wait(timeout=5)
    # Second stale read while the first refresh is in flight → no new thread.
    md.discovered_models("ollama", "http://x/v1", None, "ollama", ("s",))
    release.set()
    deadline = time.time() + 5
    while md._refresh_in_flight and time.time() < deadline:
        time.sleep(0.01)
    assert len(calls) == 1, "single-flight must coalesce concurrent refreshes"


# ---------------------------------------------------------------------------
# Provider wiring
# ---------------------------------------------------------------------------


def test_ollama_spec_provider_uses_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.providers.openai_compatible_specs import build_provider_class

    monkeypatch.setattr(md, "_fetch_for_kind", lambda *a: ["local-model:7b"])
    cls = build_provider_class("ollama")
    provider = cls(api_key="", base_url=None, model=None)
    key = md._cache_key("ollama", provider.base_url or cls.SPEC.default_base_url)
    md._write_cache({key: {"models": ["local-model:7b"], "fetched_at": time.time()}})
    models = provider.get_available_models()
    # THE headline behavior: once real local models are discovered, the
    # bogus static stub is GONE (dynamic mode replaces).
    assert models == ["local-model:7b"]
    assert "deepseek-coder:1.3b" not in models


def test_static_spec_provider_never_touches_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.providers.openai_compatible_specs import SPECS_BY_ID, build_provider_class

    marker: list = []
    monkeypatch.setattr(md, "discovered_models", lambda *a, **k: marker.append(a) or ["x"])
    static_id = next(i for i, s in SPECS_BY_ID.items() if s.dynamic_catalog is None)
    cls = build_provider_class(static_id)
    provider = cls(api_key="k", base_url=None, model=None)
    models = provider.get_available_models()
    assert models == list(SPECS_BY_ID[static_id].available_models)
    assert marker == [], "static-catalog specs must not consult discovery"


def test_openrouter_override_hybrid_keeps_curation_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.providers.openrouter_provider import OpenRouterProvider

    key = md._cache_key("openrouter", "https://openrouter.ai/api/v1")
    md._write_cache({key: {"models": ["brand-new/model", "deepseek/deepseek-v4-pro"], "fetched_at": time.time()}})
    provider = OpenRouterProvider(api_key="")  # __init__ is lazy (no network)
    models = provider.get_available_models()
    curated = OpenRouterProvider._curated_models()
    # hybrid: curated order intact at the head; discovered tail deduped in.
    assert models[: len(curated)] == curated
    assert "brand-new/model" in models
    assert models.count("deepseek/deepseek-v4-pro") == 1


def test_cache_path_honors_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import src.config as config

    monkeypatch.setattr(config, "GLOBAL_CONFIG_DIR", tmp_path / "home")
    # bypass the autouse fixture's patch for this assertion
    monkeypatch.undo()  # undo BOTH patches; re-apply config one only
    monkeypatch.setattr(config, "GLOBAL_CONFIG_DIR", tmp_path / "home")
    assert md._cache_path() == tmp_path / "home" / "model-discovery-cache.json"


def test_concurrent_refreshes_for_different_keys_both_persist() -> None:
    barrier = threading.Barrier(2, timeout=5)
    real_fetch = md._fetch_for_kind

    def _sync_refresh(key: str, models: list[str]) -> None:
        md._refresh_in_flight.add(key)
        barrier.wait()
        md._refresh(key, "test", "http://x", None, 1.0)

    import unittest.mock as mock

    with mock.patch.object(
        md, "_fetch_for_kind",
        side_effect=lambda kind, *a: {"k": None}.get(kind) or ["m-" + kind],
    ):
        t1 = threading.Thread(target=_sync_refresh, args=(md._cache_key("p1", "u"), ["a"]))
        t2 = threading.Thread(target=_sync_refresh, args=(md._cache_key("p2", "u"), ["b"]))
        t1.start(); t2.start(); t1.join(5); t2.join(5)
    entries = md._read_cache()
    # The RMW lock prevents the cross-key lost update: both entries persist.
    assert md._cache_key("p1", "u") in entries
    assert md._cache_key("p2", "u") in entries
