"""Tests for the sessions REST surface, resume hydration, and the desktop
launcher plan (stage 3 of the desktop port)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from src.server.desktop_serve import DesktopServeState, build_app

TOKEN = "sess-test-token"
AUTH = {"X-ClawCodex-Session-Token": TOKEN}


def _write_session(dir_: Path, session_id: str, *, preview: str, count: int,
                   messages: list | None = None, age_s: float = 0.0) -> None:
    data = {
        "session_id": session_id,
        "updated_at": "2026-08-08T00:00:00Z",
        "preview": preview,
        "name": None,
        "message_count": count,
        "model": "m1",
        "provider": "p1",
        "cwd": "/tmp/w",
        "mode": "default",
        "turns": 1,
        "conversation": {"max_history": 100, "messages": messages or []},
    }
    path = dir_ / f"{session_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    if age_s:
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))


def _state(tmp_path: Path) -> DesktopServeState:
    async def _spawn(*a, **kw):  # pragma: no cover
        raise AssertionError("no spawns in REST tests")

    return DesktopServeState(
        token=TOKEN,
        workspace=str(tmp_path),
        manager=None,
        spawn_agent=_spawn,
        protocol_version="0.1.0",
        sessions_dir=tmp_path / "sessions",
    )


@pytest.fixture()
def rest(tmp_path: Path) -> TestClient:
    (tmp_path / "sessions").mkdir()
    return TestClient(build_app(_state(tmp_path)))


def test_sessions_requires_token(rest: TestClient) -> None:
    assert rest.get("/api/sessions").status_code == 401


def test_sessions_lists_newest_first_with_pagination(
    rest: TestClient, tmp_path: Path
) -> None:
    d = tmp_path / "sessions"
    _write_session(d, "old-one", preview="first chat", count=4, age_s=300)
    _write_session(d, "mid-one", preview="second chat", count=6, age_s=100)
    _write_session(d, "new-one", preview="third chat", count=2, age_s=1)

    body = rest.get("/api/sessions", headers=AUTH).json()
    assert [r["id"] for r in body["sessions"]] == ["new-one", "mid-one", "old-one"]
    assert body["total"] == 3
    row = body["sessions"][0]
    assert row["preview"] == "third chat"
    assert row["title"] == "third chat"
    assert row["message_count"] == 2
    assert row["model"] == "m1"
    assert row["cwd"] == "/tmp/w"

    page = rest.get("/api/sessions?limit=1&offset=1", headers=AUTH).json()
    assert [r["id"] for r in page["sessions"]] == ["mid-one"]
    assert page["total"] == 3

    filtered = rest.get("/api/sessions?min_messages=5", headers=AUTH).json()
    assert [r["id"] for r in filtered["sessions"]] == ["mid-one"]


def test_sessions_skips_corrupt_files(rest: TestClient, tmp_path: Path) -> None:
    d = tmp_path / "sessions"
    _write_session(d, "good-one", preview="ok", count=1)
    (d / "broken.json").write_text("{not json", encoding="utf-8")

    body = rest.get("/api/sessions", headers=AUTH).json()
    assert [r["id"] for r in body["sessions"]] == ["good-one"]


def test_session_messages_hydrates_and_hides_reminders(
    rest: TestClient, tmp_path: Path
) -> None:
    d = tmp_path / "sessions"
    _write_session(
        d, "chat-a", preview="hello?", count=3,
        messages=[
            {"role": "user", "content": "hello?"},
            {"role": "assistant", "content": [{"type": "text", "text": "hi back"}]},
            {"role": "user", "content": "<system-reminder>\nnoise\n</system-reminder>"},
        ],
    )
    body = rest.get("/api/sessions/chat-a/messages", headers=AUTH).json()
    assert body["session_id"] == "chat-a"
    assert body["message_count"] == 3
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert body["messages"][1]["content"] == [{"type": "text", "text": "hi back"}]
    assert body["messages"][2]["display_kind"] == "hidden"
    assert "display_kind" not in body["messages"][0]


def test_session_messages_404_and_traversal_refused(
    rest: TestClient, tmp_path: Path
) -> None:
    assert rest.get("/api/sessions/nope/messages", headers=AUTH).status_code == 404
    res = rest.get("/api/sessions/..%2F..%2Fetc/messages", headers=AUTH)
    assert res.status_code == 404


def test_model_info_reports_defaults(
    rest: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.config.get_default_provider", lambda: "anthropic")
    monkeypatch.setattr(
        "src.config.get_provider_config",
        lambda n: {"default_model": "claude-fable-5"},
    )
    body = rest.get("/api/model/info", headers=AUTH).json()
    assert body == {"provider": "anthropic", "model": "claude-fable-5"}


def test_profile_sessions_slice_filters_sources(
    rest: TestClient, tmp_path: Path
) -> None:
    d = tmp_path / "sessions"
    _write_session(d, "chat-b", preview="normal", count=3, age_s=10)
    data = json.loads((d / "chat-b.json").read_text())
    data["mode"] = "cron"
    data["session_id"] = "cron-b"
    (d / "cron-b.json").write_text(json.dumps(data), encoding="utf-8")

    all_rows = rest.get("/api/profiles/sessions?limit=10", headers=AUTH).json()
    assert {r["id"] for r in all_rows["sessions"]} == {"chat-b", "cron-b"}
    assert all(r["profile"] == "default" for r in all_rows["sessions"])

    cron_only = rest.get(
        "/api/profiles/sessions?source=cron", headers=AUTH
    ).json()
    assert [r["id"] for r in cron_only["sessions"]] == ["cron-b"]

    excluded = rest.get(
        "/api/profiles/sessions?exclude_sources=cron,web", headers=AUTH
    ).json()
    assert [r["id"] for r in excluded["sessions"]] == ["chat-b"]


def test_sidebar_batches_slices(rest: TestClient, tmp_path: Path) -> None:
    d = tmp_path / "sessions"
    _write_session(d, "chat-c", preview="hey", count=2)
    body = rest.get(
        "/api/profiles/sessions/sidebar?recents_profile=default&recents_limit=5"
        "&cron_limit=3&messaging_limit=3",
        headers=AUTH,
    ).json()
    assert [r["id"] for r in body["recents"]["sessions"]] == ["chat-c"]
    assert body["cron"]["sessions"] == []
    assert body["messaging"]["sessions"] == []


def test_profiles_and_active_and_defaults(
    rest: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.config.get_default_provider", lambda: "anthropic")
    monkeypatch.setattr(
        "src.config.get_provider_config", lambda n: {"default_model": "m"}
    )
    monkeypatch.setattr(
        "src.config.load_config", lambda: {"env": {"K": "v"}}
    )
    body = rest.get("/api/profiles", headers=AUTH).json()
    assert len(body["profiles"]) == 1
    profile = body["profiles"][0]
    assert profile["name"] == "default"
    assert profile["is_default"] is True
    assert profile["provider"] == "anthropic"
    assert profile["has_env"] is True

    active = rest.get("/api/profiles/active", headers=AUTH).json()
    assert active == {"active": "default", "current": "default"}

    monkeypatch.setattr(
        "src.config.get_default_config",
        lambda: {"display": {"skin": "x"}, "env": {"S": "hide"}},
    )
    defaults = rest.get("/api/config/defaults", headers=AUTH).json()
    assert defaults == {"display": {"skin": "x"}}


# ─── desktop launcher plan ───────────────────────────────────────────────────


def test_desktop_launch_plan_installs_when_missing(tmp_path: Path) -> None:
    from src.entrypoints.desktop_cli import build_launch_plan

    plan = build_launch_plan(tmp_path, install=False, dev=True)
    assert plan == [["npm", "ci"], ["npm", "run", "dev"]]

    (tmp_path / "node_modules").mkdir()
    assert build_launch_plan(tmp_path, install=False, dev=True) == [["npm", "run", "dev"]]
    assert build_launch_plan(tmp_path, install=True, dev=False) == [
        ["npm", "ci"],
        ["npm", "run", "start"],
    ]


def test_desktop_launch_env_pins_backend_root(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from src.entrypoints.desktop_cli import launch_env

    monkeypatch.delenv("CLAWCODEX_DESKTOP_CLAWCODEX_ROOT", raising=False)
    env = launch_env(tmp_path)
    assert env["CLAWCODEX_DESKTOP_CLAWCODEX_ROOT"] == str(tmp_path)

    monkeypatch.setenv("CLAWCODEX_DESKTOP_CLAWCODEX_ROOT", "/explicit")
    assert launch_env(tmp_path)["CLAWCODEX_DESKTOP_CLAWCODEX_ROOT"] == "/explicit"


def test_desktop_dir_resolves_inside_repo() -> None:
    from src.entrypoints.desktop_cli import desktop_dir, repo_root

    root = repo_root()
    assert (root / "src" / "cli.py").is_file()
    assert desktop_dir(root) == root / "ui-desktop"


def test_desktop_subcommand_refuses_without_app(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    import src.entrypoints.desktop_cli as mod

    monkeypatch.setattr(mod, "repo_root", lambda: tmp_path)
    assert mod.run_desktop_subcommand([]) == 2
