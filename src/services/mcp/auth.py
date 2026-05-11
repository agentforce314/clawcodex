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

import httpx

from .oauth_error_normalization import normalize_oauth_error_body

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


KEYRING_SERVICE = "claude-code-mcp"
# Stored separately so we can enumerate ``list_servers`` (the keyring API
# doesn't iterate by service). Updated atomically alongside the keyring.
KEYRING_INDEX_FILENAME = "mcp_token_index.json"


def _serialize_token(token: TokenData) -> str:
    return json.dumps({
        "access_token": token.access_token,
        "token_type": token.token_type,
        "refresh_token": token.refresh_token,
        "expires_at": token.expires_at,
        "scope": token.scope,
    })


def _deserialize_token(raw: str) -> TokenData:
    data = json.loads(raw)
    return TokenData(
        access_token=data["access_token"],
        token_type=data.get("token_type", "Bearer"),
        refresh_token=data.get("refresh_token"),
        expires_at=data.get("expires_at"),
        scope=data.get("scope"),
    )


class McpTokenStore:
    """OAuth-token storage backed by the OS keychain (Phase 4 WI-4.2, gap #5).

    Mirrors TS' platform-native secure storage pattern (macOS Keychain,
    Linux secret-service, Windows DPAPI) — replaces the previous
    plaintext ``~/.claude/mcp_tokens.json`` file (a security regression
    vs TS that the gap analysis flagged as a blocker).

    Backend detection: if ``keyring`` resolves to ``FailKeyring`` (no
    secure backend available — uncommon on developer machines but does
    happen on stripped-down CI environments), we **fail loudly** rather
    than silently regress to plaintext. Operators who explicitly want
    plaintext can opt in via ``MCP_ALLOW_PLAINTEXT_TOKEN_STORAGE=1``;
    that path is intentionally awkward to discover.

    A small index file (``~/.claude/mcp_token_index.json``) tracks the
    set of server names whose tokens are stored — the keyring API does
    not iterate by service, so we need a side-channel to implement
    ``list_servers``. The index contains only names, never tokens.

    On first init, any pre-existing plaintext ``mcp_tokens.json`` is
    migrated into the keyring and the file is renamed to
    ``mcp_tokens.json.legacy`` with a one-time INFO log. Two release
    cycles later the migration shim can be deleted.
    """

    def __init__(self, store_path: Path | None = None) -> None:
        # ``store_path`` is retained for API compatibility (and to drive
        # the legacy-migration source). The index lives next to the
        # legacy path, with a name derived from store_path's stem so
        # tempfile-based tests get isolated index files even when they
        # all live under the same /tmp directory.
        self._legacy_path = store_path or _get_token_store_path()
        if store_path is not None:
            # Test path: index sits alongside the test's tempfile and
            # shares its stem so concurrent tempfiles don't collide.
            self._index_path = store_path.with_suffix(store_path.suffix + ".index")
        else:
            self._index_path = self._legacy_path.parent / KEYRING_INDEX_FILENAME
        self._index: set[str] = self._load_index()
        self._using_plaintext_fallback = False
        self._validate_backend()
        self._migrate_legacy_if_present()

    @staticmethod
    def _allow_plaintext_fallback() -> bool:
        return os.environ.get("MCP_ALLOW_PLAINTEXT_TOKEN_STORAGE", "").strip() == "1"

    def _validate_backend(self) -> None:
        try:
            import keyring
            from keyring.backends.fail import Keyring as FailKeyring
        except ImportError as exc:  # pragma: no cover - keyring is a hard dep
            raise RuntimeError(
                "MCP token storage requires the 'keyring' Python package. "
                "Install with: pip install keyring"
            ) from exc
        backend = keyring.get_keyring()
        if isinstance(backend, FailKeyring):
            if self._allow_plaintext_fallback():
                logger.warning(
                    "MCP token storage: no secure keyring backend available "
                    "(%s); falling back to plaintext storage at %s because "
                    "MCP_ALLOW_PLAINTEXT_TOKEN_STORAGE=1 was set. THIS IS "
                    "INSECURE — tokens will be readable to any process "
                    "running as this user.",
                    type(backend).__name__, self._legacy_path,
                )
                self._using_plaintext_fallback = True
                return
            raise RuntimeError(
                "MCP token storage requires a secure keyring backend "
                "(macOS Keychain, Linux secret-service, or Windows DPAPI), "
                "but none is available on this system. Set "
                "MCP_ALLOW_PLAINTEXT_TOKEN_STORAGE=1 to opt into plaintext "
                "storage instead — note that this is insecure."
            )

    def _load_index(self) -> set[str]:
        if not self._index_path.exists():
            return set()
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(str(s) for s in data)
        except (json.JSONDecodeError, OSError):
            pass
        return set()

    def _save_index(self) -> None:
        # Atomic write: tmp-file + rename so a crash mid-write doesn't
        # leave a corrupt JSON document that ``_load_index`` would have
        # to silently swallow on next start (losing the entire index).
        try:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._index_path.with_suffix(self._index_path.suffix + ".tmp")
            tmp.write_text(json.dumps(sorted(self._index), indent=2), encoding="utf-8")
            os.replace(tmp, self._index_path)
        except OSError as exc:
            logger.warning("Failed to save MCP token index: %s", exc)

    def _migrate_legacy_if_present(self) -> None:
        if not self._legacy_path.exists():
            return
        # Critical: when plaintext fallback is active, the legacy file IS
        # the live plaintext store. Migrating-then-renaming would archive
        # all data and leave the store empty. Skip migration entirely.
        if self._using_plaintext_fallback:
            return
        try:
            raw = self._legacy_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("MCP legacy token read failed: %s", exc)
            return
        if not raw:
            # Empty file isn't a migration source (and isn't an error); the
            # noisy "Expecting value" warning would mislead operators.
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("MCP legacy token migration failed: %s", exc)
            return
        if not isinstance(data, dict) or not data:
            return
        migrated = 0
        dropped: list[str] = []
        for server_name, token_data in data.items():
            if not isinstance(server_name, str) or not isinstance(token_data, dict):
                dropped.append(repr(server_name))
                continue
            try:
                token = TokenData(
                    access_token=token_data["access_token"],
                    token_type=token_data.get("token_type", "Bearer"),
                    refresh_token=token_data.get("refresh_token"),
                    expires_at=token_data.get("expires_at"),
                    scope=token_data.get("scope"),
                )
            except KeyError:
                dropped.append(server_name)
                continue
            self.store_token(server_name, token)
            migrated += 1
        if dropped:
            logger.warning(
                "MCP legacy token migration: skipped %d malformed entr%s: %s",
                len(dropped), "y" if len(dropped) == 1 else "ies", dropped,
            )
        if migrated:
            # Pick a non-colliding archive suffix so a previous migration's
            # backup is preserved. POSIX rename clobbers silently, which
            # would destroy a recoverable copy.
            archive = self._pick_legacy_archive_path()
            try:
                self._legacy_path.rename(archive)
            except OSError as exc:
                logger.warning(
                    "Migrated %d MCP tokens to keyring but failed to rename "
                    "legacy plaintext file %s: %s",
                    migrated, self._legacy_path, exc,
                )
            else:
                logger.info(
                    "Migrated %d MCP token(s) from legacy plaintext file "
                    "%s to keyring; legacy file renamed to %s. Delete it "
                    "after verifying.",
                    migrated, self._legacy_path, archive,
                )

    def _pick_legacy_archive_path(self) -> Path:
        """Return a path of the form ``mcp_tokens.json.legacy[.N]`` that
        does not collide with an existing file. Bounds the search at 100
        attempts; a real corruption scenario won't approach that count."""
        base = self._legacy_path.with_suffix(self._legacy_path.suffix + ".legacy")
        if not base.exists():
            return base
        for n in range(1, 100):
            candidate = base.with_suffix(base.suffix + f".{n}")
            if not candidate.exists():
                return candidate
        # Pathological — fall back to base and let rename clobber as a
        # last resort. Not reachable in practice.
        return base  # pragma: no cover

    # --- Public API -------------------------------------------------------

    def get_token(self, server_name: str) -> TokenData | None:
        if self._using_plaintext_fallback:
            return self._get_token_plaintext(server_name)
        try:
            import keyring

            raw = keyring.get_password(KEYRING_SERVICE, server_name)
        except Exception as exc:
            logger.warning("MCP keyring lookup failed for %r: %s", server_name, exc)
            return None
        if raw is None:
            return None
        try:
            return _deserialize_token(raw)
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("MCP keyring entry for %r is malformed: %s", server_name, exc)
            return None

    def store_token(self, server_name: str, token: TokenData) -> None:
        if self._using_plaintext_fallback:
            self._store_token_plaintext(server_name, token)
            return
        try:
            import keyring
            from keyring.errors import KeyringError

            keyring.set_password(KEYRING_SERVICE, server_name, _serialize_token(token))
        except KeyringError as exc:
            logger.error("MCP keyring write failed for %r: %s", server_name, exc)
            raise
        self._index.add(server_name)
        self._save_index()

    def remove_token(self, server_name: str) -> bool:
        if self._using_plaintext_fallback:
            return self._remove_token_plaintext(server_name)
        try:
            import keyring
            from keyring.errors import PasswordDeleteError

            keyring.delete_password(KEYRING_SERVICE, server_name)
            removed = True
        except PasswordDeleteError:
            # Backend confirmed the entry isn't there. Index can be
            # safely cleaned (a stale index entry would survive otherwise).
            removed = False
        except Exception as exc:
            # Transient backend failure: keyring may still hold the entry.
            # Do NOT mutate the index; otherwise list_servers() would lie
            # about what's actually stored. Caller can retry.
            logger.warning("MCP keyring delete failed for %r: %s", server_name, exc)
            return False
        if server_name in self._index:
            self._index.discard(server_name)
            self._save_index()
        return removed

    def list_servers(self) -> list[str]:
        if self._using_plaintext_fallback:
            return list(self._plaintext_tokens().keys())
        return sorted(self._index)

    def clear(self) -> None:
        for name in list(self._index):
            self.remove_token(name)
        if self._using_plaintext_fallback:
            self._clear_plaintext()

    # --- Plaintext fallback (only when explicitly opted in) ---------------

    def _plaintext_tokens(self) -> dict[str, dict[str, Any]]:
        if not self._legacy_path.exists():
            return {}
        try:
            data = json.loads(self._legacy_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _get_token_plaintext(self, server_name: str) -> TokenData | None:
        data = self._plaintext_tokens().get(server_name)
        if data is None:
            return None
        return TokenData(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
            scope=data.get("scope"),
        )

    def _store_token_plaintext(self, server_name: str, token: TokenData) -> None:
        tokens = self._plaintext_tokens()
        tokens[server_name] = {
            "access_token": token.access_token,
            "token_type": token.token_type,
            "refresh_token": token.refresh_token,
            "expires_at": token.expires_at,
            "scope": token.scope,
        }
        try:
            self._legacy_path.parent.mkdir(parents=True, exist_ok=True)
            self._legacy_path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("MCP plaintext token write failed: %s", exc)

    def _remove_token_plaintext(self, server_name: str) -> bool:
        tokens = self._plaintext_tokens()
        if server_name in tokens:
            del tokens[server_name]
            try:
                self._legacy_path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
            except OSError as exc:
                logger.warning("MCP plaintext token write failed: %s", exc)
            return True
        return False

    def _clear_plaintext(self) -> None:
        try:
            self._legacy_path.write_text("{}", encoding="utf-8")
        except OSError as exc:
            logger.warning("MCP plaintext token clear failed: %s", exc)


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
            resp_data = await self._post_token_endpoint(config.token_url, data)
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
            resp_data = await self._post_token_endpoint(config.token_url, data)
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

    async def _post_token_endpoint(
        self, token_url: str, data: dict[str, str]
    ) -> dict[str, Any]:
        """POST to an OAuth token endpoint via httpx.AsyncClient.

        Previously used ``urllib.request.urlopen``, which is synchronous
        blocking I/O and froze the event loop for the duration of the
        round-trip (~100ms–1s typical). That stalled every other
        async task — including concurrent MCP receive loops and the
        OAuth callback listener itself. Use httpx.AsyncClient with a
        30s timeout instead.

        Normalizes vendor 200+error responses (Slack-style) to a proper
        4xx error via ``normalize_oauth_error_body`` so the refresh
        path raises on ``invalid_grant`` rather than silently storing
        a nonsensical token.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        # Vendor RFC-quirks (Slack returns 200 with {"error": "..."}) —
        # normalize before status check so a vendor 200+error becomes a
        # raised error rather than a silently-stored garbage token.
        try:
            body_json: Any = response.json()
        except (ValueError, TypeError):
            body_json = None
        if isinstance(body_json, dict):
            status_code, body_json = normalize_oauth_error_body(
                response.status_code, body_json
            )
            if status_code >= 400:
                err = body_json.get("error", "oauth_error")
                desc = body_json.get("error_description", "")
                msg = f"{status_code} {err}"
                if desc:
                    msg += f": {desc}"
                raise RuntimeError(msg)
            return body_json

        response.raise_for_status()
        return response.json()

    def revoke_token(self, server_name: str) -> bool:
        return self._store.remove_token(server_name)

    def has_token(self, server_name: str) -> bool:
        return self._store.get_token(server_name) is not None

    def needs_refresh(self, server_name: str) -> bool:
        token = self._store.get_token(server_name)
        if token is None:
            return False
        return token.is_expired
