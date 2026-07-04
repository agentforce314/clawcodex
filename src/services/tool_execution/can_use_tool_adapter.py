"""Shared can_use_tool adapter — ch07 round-3 G2.

Packages the production permission semantics that ``registry.dispatch``
implements inline (``registry.py:126-175``) as the ``can_use_tool``
callable ``run_tool_use`` expects, so the orchestrator lane and any
future caller resolve permissions identically:

    has_permissions_to_use_tool -> allow / deny / ask
    ask -> handle_permission_ask(context.permission_handler)
           (fail-closed DENY when no handler)
    accepted "don't ask again" updates -> _apply_and_persist_updates

``userModified`` synthesis: ``registry.dispatch`` has no such concept;
``run_tool_use`` reads ``permission_decision.get("userModified")`` and
sets ``context.user_modified``. Rule (TS-parity groundwork): True iff the
user accepted "don't ask again" updates OR the dialog returned a fresh
``updated_input``. NOTE: zero ``context.user_modified`` readers exist
today — the future consumer is dialog-modified-input display semantics;
the rule is pinned now so it is already correct when it becomes
load-bearing.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def build_can_use_tool(context: Any) -> Callable[..., dict[str, Any]]:
    """Build a ``can_use_tool`` callable bound to ``context``.

    The returned callable matches ``resolve_hook_permission_decision``'s
    invocation shape: ``(tool, tool_input, tool_use_context,
    assistant_message, tool_use_id) -> dict``.
    """

    def can_use_tool(
        tool: Any,
        tool_input: dict[str, Any],
        _tool_use_context: Any,
        _assistant_message: Any,
        tool_use_id: str,
        force_decision: Any = None,
    ) -> dict[str, Any]:
        # ch06 round-4 PR-A GAP A — the 6th ``force_decision`` param
        # (TS CanUseToolFn / toolHooks.ts:482-499). The hook-"ask" branch
        # of resolve_hook_permission_decision passes the hook result here
        # positionally; before this param existed the call raised TypeError
        # and fell through to a FAIL-OPEN allow. A PreToolUse hook that
        # already decided allow/deny short-circuits to that behavior; an
        # "ask" (or None) falls through to the normal prompt resolution.
        from src.permissions.check import has_permissions_to_use_tool
        from src.permissions.handler import handle_permission_ask
        from src.tool_system.registry import _apply_and_persist_updates

        if isinstance(force_decision, dict):
            forced = force_decision.get("behavior")
            forced_updated = (
                force_decision.get("updatedInput")
                or force_decision.get("input")
            )
            if forced == "allow":
                return {
                    "behavior": "allow",
                    "updatedInput": forced_updated,
                    "userModified": bool(forced_updated),
                }
            if forced == "deny":
                return {
                    "behavior": "deny",
                    "message": force_decision.get("message")
                    or "permission denied by hook",
                }
            if forced == "ask":
                # critic M2 — a hook that returns `ask` must reach the
                # PROMPT, NOT rule resolution. TS routes forceDecision
                # straight to `case "ask"` (useCanUseTool.tsx:37,93) and
                # never consults rules — else a static allow-rule would
                # silently bypass a hook explicitly demanding confirmation
                # (defense-in-depth defeated). We synthesize the ask
                # decision directly from the hook and prompt on it; NO
                # has_permissions_to_use_tool call. Fail-closed to deny
                # when there's no handler (mirrors the no-hook branch). m1
                # — honor the hook's updatedInput for the prompt.
                from src.permissions.types import (
                    HookDecisionReason,
                    PermissionAskDecision,
                )

                ask_input = forced_updated or tool_input
                ask_decision = PermissionAskDecision(
                    behavior="ask",
                    message=force_decision.get("message")
                    or f"Hook requires approval for {tool.name}",
                    decision_reason=HookDecisionReason(
                        reason="PreToolUse hook returned 'ask'",
                    ),
                )
                final, chosen_updates = handle_permission_ask(
                    tool.name, ask_decision, context.permission_handler,
                    tool_input=ask_input,
                    context=context, tool_use_id=tool_use_id,
                )
                if getattr(final, "behavior", "deny") != "allow":
                    return {
                        "behavior": "deny",
                        "message": getattr(final, "message", None)
                        or "permission denied by user",
                    }
                if chosen_updates:
                    _apply_and_persist_updates(context, chosen_updates)
                return {
                    "behavior": "allow",
                    "updatedInput": getattr(final, "updated_input", None)
                    or forced_updated,
                    "userModified": bool(chosen_updates)
                    or forced_updated is not None,
                }

        decision = has_permissions_to_use_tool(
            tool,
            tool_input,
            context.permission_context,
            tool_use_context=context,
        )

        if decision.behavior == "deny":
            return {
                "behavior": "deny",
                "message": getattr(decision, "message", None)
                or "permission denied",
            }

        if decision.behavior == "ask":
            final, chosen_updates = handle_permission_ask(
                tool.name,
                decision,
                context.permission_handler,
                tool_input=tool_input,
                context=context,
                tool_use_id=tool_use_id,
            )
            if final.behavior == "deny":
                return {
                    "behavior": "deny",
                    "message": getattr(final, "message", None)
                    or "permission denied by user",
                }
            if chosen_updates:
                _apply_and_persist_updates(context, chosen_updates)
            final_updated = getattr(final, "updated_input", None)
            return {
                "behavior": "allow",
                "updatedInput": final_updated
                or getattr(decision, "updated_input", None),
                "userModified": bool(chosen_updates)
                or final_updated is not None,
            }

        return {
            "behavior": "allow",
            "updatedInput": getattr(decision, "updated_input", None),
            "userModified": False,
        }

    return can_use_tool
