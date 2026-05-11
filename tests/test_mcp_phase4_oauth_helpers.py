"""Tests for Phase 4 WI-4.4, 4.6, 4.7 OAuth helpers."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from src.services.mcp.oauth_error_normalization import (
    normalize_oauth_error_body,
)
from src.services.mcp.oauth_port import (
    _FALLBACK_PORT,
    _is_port_free,
    _port_range,
    find_available_port,
)
from src.services.mcp.oauth_redaction import (
    SENSITIVE_OAUTH_PARAMS,
    redact_sensitive_params,
)


class TestPortRange:
    def test_posix_uses_full_iana_dynamic(self):
        if sys.platform.startswith("win"):
            pytest.skip("non-Windows path")
        assert _port_range() == range(49152, 65536)

    def test_windows_uses_narrow_range(self):
        with patch.object(sys, "platform", "win32"):
            assert _port_range() == range(39152, 49152)


class TestFindAvailablePort:
    def test_returns_int_in_range(self, monkeypatch):
        monkeypatch.delenv("MCP_OAUTH_CALLBACK_PORT", raising=False)
        port = find_available_port()
        assert isinstance(port, int)
        assert 1 <= port <= 65535

    def test_env_override_pins_port(self, monkeypatch):
        monkeypatch.setenv("MCP_OAUTH_CALLBACK_PORT", "8765")
        assert find_available_port() == 8765

    def test_env_override_invalid_falls_through(self, monkeypatch):
        monkeypatch.setenv("MCP_OAUTH_CALLBACK_PORT", "not-a-number")
        port = find_available_port()
        assert isinstance(port, int)
        assert 1 <= port <= 65535

    def test_env_override_out_of_range_falls_through(self, monkeypatch):
        monkeypatch.setenv("MCP_OAUTH_CALLBACK_PORT", "99999")
        port = find_available_port()
        assert isinstance(port, int)
        assert 1 <= port <= 65535

    def test_returned_port_is_actually_bindable(self, monkeypatch):
        """Smoke: the returned port should be free at return time. Race
        window after return is acceptable (caller binds immediately)."""
        monkeypatch.delenv("MCP_OAUTH_CALLBACK_PORT", raising=False)
        port = find_available_port()
        assert _is_port_free(port) or port == _FALLBACK_PORT

    def test_all_attempts_fail_returns_fallback(self, monkeypatch):
        """When 100 attempts all fail, the allocator returns
        ``_FALLBACK_PORT`` rather than raising."""
        monkeypatch.delenv("MCP_OAUTH_CALLBACK_PORT", raising=False)
        with patch("src.services.mcp.oauth_port._is_port_free", return_value=False):
            assert find_available_port() == _FALLBACK_PORT


class TestRedactSensitiveParams:
    def test_redacts_state_and_code(self):
        url = "https://auth.example.com/authorize?client_id=abc&state=SECRET&code=ABC123"
        out = redact_sensitive_params(url)
        assert "SECRET" not in out
        assert "ABC123" not in out
        assert "client_id=abc" in out
        assert out.count("REDACTED") == 2

    def test_redacts_all_sensitive_params(self):
        params = "&".join(f"{k}=secret_{i}" for i, k in enumerate(SENSITIVE_OAUTH_PARAMS))
        url = f"https://x?{params}"
        out = redact_sensitive_params(url)
        for i in range(len(SENSITIVE_OAUTH_PARAMS)):
            assert f"secret_{i}" not in out
        assert out.count("REDACTED") == len(SENSITIVE_OAUTH_PARAMS)

    def test_preserves_non_sensitive_params(self):
        url = "https://x?response_type=code&client_id=abc&scope=read+write&state=SECRET"
        out = redact_sensitive_params(url)
        assert "response_type=code" in out
        assert "client_id=abc" in out
        assert "SECRET" not in out

    def test_empty_url_passthrough(self):
        assert redact_sensitive_params("") == ""

    def test_url_without_query_passthrough(self):
        url = "https://auth.example.com/authorize"
        assert redact_sensitive_params(url) == url

    def test_fragment_redacted_too(self):
        """Implicit OAuth flow returns tokens in the URL fragment per
        RFC 6749 §4.2.2. Fragment must be redacted to avoid credential
        leakage in logs."""
        url = "https://app.example.com/callback#access_token=SECRET&token_type=Bearer&expires_in=3600"
        out = redact_sensitive_params(url)
        assert "SECRET" not in out
        assert "REDACTED" in out
        # Non-sensitive fragment fields preserved.
        assert "token_type=Bearer" in out

    def test_case_insensitive_matching(self):
        """A non-spec-compliant server emitting ``State=...`` or
        ``CODE=...`` still gets redacted."""
        url = "https://x?State=SECRET&Code=ABC123"
        out = redact_sensitive_params(url)
        assert "SECRET" not in out
        assert "ABC123" not in out

    def test_multiple_values_for_same_key_all_redacted(self):
        """All N values of a repeated sensitive key get replaced."""
        url = "https://x?state=tok1&state=tok2&state=tok3"
        out = redact_sensitive_params(url)
        assert "tok1" not in out and "tok2" not in out and "tok3" not in out
        assert out.count("REDACTED") == 3


class TestNormalizeOauthErrorBody:
    def test_passes_through_valid_token_response(self):
        body = {"access_token": "t", "token_type": "Bearer", "expires_in": 3600}
        status, out = normalize_oauth_error_body(200, body)
        assert status == 200
        assert out == body

    def test_slack_200_with_error_promotes_to_400(self):
        body = {"ok": False, "error": "invalid_grant"}
        status, out = normalize_oauth_error_body(200, body)
        assert status == 400

    def test_error_null_does_not_promote(self):
        """Regression: ``"error": null`` is not an error — must not
        spuriously promote to 400."""
        body = {"access_token": "t", "error": None}
        status, out = normalize_oauth_error_body(200, body)
        assert status == 200

    def test_2xx_with_access_token_is_not_an_error(self):
        body = {"access_token": "t", "error": "deprecated_warning_field"}
        status, out = normalize_oauth_error_body(200, body)
        assert status == 200

    def test_4xx_with_error_is_unchanged(self):
        body = {"error": "invalid_grant"}
        status, out = normalize_oauth_error_body(400, body)
        assert status == 400

    def test_maps_invalid_refresh_token_to_invalid_grant(self):
        body = {"error": "invalid_refresh_token"}
        status, out = normalize_oauth_error_body(400, body)
        assert out["error"] == "invalid_grant"

    def test_maps_expired_refresh_token_to_invalid_grant(self):
        body = {"error": "expired_refresh_token"}
        status, out = normalize_oauth_error_body(400, body)
        assert out["error"] == "invalid_grant"

    def test_maps_token_expired_to_invalid_grant(self):
        body = {"error": "token_expired"}
        status, out = normalize_oauth_error_body(400, body)
        assert out["error"] == "invalid_grant"

    def test_does_not_remap_canonical_errors(self):
        body = {"error": "invalid_request"}
        status, out = normalize_oauth_error_body(400, body)
        assert out["error"] == "invalid_request"

    def test_non_dict_body_passthrough(self):
        status, out = normalize_oauth_error_body(200, "raw text")  # type: ignore[arg-type]
        assert status == 200
        assert out == "raw text"

    def test_combined_slack_quirk_and_vendor_code(self):
        body = {"ok": False, "error": "invalid_refresh_token"}
        status, out = normalize_oauth_error_body(200, body)
        assert status == 400
        assert out["error"] == "invalid_grant"
