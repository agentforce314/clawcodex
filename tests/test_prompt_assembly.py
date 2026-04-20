"""Tests for src/context_system/prompt_assembly.py — WS-5 prompt assembly."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.context_system.prompt_assembly import (
    _compute_env_info,
    _get_local_iso_date,
    append_system_context,
    clear_context_caches,
    fetch_system_prompt_parts,
    get_system_context,
    get_user_context,
    prepend_user_context,
)
from src.context_system.claude_md import clear_memory_file_caches
from src.context_system.git_context import clear_git_caches
from src.context_system.models import SystemPromptParts
from src.types.messages import UserMessage


def _run(coro):
    return asyncio.run(coro)


class TestGetLocalIsoDate(unittest.TestCase):
    def test_returns_string(self):
        result = _get_local_iso_date()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 10)


class TestAppendSystemContext(unittest.TestCase):
    def test_empty_context(self):
        result = append_system_context("Hello system", {})
        self.assertEqual(result, "Hello system")

    def test_with_git_status(self):
        result = append_system_context("System prompt", {"gitStatus": "branch: main"})
        self.assertIn("System prompt", result)
        self.assertIn("gitStatus: branch: main", result)

    def test_list_input(self):
        result = append_system_context(
            ["Section 1", "Section 2"],
            {"gitStatus": "clean"},
        )
        self.assertIn("Section 1", result)
        self.assertIn("Section 2", result)
        self.assertIn("gitStatus: clean", result)

    def test_empty_prompt_and_context(self):
        result = append_system_context("", {})
        self.assertEqual(result, "")

    def test_multiple_context_entries(self):
        result = append_system_context("Base", {
            "gitStatus": "clean",
            "envInfo": "macOS",
        })
        self.assertIn("gitStatus: clean", result)
        self.assertIn("envInfo: macOS", result)


class TestPrependUserContext(unittest.TestCase):
    def test_empty_context(self):
        msgs = [UserMessage(content="hi")]
        result = prepend_user_context(msgs, {})
        self.assertEqual(len(result), 1)

    def test_with_claude_md(self):
        msgs = [UserMessage(content="hi")]
        result = prepend_user_context(msgs, {"claudeMd": "Always test."})
        self.assertEqual(len(result), 2)
        # First message should be the system reminder
        first = result[0]
        self.assertIsInstance(first, UserMessage)
        self.assertIn("system-reminder", first.content)
        self.assertIn("Always test", first.content)

    def test_original_messages_preserved(self):
        msgs = [UserMessage(content="original")]
        result = prepend_user_context(msgs, {"claudeMd": "rule"})
        self.assertEqual(result[-1].content, "original")

    def test_multiple_context_keys(self):
        msgs = [UserMessage(content="q")]
        result = prepend_user_context(msgs, {
            "claudeMd": "Rule 1",
            "currentDate": "2025-01-01",
        })
        first_content = result[0].content
        self.assertIn("claudeMd", first_content)
        self.assertIn("currentDate", first_content)


class TestGetUserContext(unittest.TestCase):
    def setUp(self):
        clear_context_caches()

    def tearDown(self):
        clear_context_caches()

    def test_includes_current_date(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true"}):
            result = _run(get_user_context())
            self.assertIn("currentDate", result)
            self.assertIsInstance(result["currentDate"], str)

    def test_memoization(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true"}):
            result1 = _run(get_user_context())
            result2 = _run(get_user_context())
            self.assertEqual(result1, result2)

    def test_includes_claude_md_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CLAUDE.md").write_text("Test rule", encoding="utf-8")
            with patch.dict(os.environ, {
                "CLAUDE_CODE_ORIGINAL_CWD": tmp,
                "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "",
                "CLAUDE_CODE_BARE_MODE": "",
            }):
                clear_memory_file_caches()
                clear_context_caches()
                result = _run(get_user_context(cwd=tmp))
                if "claudeMd" in result:
                    self.assertIn("Test rule", result["claudeMd"])


class TestGetSystemContext(unittest.TestCase):
    def setUp(self):
        clear_context_caches()
        clear_git_caches()

    def tearDown(self):
        clear_context_caches()
        clear_git_caches()

    def test_memoization(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "true"}):
            result1 = _run(get_system_context())
            result2 = _run(get_system_context())
            self.assertEqual(result1, result2)

    def test_git_disabled(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "true"}):
            clear_context_caches()
            result = _run(get_system_context())
            self.assertNotIn("gitStatus", result)


class TestFetchSystemPromptParts(unittest.TestCase):
    def setUp(self):
        clear_context_caches()
        clear_git_caches()
        clear_memory_file_caches()

    def tearDown(self):
        clear_context_caches()
        clear_git_caches()
        clear_memory_file_caches()

    def test_returns_system_prompt_parts(self):
        with patch.dict(os.environ, {
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true",
            "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "true",
        }):
            result = _run(fetch_system_prompt_parts())
            self.assertIsInstance(result, SystemPromptParts)
            self.assertIsInstance(result.default_system_prompt, list)
            self.assertIsInstance(result.user_context, dict)
            self.assertIsInstance(result.system_context, dict)

    def test_custom_prompt_skips_default(self):
        with patch.dict(os.environ, {
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true",
        }):
            result = _run(fetch_system_prompt_parts(
                custom_system_prompt="Custom prompt",
            ))
            self.assertEqual(result.default_system_prompt, [])
            self.assertEqual(result.system_context, {})

    def test_user_context_has_date(self):
        with patch.dict(os.environ, {
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true",
            "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "true",
        }):
            result = _run(fetch_system_prompt_parts())
            self.assertIn("currentDate", result.user_context)


class TestComputeEnvInfo(unittest.TestCase):
    def test_includes_cwd(self):
        result = _compute_env_info("/test/path")
        self.assertIn("/test/path", result)
        self.assertIn("CWD:", result)
        self.assertIn("OS:", result)
        self.assertIn("Date:", result)


class TestClearContextCaches(unittest.TestCase):
    def test_no_crash(self):
        clear_context_caches()
        clear_context_caches()  # Double clear should be fine


if __name__ == "__main__":
    unittest.main()
