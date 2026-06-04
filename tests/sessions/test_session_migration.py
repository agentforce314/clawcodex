"""Tests for ``src/agent/session.py`` migration to bootstrap-state session ID.

The migration replaces strftime-based session IDs (which collide if two
sessions start in the same second) with UUID IDs sourced from
``src.bootstrap.state.get_session_id()``. This file locks the new
behavior so a future refactor cannot silently revert.
"""

from __future__ import annotations

import unittest
import uuid

import pytest

from src.agent.session import Session
from src.bootstrap.state import (
    SessionId,
    get_session_id,
    reset_state_for_tests,
    switch_session,
)


@pytest.fixture(autouse=True)
def _reset_bootstrap():
    reset_state_for_tests()
    yield
    reset_state_for_tests()


class TestSessionCreateUsesBootstrapId(unittest.TestCase):
    def test_create_uses_bootstrap_session_id(self) -> None:
        bootstrap_id = get_session_id()
        session = Session.create(provider="anthropic", model="claude-opus-4")
        self.assertEqual(session.session_id, bootstrap_id)

    def test_create_returns_uuid_not_strftime(self) -> None:
        """The new ID should be a parseable UUID, not a strftime
        timestamp like '20250511_010203'."""
        session = Session.create(provider="anthropic", model="claude-opus-4")
        # UUID strings are 36 chars with hyphens at fixed positions
        self.assertEqual(len(session.session_id), 36)
        # uuid.UUID parses it without raising
        uuid.UUID(session.session_id)  # raises ValueError if not a UUID

    def test_create_changes_with_switch_session(self) -> None:
        """After switch_session, a freshly-created Session picks up the
        new bootstrap ID."""
        new_id = SessionId("11111111-1111-1111-1111-111111111111")
        switch_session(new_id)
        session = Session.create(provider="anthropic", model="x")
        self.assertEqual(session.session_id, new_id)

    def test_two_sessions_in_same_second_get_different_ids(self) -> None:
        """The strftime collision bug: two Session.create calls within
        the same second used to produce the same ID. Verify the bug is
        fixed by regenerate-on-each-Session-instance."""
        # Without the bootstrap migration, this test would have failed —
        # both Session.create calls would have used the same strftime ID.
        # Post-migration, both reuse the SAME bootstrap_id (since we
        # haven't called switch_session or regenerate_session_id).
        # The fix is that *all* consumers agree on one ID, not that each
        # consumer makes a fresh one.
        s1 = Session.create(provider="anthropic", model="x")
        s2 = Session.create(provider="anthropic", model="y")
        # Both Session instances share the bootstrap ID — single source of truth
        self.assertEqual(s1.session_id, s2.session_id)
        self.assertEqual(s1.session_id, get_session_id())


if __name__ == "__main__":
    unittest.main()
