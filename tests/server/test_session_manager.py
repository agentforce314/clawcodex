"""Tests for ``src.server.session_manager.SessionManager``."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.server.session_manager import SessionManager
from src.server.types import SessionState


def _make_manager(tmp_path, **overrides):
    return SessionManager(
        workspace=str(tmp_path),
        index_path=tmp_path / 'idx.json',
        **overrides,
    )


def test_create_session_assigns_id_and_records_starting(tmp_path):
    mgr = _make_manager(tmp_path)
    info = mgr.create_session(cwd='/tmp/work')
    assert info.id.startswith('ds_')
    assert info.status == SessionState.STARTING
    assert info.work_dir == '/tmp/work'
    # Persisted to the index.
    from src.server.session_index import load_index
    idx = load_index(tmp_path / 'idx.json')
    assert info.id in idx


def test_create_session_default_cwd(tmp_path):
    mgr = _make_manager(tmp_path)
    info = mgr.create_session()
    assert info.work_dir == str(tmp_path)


def test_max_sessions_enforced(tmp_path):
    mgr = _make_manager(tmp_path, max_sessions=2)
    mgr.create_session()
    mgr.create_session()
    with pytest.raises(RuntimeError, match='max_sessions'):
        mgr.create_session()


def test_mark_running_updates_state(tmp_path):
    mgr = _make_manager(tmp_path)
    info = mgr.create_session()
    mgr.mark_running(info.id)
    assert mgr.get(info.id).status == SessionState.RUNNING


def test_mark_detached_updates_state(tmp_path):
    mgr = _make_manager(tmp_path)
    info = mgr.create_session()
    mgr.mark_detached(info.id)
    assert mgr.get(info.id).status == SessionState.DETACHED


@pytest.mark.asyncio
async def test_stop_session_removes_from_index(tmp_path):
    mgr = _make_manager(tmp_path)
    info = mgr.create_session()
    await mgr.stop_session(info.id)
    assert mgr.get(info.id) is None
    from src.server.session_index import load_index
    assert info.id not in load_index(tmp_path / 'idx.json')


@pytest.mark.asyncio
async def test_stop_unknown_session_is_noop(tmp_path):
    mgr = _make_manager(tmp_path)
    await mgr.stop_session('does-not-exist')


def test_active_session_ids_filters_stopped(tmp_path):
    mgr = _make_manager(tmp_path)
    a = mgr.create_session()
    b = mgr.create_session()
    mgr.mark_running(a.id)
    assert set(mgr.active_session_ids()) == {a.id, b.id}


@pytest.mark.asyncio
async def test_reap_idle_detached_zero_timeout_no_op(tmp_path):
    mgr = _make_manager(tmp_path, idle_timeout_ms=0)
    info = mgr.create_session()
    mgr.mark_detached(info.id)
    stopped = await mgr.reap_idle_detached()
    assert stopped == []
    assert mgr.get(info.id) is not None


@pytest.mark.asyncio
async def test_reap_idle_detached_stops_old_detached(tmp_path):
    """Reaper uses last_active_at, not created_at — push it into the past."""
    mgr = _make_manager(tmp_path, idle_timeout_ms=500)
    info = mgr.create_session()
    mgr.mark_detached(info.id)
    mgr.get(info.id).last_active_at = time.time() - 10
    stopped = await mgr.reap_idle_detached()
    assert stopped == [info.id]
    assert mgr.get(info.id) is None


@pytest.mark.asyncio
async def test_reap_idle_detached_skips_running(tmp_path):
    """RUNNING sessions are NOT reaped — only DETACHED."""
    mgr = _make_manager(tmp_path, idle_timeout_ms=500)
    info = mgr.create_session()
    mgr.mark_running(info.id)
    mgr.get(info.id).last_active_at = time.time() - 10
    stopped = await mgr.reap_idle_detached()
    assert stopped == []
    assert mgr.get(info.id) is not None


@pytest.mark.asyncio
async def test_reap_idle_detached_uses_last_active_not_created_at(tmp_path):
    """Long-lived session that was active recently must NOT be reaped."""
    mgr = _make_manager(tmp_path, idle_timeout_ms=500)
    info = mgr.create_session()
    mgr.mark_detached(info.id)
    # Created hours ago (would be reaped under the old buggy logic) but
    # active just now — must NOT be reaped.
    mgr.get(info.id).created_at = time.time() - 3600
    mgr.get(info.id).last_active_at = time.time()
    stopped = await mgr.reap_idle_detached()
    assert stopped == []
    assert mgr.get(info.id) is not None


def test_touch_bumps_last_active_at(tmp_path):
    """SessionManager.touch updates last_active_at to now."""
    mgr = _make_manager(tmp_path)
    info = mgr.create_session()
    info.last_active_at = time.time() - 1000
    mgr.touch(info.id)
    assert mgr.get(info.id).last_active_at > time.time() - 1


def test_touch_unknown_session_is_noop(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.touch('not-a-session')  # should not raise
