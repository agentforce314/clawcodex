"""JWT payload decoder + proactive token refresh scheduler.

Ports ``typescript/src/bridge/jwtUtils.ts``. **Does NOT verify
signatures** — the TS source doesn't either; this is a payload reader,
not a JWT validator.

Two surfaces:

1. ``decode_jwt_payload`` / ``decode_jwt_expiry`` — pure functions for
   reading the payload + ``exp`` claim from a base64url-encoded JWT.
   Strips the ``sk-ant-si-`` session-ingress prefix if present.

2. ``TokenRefreshScheduler`` — per-session timer that proactively
   refreshes tokens 5 minutes before expiry, with a generation counter
   to invalidate cancelled-then-rescheduled refreshes, fallback
   ``schedule_from_expires_in`` for opaque JWTs (30 s floor clamp), and
   a 30-min follow-up so long-running sessions stay authenticated.
   Constants match TS exactly.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Mapping, Union

logger = logging.getLogger(__name__)

# ─── JWT payload reading ───────────────────────────────────────────────────


def decode_jwt_payload(token: str) -> Mapping[str, Any] | None:
    """Decode a JWT's payload segment without verifying the signature.

    Strips the ``sk-ant-si-`` session-ingress prefix if present. Returns
    the parsed JSON payload as ``Mapping`` (project convention uses
    ``Any`` not ``object``; see ``src/types/messages.py:115``), or
    ``None`` if the token is malformed or the payload is not valid JSON.

    Mirrors ``typescript/src/bridge/jwtUtils.ts:21-32``.
    """
    jwt = token[len('sk-ant-si-'):] if token.startswith('sk-ant-si-') else token
    parts = jwt.split('.')
    if len(parts) != 3 or not parts[1]:
        return None
    try:
        # base64url payload may omit padding.
        body = parts[1]
        body += '=' * ((-len(body)) % 4)
        decoded = base64.urlsafe_b64decode(body).decode('utf-8')
        parsed: Any = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def decode_jwt_expiry(token: str) -> int | None:
    """Return the ``exp`` claim (Unix seconds) or None if unparseable.

    Mirrors ``typescript/src/bridge/jwtUtils.ts:38-49``.
    """
    payload = decode_jwt_payload(token)
    if payload is None:
        return None
    exp = payload.get('exp')
    if isinstance(exp, int):
        return exp
    return None


# ─── Token refresh scheduler ───────────────────────────────────────────────

# Constants — keep identical to TS so behavior matches.
TOKEN_REFRESH_BUFFER_MS = 5 * 60 * 1000
"""Refresh buffer: request a new token this many ms before expiry."""

FALLBACK_REFRESH_INTERVAL_MS = 30 * 60 * 1000
"""Fallback refresh interval when the new token's expiry is unknown."""

MAX_REFRESH_FAILURES = 3
"""Cap on consecutive failures before giving up on the refresh chain."""

REFRESH_RETRY_DELAY_MS = 60_000
"""Retry delay when ``get_access_token`` returns None."""

SCHEDULE_FROM_EXPIRES_IN_FLOOR_MS = 30_000
"""30 s floor for ``schedule_from_expires_in`` to avoid tight-loop refresh."""


GetAccessToken = Callable[[], Union[str, None, Awaitable[Union[str, None]]]]
OnRefresh = Callable[[str, str], None]


def _format_duration_ms(ms: float) -> str:
    """Format a millisecond duration as a human-readable string."""
    if ms < 60_000:
        return f'{round(ms / 1000)}s'
    minutes = int(ms // 60_000)
    seconds = round((ms % 60_000) / 1000)
    return f'{minutes}m {seconds}s' if seconds > 0 else f'{minutes}m'


class TokenRefreshScheduler:
    """Per-session refresh-timer manager.

    Mirrors ``typescript/src/bridge/jwtUtils.ts:72-256`` (``createTokenRefreshScheduler``).

    Public API:
        schedule(session_id, token)               — refresh based on JWT.exp
        schedule_from_expires_in(sid, sec)        — refresh based on TTL
        cancel(session_id)                        — cancel one session
        cancel_all()                              — cancel all sessions

    Concurrency: all methods assume single-event-loop use AND must be
    called from inside a running asyncio loop (we use
    ``asyncio.get_running_loop()`` rather than the deprecated
    ``get_event_loop()`` to avoid the implicit-loop-creation hazard on
    Python 3.12+). Cancellation via ``loop.call_later(...).cancel()`` is
    synchronous — returns immediately; the callback is just unscheduled.
    """

    def __init__(
        self,
        get_access_token: GetAccessToken,
        on_refresh: OnRefresh,
        label: str,
        refresh_buffer_ms: int = TOKEN_REFRESH_BUFFER_MS,
    ) -> None:
        self._get_access_token = get_access_token
        self._on_refresh = on_refresh
        self._label = label
        self._refresh_buffer_ms = refresh_buffer_ms
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._failure_counts: dict[str, int] = {}
        # Generation counter per session — bumped by schedule() and
        # cancel() so in-flight async _do_refresh calls can detect when
        # they've been superseded and bail out.
        self._generations: dict[str, int] = {}

    # ─── Public API ────────────────────────────────────────────────────────

    def schedule(self, session_id: str, token: str) -> None:
        """Schedule refresh based on the token's ``exp`` claim."""
        expiry_seconds = decode_jwt_expiry(token)
        if expiry_seconds is None:
            # Token is not a decodable JWT (e.g., an OAuth token from the
            # REPL bridge WS open handler). Preserve any existing timer
            # (such as the follow-up refresh set by _do_refresh) so the
            # refresh chain isn't broken.
            logger.debug(
                '[%s:token] Could not decode JWT expiry for sessionId=%s, token prefix=%s..., keeping existing timer',
                self._label,
                session_id,
                token[:15],
            )
            return

        # Cancel any existing timer — we have a concrete expiry to replace it.
        existing = self._timers.pop(session_id, None)
        if existing is not None:
            existing.cancel()

        gen = self._next_generation(session_id)
        delay_ms = expiry_seconds * 1000 - int(time.time() * 1000) - self._refresh_buffer_ms
        if delay_ms <= 0:
            logger.debug(
                '[%s:token] Token for sessionId=%s already past refresh buffer; refreshing immediately',
                self._label,
                session_id,
            )
            asyncio.get_running_loop().create_task(self._do_refresh(session_id, gen))
            return

        logger.debug(
            '[%s:token] Scheduled token refresh for sessionId=%s in %s (buffer=%ss)',
            self._label,
            session_id,
            _format_duration_ms(delay_ms),
            self._refresh_buffer_ms / 1000,
        )
        loop = asyncio.get_running_loop()
        handle = loop.call_later(delay_ms / 1000, self._fire_refresh, session_id, gen)
        self._timers[session_id] = handle

    def schedule_from_expires_in(self, session_id: str, expires_in_seconds: int) -> None:
        """Schedule refresh from an explicit TTL (for opaque JWTs).

        Used by callers whose JWT is opaque (e.g., POST /v1/code/sessions/
        {id}/bridge returns ``expires_in`` directly).

        Clamped to ``SCHEDULE_FROM_EXPIRES_IN_FLOOR_MS`` (30 s) to avoid a
        tight-loop refresh if the buffer exceeds the server's TTL.
        """
        existing = self._timers.pop(session_id, None)
        if existing is not None:
            existing.cancel()
        gen = self._next_generation(session_id)
        delay_ms = max(
            expires_in_seconds * 1000 - self._refresh_buffer_ms,
            SCHEDULE_FROM_EXPIRES_IN_FLOOR_MS,
        )
        logger.debug(
            '[%s:token] Scheduled token refresh for sessionId=%s in %s (expires_in=%ss, buffer=%ss)',
            self._label,
            session_id,
            _format_duration_ms(delay_ms),
            expires_in_seconds,
            self._refresh_buffer_ms / 1000,
        )
        loop = asyncio.get_running_loop()
        handle = loop.call_later(delay_ms / 1000, self._fire_refresh, session_id, gen)
        self._timers[session_id] = handle

    def cancel(self, session_id: str) -> None:
        """Cancel the timer for one session and bump its generation."""
        # Bump generation to invalidate any in-flight async _do_refresh.
        self._next_generation(session_id)
        timer = self._timers.pop(session_id, None)
        if timer is not None:
            timer.cancel()
        self._failure_counts.pop(session_id, None)

    def cancel_all(self) -> None:
        """Cancel all timers and bump all generations."""
        for sid in list(self._generations.keys()):
            self._next_generation(sid)
        for handle in self._timers.values():
            handle.cancel()
        self._timers.clear()
        self._failure_counts.clear()

    # ─── Internal ──────────────────────────────────────────────────────────

    def _next_generation(self, session_id: str) -> int:
        gen = self._generations.get(session_id, 0) + 1
        self._generations[session_id] = gen
        return gen

    def _fire_refresh(self, session_id: str, gen: int) -> None:
        """Timer callback — schedules the async refresh as a task."""
        asyncio.get_running_loop().create_task(self._do_refresh(session_id, gen))

    async def _do_refresh(self, session_id: str, gen: int) -> None:
        """Run one refresh cycle.

        If ``get_access_token`` returns None, increments failure count
        and reschedules until ``MAX_REFRESH_FAILURES`` is reached.
        On success: fires ``on_refresh(session_id, token)`` and schedules
        the follow-up at ``FALLBACK_REFRESH_INTERVAL_MS``.
        """
        oauth_token: str | None = None
        try:
            result = self._get_access_token()
            if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                oauth_token = await result  # type: ignore[assignment]
            else:
                oauth_token = result  # type: ignore[assignment]
        except Exception as exc:  # noqa: BLE001 -- log + continue, like TS
            logger.error(
                '[%s:token] get_access_token threw for sessionId=%s: %s',
                self._label,
                session_id,
                exc,
            )

        # If the session was cancelled or rescheduled while we were
        # awaiting, the generation will have changed — bail out to avoid
        # orphaning timers.
        if self._generations.get(session_id) != gen:
            logger.debug(
                '[%s:token] _do_refresh for sessionId=%s stale (gen %s vs %s), skipping',
                self._label,
                session_id,
                gen,
                self._generations.get(session_id),
            )
            return

        if not oauth_token:
            failures = self._failure_counts.get(session_id, 0) + 1
            self._failure_counts[session_id] = failures
            logger.error(
                '[%s:token] No OAuth token available for refresh sessionId=%s (failure %d/%d)',
                self._label,
                session_id,
                failures,
                MAX_REFRESH_FAILURES,
            )
            if failures < MAX_REFRESH_FAILURES:
                loop = asyncio.get_running_loop()
                handle = loop.call_later(
                    REFRESH_RETRY_DELAY_MS / 1000,
                    self._fire_refresh,
                    session_id,
                    gen,
                )
                self._timers[session_id] = handle
            return

        # Success: reset failure counter; fire the refresh callback;
        # schedule the follow-up.
        self._failure_counts.pop(session_id, None)
        logger.debug(
            '[%s:token] Refreshing token for sessionId=%s: new prefix=%s...',
            self._label,
            session_id,
            oauth_token[:15],
        )
        try:
            self._on_refresh(session_id, oauth_token)
        except Exception as exc:  # noqa: BLE001 -- never let on_refresh throw kill the chain
            logger.error('[%s:token] on_refresh raised for sessionId=%s: %s', self._label, session_id, exc)

        # Schedule a follow-up so long-running sessions stay authenticated.
        loop = asyncio.get_running_loop()
        handle = loop.call_later(
            FALLBACK_REFRESH_INTERVAL_MS / 1000,
            self._fire_refresh,
            session_id,
            gen,
        )
        self._timers[session_id] = handle
        logger.debug(
            '[%s:token] Scheduled follow-up refresh for sessionId=%s in %s',
            self._label,
            session_id,
            _format_duration_ms(FALLBACK_REFRESH_INTERVAL_MS),
        )


__all__ = [
    'FALLBACK_REFRESH_INTERVAL_MS',
    'GetAccessToken',
    'MAX_REFRESH_FAILURES',
    'OnRefresh',
    'REFRESH_RETRY_DELAY_MS',
    'SCHEDULE_FROM_EXPIRES_IN_FLOOR_MS',
    'TOKEN_REFRESH_BUFFER_MS',
    'TokenRefreshScheduler',
    'decode_jwt_expiry',
    'decode_jwt_payload',
]
