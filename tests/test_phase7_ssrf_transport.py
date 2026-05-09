"""Phase-7 / WI-7.3 — SSRF DNS-time validation regression tests.

Pre-Phase-7, ``execute_http_hook`` validated the URL pre-flight via
``validate_hook_url`` and then made the actual HTTP request via
``urllib.request.urlopen``. Between those two steps lay a TOCTOU
window: a hostname's DNS could resolve to a benign IP at validation
time and to a malicious IP (e.g., ``169.254.169.254`` cloud metadata)
at connection time — classic DNS rebinding.

Phase 7 closes the window with ``SsrfGuardedTransport`` (a custom
``httpx.AsyncHTTPTransport`` subclass) that:

  1. Resolves the hostname inside ``handle_async_request`` (per-call,
     not pre-flight).
  2. Validates resolved IPs against the SSRF blocklist.
  3. Pins the connection to the validated IP (URL-rewrite + Host
     header preservation) so a second DNS lookup at connection time is
     irrelevant.

The headline test simulates the rebinding attack: pre-flight resolver
returns a safe IP; DNS-time resolver returns 169.254.169.254. Pre-Phase-7
this would have succeeded (false-allow); Phase-7 transport rejects.
"""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.hooks.exec_http_hook import execute_http_hook
from src.hooks.hook_types import HookConfig
from src.hooks.ssrf_transport import (
    SsrfGuardedTransport,
    SsrfRebindingError,
    _resolve_async,
    _validate_resolved_ips,
    get_guarded_client,
)


# ---------------------------------------------------------------------------
# Pure-function unit tests for the validation helper
# ---------------------------------------------------------------------------


class TestValidateResolvedIps:
    def test_safe_public_ip_passes(self):
        result = _validate_resolved_ips("example.com", ["93.184.216.34"])
        assert result == "93.184.216.34"

    def test_metadata_ip_rejected(self):
        # AWS cloud metadata endpoint.
        result = _validate_resolved_ips("evil.example", ["169.254.169.254"])
        assert result is None

    def test_gcp_metadata_ip_rejected(self):
        # GCP cloud metadata endpoint.
        result = _validate_resolved_ips("evil.example", ["169.254.170.2"])
        assert result is None

    def test_private_ip_rejected(self):
        for private_ip in ("10.0.0.1", "192.168.1.1", "172.16.0.1"):
            result = _validate_resolved_ips("evil.example", [private_ip])
            assert result is None, f"failed to reject {private_ip}"

    def test_loopback_rejected(self):
        result = _validate_resolved_ips("evil.example", ["127.0.0.1"])
        assert result is None

    def test_link_local_rejected(self):
        result = _validate_resolved_ips("evil.example", ["169.254.0.1"])
        assert result is None

    def test_empty_list_returns_none(self):
        result = _validate_resolved_ips("nowhere", [])
        assert result is None


# ---------------------------------------------------------------------------
# Headline DNS-rebinding regression — the load-bearing test
# ---------------------------------------------------------------------------


class TestDnsRebindingRegression:
    @pytest.mark.asyncio
    async def test_pre_flight_passes_dns_time_rejects(self):
        """The chapter's worked example #1 attack scenario:

          1. Pre-flight ``validate_hook_url`` resolves the hostname
             via the system resolver — gets a safe public IP.
          2. By the time the transport tries to connect, the attacker-
             controlled DNS has flipped the answer to 169.254.169.254
             (AWS metadata).

        Pre-Phase-7 this would have produced a false-allow: the
        connection went out via urllib (which re-resolved at connect
        time) and could reach the metadata endpoint. Phase-7's transport
        re-resolves WITHIN ``handle_async_request`` and pins the
        connection to that resolved IP — so a flipped DNS at the literal
        TCP-connect moment is irrelevant.

        We simulate by patching the ``loop.getaddrinfo`` call inside
        the transport to return the malicious IP. Pre-flight is
        configured to pass (resolve_dns=False on validate_hook_url).
        """
        config = HookConfig(
            type="http",
            url="https://evil.attacker-controlled.example/hook",
        )

        async def malicious_getaddrinfo(host, port, **kwargs):
            # Simulate the rebinding: this is what the DNS server
            # returns at connection time (after pre-flight passed).
            # AWS metadata IP — the canonical SSRF target.
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                     ("169.254.169.254", port))]

        # Patch the event loop's getaddrinfo for the test. The transport
        # uses ``asyncio.get_running_loop().getaddrinfo``, so this
        # intercept catches the load-bearing resolution call.
        import asyncio
        loop = asyncio.get_event_loop()
        with patch.object(loop, "getaddrinfo", side_effect=malicious_getaddrinfo):
            # Also patch the pre-flight check to "pass" so we test the
            # transport-time rejection (not the pre-flight rejection).
            with patch(
                "src.hooks.exec_http_hook.validate_hook_url",
                return_value=(True, None),
            ):
                result = await execute_http_hook(config, {})

        # The transport rejected the connection at DNS-time.
        assert result.blocking_error is not None
        assert "SSRF" in result.blocking_error
        assert "169.254.169.254" in str(result.blocking_error) or "blocked" in result.blocking_error.lower()


# ---------------------------------------------------------------------------
# SsrfGuardedTransport unit tests
# ---------------------------------------------------------------------------


class TestSsrfGuardedTransport:
    @pytest.mark.asyncio
    async def test_blocked_hostname_raises(self):
        # localhost is in BLOCKED_HOSTS.
        transport = SsrfGuardedTransport()
        request = httpx.Request("POST", "http://localhost:8080/hook")
        with pytest.raises(SsrfRebindingError, match="localhost"):
            await transport.handle_async_request(request)

    @pytest.mark.asyncio
    async def test_ip_literal_metadata_raises(self):
        # IP literal pointing at metadata endpoint — caught even though
        # there's no DNS resolution.
        transport = SsrfGuardedTransport()
        request = httpx.Request("POST", "http://169.254.169.254/latest/meta-data/")
        with pytest.raises(SsrfRebindingError):
            await transport.handle_async_request(request)

    @pytest.mark.asyncio
    async def test_ip_literal_private_raises(self):
        transport = SsrfGuardedTransport()
        request = httpx.Request("POST", "http://10.0.0.5/foo")
        with pytest.raises(SsrfRebindingError):
            await transport.handle_async_request(request)

    @pytest.mark.asyncio
    async def test_dns_resolution_returns_metadata_raises(self):
        # Hostname (not IP literal). Resolver returns metadata IP →
        # transport rejects.
        transport = SsrfGuardedTransport()
        request = httpx.Request("POST", "http://evil.example/hook")

        async def mock_resolver(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                     ("169.254.169.254", port))]

        import asyncio
        loop = asyncio.get_event_loop()
        with patch.object(loop, "getaddrinfo", side_effect=mock_resolver):
            with pytest.raises(SsrfRebindingError, match="169.254"):
                await transport.handle_async_request(request)


# ---------------------------------------------------------------------------
# get_guarded_client returns httpx.AsyncClient with the right transport
# ---------------------------------------------------------------------------


class TestGetGuardedClient:
    def test_returns_async_client(self):
        client = get_guarded_client()
        assert isinstance(client, httpx.AsyncClient)
        # No assertion on transport identity — httpx wraps it; the
        # contract is "an AsyncClient configured with the guard."

    def test_timeout_propagated(self):
        # Custom timeout passes through.
        client = get_guarded_client(timeout=5.0)
        assert client.timeout.connect == 5.0
