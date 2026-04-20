"""Tests for R2-WS-6: Auth system."""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

import pytest

from src.auth.auth import (
    ApiKeyInfo,
    load_api_key,
    validate_api_key,
    get_api_key_source,
)
from src.auth.oauth import OAuthFlow, OAuthTokens
from src.auth.aws import AwsAuth, AwsCredentials
from src.auth.gemini import GeminiAuth


class TestApiKeyValidation:
    def test_valid_anthropic_key(self):
        assert validate_api_key("sk-ant-api03-abcdefghijklmnopqrst", "anthropic") is True

    def test_invalid_anthropic_key(self):
        assert validate_api_key("bad-key", "anthropic") is False

    def test_empty_key(self):
        assert validate_api_key("", "anthropic") is False

    def test_valid_openai_key(self):
        assert validate_api_key("sk-abcdefghijklmnopqrstuvwxyz", "openai") is True

    def test_unknown_provider_accepts_long_key(self):
        assert validate_api_key("some-long-api-key-1234567890", "custom") is True

    def test_unknown_provider_rejects_short_key(self):
        assert validate_api_key("short", "custom") is False


class TestLoadApiKey:
    def test_from_env(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test12345678901234567890"}):
            info = load_api_key("anthropic")
            assert info is not None
            assert info.key == "sk-ant-test12345678901234567890"
            assert info.source == "env"

    def test_from_config(self):
        mock_config = {
            "providers": {
                "anthropic": {"api_key": "sk-ant-config-key-12345678901234"}
            }
        }
        with patch.dict(os.environ, {}, clear=False):
            # Clear ANTHROPIC_API_KEY if set
            env = dict(os.environ)
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("CLAUDE_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                with patch("src.config.load_config", return_value=mock_config):
                    info = load_api_key("anthropic")
                    assert info is not None
                    assert info.source == "config"

    def test_returns_none_when_no_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("src.config.load_config", return_value={"providers": {}}):
                with patch("src.auth.auth._load_from_keychain", return_value=None):
                    info = load_api_key("anthropic")
                    assert info is None


class TestGetApiKeySource:
    def test_env_source(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test12345678901234567890"}):
            assert get_api_key_source("anthropic") == "env"

    def test_unknown_source(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("src.config.load_config", return_value={"providers": {}}):
                assert get_api_key_source("anthropic") == "unknown"


class TestOAuthFlow:
    def test_generate_pkce(self):
        flow = OAuthFlow()
        verifier, challenge = flow.generate_pkce()
        assert len(verifier) > 40
        assert len(challenge) > 20
        assert verifier != challenge

    def test_build_authorization_url(self):
        flow = OAuthFlow(client_id="test-client")
        url = flow.build_authorization_url()
        assert "response_type=code" in url
        assert "client_id=test-client" in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        assert "state=" in url

    def test_oauth_tokens_expiry(self):
        import time
        tokens = OAuthTokens(access_token="test", expires_at=time.time() - 100)
        assert tokens.is_expired is True

        tokens2 = OAuthTokens(access_token="test", expires_at=time.time() + 3600)
        assert tokens2.is_expired is False

        tokens3 = OAuthTokens(access_token="test", expires_at=0)
        assert tokens3.is_expired is False  # 0 means no expiry


class TestAwsAuth:
    def test_load_from_env(self):
        env = {
            "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "AWS_REGION": "us-west-2",
        }
        with patch.dict(os.environ, env, clear=False):
            auth = AwsAuth()
            creds = auth.load_credentials()
            assert creds is not None
            assert creds.access_key_id == "AKIAIOSFODNN7EXAMPLE"
            assert creds.region == "us-west-2"

    def test_is_configured(self):
        env = {
            "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "secret",
        }
        with patch.dict(os.environ, env, clear=False):
            auth = AwsAuth()
            assert auth.is_configured() is True

    def test_bedrock_endpoint(self):
        auth = AwsAuth(region="eu-west-1")
        endpoint = auth.get_bedrock_endpoint()
        assert "eu-west-1" in endpoint
        assert "bedrock-runtime" in endpoint


class TestGeminiAuth:
    def test_load_from_env(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini-key"}):
            auth = GeminiAuth()
            key = auth.load_api_key()
            assert key == "test-gemini-key"

    def test_google_api_key_fallback(self):
        env = {"GOOGLE_API_KEY": "google-key"}
        with patch.dict(os.environ, env, clear=True):
            auth = GeminiAuth()
            key = auth.load_api_key()
            assert key == "google-key"

    def test_is_configured(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "key"}):
            auth = GeminiAuth()
            assert auth.is_configured() is True
