"""Bridge between an ``ask`` decision and an interactive surface.

C1 (components-folder parity) replaced the legacy 3-arg callback
``(tool_name, message, suggestion_str) -> (allowed, enable)`` with the
request/reply shape mirroring TS ``PermissionResult``: the surface
receives the full :class:`PermissionAskRequest` (tool input drives
per-tool previews; suggestions drive the "always allow" option) and
answers with a :class:`PermissionAskReply` (which may carry the chosen
"don't ask again" updates and deny feedback).
"""

from __future__ import annotations

from typing import Any

from .types import (
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionAskHandler,
    PermissionAskRequest,
    PermissionDecision,
    PermissionDenyDecision,
    PermissionUpdate,
)


def handle_permission_ask(
    tool_name: str,
    decision: PermissionAskDecision,
    handler: PermissionAskHandler | None = None,
    tool_input: dict[str, Any] | None = None,
) -> tuple[PermissionDecision, tuple[PermissionUpdate, ...]]:
    """Resolve an ``ask`` decision through ``handler``.

    Returns ``(final_decision, chosen_updates)``. ``chosen_updates`` are
    the "don't ask again" rules the user accepted (empty on deny / plain
    allow); the caller applies them to the live context and persists the
    persistable destinations — mirroring how TS applies
    ``updatedPermissions`` from the dialog's ``PermissionResult``.
    """

    if handler is None:
        return (
            PermissionDenyDecision(
                behavior="deny",
                message=decision.message
                or f"Permission required but no handler available for {tool_name}",
                decision_reason=decision.decision_reason,
            ),
            (),
        )

    request = PermissionAskRequest(
        tool_name=tool_name,
        message=decision.message or f"Tool '{tool_name}' requires permission",
        tool_input=tool_input,
        suggestions=tuple(decision.suggestions or ()),
        decision_reason=decision.decision_reason,
    )
    reply = handler(request)

    if reply.behavior == "allow":
        return (
            PermissionAllowDecision(
                behavior="allow",
                updated_input=reply.updated_input or decision.updated_input,
                decision_reason=decision.decision_reason,
            ),
            tuple(reply.chosen_updates or ()),
        )

    feedback = (reply.message or "").strip()
    message = "Permission denied by user."
    if feedback:
        # TS deny-with-feedback: the user's note reaches the model via the
        # tool error so it can change course.
        message = f"Permission denied by user: {feedback}"
    return (
        PermissionDenyDecision(
            behavior="deny",
            message=message,
            decision_reason=decision.decision_reason,
        ),
        (),
    )


__all__ = ["handle_permission_ask"]
