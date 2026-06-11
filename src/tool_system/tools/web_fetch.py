"""WebFetch tool — fetch a URL and return extracted text content.

Features:
- HTML-to-markdown conversion (markdownify or regex fallback)
- URL validation with private host blocking
- Redirect handling with cross-domain detection
- LRU cache with 15-minute TTL
- Preapproved domain list for auto-allow
- Prompt parameter for secondary model processing
"""

from __future__ import annotations

import gzip
import html
import http.client
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError, ToolPermissionError
from ..protocol import ToolResult
from src.permissions.types import (
    PermissionPassthroughResult,
    PermissionResult,
)


# -- HTML to Markdown ----------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
# Blocks whose *contents* are noise (script/style code, inline SVG paths, etc.).
# Removed before conversion so they never leak into the extracted text — markdownify
# strips the tags but can otherwise surface their text nodes.
_NOISE_BLOCK_RE = re.compile(
    r"<(script|style|noscript|svg|template|iframe|object|embed)\b[^>]*>.*?</\1>",
    flags=re.DOTALL | re.IGNORECASE,
)


def _strip_noise_blocks(raw: str) -> str:
    return _NOISE_BLOCK_RE.sub(" ", raw)


try:
    import markdownify as _md

    def _html_to_markdown(raw: str) -> str:
        # Structured markdown: headings, lists, and links are preserved (so the
        # model can cite sources and follow structure), unlike the flat regex
        # fallback below.
        text = _md.markdownify(_strip_noise_blocks(raw), strip=["img", "script", "style"])
        return re.sub(r"\n{3,}", "\n\n", text).strip()  # collapse blank-line runs
except ImportError:
    _md = None  # type: ignore[assignment]

    def _html_to_markdown(raw: str) -> str:
        text = _TAG_RE.sub(" ", _strip_noise_blocks(raw))
        text = re.sub(r"\s+", " ", text).strip()
        return html.unescape(text)


# Tags whose subtrees carry no readable content (ported from opencode's
# extractTextFromHTML skip-list, plus svg/template).
_NOISE_TAGS = ["script", "style", "noscript", "iframe", "object", "embed", "svg", "template"]

try:
    import bs4 as _bs4  # provided by markdownify's beautifulsoup4 dependency

    def _html_to_text(raw: str) -> str:
        """Plain-text extraction: drop noise subtrees, collect text nodes.

        DOM-aware (BeautifulSoup), so it won't mangle text the way a flat regex
        tag-strip can. Mirrors opencode's htmlparser2-based ``extractTextFromHTML``.
        """
        soup = _bs4.BeautifulSoup(raw, "html.parser")
        for tag in soup(_NOISE_TAGS):
            tag.decompose()
        text = soup.get_text(separator="\n")
        return re.sub(r"\n{3,}", "\n\n", text).strip()
except ImportError:
    _bs4 = None  # type: ignore[assignment]

    def _html_to_text(raw: str) -> str:
        text = _TAG_RE.sub(" ", _strip_noise_blocks(raw))
        return html.unescape(re.sub(r"\s+", " ", text).strip())


def _convert(content: str, content_type: str, fmt: str) -> str:
    """Convert a fetched body to the requested ``fmt`` (markdown/text/html).

    Non-HTML bodies (json, plain text, already-markdown) pass through unchanged;
    binary image types return a short placeholder instead of decoded garbage.
    Mirrors opencode's deterministic ``convert`` — no model involved.
    """
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime.startswith("image/") and mime != "image/svg+xml":
        return f"[non-text content: {mime}]"
    if "html" not in mime:
        return content  # text/markdown/json/xml/etc — already usable
    if fmt == "markdown":
        return _html_to_markdown(content)
    if fmt == "text":
        return _html_to_text(content)
    return content  # fmt == "html": return the raw HTML the caller asked for


# -- URL Validation ------------------------------------------------------------

_MAX_URL_LENGTH = 2000


def _is_private_host(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def _validate_url(url: str) -> str:
    if len(url) > _MAX_URL_LENGTH:
        raise ToolInputError(f"URL too long ({len(url)} chars, max {_MAX_URL_LENGTH})")

    parsed = urllib.parse.urlparse(url)

    if parsed.scheme == "http":
        url = "https" + url[4:]
        parsed = urllib.parse.urlparse(url)

    if parsed.scheme != "https":
        raise ToolPermissionError("only http/https URLs are allowed")
    if not parsed.netloc:
        raise ToolInputError("url must include a network location")

    if parsed.username or parsed.password:
        raise ToolPermissionError("URLs with embedded credentials are not allowed")

    hostname = parsed.hostname or ""
    if "." not in hostname and hostname not in ("localhost",):
        raise ToolInputError("hostname must have at least 2 parts (e.g., example.com)")

    if hostname in ("localhost",) or hostname.endswith(".localhost") or _is_private_host(hostname):
        raise ToolPermissionError("refusing to fetch localhost/private network URLs")

    return url


# -- Redirect Handling ---------------------------------------------------------

_MAX_REDIRECTS = 10


def _strip_www(hostname: str) -> str:
    return hostname[4:] if hostname.startswith("www.") else hostname


def _is_permitted_redirect(original_url: str, redirect_url: str) -> bool:
    orig = urllib.parse.urlparse(original_url)
    redir = urllib.parse.urlparse(redirect_url)
    if orig.scheme != redir.scheme:
        return False
    if (orig.port or 443) != (redir.port or 443):
        return False
    if redir.username or redir.password:
        return False
    if _strip_www(orig.hostname or "") != _strip_www(redir.hostname or ""):
        return False
    return True


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Many sites (Cloudflare and friends) answer 403 to non-browser User-Agents, so a
# generic "claw-codex/0.1" UA silently failed on a large slice of the real web.
# When a site instead challenges the *browser* UA (Cloudflare bot-fight), we retry
# once with a plain bot UA — some WAFs pass bots while challenging browsers. Both
# behaviours are borrowed from opencode's webfetch tool.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_FALLBACK_UA = "claw-codex"

_MAX_FETCH_BYTES = 2_000_000


def _accept_header(fmt: str) -> str:
    """Content-negotiation Accept header preferring the requested format."""
    if fmt == "markdown":
        return "text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1"
    if fmt == "text":
        return "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1"
    if fmt == "html":
        return "text/html;q=1.0, application/xhtml+xml;q=0.9, text/plain;q=0.8, text/markdown;q=0.7, */*;q=0.1"
    return "*/*"


def _request_headers(fmt: str, user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": _accept_header(fmt),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }


def _charset_from_content_type(content_type: str) -> str | None:
    match = re.search(r"charset=([^\s;]+)", content_type or "", flags=re.IGNORECASE)
    return match.group(1).strip().strip('"\'') if match else None


def _read_response_body(resp) -> str:
    """Read, transparently decompress (gzip/deflate), and decode a response body."""
    raw = resp.read(_MAX_FETCH_BYTES)
    encoding = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in encoding:
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass  # not actually gzipped -> use bytes as-is
    elif "deflate" in encoding:
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            try:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)  # raw deflate, no header
            except Exception:
                pass
    charset = _charset_from_content_type(resp.headers.get("Content-Type", "")) or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:  # unknown charset label
        return raw.decode("utf-8", errors="replace")


def _is_cloudflare_challenge(e: urllib.error.HTTPError) -> bool:
    """403 with a Cloudflare bot-fight challenge marker."""
    try:
        return e.code == 403 and (e.headers.get("cf-mitigated") or "").lower() == "challenge"
    except Exception:
        return False


def _fetch_with_redirect_handling(
    url: str, timeout: float = 15, fmt: str = "markdown", user_agent: str = _BROWSER_UA
) -> tuple[str, str, int]:
    opener = urllib.request.build_opener(_NoRedirectHandler)
    current_url = url
    for _ in range(_MAX_REDIRECTS):
        req = urllib.request.Request(current_url, headers=_request_headers(fmt, user_agent))
        try:
            resp = opener.open(req, timeout=timeout)
            content_type = resp.headers.get("Content-Type", "")
            return _read_response_body(resp), content_type, resp.status
        except urllib.error.HTTPError as e:
            # Cloudflare challenged the browser UA -> retry once with a bot UA.
            if _is_cloudflare_challenge(e) and user_agent != _FALLBACK_UA:
                return _fetch_with_redirect_handling(url, timeout, fmt, _FALLBACK_UA)
            if e.code in (301, 302, 303, 307, 308):
                redirect_url = e.headers.get("Location", "")
                if not redirect_url:
                    raise ToolInputError(f"Redirect with no Location header (HTTP {e.code})")
                redirect_url = urllib.parse.urljoin(current_url, redirect_url)
                if _is_permitted_redirect(current_url, redirect_url):
                    current_url = redirect_url
                    continue
                else:
                    return (
                        f"This URL redirects to a different host: {redirect_url}\n"
                        f"Make a new WebFetch request with this URL to follow the redirect.",
                        "text/plain",
                        e.code,
                    )
            raise
    raise ToolInputError("Too many redirects")


# -- LRU Cache ----------------------------------------------------------------

_CACHE_TTL = 900  # 15 minutes
_CACHE_MAX_SIZE = 100

_url_cache: dict[str, tuple[float, str, str, int]] = {}


def _cache_get(url: str) -> tuple[str, str, int] | None:
    entry = _url_cache.get(url)
    if entry is None:
        return None
    ts, content, content_type, status = entry
    if time.time() - ts > _CACHE_TTL:
        del _url_cache[url]
        return None
    return content, content_type, status


def _cache_set(url: str, content: str, content_type: str, status: int) -> None:
    if len(_url_cache) >= _CACHE_MAX_SIZE:
        oldest_key = min(_url_cache, key=lambda k: _url_cache[k][0])
        del _url_cache[oldest_key]
    _url_cache[url] = (time.time(), content, content_type, status)


# -- Preapproved Domains ------------------------------------------------------

_PREAPPROVED_HOSTS: set[str] = {
    "docs.anthropic.com",
    "docs.python.org",
    "pypi.org",
    "docs.djangoproject.com",
    "flask.palletsprojects.com",
    "fastapi.tiangolo.com",
    "docs.pydantic.dev",
    "numpy.org",
    "pandas.pydata.org",
    "matplotlib.org",
    "scikit-learn.org",
    "pytorch.org",
    "docs.scipy.org",
    "docs.rs",
    "crates.io",
    "pkg.go.dev",
    "go.dev",
    "docs.oracle.com",
    "developer.mozilla.org",
    "react.dev",
    "nextjs.org",
    "vuejs.org",
    "angular.dev",
    "svelte.dev",
    "tailwindcss.com",
    "nodejs.org",
    "typescriptlang.org",
    "www.typescriptlang.org",
    "bun.sh",
    "deno.land",
    "deno.com",
    "docs.aws.amazon.com",
    "cloud.google.com",
    "learn.microsoft.com",
    "docs.github.com",
    "docs.docker.com",
    "kubernetes.io",
    "terraform.io",
    "docs.gitlab.com",
    "stackoverflow.com",
    "en.wikipedia.org",
    "docs.npmjs.com",
    "www.postgresql.org",
    "dev.mysql.com",
    "redis.io",
    "www.mongodb.com",
    "www.sqlite.org",
    "graphql.org",
    "swagger.io",
    "json-schema.org",
    "yaml.org",
    "semver.org",
    "httpwg.org",
    "tools.ietf.org",
    "www.rfc-editor.org",
    "docs.github.com",
    "man7.org",
    "ss64.com",
    "devdocs.io",
    "hackage.haskell.org",
    "docs.julialang.org",
    "elixir-lang.org",
    "hexdocs.pm",
    "www.ruby-lang.org",
    "rubydoc.info",
    "doc.rust-lang.org",
    "cppreference.com",
    "www.cppreference.com",
    "docs.swift.org",
    "developer.apple.com",
}

_PATH_PREFIXED_HOSTS: dict[str, list[str]] = {
    "github.com": ["/anthropics"],
    "vercel.com": ["/docs"],
}


def _is_preapproved(hostname: str, pathname: str) -> bool:
    if hostname in _PREAPPROVED_HOSTS:
        return True
    prefixes = _PATH_PREFIXED_HOSTS.get(hostname)
    if prefixes:
        for prefix in prefixes:
            if pathname == prefix or pathname.startswith(prefix + "/"):
                return True
    return False


# -- Permission Check ----------------------------------------------------------

def _check_permissions(tool_input: dict[str, Any], context: ToolContext) -> PermissionResult:
    url = tool_input.get("url", "")
    if not isinstance(url, str) or not url:
        return PermissionPassthroughResult()
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname or ""
        pathname = parsed.path or "/"
        if _is_preapproved(hostname, pathname):
            return PermissionPassthroughResult()
    except Exception:
        pass
    return PermissionPassthroughResult()


# -- Result Mapping ------------------------------------------------------------

def _map_result_to_api(result: Any, tool_use_id: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(result)}
    content = result.get("result", result.get("content", ""))
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


# -- Main Call -----------------------------------------------------------------

_VALID_FORMATS = ("markdown", "text", "html")


def _web_fetch_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    url = tool_input.get("url", "")
    prompt = tool_input.get("prompt", "")
    fmt = tool_input.get("format") or "markdown"
    if fmt not in _VALID_FORMATS:
        fmt = "markdown"

    if not isinstance(url, str) or not url:
        raise ToolInputError("url must be a non-empty string")

    url = _validate_url(url)

    start_time = time.time()

    # Cache the *converted* content; key by format so a markdown fetch and a text
    # fetch of the same URL don't collide.
    cache_key = f"{fmt}:{url}"
    cached = _cache_get(cache_key)
    if cached:
        content, content_type, status = cached
    else:
        raw, content_type, status = _fetch_with_redirect_handling(url, fmt=fmt)
        content = _convert(raw, content_type, fmt)
        _cache_set(cache_key, content, content_type, status)

    if len(content) > 100_000:
        content = content[:100_000] + "\n\n... [truncated] ..."

    duration_ms = int((time.time() - start_time) * 1000)

    result_text = content
    if prompt and isinstance(prompt, str):
        result_text = f"User prompt: {prompt}\n\nContent from {url}:\n\n{content}"

    return ToolResult(
        name="WebFetch",
        output={
            "url": url,
            "content_type": content_type,
            "result": result_text,
            "bytes": len(content.encode("utf-8")),
            "code": status,
            "duration_ms": duration_ms,
        },
    )


# -- Prompt --------------------------------------------------------------------

_WEB_FETCH_PROMPT = """IMPORTANT: WebFetch WILL FAIL for authenticated or private URLs. Before using this tool, check if the URL points to an authenticated service (e.g. Google Docs, Confluence, Jira, GitHub). If so, look for a specialized MCP tool that provides authenticated access.

- Fetches content from a specified URL and returns it as text
- Takes a URL and an optional `format` (markdown | text | html; default markdown)
- Fetches the URL content and converts HTML deterministically (no model call):
  `markdown` preserves headings/lists/links, `text` is clean plain text, `html` is raw
- Use this tool when you need to retrieve and read web content

Usage notes:
  - IMPORTANT: If an MCP-provided web fetch tool is available, prefer using that tool instead of this one, as it may have fewer restrictions.
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - Use `format: "markdown"` (default) for reading; `format: "text"` to strip all structure; `format: "html"` for the raw page
  - The `prompt` (optional) is passed through as context for what you're looking for; it does not trigger a separate model call
  - This tool is read-only and does not modify any files
  - Large results may be truncated
  - Includes a self-cleaning 15-minute cache for faster responses when repeatedly accessing the same URL
  - When a URL redirects to a different host, the tool will inform you and provide the redirect URL in a special format. You should then make a new WebFetch request with the redirect URL to fetch the content.
  - For GitHub URLs, prefer using the gh CLI via Bash instead (e.g., gh pr view, gh issue view, gh api)."""


def _web_fetch_classifier_input(input_data: dict) -> str:
    """Mirror TS ``WebFetchTool.toAutoClassifierInput`` -- include the
    secondary-model prompt when present so the classifier can spot
    URL-as-data-exfiltration patterns where the URL is innocuous but
    the prompt does the work."""
    d = input_data or {}
    url = d.get("url", "")
    prompt = d.get("prompt")
    if prompt:
        return f"{url}: {prompt}"
    return url


# -- Tool Definition -----------------------------------------------------------

WebFetchTool: Tool = build_tool(
    name="WebFetch",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch content from",
            },
            "prompt": {
                "type": "string",
                "description": "Optional. What you're looking for on the page (passed through as context; does not trigger a separate model call)",
            },
            "format": {
                "type": "string",
                "enum": ["text", "markdown", "html"],
                "description": "Format to return the content in. Defaults to markdown.",
            },
        },
        "required": ["url"],
    },
    call=_web_fetch_call,
    prompt=_WEB_FETCH_PROMPT,
    description="Fetch a URL and return extracted text content.",
    map_result_to_api=_map_result_to_api,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    check_permissions=_check_permissions,
    search_hint="web fetch url http download",
    get_activity_description=lambda input_data: f"Fetching {(input_data or {}).get('url', '')}..." if input_data else None,
    to_auto_classifier_input=_web_fetch_classifier_input,
)
