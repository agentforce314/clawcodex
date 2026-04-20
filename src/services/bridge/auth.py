"""Bridge authentication.

Mirrors TypeScript bridge/auth.ts — token-based auth for bridge sessions.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class BridgeToken:
    """An authentication token for bridge sessions."""
    token: str
    expires_at: float = 0.0
    scope: str = "session"

    @property
    def is_expired(self) -> bool:
        if self.expires_at <= 0:
            return False  # No expiry
        return time.time() >= self.expires_at

    @property
    def is_valid(self) -> bool:
        return bool(self.token) and not self.is_expired


class BridgeAuth:
    """Manages bridge authentication tokens."""

    def __init__(self) -> None:
        self._token: BridgeToken | None = None

    @property
    def current_token(self) -> BridgeToken | None:
        if self._token and self._token.is_expired:
            self._token = None
        return self._token

    @property
    def is_authenticated(self) -> bool:
        token = self.current_token
        return token is not None and token.is_valid

    def set_token(self, token: str, expires_at: float = 0.0, scope: str = "session") -> BridgeToken:
        """Set the authentication token."""
        self._token = BridgeToken(token=token, expires_at=expires_at, scope=scope)
        return self._token

    def clear(self) -> None:
        """Clear the authentication token."""
        self._token = None

    def get_auth_headers(self) -> dict[str, str]:
        """Get authorization headers for bridge requests."""
        token = self.current_token
        if token is None:
            return {}
        return {"Authorization": f"Bearer {token.token}"}
