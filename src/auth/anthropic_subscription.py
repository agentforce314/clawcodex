"""Claude Pro/Max OAuth credentials for the Anthropic provider.

This implements the authorization-code + PKCE flow formerly bundled by
OpenCode's ``opencode-anthropic-auth`` plugin.  Claude subscription use from
third-party clients is not officially supported by Anthropic; callers must
surface that fact before starting login.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
SCOPES = "org:create_api_key user:profile user:inference"
OAUTH_BETAS = ("oauth-2025-04-20", "interleaved-thinking-2025-05-14")
_refresh_lock = threading.Lock()


def credentials_path() -> Path:
    root = Path(os.environ.get("CLAWCODEX_CONFIG_DIR", Path.home() / ".clawcodex"))
    return root / "anthropic-oauth.json"


@dataclass
class SubscriptionCredentials:
    access_token: str
    refresh_token: str
    expires_at: float
    scope: str = SCOPES

    @property
    def needs_refresh(self) -> bool:
        return self.expires_at <= time.time() + 60


def load_credentials() -> SubscriptionCredentials | None:
    path = credentials_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return SubscriptionCredentials(
            access_token=str(value["access_token"]),
            refresh_token=str(value["refresh_token"]),
            expires_at=float(value["expires_at"]),
            scope=str(value.get("scope", SCOPES)),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def save_credentials(credentials: SubscriptionCredentials) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(asdict(credentials), stream, indent=2)
            stream.write("\n")
        os.replace(tmp, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def remove_credentials() -> bool:
    try:
        credentials_path().unlink()
        return True
    except FileNotFoundError:
        return False


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Claude OAuth request failed ({exc.code}): {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Claude OAuth endpoint returned an invalid response")
    return result


def begin_login() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    query = urllib.parse.urlencode({
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Claude returns this value after the '#' in its copy/paste code.
        "state": verifier,
    })
    return f"{AUTHORIZE_URL}?{query}", verifier


def complete_login(code: str, verifier: str) -> SubscriptionCredentials:
    authorization_code, separator, returned_state = code.strip().partition("#")
    if not authorization_code:
        raise ValueError("Authorization code is empty")
    payload: dict[str, Any] = {
        "code": authorization_code,
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }
    if separator:
        payload["state"] = returned_state
    result = _post_json(TOKEN_URL, payload)
    credentials = _credentials_from_response(result)
    save_credentials(credentials)
    return credentials


def _credentials_from_response(
    result: dict[str, Any], *, old_refresh_token: str = ""
) -> SubscriptionCredentials:
    try:
        access = str(result["access_token"])
        refresh = str(result.get("refresh_token") or old_refresh_token)
        expires = time.time() + float(result["expires_in"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Claude OAuth response omitted required token fields") from exc
    if not access or not refresh:
        raise RuntimeError("Claude OAuth response omitted required token fields")
    return SubscriptionCredentials(access, refresh, expires, str(result.get("scope", SCOPES)))


def get_valid_credentials() -> SubscriptionCredentials | None:
    credentials = load_credentials()
    if credentials is None or not credentials.needs_refresh:
        return credentials
    with _refresh_lock:
        credentials = load_credentials()
        if credentials is None or not credentials.needs_refresh:
            return credentials
        result = _post_json(TOKEN_URL, {
            "grant_type": "refresh_token",
            "refresh_token": credentials.refresh_token,
            "client_id": CLIENT_ID,
        })
        refreshed = _credentials_from_response(
            result, old_refresh_token=credentials.refresh_token
        )
        save_credentials(refreshed)
        return refreshed


def subscription_headers(existing: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(existing or {})
    present = [part.strip() for part in headers.get("anthropic-beta", "").split(",") if part.strip()]
    headers["anthropic-beta"] = ",".join(dict.fromkeys([*OAUTH_BETAS, *present]))
    headers["user-agent"] = "claude-cli/2.1.2 (external, cli)"
    return headers
