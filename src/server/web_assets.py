"""Static hosting for the ClawCodex web client (``ui-web/``).

``clawcodex serve`` is the desktop app's backend and stays exactly that when no
web bundle is present: every route below is *additive* and only mounts once a
built ``ui-web/dist`` is found. That keeps one gateway serving three clients —
TUI, desktop, browser — with no per-client server.

Layout of the bundle (vite, ``base: './'``)::

    dist/index.html
    dist/assets/*           hashed js/css/font chunks
    dist/favicon.svg
    dist/manifest.webmanifest

The browser client needs the session token before it can open the gateway
socket, and it has nowhere else to get one: unlike the desktop shell it is not
spawned by anything that knows the token. So ``index.html`` is served with the
token inlined as ``window.__CLAWCODEX_SESSION_TOKEN__`` — the same global the
desktop's ``dashboard-token.ts`` already scrapes from ``GET /``, so one page
serves both readers.

That page is unauthenticated by construction (it is what *hands out* the
token), which is safe exactly as long as the server is bound to loopback. The
``clawcodex web`` entry enforces that; see ``src/entrypoints/web_cli.py``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from starlette.responses import FileResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

DIST_ENV = "CLAWCODEX_WEB_DIST"

# Injected right after <head> so the global exists before any module script
# runs.
_TOKEN_SCRIPT = '<script>window.__CLAWCODEX_SESSION_TOKEN__ = {token};</script>'


def js_string(value: str) -> str:
    """``value`` as a JS string literal that is safe inside ``<script>``.

    ``json.dumps`` alone is not: it escapes quotes and backslashes but leaves
    ``<`` untouched, so a value containing ``</script>`` closes the element and
    everything after it is parsed as markup. Escaping the three HTML-special
    characters as ``\\uXXXX`` keeps the literal equal to the same string while
    making it inert to the HTML tokenizer.

    Real tokens are ``secrets.token_urlsafe`` output and contain none of this —
    but the token can also arrive from ``--token`` or the environment, and an
    injection that depends on trusting your own inputs is not a defence.
    """
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _candidates() -> list[Path]:
    """Every place a built bundle can live, most specific first.

    ``$CLAWCODEX_WEB_DIST`` is authoritative rather than merely first: someone
    who names a directory is telling us which bundle to serve, and quietly
    serving a different one when theirs is missing would be the hardest kind
    of bug to see — the app loads, from the wrong build.
    """
    override = os.environ.get(DIST_ENV)
    if override:
        return [Path(override)]
    return [
        # Source checkout: <repo>/ui-web/dist (this file is <repo>/src/server/…).
        Path(__file__).resolve().parents[2] / "ui-web" / "dist",
        # Installed wheel: the bundle is packaged beside this module.
        Path(__file__).resolve().parent / "web_dist",
    ]


def web_dist_dir() -> Path | None:
    """The built web bundle, or None when the app has not been built."""
    for path in _candidates():
        if (path / "index.html").is_file():
            return path
    return None


def web_index_html(token: str, dist: Path | None = None) -> str | None:
    """``index.html`` with the session token inlined, or None without a bundle.

    Returns None rather than raising when the bundle is missing or unreadable,
    so the caller falls back to the desktop's own token page and the server
    keeps working.
    """
    root = dist if dist is not None else web_dist_dir()
    if root is None:
        return None
    try:
        html = (root / "index.html").read_text(encoding="utf-8")
    except OSError:
        logger.warning("web: could not read %s/index.html", root, exc_info=True)
        return None

    script = _TOKEN_SCRIPT.format(token=js_string(token))
    marker = "<head>"
    index = html.find(marker)
    if index == -1:
        # No <head> to inject into — prepend, which still runs before the
        # module scripts in <body>/<head>.
        return script + html
    at = index + len(marker)
    return html[:at] + script + html[at:]


def web_routes(dist: Path | None = None) -> list[Route | Mount]:
    """Static routes for the bundle. Empty when there is nothing to serve.

    Mounted BEFORE the gateway's catch-all 404 and after ``/api/*`` — see
    ``build_app`` in :mod:`src.server.desktop_serve`.
    """
    root = dist if dist is not None else web_dist_dir()
    # An explicit ``dist=`` bypasses web_dist_dir's own existence check, so
    # confirm it before enumerating — a missing directory must mean "no
    # routes", not a FileNotFoundError out of route construction.
    if root is None or not root.is_dir():
        return []

    assets = root / "assets"
    routes: list[Route | Mount] = []
    if assets.is_dir():
        # `html=False`: a miss under /assets is a genuine 404, not the shell.
        routes.append(Mount("/assets", app=StaticFiles(directory=str(assets)), name="web-assets"))

    # Every root-level file the build emitted — icons, the manifest, whatever a
    # future build adds. Enumerated rather than named: a hardcoded list is a
    # list that goes stale, and it did (renaming favicon.svg to the real PNG
    # icon set silently 404'd every one of them).
    #
    # Enumerated rather than mounted as a directory, too: one explicit route
    # per file cannot shadow /api/*, cannot serve a traversal path, and cannot
    # start serving something merely because it appeared in the folder after
    # boot.
    for path in sorted(root.iterdir()):
        # index.html belongs to the `/` route, which injects the session token.
        if not path.is_file() or path.name == "index.html":
            continue
        routes.append(
            Route(
                f"/{path.name}",
                _file_route(path),
                name=f"web-{path.name.replace('.', '-')}",
            )
        )
    return routes


def _file_route(path: Path):
    async def handler(_request) -> Response:
        return FileResponse(path)

    return handler


__all__ = ["DIST_ENV", "js_string", "web_dist_dir", "web_index_html", "web_routes"]
