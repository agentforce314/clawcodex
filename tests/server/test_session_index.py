"""Tests for ``src.server.session_index``."""

from __future__ import annotations

import json
import time

import pytest

from src.server.session_index import (
    add_entry,
    load_index,
    remove_entry,
    save_index,
    update_last_active,
)
from src.server.types import SessionIndexEntry


def _entry(sid: str = 's1', t: float = 1000.0) -> SessionIndexEntry:
    return SessionIndexEntry(
        session_id=sid,
        transcript_session_id=f'tx_{sid}',
        cwd='/tmp/work',
        created_at=t,
        last_active_at=t,
    )


def test_load_missing_file_returns_empty(tmp_path):
    assert load_index(tmp_path / 'nope.json') == {}


def test_load_invalid_json_returns_empty(tmp_path):
    p = tmp_path / 'idx.json'
    p.write_text('not json {{{')
    assert load_index(p) == {}


def test_load_non_object_root_returns_empty(tmp_path):
    p = tmp_path / 'idx.json'
    p.write_text('[1, 2, 3]')
    assert load_index(p) == {}


def test_load_skips_malformed_entries(tmp_path):
    p = tmp_path / 'idx.json'
    p.write_text(json.dumps({
        'good': {
            'session_id': 'good',
            'transcript_session_id': 'tx',
            'cwd': '/tmp',
            'created_at': 1.0,
            'last_active_at': 2.0,
        },
        'bad': {'session_id': 'bad'},  # missing fields
        'wrong_type': 'not a dict',
    }))
    idx = load_index(p)
    assert 'good' in idx
    assert 'bad' not in idx
    assert 'wrong_type' not in idx


def test_save_and_load_round_trip(tmp_path):
    p = tmp_path / 'idx.json'
    save_index({'s1': _entry('s1')}, p)
    loaded = load_index(p)
    assert loaded['s1'].cwd == '/tmp/work'
    assert loaded['s1'].transcript_session_id == 'tx_s1'


def test_add_entry_creates_file(tmp_path):
    p = tmp_path / 'idx.json'
    add_entry(_entry('s1'), p)
    assert load_index(p)['s1'].session_id == 's1'


def test_add_entry_replaces_existing(tmp_path):
    p = tmp_path / 'idx.json'
    add_entry(_entry('s1', t=100), p)
    add_entry(_entry('s1', t=200), p)
    idx = load_index(p)
    assert idx['s1'].created_at == 200


def test_remove_entry(tmp_path):
    p = tmp_path / 'idx.json'
    add_entry(_entry('s1'), p)
    add_entry(_entry('s2'), p)
    remove_entry('s1', p)
    idx = load_index(p)
    assert 's1' not in idx
    assert 's2' in idx


def test_remove_entry_missing_is_noop(tmp_path):
    p = tmp_path / 'idx.json'
    add_entry(_entry('s1'), p)
    remove_entry('nope', p)  # should not raise
    assert 's1' in load_index(p)


def test_update_last_active(tmp_path):
    p = tmp_path / 'idx.json'
    add_entry(_entry('s1', t=100), p)
    update_last_active('s1', 5000.0, p)
    idx = load_index(p)
    assert idx['s1'].last_active_at == 5000.0
    # created_at preserved.
    assert idx['s1'].created_at == 100.0


def test_update_last_active_missing_session_is_noop(tmp_path):
    p = tmp_path / 'idx.json'
    add_entry(_entry('s1'), p)
    update_last_active('nope', 9999.0, p)  # no-op
    assert load_index(p)['s1'].last_active_at == 1000.0


def test_atomic_write_no_leftover_tempfile_on_failure(tmp_path, monkeypatch):
    """If os.replace fails mid-write, the .tmp file should not survive."""
    import os

    p = tmp_path / 'idx.json'
    add_entry(_entry('s1'), p)

    def fail_replace(*args, **kwargs):
        raise OSError('simulated rename failure')

    monkeypatch.setattr(os, 'replace', fail_replace)

    with pytest.raises(OSError):
        save_index({'s1': _entry('s1', t=999.0)}, p)

    leftover = list(tmp_path.glob('.server-sessions.*.tmp'))
    assert leftover == [], f'expected no leftover tempfile, got {leftover}'
