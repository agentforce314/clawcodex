"""SDK-message format adapter for the remote-session bridge.

Ports the **functional surface** of
``typescript/src/remote/sdkMessageAdapter.ts (302 lines)``: translate
between the SDK message format used in the local REPL and the bridge
wire format. The TS source is large because it handles many edge cases
(streamlined messages, partial assistant chunks, tool-result back-refs,
etc.); we port the **canonical translation paths** the chapter calls
out, not the long tail of edge cases.

Two directions:

  - **Wire → SDK**: incoming messages from the WS are normalized into
    the SDK shape (bridge wire format may use camelCase from older
    server versions; we use ``normalize_control_message_keys`` to fix).
  - **SDK → Wire**: outgoing user messages are wrapped in the
    ``stream-json`` envelope shape the agent expects.

Tests pin the canonical shapes; new edge cases land here as they're
discovered.
"""

from __future__ import annotations

import logging
from typing import Any

from src.bridge.messaging import normalize_control_message_keys

logger = logging.getLogger(__name__)


def adapt_wire_to_sdk(wire: dict[str, Any]) -> dict[str, Any]:
    """Normalize a wire payload to SDK-canonical shape.

    Only known camelCase keys are converted to snake_case; unknown keys
    pass through (matches ``normalize_control_message_keys``).
    """
    result = normalize_control_message_keys(wire)
    if not isinstance(result, dict):
        return wire
    return result


def adapt_sdk_to_wire_user_message(
    content: object,
    session_id: str,
    *,
    parent_tool_use_id: str | None = None,
    uuid: str | None = None,
) -> dict[str, Any]:
    """Build the stream-json envelope for a user prompt.

    Wire shape:
        {
          'type': 'user',
          'message': {'role': 'user', 'content': content},
          'parent_tool_use_id': parent_tool_use_id,
          'session_id': session_id,
          'uuid': uuid,                # optional
        }
    """
    envelope: dict[str, Any] = {
        'type': 'user',
        'message': {'role': 'user', 'content': content},
        'parent_tool_use_id': parent_tool_use_id,
        'session_id': session_id,
    }
    if uuid is not None:
        envelope['uuid'] = uuid
    return envelope


def adapt_permission_response(
    request_id: str,
    behavior: str,
    *,
    updated_input: dict | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """Build the control_response envelope for a permission decision.

    Mirrors ``RemoteSessionManager.respondToPermissionRequest`` payload
    construction.
    """
    if behavior == 'allow':
        inner_response: dict[str, Any] = {
            'behavior': 'allow',
            'updatedInput': updated_input or {},
        }
    elif behavior == 'deny':
        inner_response = {
            'behavior': 'deny',
            'message': message or '',
        }
    else:
        raise ValueError(f'unknown permission behavior: {behavior!r}')
    return {
        'type': 'control_response',
        'response': {
            'subtype': 'success',
            'request_id': request_id,
            'response': inner_response,
        },
    }


__all__ = [
    'adapt_permission_response',
    'adapt_sdk_to_wire_user_message',
    'adapt_wire_to_sdk',
]
