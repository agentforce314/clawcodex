"""Tests for Phase-6 IME declared-cursor module."""

from __future__ import annotations

import pytest

from src.tui.declared_cursor import (
    CursorDeclaration,
    DeclaredCursor,
    flush_pending,
    get_default_declared_cursor,
    publish_cursor_position,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture
def cursor() -> DeclaredCursor:
    """Yield a fresh ``DeclaredCursor`` with a captured-byte writer."""

    c = DeclaredCursor()
    return c


def _capture_writer(buf: list[str]):
    def write(text: str) -> None:
        buf.append(text)

    return write


# ------------------------------------------------------------------
# Declarations
# ------------------------------------------------------------------


def test_declare_returns_unregister(cursor: DeclaredCursor) -> None:
    unreg = cursor.declare("owner", 0, 0)
    assert callable(unreg)
    assert cursor.active() is not None
    unreg()
    assert cursor.active() is None


def test_declare_negative_rejected(cursor: DeclaredCursor) -> None:
    with pytest.raises(ValueError):
        cursor.declare("owner", -1, 0)
    with pytest.raises(ValueError):
        cursor.declare("owner", 0, -1)


def test_most_recent_declaration_wins(cursor: DeclaredCursor) -> None:
    cursor.declare("a", 5, 5)
    cursor.declare("b", 10, 10)
    active = cursor.active()
    assert active is not None
    assert active.owner == "b"
    assert active.row == 10


def test_unregister_uncovers_previous(cursor: DeclaredCursor) -> None:
    cursor.declare("a", 5, 5)
    unreg_b = cursor.declare("b", 10, 10)
    unreg_b()
    active = cursor.active()
    assert active is not None
    assert active.owner == "a"


def test_unregister_idempotent(cursor: DeclaredCursor) -> None:
    unreg = cursor.declare("a", 1, 1)
    unreg()
    unreg()  # No-op; must not raise.


def test_re_declaring_same_owner_replaces_in_place(
    cursor: DeclaredCursor,
) -> None:
    cursor.declare("owner", 0, 0)
    cursor.declare("owner", 5, 5)
    active = cursor.active()
    assert active is not None
    assert active.row == 5
    assert active.col == 5


# ------------------------------------------------------------------
# Emit
# ------------------------------------------------------------------


def test_flush_emits_csi_sequence(
    cursor: DeclaredCursor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAWCODEX_DISABLE_DECLARED_CURSOR", raising=False)
    buf: list[str] = []
    cursor.set_writer(_capture_writer(buf))
    cursor.declare("owner", 4, 7)
    assert cursor.flush_pending() is True
    # CSI sequences are 1-indexed.
    assert buf == ["\x1b[5;8H"]


def test_flush_returns_false_when_no_pending(
    cursor: DeclaredCursor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAWCODEX_DISABLE_DECLARED_CURSOR", raising=False)
    buf: list[str] = []
    cursor.set_writer(_capture_writer(buf))
    assert cursor.flush_pending() is False
    assert buf == []


def test_flush_idempotent_until_next_declaration(
    cursor: DeclaredCursor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAWCODEX_DISABLE_DECLARED_CURSOR", raising=False)
    buf: list[str] = []
    cursor.set_writer(_capture_writer(buf))
    cursor.declare("o", 1, 1)
    assert cursor.flush_pending() is True
    assert cursor.flush_pending() is False  # second call: no-op


def test_unregister_re_arms_pending_emit(
    cursor: DeclaredCursor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unregistering should mark pending so the next flush re-emits the
    new active declaration."""

    monkeypatch.delenv("CLAWCODEX_DISABLE_DECLARED_CURSOR", raising=False)
    buf: list[str] = []
    cursor.set_writer(_capture_writer(buf))
    cursor.declare("a", 1, 1)
    unreg_b = cursor.declare("b", 5, 5)
    cursor.flush_pending()
    buf.clear()
    unreg_b()
    cursor.flush_pending()
    # After unregistering b, a is now active again — flush emits a's pos.
    assert buf == ["\x1b[2;2H"]


def test_disable_env_kills_emission(
    cursor: DeclaredCursor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAWCODEX_DISABLE_DECLARED_CURSOR", "1")
    buf: list[str] = []
    cursor.set_writer(_capture_writer(buf))
    cursor.declare("o", 1, 1)
    assert cursor.flush_pending() is False
    assert buf == []


def test_writer_exception_swallowed(
    cursor: DeclaredCursor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer that raises shouldn't crash the caller — non-interactive
    harnesses rely on this."""

    monkeypatch.delenv("CLAWCODEX_DISABLE_DECLARED_CURSOR", raising=False)

    def boom(_text: str) -> None:
        raise OSError("simulated stdout failure")

    cursor.set_writer(boom)
    cursor.declare("o", 1, 1)
    # Returns False because the write failed; doesn't propagate.
    assert cursor.flush_pending() is False


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------


def test_singleton_persists_across_calls() -> None:
    a = get_default_declared_cursor()
    b = get_default_declared_cursor()
    assert a is b


def test_publish_cursor_position_uses_singleton() -> None:
    publish_cursor_position("o", 2, 3)
    active = get_default_declared_cursor().active()
    assert active is not None
    assert active.row == 2
    assert active.col == 3


def test_flush_pending_helper_uses_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAWCODEX_DISABLE_DECLARED_CURSOR", raising=False)
    buf: list[str] = []
    get_default_declared_cursor().set_writer(_capture_writer(buf))
    publish_cursor_position("o", 0, 0)
    assert flush_pending() is True
    assert buf == ["\x1b[1;1H"]


def test_clear_removes_all() -> None:
    cursor = get_default_declared_cursor()
    cursor.declare("a", 1, 1)
    cursor.declare("b", 2, 2)
    cursor.clear()
    assert cursor.active() is None
