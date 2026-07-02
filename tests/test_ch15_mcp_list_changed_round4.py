"""ch15 round-4 — MCP tools/list_changed dynamic refresh.

Covers my-docs/port-improvement-round-4/ch15-mcp-round4-plan.md: the structural
notification-dispatch hole (client._receive_loop dropped ALL notifications),
the listChanged capability, the registry swap seam, and the McpRuntime refresh.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from src.services.mcp.client import McpClient
from src.services.mcp.types import ServerCapabilities


class _Msg:
    def __init__(self, method=None, id=None, params=None, result=None, error=None):
        self.method = method
        self.id = id
        self.params = params
        self.result = result
        self.error = error


class TestClientNotificationDispatch(unittest.TestCase):
    def test_notification_routes_to_handler(self):
        client = McpClient()
        seen = []
        client.set_notification_handler(lambda m, p: seen.append((m, p)))
        client._dispatch_notification(
            _Msg(method="notifications/tools/list_changed", params={"x": 1})
        )
        self.assertEqual(seen, [("notifications/tools/list_changed", {"x": 1})])

    def test_no_handler_is_safe(self):
        client = McpClient()
        # No handler registered → dropped, no crash (back-compat).
        client._dispatch_notification(_Msg(method="notifications/tools/list_changed"))

    def test_bad_handler_does_not_raise(self):
        client = McpClient()

        def _boom(m, p):
            raise RuntimeError("bad")

        client.set_notification_handler(_boom)
        # Must not propagate — a bad handler can't kill the receive loop.
        client._dispatch_notification(_Msg(method="notifications/tools/list_changed"))

    def test_receive_loop_dispatches_notification(self):
        # The structural fix: a notification (method set, id None) reaches the
        # handler instead of being silently dropped.
        client = McpClient()
        seen = []
        client.set_notification_handler(lambda m, p: seen.append(m))

        msgs = [
            _Msg(method="notifications/tools/list_changed", params={}),
            None,  # then transport closes → loop exits
        ]

        transport = MagicMock()
        transport.is_connected = True

        async def _recv():
            return msgs.pop(0)

        transport.receive = _recv
        client._transport = transport
        asyncio.run(client._receive_loop())
        self.assertIn("notifications/tools/list_changed", seen)


class TestListChangedCapability(unittest.TestCase):
    def test_nested_listchanged_preserved(self):
        caps = ServerCapabilities(tools=True, tools_list_changed=True)
        self.assertTrue(caps.tools_list_changed)

    def test_default_false(self):
        self.assertFalse(ServerCapabilities().tools_list_changed)


class TestRegistryUnregister(unittest.TestCase):
    def _reg(self):
        from src.tool_system.registry import ToolRegistry
        return ToolRegistry()

    def _tool(self, name, aliases=()):
        t = MagicMock()
        t.name = name
        t.aliases = list(aliases)
        return t

    def test_unregister_removes_tool_and_aliases(self):
        reg = self._reg()
        t = self._tool("mcp__srv__do", aliases=["do_alias"])
        reg.register(t)
        self.assertIsNotNone(reg.get("mcp__srv__do"))
        self.assertIsNotNone(reg.get("do_alias"))
        self.assertTrue(reg.remove_tool("mcp__srv__do"))
        self.assertIsNone(reg.get("mcp__srv__do"))
        self.assertIsNone(reg.get("do_alias"))
        # Idempotent / absent-safe.
        self.assertFalse(reg.remove_tool("mcp__srv__do"))

    def test_reregister_after_remove(self):
        reg = self._reg()
        reg.register(self._tool("mcp__srv__do"))
        reg.remove_tool("mcp__srv__do")
        # No duplicate error now that it was removed.
        reg.register(self._tool("mcp__srv__do"))
        self.assertIsNotNone(reg.get("mcp__srv__do"))


class TestRuntimeApplyRefreshedTools(unittest.TestCase):
    def _rt(self):
        from src.server.mcp_runtime import McpRuntime
        rt = McpRuntime()
        # _wrap builds a real sync Tool; give it a trivial loop-less stub via
        # a fake that just returns an object with a .name.
        rt._wrap = lambda server, mt, client: _wrapped(server, mt.name)
        return rt

    def test_swap_returns_removed_full_and_new(self):
        rt = self._rt()
        rt.tools = [_wrapped("srv", "old_a"), _wrapped("srv", "old_b"),
                    _wrapped("other", "keep")]
        rt.servers = {"srv": ["old_a", "old_b"], "other": ["keep"]}

        new_raw = [_raw("new_x"), _raw("new_y")]
        removed, new_tools = rt._apply_refreshed_tools("srv", new_raw, MagicMock())

        self.assertEqual(sorted(removed),
                         ["mcp__srv__old_a", "mcp__srv__old_b"])
        self.assertEqual([t.name for t in new_tools],
                         ["mcp__srv__new_x", "mcp__srv__new_y"])
        # self.tools: other server's tool kept, srv's swapped.
        names = sorted(t.name for t in rt.tools)
        self.assertEqual(names, ["mcp__other__keep",
                                 "mcp__srv__new_x", "mcp__srv__new_y"])
        self.assertEqual(rt.servers["srv"], ["new_x", "new_y"])


class TestNotificationHandlerFactory(unittest.TestCase):
    def test_list_changed_schedules_refresh_and_swaps_registry(self):
        from src.server.agent_server import _make_mcp_notification_handler
        from src.tool_system.registry import ToolRegistry

        reg = ToolRegistry()
        old = MagicMock(); old.name = "mcp__srv__old"; old.aliases = []
        reg.register(old)

        # critic M1: the handler resolves sess.tool_registry at refresh time
        # (switch-safe), not a boot-time registry.
        sess = MagicMock()
        sess.tool_registry = reg

        rt = MagicMock()
        # Capture what schedule_tool_refresh is asked to do, then drive on_change.
        captured = {}
        rt.schedule_tool_refresh = lambda name, on_change: captured.update(
            name=name, on_change=on_change)

        handler = _make_mcp_notification_handler(rt, sess, "srv")
        # A non-list_changed notification is ignored.
        handler("notifications/resources/updated", {})
        self.assertEqual(captured, {})
        # list_changed schedules the refresh for this server.
        handler("notifications/tools/list_changed", {})
        self.assertEqual(captured["name"], "srv")

        # Drive the on_change callback → registry swap.
        new = MagicMock(); new.name = "mcp__srv__new"; new.aliases = []
        captured["on_change"](["mcp__srv__old"], [new])
        self.assertIsNone(reg.get("mcp__srv__old"))
        self.assertIsNotNone(reg.get("mcp__srv__new"))

    def test_refresh_follows_a_switched_registry(self):
        # critic M1: after a provider switch rebinds sess.tool_registry to a
        # NEW registry, the refresh must target the new one, not the boot one.
        from src.server.agent_server import _make_mcp_notification_handler
        from src.tool_system.registry import ToolRegistry

        boot_reg = ToolRegistry()
        new_reg = ToolRegistry()
        sess = MagicMock()
        sess.tool_registry = boot_reg

        captured = {}
        rt = MagicMock()
        rt.schedule_tool_refresh = lambda name, on_change: captured.update(
            on_change=on_change)
        handler = _make_mcp_notification_handler(rt, sess, "srv")
        handler("notifications/tools/list_changed", {})

        # Simulate a provider switch: sess now points at a NEW registry.
        sess.tool_registry = new_reg
        t = MagicMock(); t.name = "mcp__srv__x"; t.aliases = []
        captured["on_change"]([], [t])
        # The tool landed in the CURRENT (switched) registry, not the boot one.
        self.assertIsNotNone(new_reg.get("mcp__srv__x"))
        self.assertIsNone(boot_reg.get("mcp__srv__x"))


def _wrapped(server, tool_name):
    from src.services.mcp.mcp_string_utils import build_mcp_tool_name
    t = MagicMock()
    t.name = build_mcp_tool_name(server, tool_name)
    return t


def _raw(name):
    r = MagicMock()
    r.name = name
    return r


if __name__ == "__main__":
    unittest.main()
