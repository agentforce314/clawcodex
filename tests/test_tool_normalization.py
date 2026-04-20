from __future__ import annotations

import unittest

from src.services.api.tool_normalization import (
    has_tool_field_mapping,
    normalize_tool_arguments,
)


class TestHasToolFieldMapping(unittest.TestCase):
    def test_bash_has_mapping(self) -> None:
        self.assertTrue(has_tool_field_mapping("Bash"))

    def test_read_has_mapping(self) -> None:
        self.assertTrue(has_tool_field_mapping("Read"))

    def test_unknown_no_mapping(self) -> None:
        self.assertFalse(has_tool_field_mapping("CustomTool"))


class TestNormalizeToolArguments(unittest.TestCase):
    def test_none_returns_empty_dict(self) -> None:
        self.assertEqual(normalize_tool_arguments("Bash", None), {})

    def test_valid_json_object(self) -> None:
        result = normalize_tool_arguments("Bash", '{"command": "ls"}')
        self.assertEqual(result, {"command": "ls"})

    def test_plain_string_for_bash(self) -> None:
        result = normalize_tool_arguments("Bash", "ls -la")
        self.assertEqual(result, {"command": "ls -la"})

    def test_plain_string_for_read(self) -> None:
        result = normalize_tool_arguments("Read", "/path/to/file")
        self.assertEqual(result, {"file_path": "/path/to/file"})

    def test_plain_string_for_unknown_tool(self) -> None:
        result = normalize_tool_arguments("Unknown", "some value")
        self.assertEqual(result, {})

    def test_json_string_value_wrapping(self) -> None:
        result = normalize_tool_arguments("Bash", '"ls -la"')
        self.assertEqual(result, {"command": "ls -la"})

    def test_blank_string(self) -> None:
        result = normalize_tool_arguments("Bash", "   ")
        self.assertEqual(result, {})

    def test_structured_object_literal(self) -> None:
        result = normalize_tool_arguments("Bash", "{ 'command': 'ls' }")
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
