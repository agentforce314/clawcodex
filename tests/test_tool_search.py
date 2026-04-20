"""Tests for src/tool_system/tool_search.py — WS-5 tool search."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.tool_system.tool_search import (
    DEFAULT_AUTO_TOOL_SEARCH_PERCENTAGE,
    ToolSearchMode,
    _parse_auto_percentage,
    extract_discovered_tool_names,
    filter_tools_for_request,
    get_auto_tool_search_percentage,
    get_tool_search_mode,
    is_deferred_tool,
    is_tool_search_enabled_optimistic,
    is_tool_search_tool_available,
    model_supports_tool_reference,
)
from src.tool_system.build_tool import build_tool


def _make_tool(name: str, *, should_defer: bool = False, is_mcp: bool = False):
    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=lambda i, c: None,
        prompt=f"Tool {name}",
        should_defer=should_defer,
        is_mcp=is_mcp,
    )


class TestParseAutoPercentage(unittest.TestCase):
    def test_auto_0(self):
        self.assertEqual(_parse_auto_percentage("auto:0"), 0)

    def test_auto_50(self):
        self.assertEqual(_parse_auto_percentage("auto:50"), 50)

    def test_auto_100(self):
        self.assertEqual(_parse_auto_percentage("auto:100"), 100)

    def test_auto_negative_clamped(self):
        self.assertEqual(_parse_auto_percentage("auto:-10"), 0)

    def test_auto_over_100_clamped(self):
        self.assertEqual(_parse_auto_percentage("auto:200"), 100)

    def test_not_auto_prefix(self):
        self.assertIsNone(_parse_auto_percentage("true"))

    def test_invalid_number(self):
        self.assertIsNone(_parse_auto_percentage("auto:abc"))


class TestGetToolSearchMode(unittest.TestCase):
    def test_default_is_tst(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ENABLE_TOOL_SEARCH", None)
            os.environ.pop("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", None)
            mode = get_tool_search_mode()
            self.assertEqual(mode, ToolSearchMode.TST)

    def test_true_is_tst(self):
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "true", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": ""}):
            self.assertEqual(get_tool_search_mode(), ToolSearchMode.TST)

    def test_false_is_standard(self):
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "false"}):
            self.assertEqual(get_tool_search_mode(), ToolSearchMode.STANDARD)

    def test_auto_is_tst_auto(self):
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "auto", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": ""}):
            self.assertEqual(get_tool_search_mode(), ToolSearchMode.TST_AUTO)

    def test_auto_0_is_tst(self):
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "auto:0", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": ""}):
            self.assertEqual(get_tool_search_mode(), ToolSearchMode.TST)

    def test_auto_100_is_standard(self):
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "auto:100"}):
            self.assertEqual(get_tool_search_mode(), ToolSearchMode.STANDARD)

    def test_auto_50_is_tst_auto(self):
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "auto:50", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": ""}):
            self.assertEqual(get_tool_search_mode(), ToolSearchMode.TST_AUTO)

    def test_kill_switch(self):
        with patch.dict(os.environ, {
            "ENABLE_TOOL_SEARCH": "true",
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "true",
        }):
            self.assertEqual(get_tool_search_mode(), ToolSearchMode.STANDARD)


class TestGetAutoToolSearchPercentage(unittest.TestCase):
    def test_default(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ENABLE_TOOL_SEARCH", None)
            self.assertEqual(get_auto_tool_search_percentage(), DEFAULT_AUTO_TOOL_SEARCH_PERCENTAGE)

    def test_custom(self):
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "auto:25"}):
            self.assertEqual(get_auto_tool_search_percentage(), 25)


class TestModelSupportsToolReference(unittest.TestCase):
    def test_sonnet_supported(self):
        self.assertTrue(model_supports_tool_reference("claude-sonnet-4-6"))

    def test_opus_supported(self):
        self.assertTrue(model_supports_tool_reference("claude-opus-4-6"))

    def test_haiku_not_supported(self):
        self.assertFalse(model_supports_tool_reference("claude-3-5-haiku"))

    def test_unknown_model_supported(self):
        self.assertTrue(model_supports_tool_reference("future-model-x"))


class TestIsDeferredTool(unittest.TestCase):
    def test_mcp_tool(self):
        tool = _make_tool("mcp_tool", is_mcp=True)
        self.assertTrue(is_deferred_tool(tool))

    def test_should_defer_tool(self):
        tool = _make_tool("deferred", should_defer=True)
        self.assertTrue(is_deferred_tool(tool))

    def test_normal_tool(self):
        tool = _make_tool("normal")
        self.assertFalse(is_deferred_tool(tool))


class TestIsToolSearchToolAvailable(unittest.TestCase):
    def test_available(self):
        tools = [_make_tool("Read"), _make_tool("ToolSearch")]
        self.assertTrue(is_tool_search_tool_available(tools))

    def test_not_available(self):
        tools = [_make_tool("Read"), _make_tool("Write")]
        self.assertFalse(is_tool_search_tool_available(tools))


class TestIsToolSearchEnabledOptimistic(unittest.TestCase):
    def test_tst_mode(self):
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "true", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": ""}):
            self.assertTrue(is_tool_search_enabled_optimistic())

    def test_standard_mode(self):
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "false"}):
            self.assertFalse(is_tool_search_enabled_optimistic())


class TestExtractDiscoveredToolNames(unittest.TestCase):
    def test_empty_messages(self):
        self.assertEqual(extract_discovered_tool_names([]), set())

    def test_tool_reference_in_user_message(self):
        msgs = [{
            "type": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "123",
                "content": [
                    {"type": "tool_reference", "tool_name": "mcp__tool_x"},
                    {"type": "text", "text": "Found tool"},
                ],
            }],
        }]
        result = extract_discovered_tool_names(msgs)
        self.assertIn("mcp__tool_x", result)

    def test_no_tool_reference(self):
        msgs = [{
            "type": "user",
            "content": [{"type": "text", "text": "hello"}],
        }]
        result = extract_discovered_tool_names(msgs)
        self.assertEqual(result, set())

    def test_compact_boundary_carries_tools(self):
        msgs = [{
            "type": "system",
            "subtype": "compact_boundary",
            "compact_metadata": {
                "pre_compact_discovered_tools": ["mcp__tool_a", "mcp__tool_b"],
            },
        }]
        result = extract_discovered_tool_names(msgs)
        self.assertIn("mcp__tool_a", result)
        self.assertIn("mcp__tool_b", result)


class TestFilterToolsForRequest(unittest.TestCase):
    def test_standard_mode_returns_all(self):
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "false"}):
            tools = [
                _make_tool("Read"),
                _make_tool("mcp_tool", is_mcp=True),
            ]
            result = filter_tools_for_request(tools, "claude-sonnet-4-6")
            self.assertEqual(len(result), 2)

    def test_tst_mode_filters_undiscovered(self):
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "true", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": ""}):
            tools = [
                _make_tool("Read"),
                _make_tool("ToolSearch"),
                _make_tool("mcp_tool", is_mcp=True),
            ]
            result = filter_tools_for_request(tools, "claude-sonnet-4-6", messages=[])
            # mcp_tool should be filtered out (not discovered)
            names = [t.name for t in result]
            self.assertIn("Read", names)
            self.assertNotIn("mcp_tool", names)

    def test_discovered_tools_kept(self):
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "true", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": ""}):
            tools = [
                _make_tool("Read"),
                _make_tool("ToolSearch"),
                _make_tool("mcp_tool", is_mcp=True),
            ]
            messages = [{
                "type": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "123",
                    "content": [{"type": "tool_reference", "tool_name": "mcp_tool"}],
                }],
            }]
            result = filter_tools_for_request(tools, "claude-sonnet-4-6", messages=messages)
            names = [t.name for t in result]
            self.assertIn("mcp_tool", names)


if __name__ == "__main__":
    unittest.main()
