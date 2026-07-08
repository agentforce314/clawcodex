"""Process-global pointer to the active REPL bridge handle.

Ports ``typescript/src/bridge/replBridgeHandle.ts``.

Callers outside the React tree that owns the bridge (tools, slash
commands) need a way to invoke handle methods (subscribe, send control
events, etc.). Same one-bridge-per-process justification as
``bridge_debug.ts`` — the handle's closure captures the session ID and
``get_access_token`` that created the session.

Set from the orchestrator (Phase 5/6) when init completes; cleared on
teardown. Reading is best-effort: ``None`` means no bridge is connected.

Every set/clear publishes the local bridge ID to the concurrent-session
PID registry (``src/utils/concurrent_sessions.py``, #284) so other peers
can dedup us out of session lists — mirroring TS
``utils/concurrentSessions.updateSessionBridgeId()``.
"""

from __future__ import annotations

import logging

from src.bridge.session_id_compat import to_compat_session_id
from src.bridge.types import ReplBridgeHandle

logger = logging.getLogger(__name__)

_handle: ReplBridgeHandle | None = None


def set_repl_bridge_handle(h: ReplBridgeHandle | None) -> None:
    """Register (or clear) the active REPL bridge handle.

    Mirrors TS ``setReplBridgeHandle`` on ``replBridgeHandle.ts:18-23``,
    including the ``updateSessionBridgeId(getSelfBridgeCompatId() ??
    null)`` publish: setting the handle records our bridge compat ID in
    the PID registry so peer enumeration dedups us; clearing publishes
    ``None`` so a stale ID doesn't suppress a legitimately-remote
    session after reconnect (#284).
    """
    global _handle
    _handle = h
    try:
        from src.utils.concurrent_sessions import update_session_bridge_id

        update_session_bridge_id(get_self_bridge_compat_id())
    except Exception:
        logger.debug('[bridge:handle] bridge-id publish failed', exc_info=True)
    logger.debug(
        '[bridge:handle] %s', 'set' if h is not None else 'cleared',
    )


def get_repl_bridge_handle() -> ReplBridgeHandle | None:
    """Get the active REPL bridge handle, or ``None`` if not connected.

    Mirrors TS ``getReplBridgeHandle`` on ``replBridgeHandle.ts:25-27``.
    """
    return _handle


def get_self_bridge_compat_id() -> str | None:
    """Our own bridge session ID in the ``session_*`` compat format.

    Mirrors TS ``getSelfBridgeCompatId`` on ``replBridgeHandle.ts:33-36``.
    Returns ``None`` when no bridge is connected. The retag from ``cse_*``
    to ``session_*`` matches what ``/v1/sessions`` responses use, so
    server-driven peer dedup compares apples to apples.
    """
    h = get_repl_bridge_handle()
    if h is None:
        return None
    return to_compat_session_id(h.bridge_session_id)


def _reset_for_testing() -> None:
    """Clear the module-global pointer (tests only).

    Test cleanup helper — not part of the public API. The Phase 1 module
    pattern (see ``session_id_compat._reset_shim_gate_for_testing``)
    establishes this as the convention.
    """
    global _handle
    _handle = None


__all__ = [
    'get_repl_bridge_handle',
    'get_self_bridge_compat_id',
    'set_repl_bridge_handle',
]
