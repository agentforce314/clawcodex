"""Static hosting for the browser client (``src/server/web_assets.py``).

Two properties matter here and both are load-bearing:

1. With no built bundle, ``clawcodex serve`` is byte-for-byte the desktop
   backend it has always been — the web port must not change the app the
   Electron shell talks to.
2. With a bundle, ``GET /`` serves it with the session token inlined, because
   a browser has no other way to learn the token, and the desktop's own
   ``__CLAWCODEX_SESSION_TOKEN__`` scrape keeps working off the same page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.server.desktop_serve import DesktopServeState, build_app
from src.server.web_assets import (
    DIST_ENV,
    js_string,
    web_dist_dir,
    web_index_html,
    web_routes,
)

TOKEN = "web-token-abc"

INDEX_HTML = (
    '<!doctype html>\n<html lang="en">\n  <head>\n'
    '    <title>ClawCodex</title>\n  </head>\n'
    '  <body><div id="root"></div></body>\n</html>\n'
)


@pytest.fixture()
def bundle(tmp_path: Path) -> Path:
    """A minimal built bundle: index + one hashed chunk + the icons."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)\n", encoding="utf-8")
    (dist / "favicon-32.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (dist / "manifest.webmanifest").write_text('{"name":"ClawCodex"}', encoding="utf-8")
    return dist


def _state(tmp_path: Path) -> DesktopServeState:
    async def _spawn(*_a, **_kw):  # pragma: no cover — REST-only tests
        raise AssertionError("spawn_agent must not run for asset tests")

    return DesktopServeState(
        token=TOKEN,
        workspace=str(tmp_path),
        manager=None,
        spawn_agent=_spawn,
        protocol_version="0.1.0",
    )


# ── discovery ────────────────────────────────────────────────────────────────


def test_dist_env_override_wins(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIST_ENV, str(bundle))
    assert web_dist_dir() == bundle


def test_dist_env_override_is_authoritative(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Naming a directory that holds no bundle means "no bundle" — falling back
    to the checkout would serve a different build than the one asked for, and
    the app would load looking correct."""
    monkeypatch.setenv(DIST_ENV, str(tmp_path / "nowhere"))
    assert web_dist_dir() is None


# ── token injection ──────────────────────────────────────────────────────────


def test_index_inlines_token_before_body(bundle: Path) -> None:
    html = web_index_html(TOKEN, dist=bundle)
    assert html is not None
    assert f"window.__CLAWCODEX_SESSION_TOKEN__ = {json.dumps(TOKEN)}" in html
    # Must run before any module script, i.e. before <body>.
    assert html.index("__CLAWCODEX_SESSION_TOKEN__") < html.index("<body>")


def test_index_token_cannot_close_the_script_element(bundle: Path) -> None:
    """A token is opaque input; it must not be able to escape into markup.

    ``json.dumps`` alone leaves ``<`` untouched, so a token spelled
    ``</script>…`` would close the element and the rest would parse as HTML.
    """
    dangerous = '</script><script>alert(1)</script>'
    html = web_index_html(dangerous, dist=bundle)
    assert html is not None
    assert "<script>alert(1)</script>" not in html
    assert "\\u003c/script\\u003e" in html


def test_js_string_round_trips_through_a_js_parser() -> None:
    """Escaping must not change the VALUE, only its notation."""
    for value in ("plain-token", '</script>', 'a"b\\c', "<&>", "unicode…"):
        assert json.loads(js_string(value)) == value


def test_index_without_bundle_is_none(tmp_path: Path) -> None:
    assert web_index_html(TOKEN, dist=tmp_path / "absent") is None


# ── routes ───────────────────────────────────────────────────────────────────


def test_routes_cover_assets_and_every_root_file(bundle: Path) -> None:
    """Enumerated, not named: a hardcoded icon list goes stale the first time
    the brand assets are renamed — and silently 404s all of them."""
    paths = {getattr(route, "path", None) for route in web_routes(bundle)}
    assert paths == {"/assets", "/favicon-32.png", "/manifest.webmanifest"}


def test_new_root_files_are_served_without_a_code_change(bundle: Path) -> None:
    (bundle / "apple-touch-icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (bundle / "robots.txt").write_text("User-agent: *", encoding="utf-8")

    paths = {getattr(route, "path", None) for route in web_routes(bundle)}
    assert {"/apple-touch-icon.png", "/robots.txt"} <= paths


def test_index_is_not_served_as_a_static_file(bundle: Path) -> None:
    """`/` owns index.html because it injects the session token; a static copy
    beside it would serve the page without one."""
    paths = {getattr(route, "path", None) for route in web_routes(bundle)}
    assert "/index.html" not in paths


def test_no_routes_without_bundle(tmp_path: Path) -> None:
    assert web_routes(tmp_path / "absent") == []


# ── served app ───────────────────────────────────────────────────────────────


def test_app_serves_bundle_when_present(bundle: Path, tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIST_ENV, str(bundle))
    client = TestClient(build_app(_state(tmp_path)))

    index = client.get("/")
    assert index.status_code == 200
    assert '<div id="root">' in index.text
    assert json.dumps(TOKEN) in index.text
    # A per-process token must never be cached into a later run's browser.
    assert index.headers["cache-control"] == "no-store"

    asset = client.get("/assets/index-abc123.js")
    assert asset.status_code == 200
    assert asset.text.strip() == "console.log(1)"

    assert client.get("/favicon-32.png").status_code == 200
    assert client.get("/manifest.webmanifest").status_code == 200


def test_assets_do_not_shadow_api(bundle: Path, tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """The static mount is declared after every /api route, so the gateway's
    own surface still answers (and still refuses an unauthenticated caller)."""
    monkeypatch.setenv(DIST_ENV, str(bundle))
    client = TestClient(build_app(_state(tmp_path)))

    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/status").status_code == 401


def test_app_without_bundle_keeps_desktop_page(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """No bundle → the backend the Electron shell has always talked to."""
    monkeypatch.setattr("src.server.web_assets.web_dist_dir", lambda: None)
    monkeypatch.setattr("src.server.web_assets.web_routes", lambda dist=None: [])
    client = TestClient(build_app(_state(tmp_path)))

    index = client.get("/")
    assert index.status_code == 200
    assert "ClawCodex backend" in index.text
    assert json.dumps(TOKEN) in index.text
    assert client.get("/assets/index-abc123.js").status_code == 404
