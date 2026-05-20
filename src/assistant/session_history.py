"""Paginated assistant session-events client.

Port of ``typescript/src/assistant/sessionHistory.ts (88 lines)``.

Fetches pages of ``SDKMessage`` events from
``GET ${base_url}/v1/sessions/${session_id}/events``. Used by the
``claude assistant`` viewer-only REPL to lazy-load history on scroll-up.

Signature deviation from TS:
  TS ``createHistoryAuthCtx(sessionId)`` reads OAuth creds globally via
  ``prepareApiRequest``. The Python project has no equivalent global, so
  ``create_history_auth_ctx`` takes ``access_token``, ``org_uuid``, and
  ``base_url`` explicitly. Matches ``bridge/code_session_api.py`` and
  ``remote/remote_session_manager.py`` conventions. See the gap-analysis
  doc for the named-and-rejected alternative.

Error policy mirrors TS exactly (gap-analysis §2.1 bullet 4):
  * HTTP failure / non-200 (any 4xx/5xx) → return ``None``.
  * ``resp.json()`` raise → return ``None``.
  * Body not a dict → return ``None``.
  * Body is a dict but ``data`` missing / not a list → ``events=[]`` and
    still return a ``HistoryPage`` (not ``None``).
  * ``first_id`` is pass-through (``None`` if missing).
  * ``has_more`` is pass-through with a ``False`` default for missing key —
    mirrors TS JS-coercion of ``undefined`` to falsy.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

HISTORY_PAGE_SIZE: Final[int] = 100

_ANTHROPIC_VERSION: Final[str] = '2023-06-01'
_ANTHROPIC_BETA_CCR_BYOC: Final[str] = 'ccr-byoc-2025-07-29'
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 15.0


@dataclass(frozen=True)
class HistoryPage:
    """One page of session events.

    ``events`` is chronological within the page. ``first_id`` is the
    oldest event ID in the page — the ``before_id`` cursor for the
    next-older page. ``has_more=True`` means older events exist.
    """

    events: list[dict[str, Any]]
    first_id: str | None
    has_more: bool


@dataclass(frozen=True)
class HistoryAuthCtx:
    """Reusable auth bundle for paged history fetches.

    Built once via ``create_history_auth_ctx``, reused across pages.
    ``headers`` is typed as ``Mapping`` (read-only contract); the
    implementation passes a plain ``dict`` but consumers must not
    mutate it.
    """

    base_url: str
    headers: Mapping[str, str]


def _oauth_headers(access_token: str, org_uuid: str) -> dict[str, str]:
    """Combined header helper.

    Bundles two concerns that TS separates: ``getOAuthHeaders(accessToken)``
    (Authorization + Content-Type + anthropic-version) **and** the
    ``anthropic-beta`` / ``x-organization-uuid`` pinning that TS does at
    the ``createHistoryAuthCtx`` call site. Merging them here keeps the
    builder pure; the wire output is byte-identical to TS.

    Keeps ``Content-Type: application/json`` on the GET for parity even
    though there is no request body (TS shares ``getOAuthHeaders`` with
    POST callers — see gap-analysis §2.1 bullet 7).
    """
    return {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'anthropic-version': _ANTHROPIC_VERSION,
        'anthropic-beta': _ANTHROPIC_BETA_CCR_BYOC,
        'x-organization-uuid': org_uuid,
    }


async def create_history_auth_ctx(
    session_id: str,
    access_token: str,
    org_uuid: str,
    *,
    base_url: str = 'https://api.anthropic.com',
) -> HistoryAuthCtx:
    """Build a reusable auth context bound to a session.

    Pure builder — no I/O. Kept ``async`` for parity with TS even though
    the Python implementation performs no I/O. A future Python port of
    ``prepare_api_request`` may add token-fetch I/O here without changing
    the call sites.
    """
    return HistoryAuthCtx(
        base_url=f'{base_url.rstrip("/")}/v1/sessions/{session_id}/events',
        headers=_oauth_headers(access_token, org_uuid),
    )


async def _fetch_page(
    ctx: HistoryAuthCtx,
    params: dict[str, str | int | bool],
    label: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> HistoryPage | None:
    """Shared GET wrapper. Returns ``None`` on any failure (network,
    timeout, non-200, non-dict body, JSON parse error).

    The ``label`` arg appears only in the debug log line and disambiguates
    the two call sites in postmortems (matches TS line 59).
    """
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS) as fresh:
                resp = await fresh.get(
                    ctx.base_url, headers=ctx.headers, params=params,
                )
        else:
            resp = await client.get(
                ctx.base_url,
                headers=ctx.headers,
                params=params,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
    except (httpx.HTTPError, httpx.TimeoutException):
        logger.debug('[%s] HTTP error', label)
        return None

    if resp.status_code != 200:
        logger.debug('[%s] HTTP %d', label, resp.status_code)
        return None

    try:
        data = resp.json()
    except ValueError:
        logger.debug('[%s] non-JSON body', label)
        return None

    if not isinstance(data, dict):
        logger.debug('[%s] body is not a dict', label)
        return None

    events_raw = data.get('data')
    events: list[dict[str, Any]] = events_raw if isinstance(events_raw, list) else []
    return HistoryPage(
        events=events,
        first_id=data.get('first_id'),
        # ``False`` default mirrors TS JS-coercion of missing ``has_more``
        # (undefined → falsy). No bool() cast — server is contract source.
        has_more=data.get('has_more', False),
    )


async def fetch_latest_events(
    ctx: HistoryAuthCtx,
    limit: int = HISTORY_PAGE_SIZE,
    *,
    client: httpx.AsyncClient | None = None,
) -> HistoryPage | None:
    """Newest page: last ``limit`` events, chronological, via
    ``anchor_to_latest=true``. ``has_more=True`` means older events exist.
    Mirrors TS ``fetchLatestEvents``.
    """
    return await _fetch_page(
        ctx,
        {'limit': limit, 'anchor_to_latest': True},
        'fetch_latest_events',
        client=client,
    )


async def fetch_older_events(
    ctx: HistoryAuthCtx,
    before_id: str,
    limit: int = HISTORY_PAGE_SIZE,
    *,
    client: httpx.AsyncClient | None = None,
) -> HistoryPage | None:
    """Older page: events immediately before ``before_id`` cursor.
    Mirrors TS ``fetchOlderEvents``.
    """
    return await _fetch_page(
        ctx,
        {'limit': limit, 'before_id': before_id},
        'fetch_older_events',
        client=client,
    )


__all__ = [
    'HISTORY_PAGE_SIZE',
    'HistoryAuthCtx',
    'HistoryPage',
    'create_history_auth_ctx',
    'fetch_latest_events',
    'fetch_older_events',
]
