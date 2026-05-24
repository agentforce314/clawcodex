"""Trusted-device token source for bridge sessions — Phase 10 stub.

Ports the consumer-facing surface of
``typescript/src/bridge/trustedDevice.ts``.

**Phase 10 stub**: The full TS implementation reads a device token from
OS-specific secure storage (Keychain on macOS, DPAPI on Windows,
libsecret on Linux), enrolls via POST ``/auth/trusted_devices`` during
login, and gates the header on a GrowthBook flag. The Python build has
none of those layers yet, so this module exposes the same public API
returning ``None`` (header omitted; server falls through to its
no-enforcement path).

Two env-var overrides are honored so callers (test scripts, daemon
wrappers, CI rigs) can inject a token without OS secure storage:

* ``CLAUDE_TRUSTED_DEVICE_TOKEN`` — direct token override (matches the
  same env var TS recognizes).

The ``enroll_trusted_device()`` async function is a no-op stub today;
implementing it requires the OAuth keychain + ``secure_storage``
modules that don't exist in the Python build yet (Phase 10 follow-up).

The function signatures match TS so a future swap to real keychain-
backed storage is non-breaking for callers.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


_ENV_TOKEN = 'CLAUDE_TRUSTED_DEVICE_TOKEN'


def get_trusted_device_token() -> str | None:
    """Return the trusted-device token to send as ``X-Trusted-Device-Token``.

    Mirrors TS ``getTrustedDeviceToken`` on ``trustedDevice.ts:54-59``.
    Returns ``None`` to omit the header (server treats this as "no
    elevated auth"), unless ``CLAUDE_TRUSTED_DEVICE_TOKEN`` is set.

    Phase 10 will swap the ``None`` fallback for a keychain read.
    """
    value = os.environ.get(_ENV_TOKEN)
    return value or None


def clear_trusted_device_token_cache() -> None:
    """Clear the (currently in-memory only) trusted-device-token cache.

    Mirrors TS ``clearTrustedDeviceTokenCache`` on ``trustedDevice.ts:61-63``.
    No-op in the env-var-only Python build — the env var is the source
    of truth, no separate cache exists.
    """
    return None


def clear_trusted_device_token() -> None:
    """Clear the persisted trusted-device token from secure storage.

    Mirrors TS ``clearTrustedDeviceToken`` on ``trustedDevice.ts:69-86``.
    No-op until Phase 10 keychain integration lands. The env var (if
    set) is not cleared — that's an external configuration concern.
    """
    return None


async def enroll_trusted_device() -> None:
    """Enroll this device via POST ``/auth/trusted_devices`` (no-op stub).

    Mirrors TS ``enrollTrustedDevice`` on ``trustedDevice.ts:97-210``.
    The TS implementation requires the OAuth keychain (to read the
    access token + persist the issued device_token), the secure storage
    layer (OS-specific keychain backend), and the GrowthBook gate
    (``tengu_sessions_elevated_auth_enforcement``). All three are
    out-of-scope for the current Python build.

    A future Phase 10 expansion will fill this in. Until then the
    function is a no-op that emits a debug log so a caller hitting it
    sees the gap.
    """
    logger.debug(
        '[trusted-device] enroll_trusted_device is a no-op stub — '
        'Phase 10 keychain integration not yet ported'
    )


__all__ = [
    'clear_trusted_device_token',
    'clear_trusted_device_token_cache',
    'enroll_trusted_device',
    'get_trusted_device_token',
]
