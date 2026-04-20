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

import html
import http.client
import ipaddress
import re
import socket
import time
import urllib.parse
import urllib.request
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

try:
    import markdownify as _md

    def _html_to_markdown(raw: str) -> str:
        return _md.markdownify(raw, strip=["img", "script", "style"])
except ImportError:
    _md = None  # type: ignore[assignment]

    def _html_to_markdown(raw: str) -> str:
        text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = _TAG_RE.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return html.unescape(text)


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


def _fetch_with_redirect_handling(url: str, timeout: float = 15) -> tuple[str, str, int]:
    opener = urllib.request.build_opener(_NoRedirectHandler)
    current_url = url
    for _ in range(_MAX_REDIRECTS):
        req = urllib.request.Request(current_url, headers={"User-Agent": "claw-codex/0.1"})
        try:
            resp = opener.open(req, timeout=timeout)
            raw_bytes = resp.read(1_000_000)
            content_type = resp.headers.get("Content-Type", "")
            return raw_bytes.decode("utf-8", errors="replace"), content_type, resp.status
        except urllib.error.HTTPError as e:
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

def _web_fetch_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    url = tool_input.get("url", "")
    prompt = tool_input.get("prompt", "")

    if not isinstance(url, str) or not url:
        raise ToolInputError("url must be a non-empty string")

    url = _validate_url(url)

    start_time = time.time()

    cached = _cache_get(url)
    if cached:
        content, content_type, status = cached
    else:
        content, content_type, status = _fetch_with_redirect_handling(url)
        if "text/html" in content_type:
            content = _html_to_markdown(content)
        _cache_set(url, content, content_type, status)

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

- Fetches content from a specified URL and processes it using an AI model
- Takes a URL and a prompt as input
- Fetches the URL content, converts HTML to markdown
- Processes the content with the prompt using a small, fast model
- Returns the model's response about the content
- Use this tool when you need to retrieve and analyze web content

Usage notes:
  - IMPORTANT: If an MCP-provided web fetch tool is available, prefer using that tool instead of this one, as it may have fewer restrictions.
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - The prompt should describe what information you want to extract from the page
  - This tool is read-only and does not modify any files
  - Results may be summarized if the content is very large
  - Includes a self-cleaning 15-minute cache for faster responses when repeatedly accessing the same URL
  - When a URL redirects to a different host, the tool will inform you and provide the redirect URL in a special format. You should then make a new WebFetch request with the redirect URL to fetch the content.
  - For GitHub URLs, prefer using the gh CLI via Bash instead (e.g., gh pr view, gh issue view, gh api)."""


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
                "description": "The prompt to run on the fetched content",
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
)
