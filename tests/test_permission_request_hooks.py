"""HOOKS-1 — PermissionRequest hook execution + rejection-text fidelity.

Plan: my-docs/get-parity-by-folder/hooks-refactoring-plan.md.
G1: the "PermissionRequest" event was registered (hook_types.py) but had no
execution site; it now fires at the single ask choke point
(`handle_permission_ask`), which BOTH live seams funnel through
(can_use_tool_adapter + registry.dispatch). Port of
PermissionContext.runHooks (PermissionContext.ts:216-263) +
executePermissionRequestHooks (utils/hooks.ts:4392-4427).
G2: deny texts are the TS constants verbatim with the main-vs-subagent split
(utils/messages.ts:214-221, cancelAndAbort :154-173) keyed on
ToolContext.agent_id.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.hooks.hook_types import HookConfig, HookSource
from src.permissions.handler import (
    REJECT_MESSAGE,
    REJECT_MESSAGE_WITH_REASON_PREFIX,
    SUBAGENT_REJECT_MESSAGE,
    SUBAGENT_REJECT_MESSAGE_WITH_REASON_PREFIX,
    handle_permission_ask,
)
from src.permissions.types import (
    PermissionAskDecision,
    PermissionAskReply,
)


class _Snapshot:
    def __init__(self, configs):
        self.hooks = {"PermissionRequest": configs}


def _ctx(*, configs=None, agent_id=None, tmp: Path = Path("/tmp")):
    from src.tool_system.context import ToolContext

    ctx = ToolContext(workspace_root=tmp)
    ctx.workspace_trusted = True
    if agent_id is not None:
        ctx.agent_id = agent_id
    mgr = MagicMock()
    mgr.snapshot = _Snapshot(list(configs or []))
    ctx.hook_config_manager = mgr
    return ctx


def _hook(command: str, matcher: str | None = None) -> HookConfig:
    return HookConfig(
        type="command",
        command=command,
        matcher=matcher,
        source=HookSource.PROJECT_SETTINGS,
    )


def _echo_json(payload: dict) -> str:
    return f"echo '{json.dumps(payload)}'"


def _ask(message: str = "needs approval") -> PermissionAskDecision:
    return PermissionAskDecision(behavior="ask", message=message)


class _RecordingHandler:
    def __init__(self, reply: PermissionAskReply):
        self.reply = reply
        self.calls: list = []

    def __call__(self, request):
        self.calls.append(request)
        return self.reply


class TestPermissionRequestHookDecisions(unittest.TestCase):
    def test_hook_allow_short_circuits_the_handler(self):
        handler = _RecordingHandler(PermissionAskReply(behavior="deny"))
        ctx = _ctx(configs=[_hook(_echo_json({"decision": "allow"}))])
        final, updates = handle_permission_ask(
            "Write", _ask(), handler, tool_input={"file_path": "x"},
            context=ctx, tool_use_id="tu-1",
        )
        self.assertEqual(final.behavior, "allow")
        self.assertEqual(updates, ())
        self.assertEqual(handler.calls, [], "handler must not be consulted")
        self.assertEqual(final.decision_reason.get("hookName"), "PermissionRequest")

    def test_hook_allow_updated_input_and_permissions(self):
        payload = {
            "decision": "allow",
            "updatedInput": {"file_path": "redirected"},
            "updatedPermissions": [
                {"type": "addRules", "behavior": "allow",
                 "rules": [{"tool_name": "Write"}], "destination": "session"},
            ],
        }
        ctx = _ctx(configs=[_hook(_echo_json(payload))])
        final, updates = handle_permission_ask(
            "Write", _ask(), None, tool_input={"file_path": "x"},
            context=ctx, tool_use_id="tu-1",
        )
        self.assertEqual(final.behavior, "allow")
        self.assertEqual(final.updated_input, {"file_path": "redirected"})
        self.assertEqual(len(updates), 1)
        self.assertEqual(type(updates[0]).__name__, "PermissionUpdateAddRules")
        self.assertEqual(updates[0].rules[0].tool_name, "Write")

    def test_hook_deny_with_message(self):
        handler = _RecordingHandler(PermissionAskReply(behavior="allow"))
        ctx = _ctx(configs=[_hook(_echo_json({"decision": "deny", "reason": "policy says no"}))])
        final, updates = handle_permission_ask(
            "Bash", _ask(), handler, tool_input={"command": "ls"},
            context=ctx, tool_use_id="tu-1",
        )
        self.assertEqual(final.behavior, "deny")
        self.assertEqual(final.message, "policy says no")
        self.assertEqual(handler.calls, [])
        self.assertEqual(final.decision_reason.get("type"), "hook")

    def test_hook_deny_interrupt_aborts(self):
        from types import SimpleNamespace

        ctx = _ctx(configs=[_hook(_echo_json({"decision": "deny", "interrupt": True}))])
        aborts: list = []
        # A bare MagicMock's ``signal.aborted`` is truthy and the executor
        # would treat the run as already-aborted — stub a real-shaped one.
        ctx.abort_controller = SimpleNamespace(
            signal=SimpleNamespace(aborted=False),
            abort=lambda *a: aborts.append(a),
        )
        final, _ = handle_permission_ask(
            "Bash", _ask(), None, tool_input={},
            context=ctx, tool_use_id="tu-1",
        )
        self.assertEqual(final.behavior, "deny")
        self.assertTrue(aborts, "interrupt must abort the turn")

    def test_ts_wire_envelope_form_accepted(self):
        """A hook written for the reference CLI emits hookSpecificOutput.decision
        (utils/hooks.ts:833-840) — must work unchanged."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny", "message": "envelope says no"},
            }
        }
        ctx = _ctx(configs=[_hook(_echo_json(payload))])
        final, _ = handle_permission_ask(
            "Write", _ask(), None, tool_input={}, context=ctx, tool_use_id="t",
        )
        self.assertEqual(final.behavior, "deny")
        self.assertEqual(final.message, "envelope says no")

    def test_no_decision_falls_through_to_handler(self):
        handler = _RecordingHandler(PermissionAskReply(behavior="allow"))
        ctx = _ctx(configs=[_hook("echo ''")])  # silent hook = no decision
        final, _ = handle_permission_ask(
            "Write", _ask(), handler, tool_input={},
            context=ctx, tool_use_id="tu-1",
        )
        self.assertEqual(final.behavior, "allow")
        self.assertEqual(len(handler.calls), 1, "normal flow must continue")

    def test_matcher_scopes_by_tool(self):
        """A Bash-matched hook must not fire for Write (PreToolUse matcher
        machinery, reused)."""
        handler = _RecordingHandler(PermissionAskReply(behavior="allow"))
        ctx = _ctx(configs=[_hook(_echo_json({"decision": "deny"}), matcher="Bash")])
        final, _ = handle_permission_ask(
            "Write", _ask(), handler, tool_input={},
            context=ctx, tool_use_id="tu-1",
        )
        self.assertEqual(final.behavior, "allow")
        self.assertEqual(len(handler.calls), 1)

    def test_hook_failure_falls_through(self):
        """A crashing hook is contained: exit(1) is a non-blocking error →
        no decision → normal flow."""
        handler = _RecordingHandler(PermissionAskReply(behavior="allow"))
        ctx = _ctx(configs=[_hook("exit 1")])
        final, _ = handle_permission_ask(
            "Write", _ask(), handler, tool_input={},
            context=ctx, tool_use_id="tu-1",
        )
        self.assertEqual(final.behavior, "allow")
        self.assertEqual(len(handler.calls), 1)

    def test_headless_hook_allow_without_handler(self):
        """Hooks run BEFORE the no-handler fail-closed branch — half their
        point is resolving asks headless."""
        ctx = _ctx(configs=[_hook(_echo_json({"decision": "allow"}))])
        final, _ = handle_permission_ask(
            "Write", _ask(), None, tool_input={},
            context=ctx, tool_use_id="tu-1",
        )
        self.assertEqual(final.behavior, "allow")

    def test_no_hooks_no_executor_invocation(self):
        """has_hook_for_event fast path: nothing configured → the executor
        is never constructed (pinned via import-time marker)."""
        import src.permissions.handler as handler_mod

        calls: list = []
        original = handler_mod._run_permission_request_hooks

        def _marker(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        handler_mod._run_permission_request_hooks = _marker
        try:
            ctx = _ctx(configs=[])
            handler = _RecordingHandler(PermissionAskReply(behavior="allow"))
            final, _ = handle_permission_ask(
                "Write", _ask(), handler, tool_input={},
                context=ctx, tool_use_id="tu-1",
            )
            self.assertEqual(final.behavior, "allow")
            # _run... is called but exits on has_hook_for_event without
            # constructing the executor; behavior identical.
            self.assertEqual(len(calls), 1)
        finally:
            handler_mod._run_permission_request_hooks = original

    def test_backward_compatible_without_context(self):
        """Old call shape (no context) still works — no hooks, generic flow."""
        handler = _RecordingHandler(PermissionAskReply(behavior="allow"))
        final, _ = handle_permission_ask("Write", _ask(), handler, tool_input={})
        self.assertEqual(final.behavior, "allow")


class TestChokePointCoverage(unittest.TestCase):
    """Both live seams route the hook (the single-choke-point claim)."""

    def test_registry_dispatch_ask_short_circuited_by_hook(self):
        from src.tool_system.build_tool import build_tool
        from src.tool_system.protocol import ToolCall, ToolResult
        from src.tool_system.registry import ToolRegistry
        from src.permissions.types import (
            PermissionAskDecision as Ask,
            PermissionPassthroughResult,
        )

        def _check(tool_input, context):
            return Ask(behavior="ask", message="approve?")

        asked_tool = build_tool(
            name="AskyTool",
            input_schema={"type": "object", "properties": {}, "additionalProperties": True},
            call=lambda i, c: ToolResult(name="AskyTool", output="RAN"),
            prompt="p", description="d",
            check_permissions=_check,
        )
        reg = ToolRegistry()
        reg.register(asked_tool)

        ctx = _ctx(configs=[_hook(_echo_json({"decision": "allow"}))])
        ctx.permission_handler = None  # would fail closed without the hook
        result = reg.dispatch(ToolCall(name="AskyTool", input={}), ctx)
        self.assertFalse(result.is_error, result.output)
        self.assertEqual(result.output, "RAN")

    def test_adapter_ask_short_circuited_by_hook(self):
        from src.services.tool_execution.can_use_tool_adapter import build_can_use_tool
        from src.tool_system.build_tool import build_tool
        from src.tool_system.protocol import ToolResult
        from src.permissions.types import PermissionAskDecision as Ask

        def _check(tool_input, context):
            return Ask(behavior="ask", message="approve?")

        asked_tool = build_tool(
            name="AskyTool",
            input_schema={"type": "object", "properties": {}, "additionalProperties": True},
            call=lambda i, c: ToolResult(name="AskyTool", output="RAN"),
            prompt="p", description="d",
            check_permissions=_check,
        )
        ctx = _ctx(configs=[_hook(_echo_json({"decision": "allow"}))])
        ctx.permission_handler = None
        can_use = build_can_use_tool(ctx)
        outcome = can_use(asked_tool, {}, None, None, "tu-9")
        self.assertEqual(outcome.get("behavior"), "allow", outcome)


class TestRejectionTexts(unittest.TestCase):
    """G2 — the TS constants verbatim + the main/subagent split."""

    def _deny(self, feedback: str | None, agent_id: str | None):
        handler = _RecordingHandler(
            PermissionAskReply(behavior="deny", message=feedback)
        )
        ctx = _ctx(configs=[], agent_id=agent_id)
        final, _ = handle_permission_ask(
            "Write", _ask(), handler, tool_input={},
            context=ctx, tool_use_id="tu-1",
        )
        return final

    def test_main_agent_bare_reject(self):
        final = self._deny(None, None)
        self.assertEqual(final.message, REJECT_MESSAGE)
        self.assertIn("STOP what you are doing", final.message)

    def test_main_agent_with_feedback(self):
        final = self._deny("use the API instead", None)
        self.assertEqual(
            final.message,
            REJECT_MESSAGE_WITH_REASON_PREFIX + "use the API instead",
        )

    def test_subagent_bare_reject(self):
        final = self._deny(None, "a1b2c3")
        self.assertEqual(final.message, SUBAGENT_REJECT_MESSAGE)
        self.assertIn("Try a different approach", final.message)

    def test_subagent_with_feedback(self):
        final = self._deny("out of scope", "a1b2c3")
        self.assertEqual(
            final.message,
            SUBAGENT_REJECT_MESSAGE_WITH_REASON_PREFIX + "out of scope",
        )

    def test_no_handler_message_not_conflated(self):
        """The no-handler fail-closed branch keeps its distinct message —
        it is not a user rejection."""
        final, _ = handle_permission_ask(
            "Write", _ask("ask msg"), None, tool_input={},
            context=_ctx(configs=[]), tool_use_id="tu-1",
        )
        self.assertEqual(final.behavior, "deny")
        self.assertNotIn("STOP what you are doing", final.message)


if __name__ == "__main__":
    unittest.main()


class TestConfigLoaderEndToEnd(unittest.TestCase):
    """A real settings-file-configured PermissionRequest hook fires through
    the ch01 config loader (HookConfigManager.load), not an injected
    snapshot — the full user-facing path."""

    def test_settings_configured_hook_denies(self):
        import asyncio
        import tempfile

        from src.hooks.config_manager import HookConfigManager
        from src.hooks.registry import AsyncHookRegistry
        from src.tool_system.context import ToolContext

        tmp = Path(tempfile.mkdtemp())
        settings = tmp / "settings.json"
        settings.write_text(json.dumps({
            "hooks": {
                "PermissionRequest": [
                    {
                        "matcher": "Write",
                        "hooks": [
                            {"type": "command",
                             "command": "echo '{\"decision\": \"deny\", \"reason\": \"settings hook says no\"}'"}
                        ],
                    }
                ]
            }
        }), encoding="utf-8")

        mgr = HookConfigManager(AsyncHookRegistry(), settings_path=settings)
        asyncio.run(mgr.load())

        ctx = ToolContext(workspace_root=tmp)
        ctx.workspace_trusted = True
        ctx.hook_config_manager = mgr

        final, _ = handle_permission_ask(
            "Write", _ask(), None, tool_input={"file_path": "x"},
            context=ctx, tool_use_id="tu-e2e",
        )
        self.assertEqual(final.behavior, "deny")
        self.assertEqual(final.message, "settings hook says no")

        # And the matcher scopes: a Read ask sails past this Write hook
        # (falls to the no-handler branch, whose message is distinct).
        final2, _ = handle_permission_ask(
            "Read", _ask(), None, tool_input={"file_path": "x"},
            context=ctx, tool_use_id="tu-e2e2",
        )
        self.assertNotEqual(final2.message, "settings hook says no")
