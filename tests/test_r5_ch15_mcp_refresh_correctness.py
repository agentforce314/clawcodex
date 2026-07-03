"""R5 round-5 (ch15) — MCP refresh correctness (m1/m2/m3).

m1: _apply_refreshed_tools builds new tools BEFORE mutating self.tools, so a
    _wrap failure leaves the current tools intact (was: truncate-then-fail).
m2: the removed set is derived from self.servers[name], not a prefix match,
    so a sibling server sharing the prefix isn't wrongly captured.
m3: the tools/list_changed handler is wired only for clients that ADVERTISE
    tools.listChanged.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.services.mcp.mcp_string_utils import build_mcp_tool_name


def _wrapped(server, tool_name):
    t = MagicMock()
    t.name = build_mcp_tool_name(server, tool_name)
    return t


def _raw(name):
    r = MagicMock(); r.name = name
    return r


def _rt():
    from src.server.mcp_runtime import McpRuntime
    rt = McpRuntime()
    rt._wrap = lambda server, mt, client: _wrapped(server, mt.name)
    return rt


class TestBuildBeforeSwap(unittest.TestCase):
    """m1 — a _wrap failure must not truncate self.tools."""

    def test_wrap_failure_leaves_tools_intact(self):
        rt = _rt()
        rt.tools = [_wrapped("srv", "a"), _wrapped("other", "keep")]
        rt.servers = {"srv": ["a"], "other": ["keep"]}

        def _boom(server, mt, client):
            raise RuntimeError("wrap failed")
        rt._wrap = _boom

        with self.assertRaises(RuntimeError):
            rt._apply_refreshed_tools("srv", [_raw("new_a")], MagicMock())

        # self.tools untouched — the old srv tool + other server survive.
        names = sorted(t.name for t in rt.tools)
        self.assertEqual(names, ["mcp__other__keep", "mcp__srv__a"])
        self.assertEqual(rt.servers["srv"], ["a"])  # not clobbered


class TestNoSiblingCapture(unittest.TestCase):
    """m2 — refreshing "foo" must not nuke a sibling whose prefix overlaps."""

    def test_prefix_sibling_not_removed(self):
        rt = _rt()
        # "foo" server + a sibling registered as "foo__bar" (normalized from
        # e.g. "foo, bar") whose full tool names startswith "mcp__foo__".
        rt.tools = [
            _wrapped("foo", "x"),          # mcp__foo__x
            _wrapped("foo__bar", "y"),     # mcp__foo__bar__y  (startswith mcp__foo__)
        ]
        rt.servers = {"foo": ["x"], "foo__bar": ["y"]}

        removed, new_tools = rt._apply_refreshed_tools(
            "foo", [_raw("x2")], MagicMock())

        # Only foo's own tool is removed; the sibling survives.
        self.assertEqual(removed, ["mcp__foo__x"])
        names = sorted(t.name for t in rt.tools)
        self.assertEqual(names, ["mcp__foo__bar__y", "mcp__foo__x2"])


class TestCapabilityGatedWiring(unittest.TestCase):
    """m3 — the list_changed handler is wired only when advertised."""

    def _run_wiring(self, advertises):
        # Reproduce the agent_server wiring predicate (the load-bearing
        # condition), since driving full _build_runtime is heavy.
        cl = MagicMock()
        caps = MagicMock()
        caps.tools_list_changed = advertises
        cl.capabilities = caps
        wired = {"called": False}
        cl.set_notification_handler = lambda h: wired.__setitem__("called", True)

        _caps = getattr(cl, "capabilities", None)
        if getattr(_caps, "tools_list_changed", False):
            cl.set_notification_handler(lambda m, p: None)
        return wired["called"]

    def test_wired_when_advertised(self):
        self.assertTrue(self._run_wiring(True))

    def test_not_wired_when_not_advertised(self):
        self.assertFalse(self._run_wiring(False))

    def test_nested_listchanged_parsed_from_init_result(self):
        # critic minor: the m3 gate hinges on parsing {tools:{listChanged:true}}
        # from the real init capabilities — pin the TRUE and FALSE cases on the
        # actual parse function (not just the dataclass constructor).
        from src.services.mcp.client import _parse_server_capabilities

        adv = _parse_server_capabilities({"tools": {"listChanged": True}})
        self.assertTrue(adv.tools_list_changed)
        self.assertTrue(adv.tools)  # tools present

        # listChanged absent → False (must NOT wire refresh). The m3 gate
        # cares only about tools_list_changed. (Note: an empty {} tools cap
        # collapses tools→False under the existing bool() parse — a
        # pre-existing quirk, unchanged here and orthogonal to m3.)
        no_lc = _parse_server_capabilities({"tools": {"other": 1}})
        self.assertFalse(no_lc.tools_list_changed)
        self.assertTrue(no_lc.tools)  # non-empty dict → tools present

        # bare truthy tools (not a dict) → False.
        bare = _parse_server_capabilities({"tools": True})
        self.assertFalse(bare.tools_list_changed)

        # missing / malformed caps → all False, no raise.
        self.assertFalse(_parse_server_capabilities({}).tools_list_changed)
        self.assertFalse(_parse_server_capabilities(None).tools_list_changed)

    def test_capability_property_exposed(self):
        # The gate reads client.capabilities.tools_list_changed — confirm the
        # client exposes it (parsed at connect from the nested listChanged).
        from src.services.mcp.client import McpClient
        c = McpClient()
        self.assertTrue(hasattr(c.capabilities, "tools_list_changed"))

    def test_agent_server_wiring_gates_on_capability(self):
        # Source-level guard: the REAL wiring in agent_server must gate
        # set_notification_handler on tools_list_changed (so this can't
        # silently regress to unconditional wiring). Mirrors the ch10
        # sweeper-ordering guard approach.
        import inspect
        import re
        from src.server import agent_server

        src = inspect.getsource(agent_server)
        # The set_notification_handler call must be preceded by a
        # tools_list_changed capability check within the same wiring block.
        idx = src.index("set_notification_handler")
        window = src[max(0, idx - 400):idx]
        self.assertIn("tools_list_changed", window,
                      "notification handler wiring must be capability-gated")


if __name__ == "__main__":
    unittest.main()
