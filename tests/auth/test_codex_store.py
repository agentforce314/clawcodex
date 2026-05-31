from __future__ import annotations

import json
import os
import time
from pathlib import Path

from src.auth.codex_store import (
    CODEX_PROVIDER_ID,
    CodexOAuthTokens,
    delete_codex_tokens,
    import_codex_cli_tokens,
    read_codex_tokens,
    save_codex_tokens,
)


def test_save_and_read_codex_tokens(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    tokens = CodexOAuthTokens(
        access_token="access",
        refresh_token="refresh",
        expires_at=time.time() + 3600,
        scope="codex",
    )

    save_codex_tokens(tokens, path=auth_file, source="test")
    record = read_codex_tokens(auth_file)

    assert record is not None
    assert record.tokens.access_token == "access"
    assert record.tokens.refresh_token == "refresh"
    assert record.source == "test"
    assert record.auth_mode == "chatgpt"


def test_save_uses_provider_scoped_shape(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"

    save_codex_tokens(
        {"access_token": "access", "refresh_token": "refresh"},
        path=auth_file,
    )

    data = json.loads(auth_file.read_text(encoding="utf-8"))
    assert data["providers"][CODEX_PROVIDER_ID]["tokens"]["access_token"] == "access"
    assert data["providers"][CODEX_PROVIDER_ID]["auth_mode"] == "chatgpt"


def test_delete_codex_tokens_preserves_other_providers(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "providers": {
                    CODEX_PROVIDER_ID: {"tokens": {"access_token": "a", "refresh_token": "r"}},
                    "other": {"value": True},
                }
            }
        ),
        encoding="utf-8",
    )

    delete_codex_tokens(auth_file)

    data = json.loads(auth_file.read_text(encoding="utf-8"))
    assert CODEX_PROVIDER_ID not in data["providers"]
    assert data["providers"]["other"] == {"value": True}


def test_import_codex_cli_tokens(tmp_path: Path) -> None:
    source = tmp_path / "codex-auth.json"
    destination = tmp_path / "claw-auth.json"
    source.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "cli-access",
                    "refresh_token": "cli-refresh",
                    "expires_at": time.time() + 3600,
                }
            }
        ),
        encoding="utf-8",
    )

    tokens = import_codex_cli_tokens(source_path=source, destination_path=destination)
    record = read_codex_tokens(destination)

    assert tokens is not None
    assert record is not None
    assert record.tokens.access_token == "cli-access"
    assert record.source == "codex-cli"


def test_import_codex_cli_tokens_ignores_expired_tokens(tmp_path: Path) -> None:
    source = tmp_path / "codex-auth.json"
    destination = tmp_path / "claw-auth.json"
    source.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "cli-access",
                    "refresh_token": "cli-refresh",
                    "expires_at": time.time() - 1,
                }
            }
        ),
        encoding="utf-8",
    )

    assert import_codex_cli_tokens(source_path=source, destination_path=destination) is None
    assert not destination.exists()


def test_auth_file_permissions_are_restricted_on_posix(tmp_path: Path) -> None:
    if os.name == "nt":
        return
    auth_file = tmp_path / "auth.json"

    save_codex_tokens({"access_token": "a", "refresh_token": "r"}, path=auth_file)

    assert auth_file.stat().st_mode & 0o777 == 0o600
