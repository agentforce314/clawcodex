from __future__ import annotations

import json
import time
import urllib.error
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
    """Whether a web-search backend is configured: a Tavily API key, or a
    DeepSeek API key the official Search API accepts."""
    return _tavily_api_key() is not None or _deepseek_search_api_key() is not None


def _tavily_search(query: str, num: int = 15) -> list[dict[str, str]]:
    """Search the web via Tavily.

    Raises ``ToolInputError`` when ``TAVILY_API_KEY`` is unset (so the model and
    user get a clear "configure search" signal rather than silent empty results)
    or when the API call fails.
    """
    key = _tavily_api_key()
    if not key:
        raise ToolInputError(
            "Web search is not configured. Set the TAVILY_API_KEY environment "
            "variable (get a free key at https://app.tavily.com), or configure "
            "a DeepSeek API key to search through DeepSeek's Search API."
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
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read(2_000_000).decode("utf-8", errors="replace")
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
# DeepSeek Search API — https://api.deepseek.com/v1/tools/search
# ---------------------------------------------------------------------------
# DeepSeek opened its in-house search engine as an API on 2026-10-17
# (https://api-docs.deepseek.com/guides/search). It takes the same API key as
# DeepSeek's chat models, so a configured DeepSeek provider is enough for
# WebSearch to work without a second vendor's key. Tavily stays first whenever
# TAVILY_API_KEY is set: that key is an explicit choice of search backend,
# while this one bills per search ($0.75 per 1,000 successful requests from
# 2026-11-01) to an account configured for chat.
#
# The sibling Fetch API (``/v1/tools/fetch``) is deliberately not adopted:
# WebFetch already works without a key, and keeps its own address and size
# checks that the Fetch API would hand to the vendor.

_DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
_DEEPSEEK_SEARCH_HOST = "api.deepseek.com"
# The API rejects a query outside 1-128 Unicode characters and a ``count``
# outside 1-50.
_DEEPSEEK_QUERY_MAX_CHARS = 128
_DEEPSEEK_MAX_COUNT = 50


def _deepseek_search_api_key() -> str | None:
    """The DeepSeek API key, when the official Search API would accept it.

    Resolved the way the DeepSeek provider resolves its own
    (``providers.deepseek.api_key``, then ``DEEPSEEK_API_KEY``). A
    ``providers.deepseek.base_url`` on any other host means the key belongs to
    a gateway: it must not be sent to api.deepseek.com, and the gateway is not
    known to serve ``/v1/tools``, so the backend is unavailable.
    """
    try:
        from src.config import get_provider_config

        cfg = get_provider_config("deepseek")
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    base_url = cfg.get("base_url")
    if isinstance(base_url, str) and base_url.strip():
        if _safe_hostname(base_url.strip()) != _DEEPSEEK_SEARCH_HOST:
            return None
    from src.providers import resolve_api_key

    key = (resolve_api_key("deepseek", cfg) or "").strip()
    return key or None


def _deepseek_search_error(exc: urllib.error.HTTPError) -> str:
    """The vendor's message for a failed search, plus its documented remedy."""
    detail = ""
    try:
        payload = json.loads(exc.read(4000).decode("utf-8", errors="replace"))
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else error
        if isinstance(message, str):
            detail = message.strip().rstrip(".")
    except Exception:
        pass
    text = f"DeepSeek search error {exc.code}"
    if detail:
        text += f": {detail[:300]}"
    if exc.code == 401:
        text += ". DeepSeek rejected the API key."
    elif exc.code == 402:
        text += (
            ". The DeepSeek account balance is too low; it has to be topped up "
            "before searching again."
        )
    elif exc.code in (408, 429) or exc.code >= 500:
        text += ". This is transient; wait a moment and search again."
    return text


def _deepseek_search(query: str, num: int = 15) -> list[dict[str, str]]:
    """Search the web via DeepSeek's Search API.

    Raises ``ToolInputError`` when no usable DeepSeek key is configured, when
    the query is longer than the API accepts, or when the API call fails.
    Results keep the service's rank order; each hit carries the result's
    short ``snippet`` (its long ``summary`` and ``content`` are left out to
    keep the tool result the size Tavily's is).
    """
    key = _deepseek_search_api_key()
    if not key:
        raise ToolInputError(
            "DeepSeek web search is not configured. Set DEEPSEEK_API_KEY or "
            "configure the deepseek provider's API key."
        )
    if len(query) > _DEEPSEEK_QUERY_MAX_CHARS:
        raise ToolInputError(
            f"DeepSeek search queries are limited to {_DEEPSEEK_QUERY_MAX_CHARS} "
            f"characters; this one has {len(query)}. Shorten it and search again."
        )

    body = json.dumps(
        {
            "query": query,
            "count": max(1, min(num, _DEEPSEEK_MAX_COUNT)),
            "need_content": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        _DEEPSEEK_SEARCH_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read(2_000_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise ToolInputError(_deepseek_search_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise ToolInputError(f"DeepSeek search failed: {exc.reason}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolInputError("DeepSeek search returned a non-JSON response.") from exc
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise ToolInputError("DeepSeek search returned a response without results.")

    hits: list[dict[str, str]] = []
    for raw_hit in results:
        hit = _normalize_hit(raw_hit)  # title/url, and snippet before summary
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
    source: str = "tavily-search",
) -> dict[str, Any]:
    """Format search hits into the structured output schema.

    ``source`` labels the links block with the backend that produced it.
    """
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
            "tool_use_id": source,
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

    # Tavily when TAVILY_API_KEY is set; otherwise DeepSeek's Search API when a
    # DeepSeek key is configured. With neither, _tavily_search raises the
    # "not configured" error, which names both.
    if _tavily_api_key() is None and _deepseek_search_api_key() is not None:
        source = "deepseek-search"
        results = _deepseek_search(query, num=15)
    else:
        source = "tavily-search"
        results = _tavily_search(query, num=15)

    # Apply domain filters
    results = _apply_domain_filters(
        results,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )

    duration = time.monotonic() - start_time

    output = _format_output(query, results, duration, source=source)
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
