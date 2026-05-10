"""Project-wide exceptions for the CCR bridge layer.

Phase-local exceptions (e.g., ``DirectConnectError``) live with their
respective modules; only cross-phase exceptions live here.
"""

from __future__ import annotations


class EpochSupersededError(Exception):
    """Raised by Phase 3's V2 transport when the server returns 409
    (worker_epoch superseded).

    Mirrors TS ``replBridgeTransport.ts:230`` which throws ``new Error(
    'epoch superseded')`` to unwind in-flight callers (``request()``).
    Catching this is how callers learn the epoch was bumped — they should
    drop the current transport and recreate it with a fresh epoch.
    """


class BridgeAuthError(Exception):
    """Raised when JWT cannot be decoded or refresh fails permanently.

    The ``TokenRefreshScheduler`` (WI-2.4) has a 3-failure cap; past that,
    the scheduler stops retrying and surfaces this exception to the caller.
    """


__all__ = ['BridgeAuthError', 'EpochSupersededError']
