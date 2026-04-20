from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)

TOKEN_STORE_FILENAME = "mcp_tokens.json"


@dataclass
class OAuthConfig:
    authorization_url: str
    token_url: str
    client_id: str
    client_secret: str | None = None
    scopes: list[str] = field(default_factory=list)
    redirect_uri: str = "http://localhost:9876/callback"
    use_pkce: bool = True


@dataclass
class TokenData:
    access_token: str
    token_type: str = "Bearer"
    refresh_token: str | None = None
    expires_at: float | None = None
    scope: str | None = None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() >= self.expires_at - 30


@dataclass
class AuthResult:
    success: bool
    token: TokenData | None = None
    error: str | None = None


def _get_token_store_path() -> Path:
    env_override = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_override:
        base = Path(env_override).expanduser().resolve()
    else:
        base = Path.home() / ".claude"
    base.mkdir(parents=True, exist_ok=True)
    return base / TOKEN_STORE_FILENAME


def _generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class McpTokenStore:
    def __init__(self, store_path: Path | None = None) -> None:
        self._path = store_path or _get_token_store_path()
        self._tokens: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._tokens = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._tokens = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._tokens, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Failed to save token store: %s", e)

    def get_token(self, server_name: str) -> TokenData | None:
        data = self._tokens.get(server_name)
        if data is None:
            return None
        return TokenData(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
            scope=data.get("scope"),
        )

    def store_token(self, server_name: str, token: TokenData) -> None:
        self._tokens[server_name] = {
            "access_token": token.access_token,
            "token_type": token.token_type,
            "refresh_token": token.refresh_token,
            "expires_at": token.expires_at,
            "scope": token.scope,
        }
        self._save()

    def remove_token(self, server_name: str) -> bool:
        if server_name in self._tokens:
            del self._tokens[server_name]
            self._save()
            return True
        return False

    def list_servers(self) -> list[str]:
        return list(self._tokens.keys())

    def clear(self) -> None:
        self._tokens.clear()
        self._save()


class McpAuthManager:
    def __init__(self, token_store: McpTokenStore | None = None) -> None:
        self._store = token_store or McpTokenStore()

    def get_auth_headers(self, server_name: str) -> dict[str, str] | None:
        token = self._store.get_token(server_name)
        if token is None:
            return None
        if token.is_expired and token.refresh_token:
            return None
        return {"Authorization": f"{token.token_type} {token.access_token}"}

    async def authenticate_api_key(
        self,
        server_name: str,
        api_key: str,
    ) -> AuthResult:
        token = TokenData(
            access_token=api_key,
            token_type="Bearer",
        )
        self._store.store_token(server_name, token)
        return AuthResult(success=True, token=token)

    async def authenticate_token(
        self,
        server_name: str,
        token_value: str,
        token_type: str = "Bearer",
        expires_in: int | None = None,
    ) -> AuthResult:
        expires_at = None
        if expires_in is not None:
            expires_at = time.time() + expires_in
        token = TokenData(
            access_token=token_value,
            token_type=token_type,
            expires_at=expires_at,
        )
        self._store.store_token(server_name, token)
        return AuthResult(success=True, token=token)

    def build_oauth_url(self, config: OAuthConfig) -> tuple[str, str, str | None]:
        state = secrets.token_urlsafe(32)
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "state": state,
        }
        if config.scopes:
            params["scope"] = " ".join(config.scopes)

        verifier: str | None = None
        if config.use_pkce:
            verifier, challenge = _generate_pkce()
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"

        url = f"{config.authorization_url}?{urlencode(params)}"
        return url, state, verifier

    async def exchange_code(
        self,
        server_name: str,
        config: OAuthConfig,
        code: str,
        verifier: str | None = None,
    ) -> AuthResult:
        data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
            "client_id": config.client_id,
        }
        if config.client_secret:
            data["client_secret"] = config.client_secret
        if verifier:
            data["code_verifier"] = verifier

        try:
            body = urlencode(data).encode("utf-8")
            req = Request(
                config.token_url,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            resp = urlopen(req, timeout=30)
            resp_data = json.loads(resp.read().decode("utf-8"))

            expires_at = None
            if "expires_in" in resp_data:
                expires_at = time.time() + int(resp_data["expires_in"])

            token = TokenData(
                access_token=resp_data["access_token"],
                token_type=resp_data.get("token_type", "Bearer"),
                refresh_token=resp_data.get("refresh_token"),
                expires_at=expires_at,
                scope=resp_data.get("scope"),
            )
            self._store.store_token(server_name, token)
            return AuthResult(success=True, token=token)

        except Exception as e:
            return AuthResult(success=False, error=f"Token exchange failed: {e}")

    async def refresh_token(
        self,
        server_name: str,
        config: OAuthConfig,
    ) -> AuthResult:
        existing = self._store.get_token(server_name)
        if existing is None or existing.refresh_token is None:
            return AuthResult(success=False, error="No refresh token available")

        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": existing.refresh_token,
            "client_id": config.client_id,
        }
        if config.client_secret:
            data["client_secret"] = config.client_secret

        try:
            body = urlencode(data).encode("utf-8")
            req = Request(
                config.token_url,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            resp = urlopen(req, timeout=30)
            resp_data = json.loads(resp.read().decode("utf-8"))

            expires_at = None
            if "expires_in" in resp_data:
                expires_at = time.time() + int(resp_data["expires_in"])

            token = TokenData(
                access_token=resp_data["access_token"],
                token_type=resp_data.get("token_type", "Bearer"),
                refresh_token=resp_data.get("refresh_token", existing.refresh_token),
                expires_at=expires_at,
                scope=resp_data.get("scope"),
            )
            self._store.store_token(server_name, token)
            return AuthResult(success=True, token=token)

        except Exception as e:
            return AuthResult(success=False, error=f"Token refresh failed: {e}")

    def revoke_token(self, server_name: str) -> bool:
        return self._store.remove_token(server_name)

    def has_token(self, server_name: str) -> bool:
        return self._store.get_token(server_name) is not None

    def needs_refresh(self, server_name: str) -> bool:
        token = self._store.get_token(server_name)
        if token is None:
            return False
        return token.is_expired
