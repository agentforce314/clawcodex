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


class BridgeFatalError(Exception):
    """Non-retryable error from the environments API.

    Mirrors TS ``BridgeFatalError`` exported from ``bridgeApi.ts:56-66``.
    Carries the HTTP status code and an optional server-provided error type
    (e.g. ``'environment_expired'``, ``'lifetime'``). Callers use
    ``is_expired_error_type(err.error_type)`` (Phase 3) to decide between
    teardown-and-recreate (expired) vs. fail-loudly (genuine 401/403).

    Args:
        message: Human-readable detail (typically ``f'{verb} {status}'``).
        status: HTTP status code.
        error_type: Optional server-provided error code from the response
            body's ``data.error.type`` field.
    """

    def __init__(
        self,
        message: str,
        status: int,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type

    def __repr__(self) -> str:
        return (
            f'BridgeFatalError(status={self.status}, '
            f'error_type={self.error_type!r}, message={str(self)!r})'
        )


__all__ = ['BridgeAuthError', 'BridgeFatalError', 'EpochSupersededError']
