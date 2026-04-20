from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from ..build_tool import Tool, ValidationResult, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult


# ---------------------------------------------------------------------------
# Domain filtering
# ---------------------------------------------------------------------------


def _safe_hostname(url: str | None) -> str | None:
    """Safely extract hostname from a URL string. Returns None on parse failure."""
    if not url:
        return None
    try:
        return urlparse(url).hostname
    except Exception:
        return None


def _host_matches_domain(host: str, domain: str) -> bool:
    """Check if a hostname exactly matches a domain or is a subdomain of it.

    Example: _host_matches_domain('sub.example.com', 'example.com') -> True
             _host_matches_domain('badexample.com', 'example.com') -> False
    """
    if host == domain:
        return True
    return host.endswith("." + domain)


def _apply_domain_filters(
    hits: list[dict[str, str]],
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict[str, str]]:
    """Filter search results by allowed_domains and blocked_domains.

    Blocked filter: malformed URLs are kept (can't confirm blocked).
    Allowed filter: malformed URLs are dropped (can't confirm allowed).
    """
    out = hits
    if blocked_domains:
        def _not_blocked(h: dict[str, str]) -> bool:
            host = _safe_hostname(h.get("url"))
            if not host:
                return True  # can't confirm blocked -> keep
            return not any(_host_matches_domain(host, d) for d in blocked_domains)
        out = [h for h in out if _not_blocked(h)]
    if allowed_domains:
        def _is_allowed(h: dict[str, str]) -> bool:
            host = _safe_hostname(h.get("url"))
            if not host:
                return False  # can't confirm allowed -> drop
            return any(_host_matches_domain(host, d) for d in allowed_domains)
        out = [h for h in out if _is_allowed(h)]
    return out


# ---------------------------------------------------------------------------
# Normalize hit helper (for flexible response parsing)
# ---------------------------------------------------------------------------

_TITLE_KEYS = ("title", "headline", "name", "heading")
_URL_KEYS = ("url", "link", "href", "uri", "permalink")
_DESC_KEYS = ("description", "snippet", "content", "preview", "summary", "text", "body")


def _first_match(obj: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        val = obj.get(k)
        if isinstance(val, str) and val:
            return val
    return None


def _normalize_hit(raw: Any) -> dict[str, str] | None:
    """Extract a search hit from any object shape using well-known field aliases."""
    if not raw or not isinstance(raw, dict):
        return None
    title = _first_match(raw, _TITLE_KEYS)
    url = _first_match(raw, _URL_KEYS)
    if not title and not url:
        return None
    hit: dict[str, str] = {
        "title": title or url or "",
        "url": url or title or "",
    }
    desc = _first_match(raw, _DESC_KEYS)
    if desc:
        hit["snippet"] = desc
    return hit


# ---------------------------------------------------------------------------
# DuckDuckGo search (HTML scraping fallback)
# ---------------------------------------------------------------------------

_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
    r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _ddg_html_search(query: str, num: int = 10) -> list[dict[str, str]]:
    """Search DuckDuckGo via HTML scraping (legacy fallback)."""
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": "claw-codex/0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read(1_000_000).decode("utf-8", errors="replace")

    results: list[dict[str, str]] = []
    for match in _RESULT_RE.finditer(raw):
        results.append(
            {
                "title": _strip_tags(match.group("title")),
                "url": html.unescape(match.group("url")),
                "snippet": _strip_tags(match.group("snippet")),
            }
        )
        if len(results) >= num:
            break
    return results


# ---------------------------------------------------------------------------
# DuckDuckGo search (via duckduckgo-search package, preferred)
# ---------------------------------------------------------------------------

def _ddg_package_search(query: str, num: int = 10) -> list[dict[str, str]] | None:
    """Search DuckDuckGo via the duckduckgo-search package.

    Returns None if the package is not installed, so caller can fall back.
    """
    try:
        from duckduckgo_search import DDGS  # type: ignore[import-untyped]
    except ImportError:
        return None

    try:
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=num, safesearch="on"))
    except Exception:
        return None

    results: list[dict[str, str]] = []
    for raw in raw_results:
        hit = _normalize_hit(raw)
        if hit:
            results.append(hit)
    return results


def _search_duckduckgo(query: str, num: int = 10) -> list[dict[str, str]]:
    """Search DuckDuckGo, preferring the package API over HTML scraping."""
    pkg_results = _ddg_package_search(query, num)
    if pkg_results is not None:
        return pkg_results
    return _ddg_html_search(query, num)


# ---------------------------------------------------------------------------
# Dynamic prompt with date context (ported from TS prompt.ts)
# ---------------------------------------------------------------------------

def _get_web_search_prompt() -> str:
    """Generate the web search prompt with dynamic date context."""
    now = datetime.now()
    current_month_year = now.strftime("%B %Y")
    return (
        "\n"
        "- Allows Claude to search the web and use the results to inform responses\n"
        "- Provides up-to-date information for current events and recent data\n"
        "- Returns search result information formatted as search result blocks, including links as markdown hyperlinks\n"
        "- Use this tool for accessing information beyond Claude's knowledge cutoff\n"
        "- Searches are performed automatically within a single API call\n"
        "\n"
        "CRITICAL REQUIREMENT - You MUST follow this:\n"
        "  - After answering the user's question, you MUST include a \"Sources:\" section at the end of your response\n"
        "  - In the Sources section, list all relevant URLs from the search results as markdown hyperlinks: [Title](URL)\n"
        "  - This is MANDATORY - never skip including sources in your response\n"
        "  - Example format:\n"
        "\n"
        "    [Your answer here]\n"
        "\n"
        "    Sources:\n"
        "    - [Source Title 1](https://example.com/1)\n"
        "    - [Source Title 2](https://example.com/2)\n"
        "\n"
        "Usage notes:\n"
        "  - Domain filtering is supported to include or block specific websites\n"
        "  - Web search is only available in the US\n"
        "\n"
        "IMPORTANT - Use the correct year in search queries:\n"
        f"  - The current month is {current_month_year}. You MUST use this year when searching for recent information, documentation, or current events.\n"
        "  - Example: If the user asks for \"latest React docs\", search for \"React documentation\" with the current year, NOT last year\n"
    )


# ---------------------------------------------------------------------------
# mapResultToApi: structured output with sources (ported from TS)
# ---------------------------------------------------------------------------

def _map_result_to_api(output: Any, tool_use_id: str) -> dict[str, Any]:
    """Format web search output for the model, including source reminder."""
    if not isinstance(output, dict):
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": str(output),
        }

    query = output.get("query", "")
    results = output.get("results", [])

    formatted = f'Web search results for query: "{query}"\n\n'

    for result in results:
        if result is None:
            continue
        if isinstance(result, str):
            formatted += result + "\n\n"
        elif isinstance(result, dict):
            content = result.get("content", [])
            if content:
                formatted += f"Links: {json.dumps(content)}\n\n"
            else:
                formatted += "No links found.\n\n"

    formatted += (
        "\nREMINDER: You MUST include the sources above in your response "
        "to the user using markdown hyperlinks."
    )

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": formatted.strip(),
    }


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _validate_input(tool_input: dict[str, Any], _ctx: ToolContext) -> ValidationResult:
    """Validate web search input, including mutual exclusion of domain filters."""
    query = tool_input.get("query", "")
    if not isinstance(query, str) or not query.strip():
        return ValidationResult.fail("Error: Missing query", error_code=1)

    allowed = tool_input.get("allowed_domains")
    blocked = tool_input.get("blocked_domains")
    if allowed and blocked:
        return ValidationResult.fail(
            "Error: Cannot specify both allowed_domains and blocked_domains in the same request",
            error_code=2,
        )

    return ValidationResult.ok()


# ---------------------------------------------------------------------------
# Format provider output (structured output with snippets and links)
# ---------------------------------------------------------------------------

def _format_output(
    query: str,
    hits: list[dict[str, str]],
    duration_seconds: float,
) -> dict[str, Any]:
    """Format search hits into the structured output schema."""
    results: list[Any] = []

    # Text snippets
    snippets = []
    for h in hits:
        desc = h.get("snippet", "")
        if desc:
            snippets.append(f"**{h['title']}** -- {desc} ({h['url']})")
    if snippets:
        results.append("\n".join(snippets))

    # Structured links
    if hits:
        results.append({
            "tool_use_id": "ddg-search",
            "content": [{"title": h["title"], "url": h["url"]} for h in hits],
        })

    if not results:
        results.append("No results found.")

    return {
        "query": query,
        "results": results,
        "duration_seconds": duration_seconds,
    }


# ---------------------------------------------------------------------------
# Main call function
# ---------------------------------------------------------------------------

def _web_search_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    query = tool_input["query"]
    if not isinstance(query, str) or not query.strip():
        raise ToolInputError("query must be a non-empty string")

    allowed_domains = tool_input.get("allowed_domains")
    blocked_domains = tool_input.get("blocked_domains")

    start_time = time.monotonic()

    # Search using DuckDuckGo (package preferred, HTML fallback)
    results = _search_duckduckgo(query, num=10)

    # Apply domain filters
    results = _apply_domain_filters(
        results,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )

    duration = time.monotonic() - start_time

    output = _format_output(query, results, duration)
    return ToolResult(name="WebSearch", output=output)


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

WebSearchTool: Tool = build_tool(
    name="WebSearch",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {
                "type": "string",
                "minLength": 2,
                "description": "The search query to use",
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only include search results from these domains",
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Never include search results from these domains",
            },
        },
        "required": ["query"],
    },
    call=_web_search_call,
    prompt=_get_web_search_prompt,
    description=lambda _input: "Search the web and return top results.",
    map_result_to_api=_map_result_to_api,
    validate_input=_validate_input,
    max_result_size_chars=50_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    search_hint="web search google query",
    get_activity_description=lambda input_data: (
        f"Searching for {(input_data or {}).get('query', '')!r}" if input_data else None
    ),
)
