"""Tests for R2-WS-7: Session title generation."""

from __future__ import annotations

import pytest

from src.services.session_title import auto_title_from_message, MAX_TITLE_LENGTH


class TestAutoTitle:
    def test_simple_message(self):
        title = auto_title_from_message("Fix the login bug")
        assert title == "Fix the login bug"

    def test_empty_message(self):
        title = auto_title_from_message("")
        assert title == "Untitled session"

    def test_long_message_truncated(self):
        long = "x" * 200
        title = auto_title_from_message(long)
        assert len(title) <= MAX_TITLE_LENGTH
        assert title.endswith("...")

    def test_multiline_takes_first_line(self):
        title = auto_title_from_message("First line\nSecond line\nThird line")
        assert title == "First line"

    def test_strips_common_prefix_please(self):
        title = auto_title_from_message("please fix the bug")
        assert title == "Fix the bug"

    def test_strips_common_prefix_can_you(self):
        title = auto_title_from_message("can you help with testing")
        assert title == "Help with testing"

    def test_capitalizes_first_letter(self):
        title = auto_title_from_message("update the readme")
        assert title == "Update the readme"

    def test_already_capitalized(self):
        title = auto_title_from_message("Update the README")
        assert title == "Update the README"
