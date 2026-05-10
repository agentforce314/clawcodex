"""WebSocket close codes used by the CCR bridge layer.

Ports the close-code constants synthesized in
``typescript/src/bridge/replBridgeTransport.ts:209-365`` and
``typescript/src/remote/SessionsWebSocket.ts:34-37``.

Single source of truth so consumers (Phase 3 `replBridgeTransport`,
Phase 4 `SessionsWebSocket`, Phase 1 `directConnectManager`) do not
re-define these magic numbers.
"""

from __future__ import annotations

from typing import Final

# ─── Server-initiated close codes the bridge transport synthesizes ─────

#: Worker epoch superseded — closes both transports; replBridge's poll
#: loop reconnects with a fresh epoch.
#: ``replBridgeTransport.ts:220``.
WS_CLOSE_EPOCH_MISMATCH: Final = 4090

#: CCRClient.initialize failed — closes both; poll loop will retry on
#: the next work dispatch.
#: ``replBridgeTransport.ts:365``.
WS_CLOSE_INIT_FAILURE: Final = 4091

#: SSE reconnect-budget exhausted — distinguishable from HTTP-status
#: closes so ws_closed telemetry can branch on it.
#: ``replBridgeTransport.ts:313``.
WS_CLOSE_RECONNECT_BUDGET_EXHAUSTED: Final = 4092

# ─── claude.ai → CCR session close codes (Phase 4 SessionsWebSocket) ───

#: Server says "you are not authorized for this session" — stop
#: reconnecting permanently.
#: ``SessionsWebSocket.ts:35``.
WS_CLOSE_PERMANENT_UNAUTHORIZED: Final = 4003

#: Server says "session not found" — could be transient during
#: compaction; retry with limited linear backoff (max 3 attempts).
#: ``SessionsWebSocket.ts:26``.
WS_CLOSE_SESSION_NOT_FOUND: Final = 4001


__all__ = [
    'WS_CLOSE_EPOCH_MISMATCH',
    'WS_CLOSE_INIT_FAILURE',
    'WS_CLOSE_PERMANENT_UNAUTHORIZED',
    'WS_CLOSE_RECONNECT_BUDGET_EXHAUSTED',
    'WS_CLOSE_SESSION_NOT_FOUND',
]
