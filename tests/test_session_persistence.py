"""Tests for the session-persistence producer (SessionPersister + agent_bridge wiring).

The bar (from the /rename review): a session produced by the persister must be
genuinely resumable — ``resume_session`` round-trips it, ``list_sessions`` shows it
with the right ``message_count`` — and persistence must NEVER break the live session.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import src.services.session_storage as ss
from src.services.session_persistence import SessionPersister
from src.services.session_resume import resume_session
from src.services.session_storage import SessionStorage


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    monkeypatch.setattr(ss, "SESSIONS_DIR", d)
    return d


# --------------------------------------------------------------------------- #
# A. Persister unit
# --------------------------------------------------------------------------- #
def test_start_inits_metadata_once_and_preserves_title(sessions_dir):
    p = SessionPersister("sid-1", sessions_dir=sessions_dir)
    p.start(model="m1", cwd="/w")
    meta = SessionStorage(session_id="sid-1", sessions_dir=sessions_dir).get_metadata()
    assert meta is not None and meta.model == "m1" and meta.cwd == "/w"

    # A /rename-style title set between runs must survive a second start().
    SessionStorage(session_id="sid-1", sessions_dir=sessions_dir).update_metadata(
        title="kept-title"
    )
    p2 = SessionPersister("sid-1", sessions_dir=sessions_dir)
    p2.start(model="m2", cwd="/other")
    meta = SessionStorage(session_id="sid-1", sessions_dir=sessions_dir).get_metadata()
    assert meta.title == "kept-title"
    assert meta.model == "m1"  # first-run value kept (documented)


def test_record_and_flush_write_transcript_and_count(sessions_dir):
    p = SessionPersister("sid-2", sessions_dir=sessions_dir)
    p.start(model="m", cwd="/w")
    p.record_user("hello")
    p.record({"role": "assistant", "content": "world"})
    p.flush()

    storage = SessionStorage(session_id="sid-2", sessions_dir=sessions_dir)
    entries = storage.read_transcript()
    assert [e["role"] for e in entries] == ["user", "assistant"]
    assert entries[0]["content"] == "hello"
    assert storage.get_metadata().message_count == 2


def test_persister_never_raises(sessions_dir, caplog):
    p = SessionPersister("sid-3", sessions_dir=sessions_dir)
    p.start(model="m", cwd="/w")
    # Make every storage op explode — persister must swallow with ONE warning.
    p._storage.write_message = MagicMock(side_effect=OSError("disk gone"))
    p._storage.flush = MagicMock(side_effect=OSError("disk gone"))
    with caplog.at_level("WARNING"):
        p.record_user("x")
        p.record_user("y")
        p.flush()
    warnings = [r for r in caplog.records if "persistence disabled" in r.message]
    assert len(warnings) == 1  # latched


def test_persister_survives_ctor_failure(monkeypatch, caplog):
    monkeypatch.setattr(
        "src.services.session_storage.SessionStorage",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    with caplog.at_level("WARNING"):
        p = SessionPersister("sid-4")
        p.start(model="m", cwd="/w")  # no-ops silently
        p.record_user("x")
        p.flush()
    assert p._storage is None


# --------------------------------------------------------------------------- #
# B. Round-trip (the bar)
# --------------------------------------------------------------------------- #
def test_round_trip_resume_and_list(sessions_dir):
    p = SessionPersister("sid-rt", sessions_dir=sessions_dir)
    p.start(model="m", cwd="/w")
    p.record_user("q1")
    p.record({"role": "assistant", "content": "a1"})
    p.record_user("q2")
    p.flush()

    result = resume_session("sid-rt", sessions_dir=sessions_dir)
    assert result.success is True
    assert len(result.messages) == 3
    assert result.metadata is not None

    metas = SessionStorage.list_sessions(sessions_dir=sessions_dir)
    ids = {m.session_id: m for m in metas}
    assert "sid-rt" in ids
    assert ids["sid-rt"].message_count == 3


# --------------------------------------------------------------------------- #
# C. agent_bridge wiring (behavioral, via the esc-cancel harness pattern)
# --------------------------------------------------------------------------- #
def test_bridge_records_user_prompt_and_flushes(sessions_dir, tmp_path):
    from src.agent.session import Session
    from src.tool_system.context import ToolContext
    from src.tool_system.registry import ToolRegistry
    from src.tui.agent_bridge import AgentBridge
    from src.tui.state import AppState

    def _post(_msg: Any) -> None:
        return None

    def _no_run_worker(*_args: Any, **_kwargs: Any) -> None:
        return None

    session = Session.create("test", "test-model")
    bridge = AgentBridge(
        post_message=_post,
        session=session,
        provider=MagicMock(model="test-model"),
        tool_registry=ToolRegistry(),
        tool_context=ToolContext(workspace_root=tmp_path),
        app_state=AppState(),
        run_worker=_no_run_worker,
    )

    # Metadata initialized at construction.
    storage = SessionStorage(session_id=session.session_id, sessions_dir=sessions_dir)
    meta = storage.get_metadata()
    assert meta is not None and meta.model == "test-model"

    assert bridge.submit("persist me") is True
    bridge._finish()  # the durable flush point (runs on every terminal path)

    entries = SessionStorage(
        session_id=session.session_id, sessions_dir=sessions_dir
    ).read_transcript()
    assert any(e["role"] == "user" and e["content"] == "persist me" for e in entries)


def test_session_id_unified_with_bootstrap(sessions_dir):
    # Pin 2 (plan-critic): Session.create reads the bootstrap session id, so the
    # producer's directory id equals get_session_id() — a re-landed /rename
    # (which uses get_session_id()) provably targets the SAME session dir.
    from src.agent.session import Session
    from src.bootstrap.state import get_session_id

    session = Session.create("test", "test-model")
    assert str(session.session_id) == str(get_session_id())
