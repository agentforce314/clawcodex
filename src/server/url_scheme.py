"""``cc://`` and ``cc+unix://`` URL scheme parser for Direct Connect.

Mirrors the parser logic implied by ``main.tsx:613, 618, 635, 3872``
which accept both schemes. There is no canonical TS file for the
parser — the URL is split by the surrounding code — but the contract
is clear:

    cc://host:port/session_id?key=value
    cc+unix:///path/to/socket/session_id?key=value

For Unix sockets, the socket path can itself contain ``/``; we treat
**everything after the LAST ``/``** as the session ID.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qs


CCScheme = Literal['cc', 'cc+unix']


@dataclass(frozen=True)
class CCAddress:
    """Parsed ``cc://`` or ``cc+unix://`` address."""

    scheme: CCScheme
    #: For ``cc://``: the hostname (e.g. ``127.0.0.1``).
    #: For ``cc+unix://``: the absolute Unix-socket path.
    host_or_socket: str
    port: int | None
    session_id: str
    query: dict[str, str] = field(default_factory=dict)


def parse_cc_url(raw: str) -> CCAddress:
    """Parse a ``cc://`` or ``cc+unix://`` URL.

    Raises ``ValueError`` for any malformed input. ``urllib.parse`` is
    NOT used directly because it doesn't handle our two custom schemes
    consistently across Python versions; we parse by hand.
    """
    if raw.startswith('cc+unix://'):
        return _parse_cc_unix(raw[len('cc+unix://'):])
    if raw.startswith('cc://'):
        return _parse_cc_tcp(raw[len('cc://'):])
    raise ValueError(f'unsupported scheme: {raw!r}')


def _split_query(rest: str) -> tuple[str, dict[str, str]]:
    """Split ``path?query`` into (path, parsed_query)."""
    if '?' not in rest:
        return rest, {}
    path, _, query_str = rest.partition('?')
    parsed = parse_qs(query_str, keep_blank_values=True)
    # parse_qs returns list[str] per key; flatten to first value (last write wins).
    flat: dict[str, str] = {k: v[-1] for k, v in parsed.items()}
    return path, flat


def _parse_cc_tcp(remainder: str) -> CCAddress:
    """Parse the body of ``cc://`` (host[:port]/session_id[?query])."""
    body, query = _split_query(remainder)
    if '/' not in body:
        raise ValueError('cc:// URL missing session ID component')
    authority, _, session_id = body.rpartition('/')
    if not session_id:
        raise ValueError('cc:// URL has empty session ID')
    if not authority:
        raise ValueError('cc:// URL missing host')
    if ':' in authority:
        host, port_str = authority.rsplit(':', 1)
        try:
            port = int(port_str, 10)
        except ValueError as exc:
            raise ValueError(f'cc:// URL invalid port: {port_str!r}') from exc
        if not (1 <= port <= 65535):
            raise ValueError(f'cc:// URL port out of range: {port}')
    else:
        host = authority
        port = None
    if not host:
        raise ValueError('cc:// URL has empty host')
    return CCAddress(
        scheme='cc',
        host_or_socket=host,
        port=port,
        session_id=session_id,
        query=query,
    )


def _parse_cc_unix(remainder: str) -> CCAddress:
    """Parse the body of ``cc+unix://`` (/path/to/socket/session_id[?query]).

    The Unix socket path can contain ``/``; the session ID is the last
    path component.
    """
    body, query = _split_query(remainder)
    # cc+unix:// requires an absolute path — leading slash is consumed
    # by the scheme prefix split, so ``body`` must START with another
    # ``/`` in absolute form. Reject empty too.
    if not body or '/' not in body:
        raise ValueError('cc+unix:// URL missing path or session ID')
    socket_path, _, session_id = body.rpartition('/')
    if not session_id:
        raise ValueError('cc+unix:// URL has empty session ID')
    # Add the leading slash back: ``cc+unix:///foo/bar/sid`` →
    # remainder ``/foo/bar/sid`` → split → socket_path = '/foo/bar'.
    if not socket_path:
        raise ValueError('cc+unix:// URL has empty socket path')
    return CCAddress(
        scheme='cc+unix',
        host_or_socket=socket_path,
        port=None,
        session_id=session_id,
        query=query,
    )


__all__ = ['CCAddress', 'CCScheme', 'parse_cc_url']
