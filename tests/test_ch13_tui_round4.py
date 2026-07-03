"""ch13 round-4 acceptance tests (Python backend half): the agent-server
permission bridge forwards suggestions and reads chosen_updates so "always
allow" persists.

Covers my-docs/port-improvement-round-4/ch13-terminal-ui-round4-plan.md.
The ui-tui client half is tested by ui-tui/src/__tests__/gatewayClient.test.ts.
"""
from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock

from src.server.agent_server import (
    _deserialize_permission_update,
    _serialize_permission_update,
    _session_option_label_safe,
)


class TestSessionOptionLabel(unittest.TestCase):
    """R6 — the can_use_tool request carries the authoritative per-tool label
    so the box states the real grant scope (not a generic "for <tool>")."""

    def test_file_edit_label(self):
        from src.permissions.updates import default_session_suggestions

        req = MagicMock()
        req.suggestions = default_session_suggestions("Write", {"file_path": "/a/b"})
        req.tool_name = "Write"
        req.tool_input = {"file_path": "/a/b"}
        # A file edit grants session accept-edits mode, not a per-Write rule.
        self.assertEqual(
            _session_option_label_safe(req), "allow all edits during this session"
        )

    def test_bash_label_and_safe_on_garbage(self):
        from src.permissions.bash_suggestions import suggestions_for_bash_command

        req = MagicMock()
        req.suggestions = suggestions_for_bash_command("git status")
        req.tool_name = "Bash"
        req.tool_input = {"command": "git status"}
        label = _session_option_label_safe(req)
        self.assertIsNotNone(label)
        self.assertIn("git status", label)
        # Never raises — a malformed request yields None, not an exception.
        broken = MagicMock()
        broken.suggestions = object()  # not iterable
        self.assertIsNone(_session_option_label_safe(broken))


class TestPermissionUpdateWire(unittest.TestCase):
    def test_addrules_roundtrip(self):
        from src.permissions.bash_suggestions import suggestion_for_prefix

        s = suggestion_for_prefix("ls")[0]
        wire = _serialize_permission_update(s)
        self.assertEqual(wire["type"], "addRules")
        self.assertEqual(wire["destination"], "localSettings")
        self.assertEqual(wire["rules"][0]["tool_name"], "Bash")
        self.assertEqual(wire["rules"][0]["rule_content"], "ls:*")

        back = _deserialize_permission_update(wire)
        self.assertEqual(back.type, "addRules")
        self.assertEqual(back.destination, "localSettings")
        self.assertEqual(back.rules[0].tool_name, "Bash")
        self.assertEqual(back.rules[0].rule_content, "ls:*")

    def test_session_destination_override(self):
        # The client sends destination=session for "Allow this session".
        wire = {"type": "addRules", "destination": "session",
                "behavior": "allow",
                "rules": [{"tool_name": "Bash", "rule_content": "ls:*"}]}
        back = _deserialize_permission_update(wire)
        self.assertEqual(back.destination, "session")

    def test_unknown_type_returns_none(self):
        self.assertIsNone(_deserialize_permission_update({"type": "bogus"}))


class TestPermissionHandlerBridge(unittest.TestCase):
    """The handler forwards suggestions in the control_request and reads
    chosen_updates from the reply."""

    def _session(self):
        from src.server.agent_server import AgentServerConfig, _AgentSession

        emitted = []
        sess = _AgentSession(
            session_id="s1", cwd="/tmp",
            config=AgentServerConfig(single_session=True, permission_timeout_s=2.0),
            loop=MagicMock(), out_queue=MagicMock(),
        )
        sess._emit = lambda env: emitted.append(env)
        return sess, emitted

    def test_suggestions_forwarded_and_chosen_updates_read(self):
        from src.permissions.bash_suggestions import suggestion_for_prefix
        from src.permissions.types import PermissionAskRequest

        sess, emitted = self._session()
        suggestions = tuple(suggestion_for_prefix("ls"))
        request = PermissionAskRequest(
            tool_name="Bash", message="Allow Bash?",
            tool_input={"command": "ls"}, suggestions=suggestions,
        )

        # Drive the handler on a thread; simulate the client control_response.
        reply_holder = {}

        def _run():
            reply_holder["reply"] = sess.permission_handler(request)

        t = threading.Thread(target=_run)
        t.start()
        # Wait for the control_request to be emitted, then reply.
        import time
        deadline = time.time() + 2
        req_id = None
        while time.time() < deadline:
            for env in emitted:
                if env.get("type") == "control_request":
                    req_id = env["request_id"]
                    # The suggestions crossed the wire.
                    sent = env["request"].get("suggestions")
                    self.assertTrue(sent)
                    self.assertEqual(sent[0]["rules"][0]["rule_content"], "ls:*")
                    break
            if req_id:
                break
            time.sleep(0.02)
        self.assertIsNotNone(req_id, "control_request never emitted")

        # Simulate the TUI's "Always allow" control_response (the exact
        # envelope shape _resolve_permission consumes).
        sess._resolve_permission({
            "type": "control_response",
            "response": {
                "request_id": req_id,
                "response": {
                    "behavior": "allow",
                    "chosen_updates": [{
                        "type": "addRules", "destination": "localSettings",
                        "behavior": "allow",
                        "rules": [{"tool_name": "Bash", "rule_content": "ls:*"}],
                    }],
                },
            },
        })
        t.join(timeout=2)

        reply = reply_holder["reply"]
        self.assertEqual(reply.behavior, "allow")
        # The chosen rule was deserialized onto the reply → handle_permission_ask
        # / the adapter will persist it.
        self.assertEqual(len(reply.chosen_updates), 1)
        self.assertEqual(reply.chosen_updates[0].rules[0].rule_content, "ls:*")


class TestPermissionModeCycleGuard(unittest.TestCase):
    """critic B1 — shift+tab's cycle is server-computed and GUARDED: bypass
    is only reachable when is_bypass_permissions_mode_available."""

    def _session(self, *, mode, bypass_available):
        import asyncio

        from src.permissions.types import ToolPermissionContext
        from src.server.agent_server import AgentServerConfig, _AgentSession
        from src.tool_system.context import ToolContext

        sess = _AgentSession(
            session_id="s1", cwd="/tmp",
            config=AgentServerConfig(single_session=True),
            loop=MagicMock(), out_queue=MagicMock(),
        )
        ctx = ToolContext(workspace_root=__import__("pathlib").Path("/tmp"))
        ctx.permission_context = ToolPermissionContext(
            mode=mode, is_bypass_permissions_mode_available=bypass_available,
        )
        sess.tool_context = ctx
        replies = []
        sess._reply = lambda rid, payload: replies.append(payload)
        return sess, ctx, replies

    def _cycle(self, sess):
        import asyncio
        asyncio.run(sess._handle_control_request({
            "request_id": "r1",
            "request": {"subtype": "cycle_permission_mode"},
        }))

    def test_plan_without_bypass_goes_to_default(self):
        sess, ctx, replies = self._session(mode="plan", bypass_available=False)
        self._cycle(sess)
        self.assertEqual(replies[-1]["mode"], "default")
        self.assertEqual(ctx.permission_context.mode, "default")

    def test_plan_with_bypass_goes_to_bypass(self):
        sess, ctx, replies = self._session(mode="plan", bypass_available=True)
        self._cycle(sess)
        self.assertEqual(replies[-1]["mode"], "bypassPermissions")

    def test_default_goes_to_acceptedits(self):
        sess, ctx, replies = self._session(mode="default", bypass_available=False)
        self._cycle(sess)
        self.assertEqual(replies[-1]["mode"], "acceptEdits")


if __name__ == "__main__":
    unittest.main()
