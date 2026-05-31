"""Token storage for OpenAI Codex ChatGPT OAuth."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CODEX_PROVIDER_ID = "openai-codex"
AUTH_FILE = Path.home() / ".clawcodex" / "auth.json"
CODEX_CLI_AUTH_FILE = Path.home() / ".codex" / "auth.json"


@dataclass
class CodexOAuthTokens:
    access_token: str
    refresh_token: str
    expires_at: float | None = None
    token_type: str = "Bearer"
    scope: str | None = None

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= time.time()

    def is_expiring(self, skew_seconds: int = 120) -> bool:
        return self.expires_at is not None and self.expires_at <= time.time() + skew_seconds


@dataclass
class CodexAuthRecord:
    tokens: CodexOAuthTokens
    auth_mode: str = "chatgpt"
    last_refresh: float | None = None
    source: str = "clawcodex"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_auth_file() -> Path:
    return AUTH_FILE


def read_codex_tokens(path: Path | None = None) -> CodexAuthRecord | None:
    auth_path = path or AUTH_FILE
    state = _read_json(auth_path).get("providers", {}).get(CODEX_PROVIDER_ID)
    if not isinstance(state, dict):
        return None
    tokens = _tokens_from_mapping(state.get("tokens"))
    if tokens is None:
        return None
    return CodexAuthRecord(
        tokens=tokens,
        auth_mode=str(state.get("auth_mode") or "chatgpt"),
        last_refresh=_optional_float(state.get("last_refresh")),
        source=str(state.get("source") or "clawcodex"),
    )


def save_codex_tokens(
    tokens: CodexOAuthTokens | dict[str, Any],
    *,
    path: Path | None = None,
    source: str = "clawcodex",
) -> None:
    auth_path = path or AUTH_FILE
    token_obj = tokens if isinstance(tokens, CodexOAuthTokens) else _tokens_from_mapping(tokens)
    if token_obj is None:
        raise ValueError("Codex tokens must include access_token and refresh_token")

    data = _read_json(auth_path)
    providers = data.setdefault("providers", {})
    providers[CODEX_PROVIDER_ID] = {
        "auth_mode": "chatgpt",
        "tokens": {k: v for k, v in asdict(token_obj).items() if v is not None},
        "last_refresh": time.time(),
        "source": source,
    }
    _atomic_write_json(auth_path, data)


def delete_codex_tokens(path: Path | None = None) -> None:
    auth_path = path or AUTH_FILE
    data = _read_json(auth_path)
    providers = data.get("providers")
    if isinstance(providers, dict):
        providers.pop(CODEX_PROVIDER_ID, None)
    _atomic_write_json(auth_path, data)


def import_codex_cli_tokens(
    *,
    source_path: Path | None = None,
    destination_path: Path | None = None,
) -> CodexOAuthTokens | None:
    tokens = _tokens_from_mapping(_read_json(source_path or CODEX_CLI_AUTH_FILE).get("tokens"))
    if tokens is None or tokens.is_expired:
        return None
    save_codex_tokens(tokens, path=destination_path, source="codex-cli")
    return tokens


def _tokens_from_mapping(value: Any) -> CodexOAuthTokens | None:
    if not isinstance(value, dict):
        return None
    access_token = value.get("access_token")
    refresh_token = value.get("refresh_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return None
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        return None
    token_type = value.get("token_type")
    scope = value.get("scope")
    return CodexOAuthTokens(
        access_token=access_token.strip(),
        refresh_token=refresh_token.strip(),
        expires_at=_optional_float(value.get("expires_at")),
        token_type=token_type if isinstance(token_type, str) and token_type else "Bearer",
        scope=scope if isinstance(scope, str) else None,
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
