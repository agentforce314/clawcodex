from __future__ import annotations

from typing import Any, Callable

from .types import (
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionDecision,
    PermissionDenyDecision,
    PermissionUpdate,
)


PermissionHandlerCallback = Callable[
    [str, str, list[PermissionUpdate] | None],
    tuple[bool, dict[str, Any] | None],
]


def handle_permission_ask(
    tool_name: str,
    decision: PermissionAskDecision,
    handler: PermissionHandlerCallback | None = None,
) -> PermissionDecision:
    if handler is None:
        return PermissionDenyDecision(
            behavior="deny",
            message=decision.message or f"Permission required but no handler available for {tool_name}",
            decision_reason=decision.decision_reason,
        )

    allowed, updated_input = handler(
        tool_name,
        decision.message or f"Tool '{tool_name}' requires permission",
        decision.suggestions,
    )

    if allowed:
        return PermissionAllowDecision(
            behavior="allow",
            updated_input=updated_input or decision.updated_input,
            decision_reason=decision.decision_reason,
        )
    return PermissionDenyDecision(
        behavior="deny",
        message="Permission denied by user.",
        decision_reason=decision.decision_reason,
    )
