from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from src.utils.abortable_net import call_with_abort

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
# Tavily Search API — https://api.tavily.com/search
# ---------------------------------------------------------------------------
# Replaces the legacy DuckDuckGo backend (which scraped HTML and no longer
# returns results). Configure with the TAVILY_API_KEY environment variable (a
# key starting with ``tvly-``). Mirrors the TS adapter at
# ``typescript/src/tools/WebSearchTool/providers/tavily.ts``.

_TAVILY_URL = "https://api.tavily.com/search"


def _tavily_api_key() -> str | None:
    # Resolved via the secret store: an exported TAVILY_API_KEY wins, otherwise
    # the value stored in ~/.clawcodex/config.json under "env". Lazy import keeps
    # the tool module free of a config dependency at import time.
    from src.secret_store import get_secret

    key = (get_secret("TAVILY_API_KEY") or "").strip()
    return key or None


def is_web_search_configured() -> bool:
    """Whether a web-search backend is configured (a Tavily API key is set)."""
    return _tavily_api_key() is not None


def _tavily_search(
    query: str, num: int = 15, abort_signal=None
) -> list[dict[str, str]]:
    """Search the web via Tavily.

    Raises ``ToolInputError`` when ``TAVILY_API_KEY`` is unset (so the model and
    user get a clear "configure search" signal rather than silent empty results)
    or when the API call fails.
    """
    key = _tavily_api_key()
    if not key:
        raise ToolInputError(
            "Web search is not configured. Set the TAVILY_API_KEY environment "
            "variable (get a free key at https://app.tavily.com)."
        )

    body = json.dumps(
        {"query": query, "max_results": max(1, min(num, 20)), "include_answer": False}
    ).encode("utf-8")
    req = urllib.request.Request(
        _TAVILY_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )

    def _request() -> str:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read(2_000_000).decode("utf-8", errors="replace")

    try:
        # ESC unblocks the caller immediately; the worker dies at the
        # 20s socket timeout (#276).
        raw = call_with_abort(_request, abort_signal)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(2000).decode("utf-8", errors="replace")
        except Exception:
            pass
        raise ToolInputError(f"Tavily search error {exc.code}: {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise ToolInputError(f"Tavily search failed: {exc.reason}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolInputError("Tavily returned a non-JSON response.") from exc

    hits: list[dict[str, str]] = []
    for raw_hit in data.get("results") or []:
        hit = _normalize_hit(raw_hit)  # maps title/url/content via field aliases
        if hit:
            hits.append(hit)
    return hits


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
            "tool_use_id": "tavily-search",
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

    # Search via Tavily (requires TAVILY_API_KEY).
    results = _tavily_search(
        query, num=15, abort_signal=context.abort_controller.signal
    )

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
    # Mirrors TS WebSearchTool.toAutoClassifierInput (returns the query).
    to_auto_classifier_input=lambda input_data: (input_data or {}).get("query", ""),
)
