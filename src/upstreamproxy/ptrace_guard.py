"""``prctl(PR_SET_DUMPABLE, 0)`` heap-protection guard.

Ports ``setNonDumpable`` from
``typescript/src/upstreamproxy/upstreamproxy.ts:237-264``.

Linux-only. Calls ``libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)`` via
``ctypes``. Other platforms (macOS, Windows) silently no-op — matches TS
Bun-FFI-or-skip behavior.

Why this exists: a CCR session container reads its session token from
``/run/ccr/session_token`` into the agent process's heap, then unlinks
the file. Without ``PR_SET_DUMPABLE = 0``, a same-UID attacker (e.g., a
prompt-injected ``gdb -p $PPID``) could attach via ptrace and scrape the
token from the heap. Setting it to 0 blocks ptrace by other same-UID
processes.

Per chapter "Apply This" rule #4: *"Keep secrets heap-only in
adversarial environments. Reading the token from a file, disabling
ptrace, and unlinking the file eliminates both filesystem and
memory-inspection attack vectors."*
"""

from __future__ import annotations

import logging
import platform

logger = logging.getLogger(__name__)

#: ``PR_SET_DUMPABLE`` operation code from ``<linux/prctl.h>``. Value 4
#: across all kernel versions since 2.3.20.
PR_SET_DUMPABLE = 4


def set_non_dumpable() -> bool:
    """Block same-UID ptrace via ``prctl(PR_SET_DUMPABLE, 0)``.

    Returns ``True`` on success, ``False`` on any failure. Linux-only;
    non-Linux platforms silently return ``False`` (no-op). Failure is
    NEVER raised — this is a fail-open security guard, and a missing
    libc / hardened kernel must not break the agent loop.
    """
    if platform.system() != 'Linux':
        logger.debug(
            '[upstreamproxy] non-Linux platform (%s); skipping prctl(PR_SET_DUMPABLE)',
            platform.system(),
        )
        return False

    try:
        import ctypes
    except ImportError:
        logger.warning('[upstreamproxy] ctypes unavailable; cannot prctl')
        return False

    try:
        # Standard glibc location. Some distros expose libc under
        # different names; try the most common first.
        libc = ctypes.CDLL('libc.so.6', use_errno=True)
    except OSError:
        try:
            libc = ctypes.CDLL('libc.so', use_errno=True)
        except OSError as exc:
            logger.warning('[upstreamproxy] could not load libc: %s', exc)
            return False

    try:
        libc.prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        libc.prctl.restype = ctypes.c_int
        rc = libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (OSError, AttributeError, ValueError) as exc:
        logger.warning('[upstreamproxy] prctl call failed: %s', exc)
        return False

    if rc != 0:
        errno = ctypes.get_errno()
        logger.warning(
            '[upstreamproxy] prctl(PR_SET_DUMPABLE,0) returned nonzero rc=%d errno=%d',
            rc,
            errno,
        )
        return False
    return True


__all__ = ['PR_SET_DUMPABLE', 'set_non_dumpable']
