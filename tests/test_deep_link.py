"""Tests for Deep Links subsystem."""
from __future__ import annotations

import unittest

from src.utils.deep_link import (
    DEEP_LINK_SCHEME,
    DeepLink,
    create_prompt_link,
    create_session_link,
    parse_deep_link,
)


class TestDeepLink(unittest.TestCase):
    def test_parse_prompt_link(self) -> None:
        url = "claude-code://app/prompt?prompt=Hello+world"
        link = parse_deep_link(url)
        self.assertIsNotNone(link)
        self.assertEqual(link.action, "prompt")
        self.assertEqual(link.prompt, "Hello world")

    def test_parse_resume_link(self) -> None:
        url = "claude-code://app/resume?session_id=abc123"
        link = parse_deep_link(url)
        self.assertIsNotNone(link)
        self.assertEqual(link.action, "resume")
        self.assertEqual(link.session_id, "abc123")

    def test_parse_invalid_scheme(self) -> None:
        self.assertIsNone(parse_deep_link("https://example.com/prompt"))

    def test_parse_no_action(self) -> None:
        self.assertIsNone(parse_deep_link("claude-code://app/"))

    def test_parse_garbage(self) -> None:
        self.assertIsNone(parse_deep_link("not a url at all"))

    def test_create_prompt_link(self) -> None:
        url = create_prompt_link("Fix the bug")
        link = parse_deep_link(url)
        self.assertEqual(link.action, "prompt")
        self.assertEqual(link.prompt, "Fix the bug")

    def test_create_prompt_link_with_model(self) -> None:
        url = create_prompt_link("test", model="claude-haiku")
        link = parse_deep_link(url)
        self.assertEqual(link.model, "claude-haiku")

    def test_create_session_link(self) -> None:
        url = create_session_link("sess-42")
        link = parse_deep_link(url)
        self.assertEqual(link.action, "resume")
        self.assertEqual(link.session_id, "sess-42")

    def test_roundtrip(self) -> None:
        original = DeepLink(action="prompt", params={"prompt": "hello", "model": "sonnet"})
        url = original.to_url()
        parsed = parse_deep_link(url)
        self.assertEqual(parsed.action, original.action)
        self.assertEqual(parsed.prompt, original.prompt)
        self.assertEqual(parsed.model, original.model)

    def test_deep_link_scheme(self) -> None:
        self.assertEqual(DEEP_LINK_SCHEME, "claude-code")


if __name__ == "__main__":
    unittest.main()
