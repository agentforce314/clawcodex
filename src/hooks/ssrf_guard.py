from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

BLOCKED_HOSTS = frozenset({
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.google",
})

CLOUD_METADATA_IPS = frozenset({
    "169.254.169.254",
    "169.254.170.2",
    "fd00:ec2::254",
})


def _is_private_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    if addr.is_private:
        return True
    if addr.is_loopback:
        return True
    if addr.is_link_local:
        return True
    if addr.is_reserved:
        return True
    if isinstance(addr, ipaddress.IPv4Address):
        if str(addr).startswith("169.254."):
            return True
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return _is_private_ip(str(addr.ipv4_mapped))

    return False


def _resolve_hostname(hostname: str) -> list[str]:
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        return list({result[4][0] for result in results})
    except (socket.gaierror, OSError):
        return []


def validate_hook_url(url: str, *, resolve_dns: bool = True) -> tuple[bool, str | None]:
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL format"

    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported scheme: {parsed.scheme}. Only http and https are allowed."

    hostname = parsed.hostname
    if not hostname:
        return False, "URL has no hostname"

    if hostname in BLOCKED_HOSTS:
        return False, f"Blocked hostname: {hostname}"

    if hostname in CLOUD_METADATA_IPS:
        return False, f"Cloud metadata endpoint blocked: {hostname}"

    try:
        if _is_private_ip(hostname):
            return False, f"Private/reserved IP address blocked: {hostname}"
    except Exception:
        pass

    if resolve_dns:
        resolved_ips = _resolve_hostname(hostname)
        for ip in resolved_ips:
            if ip in CLOUD_METADATA_IPS:
                return False, f"DNS resolves to blocked cloud metadata IP: {ip}"
            if _is_private_ip(ip):
                return False, f"DNS resolves to private IP: {ip}"

    return True, None


def is_safe_url(url: str, *, resolve_dns: bool = True) -> bool:
    safe, _ = validate_hook_url(url, resolve_dns=resolve_dns)
    return safe
