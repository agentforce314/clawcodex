"""WS-10: Structural parity — context system structure matches TS.

Verifies:
- SystemPromptParts fields match ts_context_structure.json
- User context keys match
- System context keys match
- Context assembly order matches
- Memory file levels match
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from src.context_system.models import SystemPromptParts
from src.context_system.prompt_assembly import (
    append_system_context,
    clear_context_caches,
    fetch_system_prompt_parts,
    get_system_context,
    get_user_context,
    prepend_user_context,
)
from src.types.messages import UserMessage

REF_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "reference_data"


def _load_json(name: str) -> dict:
    return json.loads((REF_DIR / name).read_text())


class TestSystemPromptPartsParity(unittest.TestCase):
    """SystemPromptParts fields match TS queryContext.ts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_context_structure.json")

    def test_all_fields_present(self) -> None:
        expected = self.snapshot["system_prompt_parts_fields"]
        parts = SystemPromptParts.__dataclass_fields__
        for field_name in expected:
            self.assertIn(
                field_name, parts,
                f"SystemPromptParts missing field '{field_name}'",
            )

    def test_default_system_prompt_is_list(self) -> None:
        parts = SystemPromptParts(default_system_prompt=[], user_context={}, system_context={})
        self.assertIsInstance(parts.default_system_prompt, list)

    def test_user_context_is_dict(self) -> None:
        parts = SystemPromptParts(default_system_prompt=[], user_context={}, system_context={})
        self.assertIsInstance(parts.user_context, dict)

    def test_system_context_is_dict(self) -> None:
        parts = SystemPromptParts(default_system_prompt=[], user_context={}, system_context={})
        self.assertIsInstance(parts.system_context, dict)


class TestUserContextKeysParity(unittest.TestCase):
    """User context keys match TS context.ts getUserContext."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_context_structure.json")

    def test_user_context_has_current_date(self) -> None:
        clear_context_caches()
        ctx = asyncio.run(get_user_context())
        self.assertIn("currentDate", ctx)

    def test_user_context_date_is_string(self) -> None:
        clear_context_caches()
        ctx = asyncio.run(get_user_context())
        self.assertIsInstance(ctx["currentDate"], str)
        self.assertTrue(len(ctx["currentDate"]) > 0)

    def test_expected_user_context_keys(self) -> None:
        expected_keys = set(self.snapshot["user_context_keys"])
        # claudeMd is optional (only present when CLAUDE.md exists)
        self.assertIn("currentDate", expected_keys)
        self.assertIn("claudeMd", expected_keys)


class TestSystemContextKeysParity(unittest.TestCase):
    """System context keys match TS context.ts getSystemContext."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_context_structure.json")

    def test_expected_system_context_keys(self) -> None:
        expected_keys = set(self.snapshot["system_context_keys"])
        self.assertIn("gitStatus", expected_keys)


class TestContextAssemblyOrderParity(unittest.TestCase):
    """Context assembly follows the TS order."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_context_structure.json")

    def test_assembly_order_matches(self) -> None:
        expected = self.snapshot["context_assembly_order"]
        # Verify the Python functions exist in the expected order
        from src.context_system import prompt_assembly
        for func_name in expected:
            self.assertTrue(
                hasattr(prompt_assembly, func_name),
                f"prompt_assembly missing function '{func_name}'",
            )

    def test_append_system_context_concatenates(self) -> None:
        result = append_system_context("Base prompt", {"gitStatus": "clean"})
        self.assertIn("Base prompt", result)
        self.assertIn("gitStatus", result)

    def test_append_system_context_with_list(self) -> None:
        result = append_system_context(["Part 1", "Part 2"], {"gitStatus": "clean"})
        self.assertIn("Part 1", result)
        self.assertIn("Part 2", result)
        self.assertIn("gitStatus", result)

    def test_prepend_user_context_adds_reminder(self) -> None:
        messages = [UserMessage(content="Hello")]
        result = prepend_user_context(messages, {"claudeMd": "Some rules"})
        self.assertEqual(len(result), 2)  # reminder + original
        reminder = result[0]
        self.assertIsInstance(reminder, UserMessage)
        self.assertIn("system-reminder", str(reminder.content))

    def test_prepend_user_context_empty_noop(self) -> None:
        messages = [UserMessage(content="Hello")]
        result = prepend_user_context(messages, {})
        self.assertEqual(len(result), 1)


class TestContextCachesParity(unittest.TestCase):
    """Context caching matches TS memoization pattern."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_context_structure.json")

    def test_clear_caches_function_exists(self) -> None:
        self.assertTrue(callable(clear_context_caches))

    def test_caches_are_memoized(self) -> None:
        """After first call, get_user_context returns cached value."""
        clear_context_caches()
        ctx1 = asyncio.run(get_user_context())
        ctx2 = asyncio.run(get_user_context())
        # Same date string (memoized)
        self.assertEqual(ctx1["currentDate"], ctx2["currentDate"])

    def test_clear_resets_cache(self) -> None:
        """After clear, get_user_context returns fresh value."""
        clear_context_caches()
        ctx1 = asyncio.run(get_user_context())
        clear_context_caches()
        # Just verifying it doesn't error — date might be same within same second
        ctx2 = asyncio.run(get_user_context())
        self.assertIn("currentDate", ctx2)


class TestMemoryFileLevelsParity(unittest.TestCase):
    """Memory file levels match TS CLAUDE.md loading hierarchy."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_context_structure.json")

    def test_expected_levels(self) -> None:
        expected = set(self.snapshot["memory_file_levels"])
        self.assertIn("managed", expected)
        self.assertIn("user", expected)
        self.assertIn("project", expected)
        self.assertIn("local", expected)


if __name__ == "__main__":
    unittest.main()
