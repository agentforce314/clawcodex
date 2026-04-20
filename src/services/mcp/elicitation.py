from __future__ import annotations

import logging
import webbrowser
from typing import Any

from .auth import McpAuthManager, OAuthConfig, AuthResult

logger = logging.getLogger(__name__)


class ElicitationHandler:
    def __init__(
        self,
        auth_manager: McpAuthManager | None = None,
    ) -> None:
        self._auth_manager = auth_manager or McpAuthManager()
        self._pending_auth: dict[str, dict[str, Any]] = {}

    def detect_auth_required(self, response: dict[str, Any]) -> bool:
        error = response.get("error", {})
        if isinstance(error, dict):
            code = error.get("code")
            if code in (-32001, -32002):
                return True
            message = error.get("message", "").lower()
            if "auth" in message or "unauthorized" in message:
                return True
        return False

    async def handle_auth_required(
        self,
        server_name: str,
        response: dict[str, Any],
        *,
        oauth_config: OAuthConfig | None = None,
    ) -> AuthResult:
        error = response.get("error", {})
        if isinstance(error, dict):
            data = error.get("data", {})
            if isinstance(data, dict):
                auth_url = data.get("authorizationUrl")
                token_url = data.get("tokenUrl")
                client_id = data.get("clientId")

                if auth_url and token_url and client_id:
                    oauth_config = OAuthConfig(
                        authorization_url=auth_url,
                        token_url=token_url,
                        client_id=client_id,
                        scopes=data.get("scopes", []),
                    )

        if oauth_config is None:
            return AuthResult(
                success=False,
                error="No OAuth configuration available for authentication",
            )

        url, state, verifier = self._auth_manager.build_oauth_url(oauth_config)

        self._pending_auth[server_name] = {
            "state": state,
            "verifier": verifier,
            "oauth_config": oauth_config,
        }

        try:
            webbrowser.open(url)
        except Exception:
            pass

        return AuthResult(
            success=False,
            error=f"Authentication required. Please visit: {url}",
        )

    async def complete_auth(
        self,
        server_name: str,
        code: str,
        state: str | None = None,
    ) -> AuthResult:
        pending = self._pending_auth.get(server_name)
        if pending is None:
            return AuthResult(
                success=False,
                error="No pending authentication for this server",
            )

        if state and pending.get("state") != state:
            return AuthResult(
                success=False,
                error="State mismatch in OAuth callback",
            )

        oauth_config = pending["oauth_config"]
        verifier = pending.get("verifier")

        result = await self._auth_manager.exchange_code(
            server_name, oauth_config, code, verifier
        )

        if result.success:
            del self._pending_auth[server_name]

        return result

    def has_pending_auth(self, server_name: str) -> bool:
        return server_name in self._pending_auth

    def cancel_auth(self, server_name: str) -> None:
        self._pending_auth.pop(server_name, None)
