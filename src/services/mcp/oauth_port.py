"""OAuth callback redirect-URI port allocator.

Phase 4 WI-4.4 (gap #30). Mirrors typescript/src/services/mcp/oauthPort.ts.

RFC 8252 §7.3 (OAuth 2.0 for Native Apps) recommends loopback redirect
URIs use ports from the IANA dynamic-port range so they don't collide
with well-known services. Range:
  - Windows: 39152-49151 (TS canonical chose this narrower range to avoid
    Windows-Defender heuristics that flag connections in 49152+)
  - POSIX:    49152-65535 (the full IANA dynamic range)

OAuth providers' redirect-URI matchers are typically liberal about the
port (RFC 8252 §7.3 mandates this), so the same registered redirect URI
``http://localhost:*/callback`` matches any chosen port.

Operators can pin a specific port via ``MCP_OAUTH_CALLBACK_PORT`` env —
useful for firewalled environments or for testing.
"""

from __future__ import annotations

import logging
import os
import random
import socket
import sys

logger = logging.getLogger(__name__)

# Last-resort fallback if 100 random selections all collide with bound
# sockets (essentially impossible on a normal machine but the original
# TS code chose 3118 as an arbitrary stable fallback).
_FALLBACK_PORT = 3118
_MAX_ATTEMPTS = 100


def _port_range() -> range:
    """Per-platform IANA dynamic port range. Windows narrower per TS."""
    if sys.platform.startswith("win"):
        return range(39152, 49152)  # 39152-49151 inclusive
    return range(49152, 65536)  # 49152-65535 inclusive


def find_available_port(env_override_name: str = "MCP_OAUTH_CALLBACK_PORT") -> int:
    """Return an available loopback port for an OAuth callback listener.

    Honors ``MCP_OAUTH_CALLBACK_PORT`` (or the named env var) when set
    and parseable as a positive int; the operator is trusted to know
    that the port is free. Otherwise picks randomly from the dynamic
    range and probes via ``bind()`` to avoid races. Returns
    ``_FALLBACK_PORT`` after 100 unsuccessful attempts (pathological;
    in practice the random selection finds a free port within a few
    tries).
    """
    raw = os.environ.get(env_override_name, "").strip()
    if raw:
        try:
            port = int(raw)
            if 1 <= port <= 65535:
                return port
            logger.warning(
                "%s=%r is out of range [1, 65535]; ignoring and allocating randomly",
                env_override_name, raw,
            )
        except ValueError:
            logger.warning(
                "%s=%r is not an integer; ignoring and allocating randomly",
                env_override_name, raw,
            )

    # random.sample on a range avoids materializing a 16K-int list.
    candidates = random.sample(_port_range(), _MAX_ATTEMPTS)
    for port in candidates:
        if _is_port_free(port):
            return port
    logger.warning(
        "OAuth port allocator exhausted %d attempts in range %d-%d; "
        "falling back to %d (may already be bound)",
        _MAX_ATTEMPTS, _port_range().start, _port_range().stop - 1, _FALLBACK_PORT,
    )
    return _FALLBACK_PORT


def _is_port_free(port: int) -> bool:
    """Probe whether a port can be bound on loopback.

    Skips ``SO_REUSEADDR`` on Windows because Windows' semantics differ
    from POSIX — on Windows ``SO_REUSEADDR`` behaves like POSIX
    ``SO_REUSEPORT``, allowing two sockets to bind the same port, which
    would cause false positives. Race window after the probe is tiny
    (microseconds) and OAuth callback listeners are short-lived.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if not sys.platform.startswith("win"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
