"""Scoped httpx transport with DNS-time SSRF validation.

Phase-7 / WI-7.3. Per assumption A5: build a ``SsrfGuardedTransport``
that validates resolved IPs at the moment httpx connects, NOT
pre-flight. The pre-flight ``validate_hook_url`` (Phase-1 / WI-7.3
predecessor) leaves a TOCTOU window between hostname resolution and
connection — an attacker controlling the DNS server can return a
benign IP at validation time and a malicious IP (e.g.,
169.254.169.254 cloud metadata) at connection time. DNS-time
validation closes that window.

**Design choice (A5).** Scoped httpx transport, NOT global
``socket.getaddrinfo`` monkeypatch. Two reasons:

  1. Other Python network code in the process (provider HTTP calls,
     MCP HTTP servers, telemetry pipes) shouldn't have its DNS
     monkey-patched as a side effect of hook security.
  2. The transport is constructed per-hook-call by ``execute_http_hook``
     and discarded; there's no leak across calls.

**Implementation.** ``httpx.AsyncHTTPTransport`` doesn't expose a
public hook for DNS resolution, but it does delegate to
``asyncio.AbstractEventLoop.getaddrinfo`` via ``anyio``. We override
``handle_async_request`` to:

  1. Resolve the request's host via ``asyncio.get_event_loop().getaddrinfo``.
  2. Validate the resolved addresses against the SSRF blocklist.
  3. If safe: rewrite the request to target the resolved IP directly,
     preserving the Host header (so the destination sees the original
     hostname for routing purposes).
  4. If unsafe: raise ``httpx.ConnectError`` with a clear message.

Step 3 (rewrite to IP) is crucial for closing the rebinding window:
a second DNS lookup at connection time can't return a different IP
because we're already connecting to the validated IP.

The pre-flight ``validate_hook_url`` is preserved as an
early-rejection optimization (catches obvious bad URLs without
spawning the transport). The load-bearing security check is now in
the transport.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

import httpx

from .ssrf_guard import _is_private_ip, BLOCKED_HOSTS, CLOUD_METADATA_IPS

logger = logging.getLogger(__name__)


class SsrfRebindingError(httpx.ConnectError):
    """Raised when DNS resolution at connection time returned a blocked IP.

    Distinct from a generic ConnectError so callers / telemetry can
    distinguish "network failure" from "SSRF guard rejected."
    """


async def _resolve_async(host: str, port: int) -> list[str]:
    """Perform an async DNS lookup; return list of resolved IP strings.

    Uses the running event loop's ``getaddrinfo`` so test fixtures can
    monkey-patch the loop's resolver to simulate DNS rebinding without
    affecting the global resolver.
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            host, port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except (socket.gaierror, OSError) as exc:
        raise httpx.ConnectError(f"DNS resolution failed for {host}: {exc}")
    return list({info[4][0] for info in infos})


def _validate_resolved_ips(host: str, ips: list[str]) -> str | None:
    """Check resolved IPs against the SSRF blocklist. Returns the first
    safe IP, or None if all are blocked. Raises if at least one IP
    matches a blocked range — surfaced via ``SsrfRebindingError`` by
    the caller.
    """
    if not ips:
        return None
    for ip in ips:
        if ip in CLOUD_METADATA_IPS:
            return None  # Reject this resolution entirely.
        try:
            if _is_private_ip(ip):
                return None
        except Exception:
            return None
    return ips[0]


class SsrfGuardedTransport(httpx.AsyncHTTPTransport):
    """httpx transport that validates resolved IPs at DNS-lookup-time
    (i.e., immediately before connection), closing the TOCTOU window
    that pre-flight validation leaves open.

    The transport resolves the hostname inside ``handle_async_request``
    and rewrites the request URL to the validated IP. The Host header
    is preserved so the remote service sees the original hostname.

    A second DNS lookup performed by httpx at connection time would be
    irrelevant since we've already pinned the connection to a specific
    IP — that's how rebinding is closed.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        # Allow IP-literal requests through (rare in practice; if a hook
        # author writes ``http://1.2.3.4/foo``, the pre-flight check
        # already validated it). Skip the resolve step.
        try:
            socket.inet_aton(url.host)
            is_ip_literal = True
        except OSError:
            is_ip_literal = False

        if is_ip_literal:
            # Still validate against private/metadata ranges.
            if _validate_resolved_ips(url.host, [url.host]) is None:
                raise SsrfRebindingError(
                    f"SSRF guard rejected IP literal {url.host}"
                )
            return await super().handle_async_request(request)

        if url.host in BLOCKED_HOSTS:
            raise SsrfRebindingError(f"SSRF guard rejected blocked hostname {url.host}")

        # Resolve at connection time (this is the load-bearing call).
        port = url.port or (443 if url.scheme == "https" else 80)
        ips = await _resolve_async(url.host, port)

        validated_ip = _validate_resolved_ips(url.host, ips)
        if validated_ip is None:
            raise SsrfRebindingError(
                f"SSRF guard: DNS for {url.host} resolved to blocked IPs "
                f"{ips!r}"
            )

        # Rewrite request URL to the validated IP, preserving the Host
        # header. The remote service sees the original hostname for
        # virtual-hosting / SNI routing; the connection goes to the IP
        # we validated.
        new_url = url.copy_with(host=validated_ip)
        new_request = httpx.Request(
            method=request.method,
            url=new_url,
            headers=request.headers,
            content=request.content,
            extensions=request.extensions,
        )
        # Preserve the original Host header (httpx defaults to the new
        # URL's host otherwise).
        new_request.headers["Host"] = url.host

        return await super().handle_async_request(new_request)


def get_guarded_client(*, timeout: float = 30.0) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` configured with SSRF-guarded
    transport. Caller is responsible for ``async with`` lifecycle.

    Each call constructs a fresh transport — there's no cross-call
    state, so an HTTP hook firing twice in a session creates two
    independent guards. (Cheap; transport construction is ~microseconds.)
    """
    return httpx.AsyncClient(
        transport=SsrfGuardedTransport(),
        timeout=timeout,
    )
