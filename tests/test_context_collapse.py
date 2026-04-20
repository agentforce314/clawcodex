"""
Tests for Layer 4: Context Collapse.
"""

from __future__ import annotations

import unittest

from src.types.content_blocks import TextBlock
from src.types.messages import Message, UserMessage, AssistantMessage
from src.services.compact.context_collapse import (
    CollapseCommit,
    ContextCollapseStore,
    is_context_collapse_enabled,
    get_context_collapse_state,
    set_context_collapse_store,
)


def _msg(role: str, text: str, uuid: str = "") -> Message:
    if role == "user":
        return UserMessage(content=text, uuid=uuid)
    return AssistantMessage(content=[TextBlock(text=text)], uuid=uuid)


class TestCollapseCommit(unittest.TestCase):
    """Tests for CollapseCommit serialization."""

    def test_round_trip(self):
        c = CollapseCommit(archived=["uuid1", "uuid2"], summary="Summary text")
        d = c.to_dict()
        c2 = CollapseCommit.from_dict(d)
        self.assertEqual(c2.archived, ["uuid1", "uuid2"])
        self.assertEqual(c2.summary, "Summary text")


class TestContextCollapseStore(unittest.TestCase):
    """Tests for ContextCollapseStore."""

    def test_empty_store_returns_unchanged(self):
        store = ContextCollapseStore()
        messages = [_msg("user", "Hello", "u1"), _msg("assistant", "Hi", "a1")]
        result = store.project_view(messages)
        self.assertEqual(len(result), 2)

    def test_project_view_replaces_archived(self):
        """Archived messages are replaced with a summary."""
        store = ContextCollapseStore()
        messages = [
            _msg("user", "Old query", "u1"),
            _msg("assistant", "Old response", "a1"),
            _msg("user", "New query", "u2"),
            _msg("assistant", "New response", "a2"),
        ]
        store.add_commit(["u1", "a1"], "Summary of old exchange")
        result = store.project_view(messages)

        # u1, a1 replaced by 1 summary; u2, a2 kept
        self.assertEqual(len(result), 3)
        # First message is the summary
        self.assertIn("[Collapsed context]", result[0].content[0].text)
        self.assertIn("Summary of old exchange", result[0].content[0].text)
        # Remaining messages unchanged
        self.assertEqual(result[1].content, "New query")

    def test_multiple_commits(self):
        """Multiple sequential commits produce multiple summary injections."""
        store = ContextCollapseStore()
        messages = [
            _msg("user", "Q1", "u1"),
            _msg("assistant", "A1", "a1"),
            _msg("user", "Q2", "u2"),
            _msg("assistant", "A2", "a2"),
            _msg("user", "Q3", "u3"),
        ]
        store.add_commit(["u1", "a1"], "Summary 1")
        store.add_commit(["u2", "a2"], "Summary 2")

        result = store.project_view(messages)
        # 2 summaries + Q3 = 3
        self.assertEqual(len(result), 3)
        self.assertIn("Summary 1", result[0].content[0].text)
        self.assertIn("Summary 2", result[1].content[0].text)
        self.assertEqual(result[2].content, "Q3")

    def test_messages_not_in_commit_pass_through(self):
        """Messages not referenced by any commit are unchanged."""
        store = ContextCollapseStore()
        messages = [
            _msg("user", "Keep me", "u1"),
            _msg("assistant", "Also keep me", "a1"),
        ]
        store.add_commit(["nonexistent"], "Summary")
        result = store.project_view(messages)
        # Summary injected for nonexistent, but u1 and a1 pass through
        self.assertEqual(len(result), 2)

    def test_disabled_store_no_op(self):
        """Disabled store returns original messages."""
        store = ContextCollapseStore()
        store.enabled = False
        store.add_commit(["u1"], "Summary")
        messages = [_msg("user", "Hello", "u1")]
        result = store.project_view(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].content, "Hello")

    def test_clear_removes_all_commits(self):
        store = ContextCollapseStore()
        store.add_commit(["u1"], "Summary")
        store.clear()
        self.assertEqual(len(store.commits), 0)

    def test_serialization_round_trip(self):
        store = ContextCollapseStore()
        store.add_commit(["u1", "u2"], "Summary")
        d = store.to_dict()
        store2 = ContextCollapseStore.from_dict(d)
        self.assertEqual(len(store2.commits), 1)
        self.assertEqual(store2.commits[0].summary, "Summary")

    def test_add_commit_ignores_empty(self):
        """Empty archived or summary is a no-op."""
        store = ContextCollapseStore()
        store.add_commit([], "Summary")
        self.assertEqual(len(store.commits), 0)
        store.add_commit(["u1"], "")
        self.assertEqual(len(store.commits), 0)


class TestGlobalStoreAPI(unittest.TestCase):
    """Tests for module-level global store functions."""

    def setUp(self):
        set_context_collapse_store(None)

    def test_not_enabled_when_no_store(self):
        self.assertFalse(is_context_collapse_enabled())
        self.assertIsNone(get_context_collapse_state())

    def test_enabled_when_store_set(self):
        store = ContextCollapseStore()
        set_context_collapse_store(store)
        self.assertTrue(is_context_collapse_enabled())
        self.assertIs(get_context_collapse_state(), store)

    def tearDown(self):
        set_context_collapse_store(None)


if __name__ == "__main__":
    unittest.main()
