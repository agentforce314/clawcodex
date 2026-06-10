"""Tests for WebFetch parse/extract improvements: structured markdown,
noise stripping, browser headers, and transparent gzip/deflate decoding."""

from __future__ import annotations

import gzip
import urllib.error
import zlib
from pathlib import Path

from src.tool_system.context import ToolContext
from src.tool_system.tools import web_fetch
from src.tool_system.tools.web_fetch import (
    WebFetchTool,
    _accept_header,
    _charset_from_content_type,
    _convert,
    _fetch_with_redirect_handling,
    _html_to_markdown,
    _html_to_text,
    _read_response_body,
    _strip_noise_blocks,
)


class _Resp:
    """Minimal urlopen-style response: .read(n), .headers, .status."""

    def __init__(self, raw: bytes, headers: dict, status: int = 200):
        self._raw = raw
        self.headers = headers
        self.status = status

    def read(self, _n=None):
        return self._raw


# --- HTML -> markdown -----------------------------------------------------


def test_strip_noise_blocks_removes_script_and_style():
    html = '<p>keep</p><script>var s="drop-js"</script><style>.x{color:red}</style>'
    out = _strip_noise_blocks(html)
    assert "drop-js" not in out
    assert "color:red" not in out
    assert "keep" in out


def test_html_to_markdown_preserves_structure_and_drops_noise():
    html = (
        '<html><body><h1>Title</h1>'
        '<script>alert("secret-js")</script>'
        '<p>Read <a href="https://example.org/doc">the docs</a> now.</p>'
        '<style>.z{color:red}</style></body></html>'
    )
    md = _html_to_markdown(html)
    assert "secret-js" not in md          # script contents dropped
    assert "color:red" not in md          # style contents dropped
    assert "Title" in md
    if web_fetch._md is not None:          # markdownify installed -> link kept
        assert "[the docs](https://example.org/doc)" in md
    else:                                  # regex fallback -> text only
        assert "the docs" in md


# --- body decoding: gzip / deflate / charset ------------------------------


def test_read_response_body_gzip():
    body = b"<h1>Hello</h1>"
    resp = _Resp(gzip.compress(body), {"Content-Encoding": "gzip", "Content-Type": "text/html"})
    assert "<h1>Hello</h1>" in _read_response_body(resp)


def test_read_response_body_deflate():
    body = b"<p>deflated</p>"
    resp = _Resp(zlib.compress(body), {"Content-Encoding": "deflate", "Content-Type": "text/html"})
    assert "deflated" in _read_response_body(resp)


def test_read_response_body_plain_identity():
    resp = _Resp(b"<p>plain</p>", {"Content-Type": "text/html"})
    assert "plain" in _read_response_body(resp)


def test_read_response_body_respects_charset():
    body = "Café".encode("latin-1")
    resp = _Resp(body, {"Content-Type": "text/html; charset=latin-1"})
    assert "Café" in _read_response_body(resp)


def test_charset_from_content_type():
    assert _charset_from_content_type("text/html; charset=ISO-8859-1") == "ISO-8859-1"
    assert _charset_from_content_type('text/html; charset="utf-8"') == "utf-8"
    assert _charset_from_content_type("text/html") is None
    assert _charset_from_content_type("") is None


# --- fetch sends browser-like headers -------------------------------------


def test_fetch_sends_browser_headers(monkeypatch):
    captured: dict = {}

    def fake_open(self, req, timeout=None):
        captured["ua"] = req.get_header("User-agent")
        captured["accept_encoding"] = req.get_header("Accept-encoding")
        return _Resp(b"<p>ok</p>", {"Content-Type": "text/html"})

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open)
    text, content_type, status = _fetch_with_redirect_handling("https://example.com/")

    assert "Mozilla/5.0" in (captured["ua"] or "")     # not "claw-codex/0.1"
    assert "gzip" in (captured["accept_encoding"] or "")
    assert status == 200
    assert "ok" in text


# --- format dispatch (borrowed from opencode) -----------------------------

_HTML = (
    '<html><body><h1>Heading</h1>'
    '<script>track("x")</script>'
    '<p>Visit <a href="https://ex.org/d">the docs</a>.</p></body></html>'
)


def test_convert_markdown_preserves_links():
    out = _convert(_HTML, "text/html", "markdown")
    assert "Heading" in out
    assert 'track("x")' not in out
    if web_fetch._md is not None:
        assert "[the docs](https://ex.org/d)" in out


def test_convert_text_is_plain_no_markdown_syntax():
    out = _convert(_HTML, "text/html", "text")
    assert "Heading" in out and "the docs" in out
    assert 'track("x")' not in out      # script subtree dropped
    assert "](https://" not in out      # plain text, not markdown links


def test_convert_html_returns_raw():
    assert _convert("<p>raw</p>", "text/html", "html") == "<p>raw</p>"


def test_convert_non_html_passthrough():
    body = '{"k": 1}'
    assert _convert(body, "application/json", "markdown") == body


def test_convert_image_guard():
    out = _convert("\x89PNG\x0d", "image/png", "markdown")
    assert "non-text content" in out and "image/png" in out


def test_html_to_text_drops_noise_tags():
    out = _html_to_text('<div>keep<iframe>frame</iframe><style>.a{}</style></div>')
    assert "keep" in out
    assert "frame" not in out and ".a{}" not in out


def test_accept_header_per_format():
    assert "text/markdown" in _accept_header("markdown")
    assert _accept_header("text").startswith("text/plain")
    assert _accept_header("html").startswith("text/html")


# --- Cloudflare challenge retry (borrowed from opencode) ------------------


def test_cloudflare_challenge_retry(monkeypatch):
    seen_uas: list = []

    def fake_open(self, req, timeout=None):
        seen_uas.append(req.get_header("User-agent"))
        if len(seen_uas) == 1:
            raise urllib.error.HTTPError(
                req.full_url, 403, "Forbidden", {"cf-mitigated": "challenge"}, None
            )
        return _Resp(b"<p>passed</p>", {"Content-Type": "text/html"})

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open)
    text, content_type, status = _fetch_with_redirect_handling("https://example.com/")

    assert len(seen_uas) == 2                 # retried once
    assert seen_uas[0] != seen_uas[1]         # with a different UA
    assert "Mozilla/5.0" in seen_uas[0]       # first try: browser UA
    assert "passed" in text


def test_plain_403_not_retried(monkeypatch):
    seen = []

    def fake_open(self, req, timeout=None):
        seen.append(1)
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open)
    try:
        _fetch_with_redirect_handling("https://example.com/")
    except urllib.error.HTTPError:
        pass
    assert len(seen) == 1                      # no challenge marker -> no retry


# --- end-to-end format param through WebFetchTool.call --------------------


def test_format_param_end_to_end(monkeypatch):
    html = b'<html><body><h1>T</h1><p>Body</p></body></html>'

    def fake_open(self, req, timeout=None):
        return _Resp(html, {"Content-Type": "text/html"})

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open)
    ctx = ToolContext(workspace_root=Path("/tmp"))
    out = WebFetchTool.call({"url": "https://example.com/x", "format": "text"}, ctx).output
    assert "Body" in out["result"]
    assert "](https://" not in out["result"]   # text format, not markdown
