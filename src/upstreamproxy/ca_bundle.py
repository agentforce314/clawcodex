"""CA-bundle download + PEM validation.

Ports ``isValidPemContent`` and ``downloadCaBundle`` from
``typescript/src/upstreamproxy/upstreamproxy.ts:211-216, 266-304``.

The CCR upstream-proxy server signs traffic with a forged CA so it can
MITM credential injection. The CLI downloads that CA and concatenates it
with the system CA bundle, then exports the combined bundle's path via
``SSL_CERT_FILE`` etc. so child subprocesses (curl, gh, python) trust
the proxy.

Two security guards:
  1. ``isValidPemContent`` — refuses to write non-PEM bytes (e.g., HTML
     error page from a compromised server) into the system bundle.
  2. 5-second timeout on the fetch — a hung endpoint cannot stall CLI
     startup forever.

Both fail open: any failure returns ``False`` and the proxy stays
disabled.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

#: Matches one well-formed PEM certificate block. The non-greedy body
#: ensures multiple blocks in a bundle are matched separately. This
#: regex is structural-only; we don't parse the cert itself (the OpenSSL
#: layer downstream of ``SSL_CERT_FILE`` will reject malformed certs).
_PEM_BLOCK_RE = re.compile(
    rb'-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----'
)

DEFAULT_CA_FETCH_TIMEOUT_SECONDS = 5.0


def is_valid_pem_content(content: str | bytes) -> bool:
    """Return True if ``content`` contains at least one well-formed PEM block.

    Used as a security guard before writing the downloaded CA into the
    system bundle. A compromised upstream proxy might respond with HTML
    or JSON; without this check, that arbitrary content would be
    appended to the trust store.

    Mirrors ``upstreamproxy.ts:211-216`` and the test cases at
    ``upstreamproxy.test.ts:1-43``.
    """
    if isinstance(content, str):
        if not content.strip():
            return False
        data = content.encode('utf-8', errors='replace')
    else:
        if not content.strip():
            return False
        data = content
    return bool(_PEM_BLOCK_RE.search(data))


async def download_ca_bundle(
    base_url: str,
    system_ca_path: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    *,
    timeout_seconds: float = DEFAULT_CA_FETCH_TIMEOUT_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Fetch the CCR upstream-proxy CA, concat with system CA, write atomically.

    Returns ``True`` on success, ``False`` on any failure (fail-open).

    Steps:
      1. ``GET ${base_url}/v1/code/upstreamproxy/ca-cert`` with a 5s
         timeout. Non-2xx responses, network errors, and timeouts all
         return ``False``.
      2. Validate the response body via ``is_valid_pem_content``. A
         non-PEM response (HTML/JSON/arbitrary bytes from a compromised
         server) returns ``False`` rather than corrupting the bundle.
      3. Read the system CA bundle. Missing file is OK — we just write
         the CCR CA alone (TS does the same: ``catch(() => '')``).
      4. Write the concatenated bundle atomically: write to a temp file
         in the same directory, then ``os.rename`` (POSIX atomic).

    Mirrors ``upstreamproxy.ts:266-304``. Parameter ``client`` is for
    tests — production callers can omit and a fresh ``AsyncClient`` is
    constructed per call.
    """
    out_path = Path(out_path)
    system_ca_path = Path(system_ca_path)
    url = f'{base_url}/v1/code/upstreamproxy/ca-cert'

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout_seconds) as fresh:
                resp = await fresh.get(url)
        else:
            resp = await client.get(url, timeout=timeout_seconds)
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning('[upstreamproxy] ca-cert download failed: %s; proxy disabled', exc)
        return False

    if resp.status_code < 200 or resp.status_code >= 300:
        logger.warning(
            '[upstreamproxy] ca-cert fetch %d; proxy disabled', resp.status_code
        )
        return False

    ccr_ca = resp.content
    if not is_valid_pem_content(ccr_ca):
        logger.warning(
            '[upstreamproxy] ca-cert response is not valid PEM; proxy disabled'
        )
        return False

    # System bundle may not exist (alpine, distroless); empty fallback
    # matches TS ``readFile(systemCaPath, 'utf8').catch(() => '')``.
    try:
        system_ca = system_ca_path.read_bytes()
    except OSError:
        system_ca = b''

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: same-directory tempfile + os.rename. POSIX
        # rename is atomic when both paths are on the same filesystem.
        # Linux/macOS guarantee this; Windows raises if dst exists, but
        # the upstreamproxy is Linux-only in production.
        fd, tmp_path = tempfile.mkstemp(dir=out_path.parent, prefix='.ca-bundle.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as fh:
                fh.write(system_ca)
                if system_ca and not system_ca.endswith(b'\n'):
                    fh.write(b'\n')
                fh.write(ccr_ca)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, out_path)
        except OSError:
            # Best-effort cleanup of the tempfile on failure.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.warning('[upstreamproxy] ca-bundle write failed: %s; proxy disabled', exc)
        return False
    return True


__all__ = [
    'DEFAULT_CA_FETCH_TIMEOUT_SECONDS',
    'download_ca_bundle',
    'is_valid_pem_content',
]
