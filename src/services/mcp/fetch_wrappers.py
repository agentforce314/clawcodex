"""Fetch wrappers: httpx client factory with MCP-appropriate timeouts.

Phase 2 WI-2.4 (FU#6). Mirrors TS' ``wrapFetchWithTimeout`` from
typescript/src/services/mcp/auth.ts. The default ``httpx.AsyncClient``
timeout is 5 seconds — far too short for slow MCP servers, long-running
tool calls, and corporate networks with deep TLS chains. This module
exposes a single factory ``build_mcp_http_client(headers=...)`` that
returns a client with TS-canonical timeouts:

* ``connect=15s``  — long enough for slow TLS handshakes; short enough
  that an unreachable host fails fast (matches the cold-OAuth-discovery
  budget AUTH_REQUEST_TIMEOUT_MS=30000 minus headroom).
* ``read=300s``    — five-minute read budget, matching the per-tool
  timeout ``DEFAULT_MCP_TOOL_TIMEOUT_MS`` so a long-running MCP tool
  call doesn't get killed by the transport. Per-tool timeouts override.
* ``write=30s``    — generous write budget for large request payloads.
* ``pool=10s``     — short pool-acquire budget; connection-pool
  starvation should fail loud rather than wait minutes.

Why a factory: the SDK's ``streamable_http_client`` takes an optional
``http_client`` parameter and adopts caller-provided clients without
closing them. We hand it a pre-configured client built here so the
timeout policy is uniform across HttpTransport, SseTransport, and
ad-hoc fetches (claudeai loader, OAuth discovery — though those have
their own narrower timeouts where appropriate).
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Per-segment timeouts. Tuned for MCP workloads: long-running tool
# calls + cold TLS handshakes + corporate proxies. Operators can
# override via the MCP_*_TIMEOUT_S env vars below.
DEFAULT_CONNECT_TIMEOUT_S: float = 15.0
DEFAULT_READ_TIMEOUT_S: float = 300.0
DEFAULT_WRITE_TIMEOUT_S: float = 30.0
DEFAULT_POOL_TIMEOUT_S: float = 10.0


def _env_float(name: str, default: float) -> float:
    """Read a positive-float env-var override; fall back to ``default``.

    Invalid / non-positive values are logged and ignored.
    """
    import os

    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
        if value > 0:
            return value
    except ValueError:
        pass
    logger.warning(
        "%s=%r is not a positive float; using default %s",
        name, raw, default,
    )
    return default


def build_mcp_timeout() -> httpx.Timeout:
    """Construct the httpx ``Timeout`` used by every MCP transport."""
    return httpx.Timeout(
        connect=_env_float("MCP_CONNECT_TIMEOUT_S", DEFAULT_CONNECT_TIMEOUT_S),
        read=_env_float("MCP_READ_TIMEOUT_S", DEFAULT_READ_TIMEOUT_S),
        write=_env_float("MCP_WRITE_TIMEOUT_S", DEFAULT_WRITE_TIMEOUT_S),
        pool=_env_float("MCP_POOL_TIMEOUT_S", DEFAULT_POOL_TIMEOUT_S),
    )


def build_mcp_http_client(
    *,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """Return an ``httpx.AsyncClient`` with MCP-appropriate timeouts.

    The SDK's ``streamable_http_client`` adopts caller-provided clients
    without closing them, so the caller is responsible for the client's
    lifecycle (the transport adapters register ``aclose()`` on their
    exit stack). Headers when provided are baked into the client so all
    requests carry them — matches the SDK's expectation for header
    propagation on Streamable HTTP and SSE.
    """
    return httpx.AsyncClient(
        timeout=build_mcp_timeout(),
        headers=headers,
    )
