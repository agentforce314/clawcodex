"""Session ID tag translation helpers for the CCR v2 compat layer.

Ports ``typescript/src/bridge/sessionIdCompat.ts``.

The CCR v2 compat layer issues tagged IDs in two prefixes:

* ``cse_*`` — the v2 infrastructure tag (work poll, worker endpoints).
* ``session_*`` — the v1 compat tag (client-facing /v1/sessions endpoints).

Both encode the same underlying UUID. The translation helpers re-tag IDs as
needed when calls cross layers. The ``set_cse_shim_gate()`` hook lets a
caller inject a kill-switch for the shim without this module needing to
import bridge_enabled.py (mirrors TS module-isolation reasoning at
``sessionIdCompat.ts:11-13``).
"""

from __future__ import annotations

from typing import Callable

_CSE_PREFIX = 'cse_'
_SESSION_PREFIX = 'session_'

_cse_shim_enabled: Callable[[], bool] | None = None


def set_cse_shim_gate(gate: Callable[[], bool]) -> None:
    """Register the cse_-shim kill-switch.

    Mirrors TS ``setCseShimGate`` on ``sessionIdCompat.ts:21-23``. When the
    gate returns ``False``, ``to_compat_session_id`` becomes a no-op. The
    gate defaults to "active" (shim on) when this is never called, matching
    TS ``isCseShimEnabled()``'s default-true behavior.
    """
    global _cse_shim_enabled
    _cse_shim_enabled = gate


def to_compat_session_id(session_id: str) -> str:
    """Re-tag a ``cse_*`` session ID to ``session_*`` for the v1 compat API.

    Mirrors TS ``toCompatSessionId`` on ``sessionIdCompat.ts:38-42``.
    No-op for IDs that aren't ``cse_*``. When the cse-shim gate is disabled,
    also a no-op (returns input unchanged).
    """
    if not session_id.startswith(_CSE_PREFIX):
        return session_id
    if _cse_shim_enabled is not None and not _cse_shim_enabled():
        return session_id
    return _SESSION_PREFIX + session_id[len(_CSE_PREFIX):]


def to_infra_session_id(session_id: str) -> str:
    """Re-tag a ``session_*`` ID to ``cse_*`` for infrastructure-layer calls.

    Mirrors TS ``toInfraSessionId`` on ``sessionIdCompat.ts:54-57``. Inverse
    of ``to_compat_session_id``. No-op for IDs that aren't ``session_*``.
    Unlike the compat direction, this is NOT gated by the cse-shim — once
    a caller crosses below the compat layer, the infra tag is what the
    server expects regardless of the shim's state.
    """
    if not session_id.startswith(_SESSION_PREFIX):
        return session_id
    return _CSE_PREFIX + session_id[len(_SESSION_PREFIX):]


def _reset_shim_gate_for_testing() -> None:
    """Reset module-global gate to ``None`` (tests only).

    Test cleanup helper — not part of the public API. Callers must not
    rely on this in production code.
    """
    global _cse_shim_enabled
    _cse_shim_enabled = None


__all__ = [
    'set_cse_shim_gate',
    'to_compat_session_id',
    'to_infra_session_id',
]
