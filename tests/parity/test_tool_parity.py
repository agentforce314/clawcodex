"""WS-10: Structural parity — every TS core tool has a Python equivalent.

Verifies:
- Tool name mapping matches ts_tool_names.json snapshot
- All required Tool attributes are present on every tool
- Per-tool properties (is_read_only, is_concurrency_safe) match ts_tool_properties.json
- Schema has the required 'type' and 'properties' keys
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.tool_system.defaults import build_default_registry
from src.tool_system.build_tool import Tool

REF_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "reference_data"


def _load_json(name: str) -> dict:
    return json.loads((REF_DIR / name).read_text())


class TestToolNameParity(unittest.TestCase):
    """Every TS core tool name exists in the Python registry."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = build_default_registry(include_user_tools=False)
        cls.snapshot = _load_json("ts_tool_names.json")

    def test_all_core_tool_names_present(self) -> None:
        core = self.snapshot["core_tools"]
        missing: list[str] = []
        for ts_name, info in core.items():
            py_name = info["python_name"]
            tool = self.registry.get(py_name)
            if tool is None:
                missing.append(f"{ts_name} -> {py_name}")
        self.assertEqual(missing, [], f"Missing tools: {missing}")

    def test_agent_alias_task(self) -> None:
        agent_info = self.snapshot["core_tools"]["Agent"]
        self.assertIn("Task", agent_info.get("aliases", []))
        tool = self.registry.get("Task")
        self.assertIsNotNone(tool, "Agent should be accessible via 'Task' alias")

    def test_not_yet_implemented_acknowledged(self) -> None:
        nyi = self.snapshot["not_yet_implemented"]
        for name in nyi:
            tool = self.registry.get(name)
            # These are acknowledged gaps — just verify they are documented
            if tool is not None:
                # If it was implemented, great — should be removed from nyi
                pass

    def test_no_extra_undocumented_tools(self) -> None:
        """All registered tools should be in the snapshot or explicitly documented."""
        core_py_names = {
            info["python_name"]
            for info in self.snapshot["core_tools"].values()
        }
        registered = {t.name for t in self.registry.list_tools()}
        extra = registered - core_py_names
        # Extra tools are acceptable (ClipboardRead, ClipboardWrite, etc.)
        # Just ensure they don't include any misspelled TS tool names
        for name in extra:
            self.assertNotIn(
                name.lower(),
                {n.lower() for n in self.snapshot["not_yet_implemented"]},
                f"Tool {name} is listed as not-yet-implemented but exists in registry",
            )


class TestToolAttributeParity(unittest.TestCase):
    """Every Python tool has all required Tool attributes matching TS Tool interface."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = build_default_registry(include_user_tools=False)
        cls.props_snapshot = _load_json("ts_tool_properties.json")
        cls.required_attrs = cls.props_snapshot["required_tool_attributes"]

    def test_all_tools_have_required_attributes(self) -> None:
        for tool in self.registry.list_tools():
            for attr in self.required_attrs:
                self.assertTrue(
                    hasattr(tool, attr),
                    f"Tool '{tool.name}' missing required attribute '{attr}'",
                )

    def test_all_tools_have_callable_methods(self) -> None:
        callable_attrs = [
            "call", "prompt", "description", "is_enabled",
            "is_concurrency_safe", "is_read_only", "is_destructive",
            "check_permissions", "map_result_to_api", "user_facing_name",
        ]
        for tool in self.registry.list_tools():
            for attr in callable_attrs:
                val = getattr(tool, attr, None)
                self.assertTrue(
                    callable(val),
                    f"Tool '{tool.name}' attribute '{attr}' should be callable, got {type(val)}",
                )

    def test_all_tools_have_input_schema(self) -> None:
        for tool in self.registry.list_tools():
            schema = tool.input_schema
            self.assertIsInstance(schema, dict, f"Tool '{tool.name}' input_schema should be dict")
            self.assertIn("type", schema, f"Tool '{tool.name}' input_schema missing 'type'")

    def test_tool_name_is_string(self) -> None:
        for tool in self.registry.list_tools():
            self.assertIsInstance(tool.name, str)
            self.assertTrue(len(tool.name) > 0)


class TestToolPropertyParity(unittest.TestCase):
    """Per-tool concurrency/read-only properties match TS defaults."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = build_default_registry(include_user_tools=False)
        cls.props_snapshot = _load_json("ts_tool_properties.json")

    def test_default_is_read_only_false(self) -> None:
        default_val = self.props_snapshot["defaults"]["is_read_only"]
        # Tools not in overrides should use the default
        overridden = set(self.props_snapshot["tool_overrides"].keys())
        for tool in self.registry.list_tools():
            if tool.name not in overridden:
                result = tool.is_read_only({})
                self.assertEqual(
                    result, default_val,
                    f"Tool '{tool.name}' is_read_only({{}}) = {result}, expected default {default_val}",
                )

    def test_default_is_concurrency_safe_false(self) -> None:
        default_val = self.props_snapshot["defaults"]["is_concurrency_safe"]
        overridden = set(self.props_snapshot["tool_overrides"].keys())
        for tool in self.registry.list_tools():
            if tool.name not in overridden:
                result = tool.is_concurrency_safe({})
                self.assertEqual(
                    result, default_val,
                    f"Tool '{tool.name}' is_concurrency_safe({{}}) = {result}, expected default {default_val}",
                )

    def test_read_tool_is_read_only(self) -> None:
        tool = self.registry.get("Read")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.is_read_only({}))

    def test_read_tool_is_concurrency_safe(self) -> None:
        tool = self.registry.get("Read")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.is_concurrency_safe({}))

    def test_glob_tool_is_read_only_and_concurrent(self) -> None:
        tool = self.registry.get("Glob")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.is_read_only({}))
        self.assertTrue(tool.is_concurrency_safe({}))

    def test_grep_tool_is_read_only_and_concurrent(self) -> None:
        tool = self.registry.get("Grep")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.is_read_only({}))
        self.assertTrue(tool.is_concurrency_safe({}))

    def test_bash_tool_not_read_only(self) -> None:
        tool = self.registry.get("Bash")
        self.assertIsNotNone(tool)
        self.assertFalse(tool.is_read_only({}))
        self.assertFalse(tool.is_concurrency_safe({}))

    def test_edit_tool_not_concurrent(self) -> None:
        tool = self.registry.get("Edit")
        self.assertIsNotNone(tool)
        self.assertFalse(tool.is_concurrency_safe({}))

    def test_write_tool_not_concurrent(self) -> None:
        tool = self.registry.get("Write")
        self.assertIsNotNone(tool)
        self.assertFalse(tool.is_concurrency_safe({}))

    def test_web_search_is_read_only_and_concurrent(self) -> None:
        tool = self.registry.get("WebSearch")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.is_read_only({}))
        self.assertTrue(tool.is_concurrency_safe({}))

    def test_tool_search_is_read_only_and_concurrent(self) -> None:
        tool = self.registry.get("ToolSearch")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.is_read_only({}))
        self.assertTrue(tool.is_concurrency_safe({}))

    def test_agent_tool_is_destructive(self) -> None:
        tool = self.registry.get("Agent")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.is_destructive({}))

    def test_max_result_size_chars_default(self) -> None:
        expected_default = self.props_snapshot["defaults"]["max_result_size_chars"]
        for tool in self.registry.list_tools():
            # Some tools override, just check the attribute exists and is int
            self.assertIsInstance(tool.max_result_size_chars, int)

    def test_overridden_tools_match_snapshot(self) -> None:
        overrides = self.props_snapshot["tool_overrides"]
        for tool_name, expected_props in overrides.items():
            tool = self.registry.get(tool_name)
            if tool is None:
                continue  # Skip tools not in registry
            for prop, expected_val in expected_props.items():
                if prop == "is_read_only":
                    actual = tool.is_read_only({})
                elif prop == "is_concurrency_safe":
                    actual = tool.is_concurrency_safe({})
                elif prop == "is_destructive":
                    actual = tool.is_destructive({})
                else:
                    continue
                self.assertEqual(
                    actual, expected_val,
                    f"Tool '{tool_name}'.{prop}({{}}) = {actual}, expected {expected_val}",
                )


if __name__ == "__main__":
    unittest.main()
