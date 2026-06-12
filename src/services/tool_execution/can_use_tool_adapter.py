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
        _tool_use_id: str,
    ) -> dict[str, Any]:
        from src.permissions.check import has_permissions_to_use_tool
        from src.permissions.handler import handle_permission_ask
        from src.tool_system.registry import _apply_and_persist_updates

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
