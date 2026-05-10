"""CCR upstream-proxy initialization + env-var derivation.

Ports ``initUpstreamProxy``, ``getUpstreamProxyEnv``,
``resetUpstreamProxyForTests`` from
``typescript/src/upstreamproxy/upstreamproxy.ts:79-204``.

Lifecycle (the chapter's 6-step ordered setup at §"Upstream Proxy"):
    1. Read ``/run/ccr/session_token`` (or override).
    2. ``prctl(PR_SET_DUMPABLE, 0)`` to block same-UID ptrace.
    3. Download CA cert + concat with system bundle.
    4. Start CONNECT-over-WS relay on an ephemeral port.
    5. Unlink the token file (only AFTER the listener is up — a
       supervisor restart can retry with the token still on disk).
    6. Export ``HTTPS_PROXY``/``SSL_CERT_FILE``/etc. env vars for child
       subprocesses (``getUpstreamProxyEnv()``).

Two env-var gates: ``CLAUDE_CODE_REMOTE`` AND
``CCR_UPSTREAM_PROXY_ENABLED`` MUST both be truthy. Plus
``CLAUDE_CODE_REMOTE_SESSION_ID`` MUST be set. If any gate is off, the
proxy is disabled — without these gates the proxy would MITM traffic
in non-CCR contexts.

**Fail-open everywhere.** Any error during init returns the disabled
state sentinel (``UpstreamProxyState(enabled=False)``); the agent loop
keeps running with no proxy. This matches chapter "Apply This" rule #5:
*"The upstream proxy fails open because it provides enhanced
functionality (credential injection), not core functionality (model
inference)."*
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from src.bridge.no_proxy import default_no_proxy

from .ca_bundle import download_ca_bundle
from .ptrace_guard import set_non_dumpable
from .relay import UpstreamProxyRelay, start_upstream_proxy_relay

logger = logging.getLogger(__name__)

#: Default location of the session-token file inside a CCR container.
SESSION_TOKEN_PATH = '/run/ccr/session_token'

#: Default location of the system CA bundle on Debian/Ubuntu CCR base
#: images. macOS dev environments use this path too via Homebrew's
#: ``ca-certificates``.
SYSTEM_CA_BUNDLE = '/etc/ssl/certs/ca-certificates.crt'

#: Default location of the merged CCR + system CA bundle.
def _default_ca_bundle_out() -> Path:
    return Path.home() / '.ccr' / 'ca-bundle.crt'


@dataclass(frozen=True)
class UpstreamProxyState:
    """Disabled-or-enabled state sentinel (returned by ``init_upstream_proxy``).

    When ``enabled`` is ``False``, ``port`` and ``ca_bundle_path`` are
    ``None``; when ``enabled`` is ``True``, both are set.
    """

    enabled: bool
    port: int | None = None
    ca_bundle_path: str | None = None
    relay: UpstreamProxyRelay | None = None


_DISABLED = UpstreamProxyState(enabled=False)

# Module-level state. ``init_upstream_proxy`` writes; ``get_upstream_proxy_env``
# reads. Per A11 + chapter §"Apply This" rule #5, all writes are fail-open
# (errors return DISABLED; never raise).
_state: UpstreamProxyState = _DISABLED


def _is_env_truthy(value: str | None) -> bool:
    """Truthy test matching ``isEnvTruthy`` semantics in TS.

    Returns ``True`` when value is non-empty and not one of
    ``"0", "false", "no", "off"`` (case-insensitive). Mirrors the Bun
    helper that gates ``initUpstreamProxy``.
    """
    if value is None:
        return False
    return value.lower() not in ('', '0', 'false', 'no', 'off')


async def init_upstream_proxy(
    *,
    token_path: str | os.PathLike[str] | None = None,
    system_ca_path: str | os.PathLike[str] | None = None,
    ca_bundle_path: str | os.PathLike[str] | None = None,
    ccr_base_url: str | None = None,
) -> UpstreamProxyState:
    """Initialize the CCR upstream proxy.

    Returns the resulting state. Safe to call from non-CCR contexts —
    the env-var gates short-circuit and ``DISABLED`` is returned with no
    side effects. All overrides are for tests; production callers omit
    them.

    The function is idempotent only via the disabled-on-failure path:
    if a previous call set ``_state`` to enabled, calling again does NOT
    re-init. Tests should call ``reset_for_tests`` between cases.
    """
    global _state

    # Gate 1: ``CLAUDE_CODE_REMOTE``.
    if not _is_env_truthy(os.environ.get('CLAUDE_CODE_REMOTE')):
        return _state

    # Gate 2: ``CCR_UPSTREAM_PROXY_ENABLED``. CCR injects this from
    # ``StartupContext.EnvironmentVariables`` so the GrowthBook
    # decision is server-side (each container is a fresh process with
    # no GB cache, so a client-side check would always default to false).
    if not _is_env_truthy(os.environ.get('CCR_UPSTREAM_PROXY_ENABLED')):
        return _state

    # Gate 3: session ID (used in the ``Proxy-Authorization: Basic``
    # tunnel header).
    session_id = os.environ.get('CLAUDE_CODE_REMOTE_SESSION_ID')
    if not session_id:
        logger.warning(
            '[upstreamproxy] CLAUDE_CODE_REMOTE_SESSION_ID unset; proxy disabled'
        )
        return _state

    # Step 1: read the session token.
    token_path = Path(token_path) if token_path is not None else Path(SESSION_TOKEN_PATH)
    token = await _read_token(token_path)
    if not token:
        logger.debug('[upstreamproxy] no session token file; proxy disabled')
        return _state

    # Step 2: ptrace guard (Linux-only; no-op elsewhere).
    set_non_dumpable()

    # Step 3: resolve base URL and download the CA bundle.
    base_url = (
        ccr_base_url
        or os.environ.get('ANTHROPIC_BASE_URL')
        or 'https://api.anthropic.com'
    )
    ca_bundle_resolved = (
        Path(ca_bundle_path) if ca_bundle_path is not None else _default_ca_bundle_out()
    )
    system_ca_resolved = (
        Path(system_ca_path) if system_ca_path is not None else Path(SYSTEM_CA_BUNDLE)
    )
    ok = await download_ca_bundle(
        base_url=base_url,
        system_ca_path=system_ca_resolved,
        out_path=ca_bundle_resolved,
    )
    if not ok:
        return _state  # download_ca_bundle already logged the reason

    # Step 4: start the relay (ephemeral port).
    ws_url = base_url.replace('http://', 'ws://', 1).replace('https://', 'wss://', 1)
    ws_url = ws_url.rstrip('/') + '/v1/code/upstreamproxy/ws'
    try:
        relay = await start_upstream_proxy_relay(
            ws_url=ws_url, session_id=session_id, token=token
        )
    except Exception as exc:  # noqa: BLE001 -- fail-open per A11
        logger.warning(
            '[upstreamproxy] relay start failed: %s; proxy disabled', exc
        )
        return _state

    # Step 5: unlink the token file (now that the relay is up — a
    # supervisor restart before this point can retry with the token
    # still on disk).
    try:
        token_path.unlink()
    except FileNotFoundError:
        pass  # already gone — fine
    except OSError:
        logger.warning('[upstreamproxy] token file unlink failed', exc_info=True)

    _state = UpstreamProxyState(
        enabled=True,
        port=relay.port,
        ca_bundle_path=str(ca_bundle_resolved),
        relay=relay,
    )
    logger.info('[upstreamproxy] enabled on 127.0.0.1:%d', relay.port)
    return _state


def get_upstream_proxy_env() -> dict[str, str]:
    """Env-var dict for child subprocesses (Bash/MCP/LSP/hooks).

    When the proxy is enabled: returns the 9-var recipe
    (``HTTPS_PROXY``/``https_proxy``/``NO_PROXY``/``no_proxy``/
    ``SSL_CERT_FILE``/``NODE_EXTRA_CA_CERTS``/``REQUESTS_CA_BUNDLE``/
    ``CURL_CA_BUNDLE``). The TS recipe also sets ``proxy`` in lowercase
    via ``HTTPS_PROXY`` lowercase variant; we follow the same pattern.

    When the proxy is disabled: if the parent already has both
    ``HTTPS_PROXY`` and ``SSL_CERT_FILE`` set, we inherit those
    (handles the case where a child CLI process can't re-init the
    relay — token file already unlinked — but the parent's relay is
    still running and reachable). Otherwise returns empty dict.

    Mirrors ``upstreamproxy.ts:160-199``.
    """
    if _state.enabled and _state.port is not None and _state.ca_bundle_path is not None:
        proxy_url = f'http://127.0.0.1:{_state.port}'
        no_proxy = default_no_proxy()
        ca = _state.ca_bundle_path
        return {
            'HTTPS_PROXY': proxy_url,
            'https_proxy': proxy_url,
            'NO_PROXY': no_proxy,
            'no_proxy': no_proxy,
            'SSL_CERT_FILE': ca,
            'NODE_EXTRA_CA_CERTS': ca,
            'REQUESTS_CA_BUNDLE': ca,
            'CURL_CA_BUNDLE': ca,
        }

    # Disabled-but-inherited path: child CLI processes can't re-init
    # the relay (token file already unlinked by parent), but the
    # parent's relay is still reachable on 127.0.0.1:<port>. Pass
    # through the parent's proxy env so subprocess inherits the
    # same routing.
    if os.environ.get('HTTPS_PROXY') and os.environ.get('SSL_CERT_FILE'):
        inherited: dict[str, str] = {}
        for key in (
            'HTTPS_PROXY',
            'https_proxy',
            'NO_PROXY',
            'no_proxy',
            'SSL_CERT_FILE',
            'NODE_EXTRA_CA_CERTS',
            'REQUESTS_CA_BUNDLE',
            'CURL_CA_BUNDLE',
        ):
            value = os.environ.get(key)
            if value is not None:
                inherited[key] = value
        return inherited
    return {}


def reset_for_tests() -> None:
    """Reset module state to DISABLED. Test-only helper."""
    global _state
    _state = _DISABLED


async def _read_token(path: Path) -> str | None:
    """Read and trim the session token from ``path``.

    Returns ``None`` for missing file, empty content, or any read error.
    """
    try:
        # We use ``run_in_executor`` to keep async semantics; the file
        # is small but reading from /run/ccr/* on a slow tmpfs is
        # non-instant under contention.
        import asyncio

        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, path.read_text, 'utf-8')
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning('[upstreamproxy] token read failed: %s', exc)
        return None
    token = raw.strip()
    return token if token else None


__all__ = [
    'SESSION_TOKEN_PATH',
    'SYSTEM_CA_BUNDLE',
    'UpstreamProxyState',
    'get_upstream_proxy_env',
    'init_upstream_proxy',
    'reset_for_tests',
]
