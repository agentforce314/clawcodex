from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest

from src.auth import openai_subscription as auth
from src.providers import openai_subscription_models as catalog


@pytest.fixture
def credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    creds = auth.SubscriptionCredentials("secret-token", "secret-refresh", time.time() + 3600, "acct")
    monkeypatch.setattr(catalog, "load_credentials", lambda: creds)
    return creds


def test_fetch_uses_this_login_and_only_visible_models(credentials, monkeypatch):
    get = Mock(return_value=httpx.Response(200, request=httpx.Request("GET", catalog.MODELS_ENDPOINT), json={
        "models": [
            {"slug": "internal", "visibility": "hide"},
            {"slug": "gpt-5.6-terra", "visibility": "list", "supported_in_api": True},
            {"slug": "gpt-5.3-codex-spark", "visibility": "list", "supported_in_api": False},
            {"slug": "gpt-5.6-terra", "visibility": "list"},
            {"visibility": "list"}, None,
        ],
    }))
    monkeypatch.setattr(httpx, "get", get)
    assert catalog.get_subscription_models(background=False) == ["gpt-5.6-terra", "gpt-5.3-codex-spark"]
    args, kwargs = get.call_args
    assert args == (catalog.MODELS_ENDPOINT,)
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
    assert kwargs["headers"]["chatgpt-account-id"] == "acct"
    assert kwargs["params"] == {"client_version": catalog.CATALOG_CLIENT_VERSION}
    # The live list replaces the static one and survives later picker reads.
    assert catalog.get_subscription_models() == ["gpt-5.6-terra", "gpt-5.3-codex-spark"]
    assert get.call_count == 1
    saved = auth.credentials_path().with_name("openai-models-cache.json").read_text()
    assert "secret-token" not in saved and "secret-refresh" not in saved


@pytest.mark.parametrize("changed", [{"account_id": "other"}, {"access_token": "new-token"}])
def test_cache_cannot_leak_models_between_logins(credentials, monkeypatch, changed):
    fetch = Mock(side_effect=[["gpt-5.6-sol"], ["gpt-5.6-terra"]])
    monkeypatch.setattr(catalog, "_fetch_models", fetch)
    assert catalog.get_subscription_models(credentials, background=False) == ["gpt-5.6-sol"]
    assert catalog.get_subscription_models(replace(credentials, **changed), background=False) == ["gpt-5.6-terra"]
    assert fetch.call_count == 2


def test_stale_cache_survives_failed_refresh_and_throttles_retries(credentials, monkeypatch):
    fetch = Mock(return_value=["gpt-5.6-terra"])
    monkeypatch.setattr(catalog, "_fetch_models", fetch)
    catalog.get_subscription_models(background=False)
    path = auth.credentials_path().with_name("openai-models-cache.json")
    saved = json.loads(path.read_text())
    saved["fetched_at"] = 0
    path.write_text(json.dumps(saved))
    fetch.return_value = None
    assert catalog.get_subscription_models(background=False, force=True) == ["gpt-5.6-terra"]
    assert catalog.get_subscription_models(background=False) == ["gpt-5.6-terra"]
    assert fetch.call_count == 2


def test_empty_live_catalog_does_not_restore_static_models(credentials, monkeypatch):
    monkeypatch.setattr(catalog, "_fetch_models", lambda _: [])
    assert catalog.get_subscription_models(background=False) == []
    assert catalog.get_subscription_models() == []


def test_cold_failure_has_conservative_fallback(credentials, monkeypatch):
    monkeypatch.setattr(catalog, "_fetch_models", lambda _: None)
    assert catalog.get_subscription_models(background=False) == ["gpt-5.5"]


def test_corrupt_cache_is_refetched(credentials, monkeypatch):
    auth.credentials_path().with_name("openai-models-cache.json").write_text("[]")
    monkeypatch.setattr(catalog, "_fetch_models", lambda _: ["gpt-5.6-terra"])
    assert catalog.get_subscription_models(background=False) == ["gpt-5.6-terra"]


def test_background_discovery_is_nonblocking_and_single_flight(credentials, monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    original = catalog._refresh

    def fetch(creds):
        calls.append(creds)
        started.set()
        assert release.wait(3)
        return ["gpt-5.6-terra"]

    def refresh(*args):
        try:
            original(*args)
        finally:
            finished.set()

    monkeypatch.setattr(catalog, "_fetch_models", fetch)
    monkeypatch.setattr(catalog, "_refresh", refresh)
    try:
        assert catalog.get_subscription_models() == ["gpt-5.5"]
        assert started.wait(1)
        assert catalog.get_subscription_models() == ["gpt-5.5"]
        assert len(calls) == 1
    finally:
        release.set()
        assert finished.wait(3)
    assert catalog.get_subscription_models() == ["gpt-5.6-terra"]


def test_expired_credentials_do_not_refresh_tokens_on_picker_thread(credentials, monkeypatch):
    fetch = Mock()
    monkeypatch.setattr(catalog, "_fetch_models", fetch)
    assert catalog.get_subscription_models(replace(credentials, expires_at=0), background=False) == ["gpt-5.5"]
    fetch.assert_not_called()


def test_missing_login_does_not_reuse_cache(credentials, monkeypatch):
    monkeypatch.setattr(catalog, "_fetch_models", lambda _: ["gpt-5.6-terra"])
    catalog.get_subscription_models(background=False)
    monkeypatch.setattr(catalog, "load_credentials", lambda: None)
    assert catalog.get_subscription_models() == []


def test_successful_unlisted_model_survives_catalog_refresh(credentials, monkeypatch):
    monkeypatch.setattr(catalog, "_fetch_models", lambda _: ["gpt-5.6-sol"])
    assert catalog.get_subscription_models(background=False) == ["gpt-5.6-sol"]
    catalog.record_subscription_model(credentials, "gpt-6-astra")
    assert catalog.get_subscription_models(background=False, force=True) == ["gpt-6-astra", "gpt-5.6-sol"]
    assert catalog.get_subscription_models() == ["gpt-6-astra", "gpt-5.6-sol"]


def test_verified_model_is_not_offered_to_another_login(credentials, monkeypatch):
    monkeypatch.setattr(catalog, "_fetch_models", lambda _: ["gpt-5.6-terra"])
    catalog.record_subscription_model(credentials, "gpt-6-astra")
    other = replace(credentials, account_id="free-account", access_token="other-token")
    assert catalog.get_subscription_models(other, background=False) == ["gpt-5.6-terra"]


def test_rejected_model_loses_previous_verification(credentials, monkeypatch):
    monkeypatch.setattr(catalog, "_fetch_models", lambda _: ["gpt-5.6-sol"])
    catalog.get_subscription_models(background=False)
    catalog.record_subscription_model(credentials, "gpt-6-astra")
    catalog.record_subscription_model(credentials, "gpt-6-astra", available=False)
    assert catalog.get_subscription_models() == ["gpt-5.6-sol"]


@pytest.mark.parametrize("discovered", [["gpt-5.5", "gpt-5.6-sol"], None])
def test_rejected_model_stays_hidden_after_refresh_or_fallback(credentials, monkeypatch, discovered):
    monkeypatch.setattr(catalog, "_fetch_models", lambda _: discovered)
    catalog.get_subscription_models(background=False)
    catalog.record_subscription_model(credentials, "gpt-5.5", available=False)
    expected = ["gpt-5.6-sol"] if discovered else []
    assert catalog.get_subscription_models(background=False, force=True) == expected
    assert catalog.get_subscription_models() == expected


def test_rejection_expires_so_model_can_be_retried(credentials, monkeypatch):
    monkeypatch.setattr(catalog, "_fetch_models", lambda _: ["gpt-5.6-sol"])
    catalog.get_subscription_models(background=False)
    catalog.record_subscription_model(credentials, "gpt-5.6-sol", available=False)
    assert catalog.get_subscription_models() == []
    now = time.time()
    monkeypatch.setattr(catalog.time, "time", lambda: now + catalog.TTL_SECONDS + 1)
    assert catalog.get_subscription_models(background=False, force=True) == ["gpt-5.6-sol"]


def test_success_clears_previous_rejection(credentials, monkeypatch):
    monkeypatch.setattr(catalog, "_fetch_models", lambda _: [])
    catalog.get_subscription_models(background=False)
    catalog.record_subscription_model(credentials, "gpt-6-astra", available=False)
    catalog.record_subscription_model(credentials, "gpt-6-astra")
    assert catalog.get_subscription_models() == ["gpt-6-astra"]


def test_refresh_preserves_rejection_recorded_during_http(credentials, monkeypatch):
    def fetch(creds):
        catalog.record_subscription_model(creds, "gpt-5.6-sol", available=False)
        return ["gpt-5.6-sol", "gpt-5.6-terra"]

    monkeypatch.setattr(catalog, "_fetch_models", fetch)
    assert catalog.get_subscription_models(background=False) == ["gpt-5.6-terra"]


def test_expired_verification_is_not_offered(credentials, monkeypatch):
    monkeypatch.setattr(catalog, "_fetch_models", lambda _: ["gpt-5.6-sol"])
    catalog.get_subscription_models(background=False)
    catalog.record_subscription_model(credentials, "gpt-6-astra")
    path = auth.credentials_path().with_name("openai-models-cache.json")
    saved = json.loads(path.read_text())
    saved["verified_models"]["gpt-6-astra"] = 0
    path.write_text(json.dumps(saved))
    assert catalog.get_subscription_models() == ["gpt-5.6-sol"]


def test_refresh_preserves_verification_recorded_during_http(credentials, monkeypatch):
    def fetch(creds):
        catalog.record_subscription_model(creds, "gpt-6-astra")
        return ["gpt-5.6-sol"]

    monkeypatch.setattr(catalog, "_fetch_models", fetch)
    assert catalog.get_subscription_models(background=False) == ["gpt-6-astra", "gpt-5.6-sol"]


def test_subscription_login_saves_discovered_default(credentials, monkeypatch):
    from src.cli import _handle_openai_subscription_login

    monkeypatch.setattr(auth, "has_codex_cli_credentials", lambda: True)
    monkeypatch.setattr(auth, "import_codex_cli_credentials", lambda: credentials)
    monkeypatch.setattr(catalog, "_fetch_models", lambda _: ["gpt-5.6-terra", "gpt-5.6-luna"])
    save = Mock()
    monkeypatch.setattr("src.config.set_api_key", save)
    monkeypatch.setattr("src.config.set_default_provider", Mock())
    prompt = Mock()
    prompt.ask.side_effect = ["yes", "import-codex-cli"]
    assert _handle_openai_subscription_login(Mock(), prompt) == 0
    assert save.call_args.kwargs["default_model"] == "gpt-5.6-terra"
