"""Phase 0 smoke test: WS + SSE deps importable, asyncio API present."""

from __future__ import annotations


def test_websockets_asyncio_paths_importable() -> None:
    """Per A1: ``websockets >= 14.0`` exposes ``asyncio.{client,server}``."""
    import websockets.asyncio.client as ws_client
    import websockets.asyncio.server as ws_server

    assert hasattr(ws_client, 'connect'), 'websockets.asyncio.client.connect missing'
    assert hasattr(ws_server, 'serve'), 'websockets.asyncio.server.serve missing'


def test_websockets_top_level_alias_points_at_asyncio_api() -> None:
    """Top-level ``websockets.connect`` aliases the asyncio API on >=14.0."""
    import websockets

    assert hasattr(websockets, 'connect'), 'websockets.connect missing'
    assert hasattr(websockets, 'serve'), 'websockets.serve missing'


def test_httpx_sse_importable() -> None:
    import httpx_sse

    assert hasattr(httpx_sse, 'aconnect_sse'), 'httpx_sse.aconnect_sse missing'


def test_httpx_transitively_available() -> None:
    """httpx is pulled in by the anthropic SDK; we don't declare it."""
    import httpx

    assert hasattr(httpx, 'AsyncClient')
