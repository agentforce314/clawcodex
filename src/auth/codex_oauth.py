"""OpenAI Codex ChatGPT OAuth device-code flow."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from .codex_store import (
    CODEX_PROVIDER_ID,
    CodexAuthRecord,
    CodexOAuthTokens,
    get_auth_file,
    import_codex_cli_tokens,
    read_codex_tokens,
    save_codex_tokens,
)

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_DEVICE_USER_CODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
CODEX_DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
CODEX_DEVICE_VERIFICATION_URL = "https://auth.openai.com/codex/device"
CODEX_DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120


class CodexAuthError(RuntimeError):
    def __init__(self, message: str, *, code: str = "codex_auth_error", relogin_required: bool = False):
        super().__init__(message)
        self.code = code
        self.relogin_required = relogin_required


@dataclass
class CodexDeviceFlow:
    user_code: str
    device_auth_id: str
    verification_uri: str = CODEX_DEVICE_VERIFICATION_URL
    interval: int = 5


@dataclass
class CodexRuntimeCredentials:
    provider: str
    api_key: str
    base_url: str
    source: str
    auth_mode: str
    last_refresh: float | None = None


@dataclass
class CodexAuthStatus:
    is_authenticated: bool
    auth_file: str
    source: str | None = None
    expires_at: float | None = None
    last_refresh: float | None = None
    error: str | None = None


def start_codex_device_flow(*, timeout_seconds: float = 15.0) -> CodexDeviceFlow:
    try:
        response = httpx.post(
            CODEX_DEVICE_USER_CODE_URL,
            json={"client_id": CODEX_OAUTH_CLIENT_ID},
            headers={"Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
    except Exception as exc:
        raise CodexAuthError(f"Failed to request Codex device code: {exc}", code="device_code_request_failed") from exc
    if response.status_code != 200:
        raise CodexAuthError(
            f"Codex device code request returned status {response.status_code}.",
            code="device_code_request_error",
        )
    data = _response_json(response, "device_code_invalid_json")
    user_code = data.get("user_code")
    device_auth_id = data.get("device_auth_id")
    if not isinstance(user_code, str) or not user_code:
        raise CodexAuthError("Codex device code response is missing user_code.", code="device_code_incomplete")
    if not isinstance(device_auth_id, str) or not device_auth_id:
        raise CodexAuthError("Codex device code response is missing device_auth_id.", code="device_code_incomplete")
    return CodexDeviceFlow(
        user_code=user_code,
        device_auth_id=device_auth_id,
        verification_uri=str(data.get("verification_uri") or CODEX_DEVICE_VERIFICATION_URL),
        interval=max(3, int(data.get("interval") or 5)),
    )


def poll_codex_device_flow(flow: CodexDeviceFlow, *, timeout_seconds: float = 15.0) -> dict[str, str] | None:
    try:
        response = httpx.post(
            CODEX_DEVICE_TOKEN_URL,
            json={"device_auth_id": flow.device_auth_id, "user_code": flow.user_code},
            headers={"Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
    except Exception as exc:
        raise CodexAuthError(f"Codex device auth polling failed: {exc}", code="device_code_poll_failed") from exc
    if response.status_code in {403, 404}:
        return None
    if response.status_code != 200:
        raise CodexAuthError(
            f"Codex device auth polling returned status {response.status_code}.",
            code="device_code_poll_error",
        )
    data = _response_json(response, "device_code_poll_invalid_json")
    authorization_code = data.get("authorization_code")
    code_verifier = data.get("code_verifier")
    if not isinstance(authorization_code, str) or not authorization_code:
        raise CodexAuthError("Codex device auth response is missing authorization_code.", code="device_code_incomplete_exchange")
    if not isinstance(code_verifier, str) or not code_verifier:
        raise CodexAuthError("Codex device auth response is missing code_verifier.", code="device_code_incomplete_exchange")
    return {"authorization_code": authorization_code, "code_verifier": code_verifier}


def exchange_codex_authorization(
    authorization_code: str,
    code_verifier: str,
    *,
    timeout_seconds: float = 15.0,
) -> CodexOAuthTokens:
    return _token_request(
        {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": CODEX_DEVICE_REDIRECT_URI,
            "client_id": CODEX_OAUTH_CLIENT_ID,
            "code_verifier": code_verifier,
        },
        timeout_seconds=timeout_seconds,
        error_code="token_exchange_error",
    )


def refresh_codex_tokens(refresh_token: str, *, timeout_seconds: float = 20.0) -> CodexOAuthTokens:
    if not refresh_token.strip():
        raise CodexAuthError("Codex auth is missing refresh_token.", code="codex_auth_missing_refresh_token", relogin_required=True)
    return _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CODEX_OAUTH_CLIENT_ID,
        },
        timeout_seconds=timeout_seconds,
        error_code="codex_refresh_failed",
    )


def login_codex_device_flow(*, console: Any | None = None, timeout_seconds: float = 15 * 60) -> CodexOAuthTokens:
    flow = start_codex_device_flow()
    _print(console, "To continue, follow these steps:\n")
    _print(console, "  1. Open this URL in your browser:")
    _print(console, f"     {flow.verification_uri}\n")
    _print(console, "  2. Enter this code:")
    _print(console, f"     {flow.user_code}\n")
    _print(console, "Waiting for sign-in... (press Ctrl+C to cancel)")

    started = time.monotonic()
    while time.monotonic() - started < timeout_seconds:
        time.sleep(flow.interval)
        result = poll_codex_device_flow(flow)
        if result is None:
            continue
        tokens = exchange_codex_authorization(result["authorization_code"], result["code_verifier"])
        save_codex_tokens(tokens, source="device-code")
        return tokens
    raise CodexAuthError("Codex login timed out.", code="device_code_timeout", relogin_required=True)


def resolve_codex_runtime_credentials(
    *,
    force_refresh: bool = False,
    refresh_if_expiring: bool = True,
    refresh_skew_seconds: int = CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
) -> CodexRuntimeCredentials:
    record = read_codex_tokens()
    if record is None:
        imported = import_codex_cli_tokens()
        if imported is not None:
            record = CodexAuthRecord(tokens=imported, source="codex-cli")
    if record is None:
        raise CodexAuthError(
            "OpenAI Codex is not authenticated. Run `clawcodex login` and select openai-codex.",
            code="codex_auth_missing",
            relogin_required=True,
        )

    tokens = record.tokens
    if force_refresh or (refresh_if_expiring and tokens.is_expiring(refresh_skew_seconds)):
        refreshed = refresh_codex_tokens(tokens.refresh_token)
        save_codex_tokens(refreshed, source=record.source)
        tokens = refreshed

    return CodexRuntimeCredentials(
        provider=CODEX_PROVIDER_ID,
        api_key=tokens.access_token,
        base_url=CODEX_BASE_URL,
        source=record.source,
        auth_mode=record.auth_mode,
        last_refresh=record.last_refresh,
    )


def get_codex_auth_status() -> CodexAuthStatus:
    record = read_codex_tokens()
    if record is None:
        return CodexAuthStatus(False, str(get_auth_file()), error="Not logged in")
    return CodexAuthStatus(
        True,
        str(get_auth_file()),
        source=record.source,
        expires_at=record.tokens.expires_at,
        last_refresh=record.last_refresh,
    )


def _token_request(data: dict[str, str], *, timeout_seconds: float, error_code: str) -> CodexOAuthTokens:
    try:
        response = httpx.post(
            CODEX_OAUTH_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            timeout=timeout_seconds,
        )
    except Exception as exc:
        raise CodexAuthError(f"Codex token request failed: {exc}", code=error_code) from exc
    if response.status_code != 200:
        message = f"Codex token request returned status {response.status_code}."
        relogin_required = response.status_code in {400, 401, 403}
        try:
            body = response.json()
            description = body.get("error_description") or body.get("message")
            error = body.get("error")
            if isinstance(error, dict):
                description = error.get("message") or description
                error = error.get("code") or error.get("type")
            if isinstance(description, str) and description:
                message = f"Codex token request failed: {description}"
            if isinstance(error, str) and error in {"invalid_grant", "invalid_token", "invalid_request"}:
                relogin_required = True
        except Exception:
            pass
        raise CodexAuthError(message, code=error_code, relogin_required=relogin_required)
    payload = _response_json(response, f"{error_code}_invalid_json")
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token") or data.get("refresh_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise CodexAuthError("Codex token response is missing access_token.", code=f"{error_code}_missing_access_token", relogin_required=True)
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise CodexAuthError("Codex token response is missing refresh_token.", code=f"{error_code}_missing_refresh_token", relogin_required=True)
    expires_at = None
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)):
        expires_at = time.time() + float(expires_in)
    return CodexOAuthTokens(
        access_token=access_token.strip(),
        refresh_token=refresh_token.strip(),
        expires_at=expires_at,
        token_type=str(payload.get("token_type") or "Bearer"),
        scope=payload.get("scope") if isinstance(payload.get("scope"), str) else None,
    )


def _response_json(response: httpx.Response, code: str) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception as exc:
        raise CodexAuthError("Codex OAuth response returned invalid JSON.", code=code) from exc
    if not isinstance(data, dict):
        raise CodexAuthError("Codex OAuth response returned invalid JSON shape.", code=code)
    return data


def _print(console: Any | None, message: str) -> None:
    if console is not None:
        console.print(message)
    else:
        print(message)
