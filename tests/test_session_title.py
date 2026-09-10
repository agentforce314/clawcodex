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


class TestGeneratedTitle:
    def test_cleans_quotes_labels_and_trailing_stops(self):
        from src.services.session_title import clean_generated_title

        assert clean_generated_title('"Deep dive into repository."') == "Deep dive into repository"
        assert clean_generated_title("Title: Repo Deep Dive\nSecond line") == "Repo Deep Dive"
        assert clean_generated_title("  \n  Fix login bug!  ") == "Fix login bug"

    def test_nothing_usable_is_none(self):
        from src.services.session_title import clean_generated_title

        assert clean_generated_title("") is None
        assert clean_generated_title('""') is None
        assert clean_generated_title(None) is None
        assert clean_generated_title(42) is None

    def test_caps_the_length(self):
        from src.services.session_title import clean_generated_title

        title = clean_generated_title("x" * 200)
        assert title is not None
        assert len(title) <= MAX_TITLE_LENGTH
        assert title.endswith("...")

    @pytest.mark.asyncio
    async def test_asks_the_session_provider_once(self):
        from src.services.session_title import generate_title_with_provider

        class Provider:
            calls: list = []

            async def chat_async(self, messages, tools=None, **kwargs):
                Provider.calls.append((messages, kwargs))
                from types import SimpleNamespace

                return SimpleNamespace(content="Repository deep dive\n")

        title = await generate_title_with_provider(Provider(), "make a plan and do a deep dive of this repo")
        assert title == "Repository deep dive"
        assert len(Provider.calls) == 1
        messages, kwargs = Provider.calls[0]
        assert "make a plan and do a deep dive" in messages[0]["content"]
        # Enough for a reasoning model to think before the six words come out.
        assert kwargs["max_tokens"] >= 256

    @pytest.mark.asyncio
    async def test_failures_and_blanks_answer_none(self):
        from src.services.session_title import generate_title_with_provider

        class Broken:
            async def chat_async(self, *a, **k):
                raise RuntimeError("no network")

        class Slow:
            async def chat_async(self, *a, **k):
                import asyncio

                await asyncio.sleep(1)

        assert await generate_title_with_provider(Broken(), "hello") is None
        assert await generate_title_with_provider(Slow(), "hello", timeout_s=0.01) is None
        assert await generate_title_with_provider(None, "hello") is None
        assert await generate_title_with_provider(Broken(), "   ") is None
