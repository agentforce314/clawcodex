import json
import os
import tempfile
import time
import pytest

from src.services.mcp.auth import (
    McpAuthManager,
    McpTokenStore,
    OAuthConfig,
    TokenData,
    AuthResult,
    _generate_pkce,
)


class TestTokenData:
    def test_not_expired(self):
        token = TokenData(
            access_token="test",
            expires_at=time.time() + 3600,
        )
        assert token.is_expired is False

    def test_expired(self):
        token = TokenData(
            access_token="test",
            expires_at=time.time() - 100,
        )
        assert token.is_expired is True

    def test_no_expiry(self):
        token = TokenData(access_token="test")
        assert token.is_expired is False


class TestMcpTokenStore:
    def test_store_and_get(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            token = TokenData(access_token="abc123", token_type="Bearer")
            store.store_token("server1", token)

            retrieved = store.get_token("server1")
            assert retrieved is not None
            assert retrieved.access_token == "abc123"
        os.unlink(f.name)

    def test_get_nonexistent(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            assert store.get_token("nonexistent") is None
        os.unlink(f.name)

    def test_remove_token(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            store.store_token("server1", TokenData(access_token="test"))
            assert store.remove_token("server1") is True
            assert store.get_token("server1") is None
        os.unlink(f.name)

    def test_remove_nonexistent(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            assert store.remove_token("nonexistent") is False
        os.unlink(f.name)

    def test_list_servers(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            store.store_token("s1", TokenData(access_token="t1"))
            store.store_token("s2", TokenData(access_token="t2"))
            assert set(store.list_servers()) == {"s1", "s2"}
        os.unlink(f.name)

    def test_clear(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            store.store_token("s1", TokenData(access_token="t1"))
            store.clear()
            assert store.list_servers() == []
        os.unlink(f.name)

    def test_persistence(self):
        from pathlib import Path
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)
            store1 = McpTokenStore(store_path=path)
            store1.store_token("server1", TokenData(access_token="persistent"))

            store2 = McpTokenStore(store_path=path)
            retrieved = store2.get_token("server1")
            assert retrieved is not None
            assert retrieved.access_token == "persistent"
        os.unlink(f.name)


class TestMcpAuthManager:
    @pytest.mark.asyncio
    async def test_authenticate_api_key(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            manager = McpAuthManager(token_store=store)
            result = await manager.authenticate_api_key("server1", "my-api-key")
            assert result.success is True
            assert result.token is not None
            assert result.token.access_token == "my-api-key"
        os.unlink(f.name)

    @pytest.mark.asyncio
    async def test_authenticate_token(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            manager = McpAuthManager(token_store=store)
            result = await manager.authenticate_token(
                "server1", "my-token", expires_in=3600
            )
            assert result.success is True
            assert result.token is not None
            assert not result.token.is_expired
        os.unlink(f.name)

    def test_get_auth_headers(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            manager = McpAuthManager(token_store=store)

            assert manager.get_auth_headers("server1") is None

            store.store_token("server1", TokenData(
                access_token="test-token",
                token_type="Bearer",
                expires_at=time.time() + 3600,
            ))

            headers = manager.get_auth_headers("server1")
            assert headers is not None
            assert headers["Authorization"] == "Bearer test-token"
        os.unlink(f.name)

    def test_has_token(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            manager = McpAuthManager(token_store=store)
            assert manager.has_token("server1") is False
            store.store_token("server1", TokenData(access_token="test"))
            assert manager.has_token("server1") is True
        os.unlink(f.name)

    def test_revoke_token(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            manager = McpAuthManager(token_store=store)
            store.store_token("server1", TokenData(access_token="test"))
            assert manager.revoke_token("server1") is True
            assert manager.has_token("server1") is False
        os.unlink(f.name)

    def test_needs_refresh(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            manager = McpAuthManager(token_store=store)

            store.store_token("server1", TokenData(
                access_token="test",
                expires_at=time.time() - 100,
            ))
            assert manager.needs_refresh("server1") is True

            store.store_token("server2", TokenData(
                access_token="test",
                expires_at=time.time() + 3600,
            ))
            assert manager.needs_refresh("server2") is False
        os.unlink(f.name)

    def test_build_oauth_url(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store = McpTokenStore(store_path=__import__("pathlib").Path(f.name))
            manager = McpAuthManager(token_store=store)

            config = OAuthConfig(
                authorization_url="https://auth.example.com/authorize",
                token_url="https://auth.example.com/token",
                client_id="test-client",
                scopes=["read", "write"],
            )
            url, state, verifier = manager.build_oauth_url(config)
            assert "auth.example.com" in url
            assert "test-client" in url
            assert state is not None
            assert verifier is not None
        os.unlink(f.name)


class TestGeneratePkce:
    def test_generates_pair(self):
        verifier, challenge = _generate_pkce()
        assert len(verifier) > 0
        assert len(challenge) > 0
        assert verifier != challenge

    def test_different_each_time(self):
        v1, c1 = _generate_pkce()
        v2, c2 = _generate_pkce()
        assert v1 != v2
