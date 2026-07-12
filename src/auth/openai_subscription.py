"""ChatGPT Plus/Pro OAuth credentials for the OpenAI provider.

This implements the "Sign in with ChatGPT" flow the Codex CLI uses
(authorization-code + PKCE against ``auth.openai.com`` with a localhost
callback), as reimplemented by OpenCode's ``openai`` plugin
(``reference_projects/opencode/packages/opencode/src/plugin/openai/codex.ts``).
Requests then go to the ChatGPT Codex backend instead of the metered
platform API — see ``src/providers/openai_responses.py``.

Subscription use from third-party clients rides Codex CLI's official OAuth
app; it is subject to OpenAI's terms and may stop working. Callers must
surface that before starting login.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
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

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/oauth/authorize"
TOKEN_URL = f"{ISSUER}/oauth/token"
DEVICE_CODE_URL = f"{ISSUER}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{ISSUER}/api/accounts/deviceauth/token"
DEVICE_VERIFY_URL = f"{ISSUER}/codex/device"
CALLBACK_PORT = 1455
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/auth/callback"
SCOPES = "openid profile email offline_access"
# Wire identity: match Codex CLI exactly (OpenCode ships its own name and
# works, but codex_cli_rs is the value the backend is guaranteed to accept).
ORIGINATOR = "codex_cli_rs"
CODEX_API_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"

_refresh_lock = threading.Lock()


def credentials_path() -> Path:
    root = Path(os.environ.get("CLAWCODEX_CONFIG_DIR", Path.home() / ".clawcodex"))
    return root / "openai-oauth.json"


@dataclass
class SubscriptionCredentials:
    access_token: str
    refresh_token: str
    expires_at: float
    account_id: str = ""
    id_token: str = ""

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
            account_id=str(value.get("account_id", "")),
            id_token=str(value.get("id_token", "")),
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


# --- token endpoint ---------------------------------------------------------


def _post_form(url: str, payload: dict[str, str]) -> dict[str, Any]:
    """POST form-urlencoded (the OpenAI token endpoint rejects JSON bodies)."""
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"ChatGPT OAuth request failed ({exc.code}): {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("ChatGPT OAuth endpoint returned an invalid response")
    return result


def _post_json(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST JSON, returning ``(status, body)`` — device-auth endpoints signal
    "keep polling" via 403/404, so HTTP errors are data here, not exceptions."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
            return response.status, body if isinstance(body, dict) else {}
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
        except (ValueError, json.JSONDecodeError):
            body = {}
        return exc.code, body if isinstance(body, dict) else {}


# --- JWT claims (unverified — client-side routing hint only) ----------------


def parse_jwt_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _account_id_from_claims(claims: dict[str, Any]) -> str:
    direct = claims.get("chatgpt_account_id")
    if isinstance(direct, str) and direct:
        return direct
    auth_claim = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict):
        nested = auth_claim.get("chatgpt_account_id")
        if isinstance(nested, str) and nested:
            return nested
    organizations = claims.get("organizations")
    if isinstance(organizations, list) and organizations:
        first = organizations[0]
        if isinstance(first, dict) and isinstance(first.get("id"), str):
            return first["id"]
    return ""


def extract_account_id(id_token: str, access_token: str = "") -> str:
    """ChatGPT account id, preferring the id_token's claims (OpenCode order)."""
    for token in (id_token, access_token):
        if token:
            account_id = _account_id_from_claims(parse_jwt_claims(token))
            if account_id:
                return account_id
    return ""


# --- authorization-code + PKCE flow ------------------------------------------


def begin_login() -> tuple[str, str, str]:
    """Build the authorize URL. Returns ``(url, verifier, state)``."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Codex-flow switches (mirrors OpenCode's authorize URL verbatim).
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "state": state,
        "originator": ORIGINATOR,
    })
    return f"{AUTHORIZE_URL}?{query}", verifier, state


_SUCCESS_PAGE = (
    "<!doctype html><title>ClawCodex</title>"
    "<h1>Authorization successful</h1><p>You can close this window and return "
    "to ClawCodex.</p>"
)


def _error_page(message: str) -> str:
    safe = (
        message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return (
        "<!doctype html><title>ClawCodex</title>"
        f"<h1>Authorization failed</h1><p>{safe}</p>"
    )


def wait_for_callback(state: str, timeout: float = 300.0) -> str:
    """Serve ``http://localhost:1455/auth/callback`` until the browser
    redirect delivers the authorization code (or ``timeout`` expires).

    Binds to localhost only. A ``state`` mismatch is rejected (CSRF guard,
    same as OpenCode's server). Raises ``RuntimeError`` on OAuth errors,
    timeout, or if the port is taken (typically a concurrent Codex CLI
    login — the caller should suggest the device-code flow).
    """
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/auth/callback":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return
            params = urllib.parse.parse_qs(parsed.query)
            error = (params.get("error_description") or params.get("error") or [""])[0]
            code = (params.get("code") or [""])[0]
            returned_state = (params.get("state") or [""])[0]
            if error:
                result["error"] = error
            elif not code:
                result["error"] = "Missing authorization code"
            elif returned_state != state:
                result["error"] = "Invalid OAuth state (possible CSRF)"
            else:
                result["code"] = code
            failed = "error" in result
            self.send_response(400 if failed else 200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            page = _error_page(result["error"]) if failed else _SUCCESS_PAGE
            self.wfile.write(page.encode("utf-8"))
            done.set()

        def log_message(self, *args: Any) -> None:  # silence request logging
            return

    try:
        server = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), Handler)
    except OSError as exc:
        raise RuntimeError(
            f"Could not listen on localhost:{CALLBACK_PORT} ({exc}). "
            "Close the application using that port (often another CLI's login "
            "flow) or use the device-code login instead."
        ) from exc
    server.timeout = 1.0
    try:
        deadline = time.time() + timeout
        while not done.is_set():
            if time.time() > deadline:
                raise RuntimeError("Timed out waiting for the browser authorization")
            server.handle_request()
    finally:
        server.server_close()
    if "error" in result:
        raise RuntimeError(f"ChatGPT authorization failed: {result['error']}")
    return result["code"]


def complete_login(code: str, verifier: str) -> SubscriptionCredentials:
    result = _post_form(TOKEN_URL, {
        "grant_type": "authorization_code",
        "code": code.strip(),
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    })
    credentials = _credentials_from_response(result)
    save_credentials(credentials)
    return credentials


# --- device-code flow (headless boxes; OpenCode's second method) -------------


def begin_device_login() -> dict[str, str]:
    """Start device authorization. Returns ``{device_auth_id, user_code,
    interval, verify_url}`` — show ``user_code`` and ``verify_url`` to the user."""
    status, body = _post_json(DEVICE_CODE_URL, {"client_id": CLIENT_ID})
    if status != 200 or "user_code" not in body:
        raise RuntimeError(f"Failed to initiate device authorization ({status})")
    return {
        "device_auth_id": str(body.get("device_auth_id", "")),
        "user_code": str(body.get("user_code", "")),
        "interval": str(body.get("interval", "5")),
        "verify_url": DEVICE_VERIFY_URL,
    }


def poll_device_login(
    device: dict[str, str], timeout: float = 900.0
) -> SubscriptionCredentials:
    """Poll until the user enters the code, then exchange and persist tokens."""
    try:
        interval = max(int(device.get("interval", "5") or "5"), 1)
    except ValueError:
        interval = 5
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = _post_json(DEVICE_TOKEN_URL, {
            "device_auth_id": device["device_auth_id"],
            "user_code": device["user_code"],
        })
        if status == 200 and body.get("authorization_code"):
            result = _post_form(TOKEN_URL, {
                "grant_type": "authorization_code",
                "code": str(body["authorization_code"]),
                "redirect_uri": f"{ISSUER}/deviceauth/callback",
                "client_id": CLIENT_ID,
                "code_verifier": str(body.get("code_verifier", "")),
            })
            credentials = _credentials_from_response(result)
            save_credentials(credentials)
            return credentials
        if status not in (403, 404):
            raise RuntimeError(f"Device authorization failed ({status})")
        # 403/404 = "not approved yet" — keep polling (+3s safety margin,
        # matching OpenCode's OAUTH_POLLING_SAFETY_MARGIN_MS).
        time.sleep(interval + 3)
    raise RuntimeError("Timed out waiting for device authorization")


# --- import from Codex CLI ----------------------------------------------------


def codex_cli_auth_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"


def has_codex_cli_credentials() -> bool:
    try:
        value = json.loads(codex_cli_auth_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    tokens = value.get("tokens") if isinstance(value, dict) else None
    return bool(
        isinstance(tokens, dict)
        and tokens.get("access_token")
        and tokens.get("refresh_token")
    )


def import_codex_cli_credentials() -> SubscriptionCredentials:
    """Copy an existing Codex CLI ChatGPT login into the clawcodex store.

    The file has no expiry field, so it is derived from the access token's
    ``exp`` claim. The copies then refresh independently; if OpenAI ever
    rotates refresh tokens exclusively, the Codex CLI copy may need a
    re-login — the CLI warns about this at import time.
    """
    path = codex_cli_auth_path()
    value = json.loads(path.read_text(encoding="utf-8"))
    tokens = value.get("tokens") or {}
    access = str(tokens.get("access_token") or "")
    refresh = str(tokens.get("refresh_token") or "")
    if not access or not refresh:
        raise RuntimeError(f"No ChatGPT tokens found in {path}")
    id_token = str(tokens.get("id_token") or "")
    claims = parse_jwt_claims(access)
    expires_at = float(claims.get("exp") or 0.0)
    if expires_at <= 0:
        # Unknown expiry — treat as already stale so first use refreshes.
        expires_at = time.time() - 1
    account_id = str(tokens.get("account_id") or "") or extract_account_id(
        id_token, access
    )
    credentials = SubscriptionCredentials(
        access_token=access,
        refresh_token=refresh,
        expires_at=expires_at,
        account_id=account_id,
        id_token=id_token,
    )
    save_credentials(credentials)
    return credentials


# --- refresh ------------------------------------------------------------------


def _credentials_from_response(
    result: dict[str, Any], *, old: SubscriptionCredentials | None = None
) -> SubscriptionCredentials:
    try:
        access = str(result["access_token"])
        refresh = str(result.get("refresh_token") or (old.refresh_token if old else ""))
        expires = time.time() + float(result.get("expires_in") or 3600)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("ChatGPT OAuth response omitted required token fields") from exc
    if not access or not refresh:
        raise RuntimeError("ChatGPT OAuth response omitted required token fields")
    id_token = str(result.get("id_token") or (old.id_token if old else ""))
    account_id = extract_account_id(id_token, access) or (old.account_id if old else "")
    return SubscriptionCredentials(access, refresh, expires, account_id, id_token)


def get_valid_credentials() -> SubscriptionCredentials | None:
    credentials = load_credentials()
    if credentials is None or not credentials.needs_refresh:
        return credentials
    with _refresh_lock:
        credentials = load_credentials()
        if credentials is None or not credentials.needs_refresh:
            return credentials
        result = _post_form(TOKEN_URL, {
            "grant_type": "refresh_token",
            "refresh_token": credentials.refresh_token,
            "client_id": CLIENT_ID,
        })
        refreshed = _credentials_from_response(result, old=credentials)
        save_credentials(refreshed)
        return refreshed


def force_refresh() -> SubscriptionCredentials | None:
    """Refresh regardless of local expiry (reactive 401 handling)."""
    with _refresh_lock:
        credentials = load_credentials()
        if credentials is None:
            return None
        result = _post_form(TOKEN_URL, {
            "grant_type": "refresh_token",
            "refresh_token": credentials.refresh_token,
            "client_id": CLIENT_ID,
        })
        refreshed = _credentials_from_response(result, old=credentials)
        save_credentials(refreshed)
        return refreshed
