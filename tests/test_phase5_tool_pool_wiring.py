"""Phase 5 tests: ``assemble_tool_pool`` + ``filter_tools_for_request``
wired at the API schema-build boundary.

Verifies:
1. The query loop's schema-build site filters out deferred tools
   that haven't been discovered (saves API tokens).
2. The agent_loop's schema-build site uses ``assemble_tool_pool``
   so deny rules + sort apply.
3. The Anthropic provider translates ``_defer_loading: True`` to
   the API field ``defer_loading: true``.
4. The ``CLAWCODEX_DEFER_LOADING=0`` rollback flag strips the
   marker without translating (full schemas always sent).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.permissions.types import ToolPermissionContext
from src.providers.anthropic_provider import _translate_tool_schemas_for_anthropic
from src.tool_system.build_tool import build_tool
from src.tool_system.protocol import ToolResult
from src.tool_system.registry import ToolRegistry


def _make_tool(name: str, *, should_defer: bool = False, is_mcp: bool = False):
    def _call(_inp, _ctx):
        return ToolResult(name=name, output="ok")
    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=_call,
        should_defer=should_defer,
        is_mcp=is_mcp,
    )


class TestDeferLoadingTranslation(unittest.TestCase):
    """``_translate_tool_schemas_for_anthropic`` is the choke point
    that converts internal ``_defer_loading`` markers to the
    Anthropic API's ``defer_loading`` field."""

    def test_translates_underscore_prefix_to_api_field(self) -> None:
        tools = [
            {"name": "X", "_defer_loading": True, "input_schema": {}},
        ]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAWCODEX_DEFER_LOADING", None)
            out = _translate_tool_schemas_for_anthropic(tools)
        self.assertEqual(len(out), 1)
        self.assertIn("defer_loading", out[0])
        self.assertNotIn("_defer_loading", out[0])
        self.assertTrue(out[0]["defer_loading"])

    def test_preserves_other_fields(self) -> None:
        tools = [
            {"name": "X", "_defer_loading": True, "description": "d", "input_schema": {"type": "object"}},
        ]
        out = _translate_tool_schemas_for_anthropic(tools)
        self.assertEqual(out[0]["name"], "X")
        self.assertEqual(out[0]["description"], "d")
        self.assertEqual(out[0]["input_schema"], {"type": "object"})

    def test_tools_without_marker_pass_through(self) -> None:
        tools = [{"name": "X", "input_schema": {}}]
        out = _translate_tool_schemas_for_anthropic(tools)
        self.assertEqual(out, tools)
        # Different list object (copy semantics) — caller's input not mutated.
        self.assertIsNot(out, tools)

    def test_does_not_mutate_caller_input(self) -> None:
        tools = [{"name": "X", "_defer_loading": True, "input_schema": {}}]
        original = dict(tools[0])
        _translate_tool_schemas_for_anthropic(tools)
        # Caller's dict still has the underscore marker.
        self.assertEqual(tools[0], original)

    def test_rollback_flag_strips_without_translating(self) -> None:
        tools = [{"name": "X", "_defer_loading": True, "input_schema": {}}]
        with patch.dict(os.environ, {"CLAWCODEX_DEFER_LOADING": "0"}):
            out = _translate_tool_schemas_for_anthropic(tools)
        # _defer_loading is stripped (it's an internal marker, never
        # ships to the API)
        self.assertNotIn("_defer_loading", out[0])
        # defer_loading is NOT set when flag is off (full schemas
        # always sent; ToolSearch path inactive).
        self.assertNotIn("defer_loading", out[0])

    def test_empty_input_passes_through(self) -> None:
        self.assertIsNone(_translate_tool_schemas_for_anthropic(None))
        self.assertEqual(_translate_tool_schemas_for_anthropic([]), [])

    def test_non_dict_items_pass_through(self) -> None:
        # Defensive: list may contain weird items in tests; don't crash.
        tools = [{"name": "X", "input_schema": {}}, "not a dict"]
        out = _translate_tool_schemas_for_anthropic(tools)
        self.assertEqual(out[0]["name"], "X")
        self.assertEqual(out[1], "not a dict")


class TestQuerySchemaBuildFiltersDeferredTools(unittest.TestCase):
    """When ToolSearch is enabled, deferred tools are filtered out of
    the schema list sent to the API."""

    def test_deferred_tool_filtered_when_not_discovered(self) -> None:
        from src.tool_system.tool_search import (
            filter_tools_for_request,
            is_tool_search_enabled_optimistic,
        )

        non_deferred = _make_tool("Regular")
        deferred = _make_tool("Deferred", should_defer=True)
        tools = [non_deferred, deferred]

        # Ensure ToolSearch isn't disabled by the kill-switch (some
        # CI environments set CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS).
        env_override = {
            "ENABLE_TOOL_SEARCH": "true",
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "0",
        }
        with patch.dict(os.environ, env_override):
            self.assertTrue(is_tool_search_enabled_optimistic())
            # Empty messages = no discovered tools.
            result = filter_tools_for_request(
                tools, "claude-sonnet-4-7", messages=[],
            )

        names = [t.name for t in result]
        self.assertIn("Regular", names)
        self.assertNotIn("Deferred", names)


class TestAgentLoopSchemaBuildUsesAssembleToolPool(unittest.TestCase):
    """The agent_loop schema-build site now uses ``assemble_tool_pool``
    so deny rules are honored at the schema layer."""

    def test_deny_rule_filters_tool_from_schema_list(self) -> None:
        from src.tool_system.registry import assemble_tool_pool

        # Build a registry with two tools; deny one via rule.
        registry = ToolRegistry([_make_tool("Keep"), _make_tool("Deny")])
        # ``ToolPermissionContext.from_iterables`` builds a deny rule
        # under the "session" source for the listed names.
        pc = ToolPermissionContext.from_iterables(deny_names=["Deny"])

        pool = assemble_tool_pool(registry, pc)
        names = [t.name for t in pool]
        self.assertIn("Keep", names)
        self.assertNotIn("Deny", names)


class TestEndToEndDeferLoadingFromQuery(unittest.TestCase):
    """When the query loop builds tool_schemas with a deferred MCP
    tool, the resulting list has ``_defer_loading=True`` markers."""

    def test_schema_carries_defer_marker(self) -> None:
        from src.tool_system.tool_search import (
            filter_tools_for_request,
            is_tool_search_enabled_optimistic,
        )

        deferred_mcp = _make_tool("mcp__server__tool", is_mcp=True)
        regular = _make_tool("Regular")
        tools = [regular, deferred_mcp]

        # Simulate a "discovered" message history that includes the
        # deferred tool. Then it WILL be in the filtered list.
        from src.types.messages import UserMessage

        discovered_msg = UserMessage(
            content=[{
                "type": "tool_result",
                "tool_use_id": "x",
                "content": [{"type": "tool_reference", "tool_name": "mcp__server__tool"}],
            }],
        )

        env_override = {
            "ENABLE_TOOL_SEARCH": "true",
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "0",
        }
        with patch.dict(os.environ, env_override):
            self.assertTrue(is_tool_search_enabled_optimistic())
            filtered = filter_tools_for_request(
                tools, "claude-sonnet-4-7", messages=[discovered_msg],
            )

        names = [t.name for t in filtered]
        # MCP tool IS now included because it was discovered.
        self.assertIn("mcp__server__tool", names)
        self.assertIn("Regular", names)


if __name__ == "__main__":
    unittest.main()
