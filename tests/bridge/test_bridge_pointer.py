"""Tests for ``src.bridge.bridge_pointer``.

Covers schema validation, round-trip, host/dir staleness checks, and
the atomic-write contract.
"""

from __future__ import annotations

import json
import os

import pytest

from src.bridge.bridge_pointer import (
    BridgePointer,
    clear_pointer,
    read_pointer,
    write_pointer,
)


# ── round-trip ──────────────────────────────────────────────────────────


def test_write_then_read_round_trip(tmp_path) -> None:
    """A freshly-written pointer reads back with the same fields."""
    working_dir = str(tmp_path)
    write_pointer(
        working_dir,
        bridge_id='br-1',
        environment_id='env-srv-1',
        session_id='cse_a',
        machine_name='host-1',
    )
    p = read_pointer(working_dir, machine_name='host-1')
    assert p is not None
    assert p.bridge_id == 'br-1'
    assert p.environment_id == 'env-srv-1'
    assert p.session_id == 'cse_a'
    assert p.machine_name == 'host-1'
    assert os.path.abspath(p.dir) == os.path.abspath(working_dir)
    assert p.created_at_ms > 0
    assert p.updated_at_ms >= p.created_at_ms


def test_session_id_none_is_serialized_and_parsed(tmp_path) -> None:
    """``session_id=None`` survives the round-trip — represents a
    bridge that's registered but hasn't received its first work yet."""
    write_pointer(
        str(tmp_path),
        bridge_id='br-1',
        environment_id='env-srv-1',
        session_id=None,
        machine_name='host-1',
    )
    p = read_pointer(str(tmp_path), machine_name='host-1')
    assert p is not None
    assert p.session_id is None


def test_write_preserves_created_at(tmp_path) -> None:
    """When ``created_at_ms`` is passed explicitly, it overrides the
    default (current time) — used to preserve the original timestamp
    across recreations within a perpetual run."""
    write_pointer(
        str(tmp_path),
        bridge_id='br-1',
        environment_id='env-srv-1',
        session_id='cse_a',
        machine_name='host-1',
        created_at_ms=1_000_000,
    )
    p = read_pointer(str(tmp_path), machine_name='host-1')
    assert p is not None
    assert p.created_at_ms == 1_000_000
    assert p.updated_at_ms >= 1_000_000


# ── absent / corrupt files ─────────────────────────────────────────────


def test_read_missing_file_returns_none(tmp_path) -> None:
    assert read_pointer(str(tmp_path), machine_name='host-1') is None


def test_read_malformed_json_returns_none(tmp_path) -> None:
    """A pointer file with non-JSON content reads as absent — never
    raises into the daemon's startup path."""
    path = os.path.join(str(tmp_path), '.clawcodex', 'bridge-pointer.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('not json at all { ')
    assert read_pointer(str(tmp_path), machine_name='host-1') is None


@pytest.mark.parametrize('missing_field', [
    'bridge_id', 'environment_id', 'machine_name',
    'dir', 'created_at_ms', 'updated_at_ms',
])
def test_read_missing_required_field_returns_none(
    tmp_path, missing_field: str,
) -> None:
    """A pointer that's missing any required field is treated as absent."""
    base = {
        'schema_version': 1,
        'bridge_id': 'br-1',
        'environment_id': 'env-srv-1',
        'session_id': 'cse_a',
        'machine_name': 'host-1',
        'dir': str(tmp_path),
        'created_at_ms': 1000,
        'updated_at_ms': 2000,
    }
    del base[missing_field]
    path = os.path.join(str(tmp_path), '.clawcodex', 'bridge-pointer.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(base, fh)
    assert read_pointer(str(tmp_path), machine_name='host-1') is None


def test_read_wrong_schema_version_returns_none(tmp_path) -> None:
    """Future schema versions can't be parsed by older code; safer to
    treat them as absent than to misinterpret."""
    path = os.path.join(str(tmp_path), '.clawcodex', 'bridge-pointer.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump({
            'schema_version': 999,
            'bridge_id': 'br-1',
            'environment_id': 'env-srv-1',
            'session_id': None,
            'machine_name': 'host-1',
            'dir': str(tmp_path),
            'created_at_ms': 1000,
            'updated_at_ms': 2000,
        }, fh)
    assert read_pointer(str(tmp_path), machine_name='host-1') is None


# ── staleness ──────────────────────────────────────────────────────────


def test_read_rejects_mismatched_machine_name(tmp_path) -> None:
    """A pointer written by host A must NOT be readable from host B —
    even if the file path is the same (could happen on a shared
    NFS HOME). Prevents cross-host state corruption."""
    write_pointer(
        str(tmp_path),
        bridge_id='br-1',
        environment_id='env-srv-1',
        session_id='cse_a',
        machine_name='host-A',
    )
    assert read_pointer(str(tmp_path), machine_name='host-B') is None
    # Original host still reads it fine.
    assert read_pointer(str(tmp_path), machine_name='host-A') is not None


def test_read_rejects_mismatched_dir(tmp_path) -> None:
    """A pointer file moved (via symlink or rename) to a different dir
    is rejected — the ``dir`` field captures the absolute path at
    write time and read_pointer verifies it matches."""
    # Write under tmp_path, then physically move the .clawcodex/ dir to
    # a sibling and try to read from there.
    write_pointer(
        str(tmp_path),
        bridge_id='br-1',
        environment_id='env-srv-1',
        session_id='cse_a',
        machine_name='host-1',
    )
    sibling = tmp_path.parent / 'sibling'
    sibling.mkdir()
    os.rename(
        os.path.join(str(tmp_path), '.clawcodex'),
        os.path.join(str(sibling), '.clawcodex'),
    )
    assert read_pointer(str(sibling), machine_name='host-1') is None


# ── clear ──────────────────────────────────────────────────────────────


def test_clear_removes_the_file(tmp_path) -> None:
    write_pointer(
        str(tmp_path),
        bridge_id='br-1', environment_id='env-srv-1',
        session_id=None, machine_name='host-1',
    )
    assert read_pointer(str(tmp_path), machine_name='host-1') is not None
    clear_pointer(str(tmp_path))
    assert read_pointer(str(tmp_path), machine_name='host-1') is None


def test_clear_missing_file_is_noop(tmp_path) -> None:
    """Idempotent — clearing when no pointer exists must not raise."""
    clear_pointer(str(tmp_path))  # no exception
    assert read_pointer(str(tmp_path), machine_name='host-1') is None


# ── atomic write ───────────────────────────────────────────────────────


def test_write_replaces_atomically(tmp_path) -> None:
    """Successive writes leave a valid pointer (no half-written file)."""
    for i in range(5):
        write_pointer(
            str(tmp_path),
            bridge_id='br-1',
            environment_id=f'env-srv-{i}',
            session_id=f'cse_{i}',
            machine_name='host-1',
        )
        p = read_pointer(str(tmp_path), machine_name='host-1')
        assert p is not None
        assert p.environment_id == f'env-srv-{i}'
        assert p.session_id == f'cse_{i}'


def test_write_creates_claude_subdir_if_missing(tmp_path) -> None:
    """If ``<dir>/.clawcodex/`` doesn't exist yet, write_pointer creates
    it — operators shouldn't have to pre-provision the directory."""
    working_dir = str(tmp_path / 'fresh')
    os.makedirs(working_dir)
    assert not os.path.exists(os.path.join(working_dir, '.clawcodex'))
    write_pointer(
        working_dir,
        bridge_id='br-1', environment_id='env-srv-1',
        session_id=None, machine_name='host-1',
    )
    assert os.path.exists(
        os.path.join(working_dir, '.clawcodex', 'bridge-pointer.json')
    )
