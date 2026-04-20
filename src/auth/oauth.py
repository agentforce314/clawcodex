"""OAuth authorization code flow matching TypeScript services/oauth/.

Supports PKCE, localhost callback server, token exchange, and refresh.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_AUTH_URL = "https://console.anthropic.com/oauth/authorize"
DEFAULT_TOKEN_URL = "https://console.anthropic.com/oauth/token"
DEFAULT_REDIRECT_PORT = 54321
DEFAULT_CLIENT_ID = "clawcodex-cli"


@dataclass
class OAuthTokens:
    """OAuth token set."""
    access_token: str
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at: float = 0.0  # Unix timestamp
    scope: str = ""

    @property
    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() >= self.expires_at


@dataclass
class OAuthFlow:
    """OAuth PKCE authorization code flow."""

    auth_url: str = DEFAULT_AUTH_URL
    token_url: str = DEFAULT_TOKEN_URL
    client_id: str = DEFAULT_CLIENT_ID
    redirect_port: int = DEFAULT_REDIRECT_PORT
    scope: str = "api"

    _code_verifier: str = field(default="", init=False, repr=False)
    _state: str = field(default="", init=False, repr=False)

    def generate_pkce(self) -> tuple[str, str]:
        """Generate PKCE code_verifier and code_challenge."""
        self._code_verifier = secrets.token_urlsafe(64)[:128]
        digest = hashlib.sha256(self._code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return self._code_verifier, code_challenge

    def build_authorization_url(self) -> str:
        """Build the full authorization URL with PKCE."""
        self._state = secrets.token_urlsafe(32)
        _, code_challenge = self.generate_pkce()

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": f"http://localhost:{self.redirect_port}/callback",
            "scope": self.scope,
            "state": self._state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.auth_url}?{urllib.parse.urlencode(params)}"

    def wait_for_callback(self, timeout: float = 120.0) -> str | None:
        """Start localhost server and wait for the OAuth callback.

        Returns the authorization code, or None on timeout.
        """
        code_holder: dict[str, str | None] = {"code": None}

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self_handler) -> None:  # noqa: N805
                parsed = urllib.parse.urlparse(self_handler.path)
                params = urllib.parse.parse_qs(parsed.query)
                if "code" in params:
                    code_holder["code"] = params["code"][0]
                    self_handler.send_response(200)
                    self_handler.send_header("Content-Type", "text/html")
                    self_handler.end_headers()
                    self_handler.wfile.write(
                        b"<html><body><h2>Authorization successful!</h2>"
                        b"<p>You can close this window.</p></body></html>"
                    )
                else:
                    self_handler.send_response(400)
                    self_handler.end_headers()

            def log_message(self_handler, format: str, *args: Any) -> None:  # noqa: N805
                pass  # Suppress server logs

        server = http.server.HTTPServer(("localhost", self.redirect_port), CallbackHandler)
        server.timeout = timeout

        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        server.server_close()
        return code_holder["code"]

    def exchange_code(self, code: str) -> OAuthTokens | None:
        """Exchange authorization code for tokens."""
        try:
            import urllib.request

            data = urllib.parse.urlencode({
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.client_id,
                "redirect_uri": f"http://localhost:{self.redirect_port}/callback",
                "code_verifier": self._code_verifier,
            }).encode("utf-8")

            req = urllib.request.Request(
                self.token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())

            expires_in = body.get("expires_in", 3600)
            return OAuthTokens(
                access_token=body["access_token"],
                refresh_token=body.get("refresh_token", ""),
                token_type=body.get("token_type", "Bearer"),
                expires_at=time.time() + expires_in,
                scope=body.get("scope", ""),
            )
        except Exception as e:
            logger.error("OAuth token exchange failed: %s", e)
            return None

    def refresh_tokens(self, refresh_token: str) -> OAuthTokens | None:
        """Refresh an expired access token."""
        try:
            import urllib.request

            data = urllib.parse.urlencode({
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
            }).encode("utf-8")

            req = urllib.request.Request(
                self.token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())

            expires_in = body.get("expires_in", 3600)
            return OAuthTokens(
                access_token=body["access_token"],
                refresh_token=body.get("refresh_token", refresh_token),
                token_type=body.get("token_type", "Bearer"),
                expires_at=time.time() + expires_in,
                scope=body.get("scope", ""),
            )
        except Exception as e:
            logger.error("OAuth token refresh failed: %s", e)
            return None
