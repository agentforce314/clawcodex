"""Tests for the desktop gateway server surface (``clawcodex serve``).

Covers the boot contract the Electron shell (``ui-desktop``) depends on:
ready-marker announce + ready file, REST auth (header/bearer), the
token-carrying index page, and WebSocket token rejection. The JSON-RPC
gateway protocol itself is covered in ``test_desktop_gateway.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.server.desktop_serve import DesktopServeState, build_app


TOKEN = "test-token-123"


def _state(tmp_path: Path) -> DesktopServeState:
    async def _spawn(*a, **kw):  # pragma: no cover - not reached in these tests
        raise AssertionError("spawn_agent must not run for REST surface tests")

    return DesktopServeState(
        token=TOKEN,
        workspace=str(tmp_path),
        manager=None,
        spawn_agent=_spawn,
        protocol_version="0.1.0",
    )


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(build_app(_state(tmp_path)))


def test_health_is_unauthenticated(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_status_requires_token(client: TestClient) -> None:
    assert client.get("/api/status").status_code == 401


def test_status_accepts_session_token_header(client: TestClient) -> None:
    res = client.get("/api/status", headers={"X-ClawCodex-Session-Token": TOKEN})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["auth_required"] is False
    assert body["protocol_version"] == "0.1.0"


def test_status_accepts_bearer(client: TestClient) -> None:
    res = client.get("/api/status", headers={"Authorization": f"Bearer {TOKEN}"})
    assert res.status_code == 200


def test_status_rejects_wrong_token(client: TestClient) -> None:
    res = client.get("/api/status", headers={"X-ClawCodex-Session-Token": "nope"})
    assert res.status_code == 401


def test_index_serves_adoptable_token(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "window.__CLAWCODEX_SESSION_TOKEN__" in res.text
    assert json.dumps(TOKEN) in res.text


def test_config_requires_token_and_redacts_secrets(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/api/config").status_code == 401

    fake = {
        "display": {"skin": "dark"},
        "env": {"TAVILY_API_KEY": "sk-hidden"},
        "providers": {"anthropic": {"api_key": "sk-secret", "default_model": "m"}},
        "mcp": [{"name": "s", "auth_token": "t-secret", "url": "http://x"}],
    }
    monkeypatch.setattr("src.config.load_config", lambda: fake)
    res = client.get("/api/config", headers={"X-ClawCodex-Session-Token": TOKEN})
    assert res.status_code == 200
    body = res.json()
    assert body["display"] == {"skin": "dark"}
    assert "env" not in body
    assert body["providers"]["anthropic"] == {"default_model": "m"}
    assert body["mcp"] == [{"name": "s", "url": "http://x"}]
    assert "secret" not in res.text and "sk-hidden" not in res.text


def test_ws_rejects_missing_or_wrong_token(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    for query in ("", "?token=wrong"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/ws{query}") as ws:
                ws.receive_json()


def test_announce_ready_prints_marker_and_writes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from src.entrypoints.serve_cli import READY_FILE_ENV, _announce_ready

    ready = tmp_path / "nested" / "ready.json"
    monkeypatch.setenv(READY_FILE_ENV, str(ready))
    _announce_ready(43210)

    out = capsys.readouterr().out
    assert "CLAWCODEX_BACKEND_READY port=43210" in out
    assert json.loads(ready.read_text()) == {"port": 43210}


def test_serve_cli_parser_defaults() -> None:
    from src.entrypoints.serve_cli import _build_parser

    args = _build_parser().parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 0
    assert args.permission_mode == "default"


def test_serve_cli_accepts_desktop_spawn_shape() -> None:
    # The Electron shell spawns exactly this arg shape (backend-command.ts).
    from src.entrypoints.serve_cli import _build_parser

    args = _build_parser().parse_args(
        ["--profile", "default", "--host", "127.0.0.1", "--port", "0"]
    )
    assert args.profile == "default"
    assert args.port == 0
