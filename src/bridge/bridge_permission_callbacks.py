"""Bridge permission callbacks Protocol.

Ports ``typescript/src/bridge/bridgePermissionCallbacks.ts``.

Defines the bridge-side surface for handling permission requests routed
from the server: a ``BridgePermissionResponse`` payload (``allow`` /
``deny`` with optional input rewrite + updated permissions), a type guard
for validating parsed wire payloads, and a ``BridgePermissionCallbacks``
Protocol bundling the send/receive surface that orchestrators wire to the
transport.

The richer ``AllowResponse``/``DenyResponse`` discriminated union (used
post-parse) lives in ``messaging.py``; this module is the lower-level
TypedDict + type-guard layer.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, TypedDict, TypeGuard


class BridgePermissionResponse(TypedDict, total=False):
    """Permission response payload as it appears on the wire.

    Mirrors TS ``BridgePermissionResponse`` on ``bridgePermissionCallbacks.ts:3-8``.
    ``behavior`` is required; the other fields are optional and only meaningful
    when ``behavior == 'allow'`` (updatedInput / updatedPermissions) or
    ``'deny'`` (message).

    **camelCase field names are intentional**: this TypedDict is the
    *pre-normalization* wire shape sent by the claude.ai web app. The
    *post-normalization* discriminated union (``AllowResponse`` /
    ``DenyResponse`` in ``messaging.py``) uses snake_case Python idioms.
    The router (``messaging.normalize_control_message_keys``) converts
    between the two.
    """

    behavior: str  # 'allow' | 'deny'
    updatedInput: dict[str, Any]
    updatedPermissions: list[dict[str, Any]]
    message: str


def is_bridge_permission_response(
    value: object,
) -> TypeGuard[BridgePermissionResponse]:
    """Type guard for a parsed control_response payload.

    Mirrors TS ``isBridgePermissionResponse`` on
    ``bridgePermissionCallbacks.ts:32-40``. Checks the required ``behavior``
    discriminant rather than using an unsafe cast.
    """
    if not isinstance(value, dict):
        return False
    behavior = value.get('behavior')
    return behavior == 'allow' or behavior == 'deny'


class BridgePermissionCallbacks(Protocol):
    """Send/receive surface for permission requests.

    Mirrors TS ``BridgePermissionCallbacks`` on
    ``bridgePermissionCallbacks.ts:10-27``. Wired by orchestrators to a
    transport (``ReplBridgeHandle``) and a request registry. Implementations
    are stateful (the ``on_response`` handler registry); the Protocol just
    declares the surface.
    """

    def send_request(
        self,
        request_id: str,
        tool_name: str,
        input: dict[str, Any],
        tool_use_id: str,
        description: str,
        permission_suggestions: list[dict[str, Any]] | None = None,
        blocked_path: str | None = None,
    ) -> None: ...

    def send_response(
        self, request_id: str, response: BridgePermissionResponse
    ) -> None: ...

    def cancel_request(self, request_id: str) -> None:
        """Cancel a pending control_request so the web app can dismiss its prompt."""
        ...

    def on_response(
        self,
        request_id: str,
        handler: Callable[[BridgePermissionResponse], None],
    ) -> Callable[[], None]:
        """Register a one-shot response handler. Returns an unsubscribe callable."""
        ...


__all__ = [
    'BridgePermissionCallbacks',
    'BridgePermissionResponse',
    'is_bridge_permission_response',
]
