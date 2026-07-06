"""Chapter C1 — PreToolUse ``hookSpecificOutput.permissionDecision``.

A PreToolUse hook emitting the DOCUMENTED structured form
(types/hooks.ts:73-78) parsed fine in the port but was silently ignored —
hook_executor read only the PermissionRequest ``decision`` envelope. These
execute REAL hook commands (echo the JSON) through ``_execute_command_hook``
and pin the TS mapping (utils/hooks.ts:726-800): permissionDecision
OVERRIDES the flat decision; deny's message = permissionDecisionReason ||
reason || "Blocked by hook"; unknown value → warn + drop; hso updatedInput /
additionalContext extracted.
"""
from __future__ import annotations

import asyncio
import json
import shlex

import pytest

from src.hooks.hook_executor import _execute_command_hook
from src.hooks.hook_types import HookConfig, HookSource


def _hook(payload: dict) -> HookConfig:
    return HookConfig(
        type="command",
        command=f"echo {shlex.quote(json.dumps(payload))}",
        source=HookSource.USER_SETTINGS,
    )


def _run(payload: dict):
    return asyncio.run(_execute_command_hook(_hook(payload), {"tool_name": "Bash"}))


def _pre(hso_extra: dict, **flat) -> dict:
    return {**flat, "hookSpecificOutput": {"hookEventName": "PreToolUse", **hso_extra}}


class TestPermissionDecision:
    def test_deny_with_reason(self):
        r = _run(_pre({"permissionDecision": "deny",
                       "permissionDecisionReason": "policy says no"}))
        assert r.permission_behavior == "deny"
        assert r.hook_permission_decision_reason == "policy says no"

    def test_allow(self):
        r = _run(_pre({"permissionDecision": "allow"}))
        assert r.permission_behavior == "allow"

    def test_ask(self):
        r = _run(_pre({"permissionDecision": "ask"}))
        assert r.permission_behavior == "ask"

    def test_overrides_flat_decision(self):
        # TS: "Override with more specific permission decision if provided" —
        # the opposite precedence of the PermissionRequest envelope.
        r = _run(_pre({"permissionDecision": "deny",
                       "permissionDecisionReason": "specific wins"},
                      decision="allow"))
        assert r.permission_behavior == "deny"
        assert r.hook_permission_decision_reason == "specific wins"

    def test_deny_reason_fallback_to_flat_reason(self):
        # permissionDecisionReason || reason || "Blocked by hook"
        r = _run(_pre({"permissionDecision": "deny"}, reason="flat reason"))
        assert r.hook_permission_decision_reason == "flat reason"
        r2 = _run(_pre({"permissionDecision": "deny"}))
        assert r2.hook_permission_decision_reason == "Blocked by hook"

    def test_unknown_value_warn_and_drop(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="src.hooks.hook_executor"):
            r = _run(_pre({"permissionDecision": "maybe"}))
        assert r.permission_behavior is None  # dropped, not honored
        assert any("permissionDecision" in m for m in caplog.messages)

    def test_updated_input_extracted(self):
        r = _run(_pre({"permissionDecision": "allow",
                       "updatedInput": {"command": "ls -la"}}))
        assert r.updated_input == {"command": "ls -la"}

    def test_reason_without_decision_still_recorded(self):
        # TS sets hookPermissionDecisionReason from the hso field in the
        # PreToolUse case (port: only when present/non-empty).
        r = _run(_pre({"permissionDecisionReason": "context only"}))
        assert r.hook_permission_decision_reason == "context only"
        assert r.permission_behavior is None


class TestAdditionalContext:
    def test_pretooluse_additional_context_appended(self):
        r = _run(_pre({"permissionDecision": "allow",
                       "additionalContext": "heads up"}))
        assert r.additional_contexts == ["heads up"]

    def test_userpromptsubmit_form_additional_context(self):
        r = _run({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                         "additionalContext": "from UPS"}})
        assert r.additional_contexts == ["from UPS"]

    def test_appends_after_flat_additional_contexts(self):
        r = _run(_pre({"additionalContext": "hso one"},
                      additionalContexts=["flat one"]))
        assert r.additional_contexts == ["flat one", "hso one"]


class TestUnregressed:
    def test_flat_decision_still_works(self):
        r = _run({"decision": "deny", "reason": "flat"})
        assert r.permission_behavior == "deny"
        assert r.hook_permission_decision_reason == "flat"

    def test_permissionrequest_envelope_still_fill_only(self):
        # the PermissionRequest dict envelope only fills when the flat form
        # is unset (unchanged behavior)
        r = _run({
            "decision": "allow",
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny", "message": "no"},
            },
        })
        assert r.permission_behavior == "allow"  # flat wins for the envelope


class TestEventNameGate:
    """critic C1-M1: the emitted hookSpecificOutput.hookEventName must match
    the RUNNING event, else the payload is dropped (TS throws + rejects,
    hooks.ts:757-765). Otherwise a PermissionRequest hook emitting the
    PreToolUse form would leak an `allow` into the grant — fail-open."""

    def _run_on_event(self, payload: dict, event: str):
        import asyncio
        from src.hooks.hook_executor import _execute_command_hook

        return asyncio.run(_execute_command_hook(
            _hook(payload), {"tool_name": "Bash", "hook_event": event}
        ))

    def test_wrong_event_permission_decision_dropped(self):
        # running PermissionRequest, emitting the PreToolUse allow form
        r = self._run_on_event(
            _pre({"permissionDecision": "allow"}), "PermissionRequest"
        )
        assert r.permission_behavior is None  # NOT granted (was fail-open)

    def test_wrong_event_additional_context_dropped(self):
        # m1: additionalContext over-extraction on a wrong-event form
        r = self._run_on_event(
            {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                    "additionalContext": "leak"}},
            "PostToolUse",
        )
        assert not r.additional_contexts

    def test_matching_event_still_works(self):
        r = self._run_on_event(_pre({"permissionDecision": "deny"}), "PreToolUse")
        assert r.permission_behavior == "deny"

    def test_no_running_event_skips_gate(self):
        # direct/test calls pass no hook_event → gate skipped (TS's
        # `if (expectedHookEvent && …)`); the unit tests above rely on this
        r = _run(_pre({"permissionDecision": "allow"}))
        assert r.permission_behavior == "allow"


class TestPreToolAdditionalContextReachesModel:
    """critic C1-M2: the pre-tool additionalContext must actually be YIELDED
    as a consumable attachment (not dropped by run_pre_tool_use_hooks/the
    tool_execution consumer)."""

    def test_additional_context_yielded_by_pre_tool_hooks(self, monkeypatch):
        # drive run_pre_tool_use_hooks with a real PreToolUse hook (via the
        # snapshot source has_hook_for_event/execute_pre_tool_hooks read) and
        # assert an additionalContext result carrying the attachment is
        # produced (the yield the tool_execution consumer branch handles).
        import asyncio
        from pathlib import Path
        from types import SimpleNamespace

        from src.hooks.hook_types import HookConfig, HookSource
        from src.tool_system.context import ToolContext, ToolUseOptions

        cfg = HookConfig(
            type="command",
            command=(
                "echo '{\"hookSpecificOutput\":{\"hookEventName\":"
                "\"PreToolUse\",\"additionalContext\":\"ctx-for-model\"}}'"
            ),
            source=HookSource.USER_SETTINGS,
            matcher="Bash",
        )
        monkeypatch.setattr(
            "src.hooks.hook_executor._get_hooks_from_snapshot",
            lambda ctx: {"PreToolUse": [cfg]},
        )
        # WI-0.2 trust gate skips non-policy hooks for an untrusted workspace
        monkeypatch.setattr(
            "src.hooks.hook_executor.should_skip_hook_due_to_trust",
            lambda ctx: False,
        )

        ctx = ToolContext(workspace_root=Path("/tmp"))
        ctx.options = ToolUseOptions(tools=[])
        tool = SimpleNamespace(name="Bash", is_mcp=False)

        from src.services.tool_execution.tool_hooks import run_pre_tool_use_hooks

        async def go():
            return [r async for r in run_pre_tool_use_hooks(ctx, tool, {"command": "ls"}, "tu-1")]

        results = asyncio.run(go())
        ac = [r for r in results if isinstance(r, dict) and r.get("type") == "additionalContext"]
        assert ac, f"no additionalContext yielded: {results}"
        assert "ctx-for-model" in str(ac[0])
